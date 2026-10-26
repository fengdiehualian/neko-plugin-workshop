"""手机客户端：局域网（asyncio TCP + UDP 自动发现）+ 蓝牙（winrt，可选）。

安全设计：
- token 不在任何通道上明文传输；控制命令走挑战-应答认证：
  1) 客户端发送 {cmd, nonce} → 手机回一次性 challenge；
  2) 客户端在同一条连接上回发 proof = HMAC-SHA256(token, "cmd|nonce|challenge")；
  3) proof 与命令、客户端 nonce、挑战三者绑定，不能挪用到别的命令，也无法重放。
- 控制命令的第一包若直接 ok=true（未要求验证），客户端视为遭劫持并拒绝。
- 挑战按连接保存：一条连接上的验证互相不干扰。
- UDP 发现请求带随机数并要求原样回显，防止旧广播包重放。

协议：NDJSON。纯标准库；蓝牙通道需要可选安装 winrt-* 包。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import socket
import tempfile
from pathlib import Path
from typing import Any, Optional

CONTROL_CMDS = {"lock", "applock", "unlock"}

STATE_PATH = Path(tempfile.gettempdir()) / "yuilock_phone_ip.json"


def _auth_proof(token: str, cmd: str, nonce: str, challenge: str) -> str:
    msg = f"{cmd}|{nonce}|{challenge}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()


class YuiLockClient:
    def __init__(self, host: Any = "auto", port: int = 48912, token: str = "",
                 transport: str = "auto", bt_device_name: str = "", logger=None):
        self.host = str(host or "auto").strip()
        self.port = int(port)
        self.token = str(token or "")
        self.transport = str(transport or "auto").lower()
        self.bt_device_name = str(bt_device_name or "")
        self.log = logger

    # ---------------- 局域网 ----------------

    async def discover(self, timeout: float = 2.0) -> Optional[str]:
        """UDP 广播发现手机（请求带随机数，回复必须原样回显），返回 IP。

        注意：不要用 remote_addr（connected UDP socket）——Windows/Linux 会丢弃
        源地址不等于连接对端的回包，手机从真实 IP 回复就会被内核丢掉，
        发现永远失败。这里用非连接 socket 显式 sendto，并向
        255.255.255.255 和每个本地接口的子网广播地址各发一次（多网卡/多网段环境）。
        """
        loop = asyncio.get_running_loop()
        found: asyncio.Queue = asyncio.Queue()
        nonce = secrets.token_hex(8)
        req = json.dumps({"yui_lock_discover": True, "n": nonce})
        self_token = self.token

        class P(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr):
                try:
                    m = json.loads(data.decode("utf-8"))
                    if not (m.get("yui_lock_service") and m.get("n") == nonce):
                        return
                    # 已配对（有 token）时，发现应答必须带 HMAC 防伪造
                    if self_token:
                        want = hmac.new(self_token.encode("utf-8"),
                                        ("discover|" + nonce).encode("utf-8"),
                                        hashlib.sha256).hexdigest()
                        if not hmac.compare_digest(want, str(m.get("sig", "")).lower()):
                            return
                    found.put_nowait(addr[0])
                except Exception:
                    pass

        dests = self._broadcast_targets()
        transport, _ = await loop.create_datagram_endpoint(
            P, local_addr=("0.0.0.0", 0), allow_broadcast=True)
        try:
            for dest in dests:
                try:
                    transport.sendto(req.encode("utf-8"), (dest, self.port))
                except OSError:
                    continue
            return await asyncio.wait_for(found.get(), timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            transport.close()

    @staticmethod
    def _broadcast_targets() -> list[str]:
        """255.255.255.255 + 本机每个 IPv4 网段的子网广播地址（192.168.1.x → 192.168.1.255）。"""
        targets = ["255.255.255.255"]
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if not ip or ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                parts = ip.split(".")
                if len(parts) == 4:
                    targets.append(".".join(parts[:3] + ["255"]))
        except Exception:
            pass
        # 去重保序
        seen: set[str] = set()
        return [t for t in targets if not (t in seen or seen.add(t))]

    async def _exchange_lan(self, ip: str, payload: dict, timeout: float) -> dict:
        """在同一条 TCP 连接上完成（可能的）挑战-应答两步。"""
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, self.port), timeout)
        try:
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await writer.drain()
            line1 = await asyncio.wait_for(reader.readline(), timeout)
            if not line1:
                raise RuntimeError("手机无响应")
            r1 = json.loads(line1.decode("utf-8"))
            if payload.get("cmd") in CONTROL_CMDS:
                # 控制命令的第一包必须是对称挑战信封：直接 ok=true 是伪造；
                # 未签名的失败也不采信（防中间人把结果改写成失败）
                is_envelope = r1.get("auth") == "hmac-sha256" and r1.get("challenge")
                if r1.get("ok") and not r1.get("auth"):
                    return {"ok": False,
                            "error": "认证流程异常：对端未要求配对验证，已拒绝执行"}
                if not is_envelope:
                    return {"ok": False,
                            "error": "认证流程异常：对端未返回配对挑战，响应不可信（可能遭篡改或配置不匹配）"}
                proof = _auth_proof(self.token, str(payload.get("cmd")),
                                    str(payload.get("nonce")), str(r1["challenge"]))
                writer.write((json.dumps(dict(payload, proof=proof)) + "\n").encode("utf-8"))
                await writer.drain()
                line2 = await asyncio.wait_for(reader.readline(), timeout)
                if not line2:
                    raise RuntimeError("手机无响应")
                r2 = json.loads(line2.decode("utf-8"))
                # 失败与成功同样验签：防把已签的成功改写成失败
                if not self._verify_resp(r2, payload, str(r1["challenge"])):
                    return {"ok": False,
                            "error": "响应签名校验失败，结果不可信（可能遭篡改）"}
                return r2
            return r1
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    # ---------------- 蓝牙（可选） ----------------

    async def _exchange_bt(self, payload: dict, timeout: float) -> dict:
        try:
            import winrt.windows.devices.bluetooth as wbt
            import winrt.windows.devices.bluetooth.rfcomm as wrf
            import winrt.windows.devices.enumeration as wen
            import winrt.windows.networking.sockets as wns
            import winrt.windows.storage.streams as wst
        except Exception:
            return {"ok": False, "error": (
                "蓝牙通道未启用：需要在 N.E.K.O. 的 Python 环境安装 "
                "winrt-runtime winrt-Windows.Devices.Bluetooth winrt-Windows.Devices.Bluetooth.Rfcomm "
                "winrt-Windows.Devices.Enumeration winrt-Windows.Networking.Sockets "
                "winrt-Windows.Storage.Streams（局域网不受影响）")}
        selector = wrf.RfcommDeviceService.get_device_selector(wrf.RfcommServiceId.serial_port())
        devs = await wen.DeviceInformation.find_all_async(selector)
        target = None
        for d in devs:
            if not self.bt_device_name or self.bt_device_name.lower() in (d.name or "").lower():
                target = d
                break
        if target is None:
            return {"ok": False, "error": "未找到已配对的手机（先在 Windows 蓝牙设置里配对）"}
        bd = await wbt.BluetoothDevice.from_device_async(target)
        svc = await wrf.RfcommDeviceService.from_bluetooth_address_async(
            bd.bluetooth_address, wrf.RfcommServiceId.serial_port())
        if svc is None:
            return {"ok": False, "error": "手机蓝牙服务不可达（确认手机上 Yui Lock 服务已启动）"}
        sock = wns.StreamSocket()
        await asyncio.wait_for(sock.connect_async(
            svc.connection_host_name, svc.connection_service_name), timeout)

        async def send_recv(pl: dict) -> dict:
            writer = wst.DataWriter(sock.output_stream)
            writer.write_bytes((json.dumps(pl) + "\n").encode("utf-8"))
            await writer.store_async()
            await writer.flush_async()
            reader = wst.DataReader(sock.input_stream)
            await asyncio.wait_for(reader.load_async(4096), timeout)
            n = reader.unconsumed_data_length
            data = bytes(reader.read_bytes(n)) if n else b""
            if not data:
                raise RuntimeError("手机无响应")
            return json.loads(data.decode("utf-8"))

        try:
            r1 = await send_recv(payload)
            if payload.get("cmd") in CONTROL_CMDS:
                is_envelope = r1.get("auth") == "hmac-sha256" and r1.get("challenge")
                if r1.get("ok") and not r1.get("auth"):
                    return {"ok": False,
                            "error": "认证流程异常：对端未要求配对验证，已拒绝执行"}
                if not is_envelope:
                    return {"ok": False,
                            "error": "认证流程异常：对端未返回配对挑战，响应不可信（可能遭篡改或配置不匹配）"}
                proof = _auth_proof(self.token, str(payload.get("cmd")),
                                    str(payload.get("nonce")), str(r1["challenge"]))
                r2 = await send_recv(dict(payload, proof=proof))
                # 失败与成功同样验签：防把已签的成功改写成失败
                if not self._verify_resp(r2, payload, str(r1["challenge"])):
                    return {"ok": False,
                            "error": "响应签名校验失败，结果不可信（可能遭篡改）"}
                return r2
            return r1
        finally:
            sock.close()

    def _verify_resp(self, r2: dict, payload: dict, challenge: str) -> bool:
        """核对手机对最终结果的 HMAC（覆盖 challenge/命令/nonce/结果）。"""
        sig = str(r2.get("sig", ""))
        if not sig:
            return False
        msg = "resp|{}|{}|{}|{}|{}".format(
            payload.get("cmd"), payload.get("nonce"), challenge,
            "1" if r2.get("ok") else "0", str(r2.get("error", "")))
        want = hmac.new(self.token.encode("utf-8"), msg.encode("utf-8"),
                        hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, sig.lower())

    # ---------------- 对外 ----------------

    async def send(self, payload: dict, timeout: float = 8.0) -> dict:
        """发送命令。控制命令在同一条连接上自动完成挑战-应答认证。"""
        payload = dict(payload)
        cmd = payload.get("cmd")
        if cmd in CONTROL_CMDS:
            if not self.token:
                return {"ok": False,
                        "error": "插件未配置 token（手机端已启用配对令牌校验，请在 plugin.toml [yuilock] 填写）"}
            payload["nonce"] = secrets.token_hex(8)
        if self.transport == "bt":
            return await self._wrap_bt(payload, timeout)
        try:
            return await self._lan_path(payload, timeout)
        except Exception as e:
            if self.transport == "lan":
                return {"ok": False, "error": f"局域网: {e}"}
            bt_err = await self._wrap_bt(payload, timeout)
            return bt_err if bt_err.get("ok") or bt_err.get("auth") else {
                "ok": False, "error": f"局域网: {e}；蓝牙: {bt_err.get('error', '不可用')}"}

    async def _lan_path(self, payload: dict, timeout: float) -> dict:
        ip = self.host
        if ip in ("", "auto", "none", None):
            # 1) 先试上次成功过的 IP（手机 IP 一般不变，跳过广播，更快更稳）
            cached = self._load_cached_ip()
            if cached:
                try:
                    return await self._exchange_lan(cached, payload, timeout)
                except Exception:
                    pass  # 缓存失效（换了网/手机重启），落到广播
            # 2) 广播发现，多试两轮（手机从冻结中唤醒需要一点时间）
            ip = None
            for _ in range(2):
                ip = await self.discover(timeout=2.5)
                if ip:
                    break
            if not ip:
                raise RuntimeError(
                    "广播未发现手机。排查：① 手机 Yui Lock 服务是否已启动；"
                    "② 手机与电脑是否同一 Wi-Fi（部分路由开了 AP 隔离）；"
                    "③ 手机是否被系统冻结/杀后台——在 Yui Lock 里点「忽略电池优化」并"
                    "允许自启动/后台运行（国产手机需在系统设置或管家应用里设置）")
            self._save_cached_ip(ip)
        return await self._exchange_lan(ip, payload, timeout)

    def _load_cached_ip(self) -> Optional[str]:
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            ip = str(data.get("ip", "")).strip()
            if ip and ip.count(".") == 3:
                return ip
        except Exception:
            pass
        return None

    def _save_cached_ip(self, ip: str) -> None:
        try:
            STATE_PATH.write_text(json.dumps({"ip": ip}), encoding="utf-8")
        except Exception:
            pass

    async def _wrap_bt(self, payload: dict, timeout: float) -> dict:
        try:
            return await self._exchange_bt(payload, timeout)
        except Exception as e:
            return {"ok": False, "error": f"蓝牙: {e}"}

    async def ping(self) -> dict:
        return await self.send({"cmd": "ping"})

    async def lock_screen(self) -> dict:
        return await self.send({"cmd": "lock"})

    async def lock_apps(self) -> dict:
        return await self.send({"cmd": "applock"})

    async def unlock(self) -> dict:
        return await self.send({"cmd": "unlock"})
