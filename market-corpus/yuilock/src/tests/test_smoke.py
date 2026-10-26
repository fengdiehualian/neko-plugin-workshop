"""
Yui 手机/电脑锁 - 冒烟测试

校验插件结构、plugin.toml 入口、手机客户端协议（挑战-应答认证：
proof 绑定 cmd|nonce|challenge、按连接隔离、拒绝未认证直接成功）。
不依赖 plugin.sdk。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import secrets
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_ID = "yuilock"


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPluginStructure(unittest.TestCase):
    def test_plugin_toml_exists(self):
        self.assertTrue(
            os.path.isfile(os.path.join(ROOT, "plugin.toml")),
            "根目录 plugin.toml 缺失",
        )

    def test_entry_module_exists(self):
        entry = os.path.join(ROOT, "__init__.py")
        self.assertTrue(os.path.isfile(entry), f"入口模块缺失: {entry}")

    def test_plugin_toml_entry(self):
        import tomllib

        with open(os.path.join(ROOT, "plugin.toml"), "rb") as f:
            data = tomllib.load(f)
        plugin = data["plugin"]
        self.assertEqual(plugin["id"], PLUGIN_ID)
        for field in ("name", "version", "entry"):
            self.assertTrue(str(plugin.get(field, "")).strip(), f"{field} 必填")
        self.assertEqual(
            plugin["entry"],
            f"plugins.{PLUGIN_ID}:YuiLockPlugin",
            f"plugin.toml entry 应为 plugins.{PLUGIN_ID}:YuiLockPlugin",
        )

    def test_sources_parse(self):
        import ast

        for name in ("__init__.py", "client.py", "pclock.py",
                     "pc_locker.py", "pair_qr.py", "web_panel.py"):
            path = os.path.join(ROOT, name)
            self.assertTrue(os.path.isfile(path), f"缺少文件: {name}")
            with open(path, encoding="utf-8") as f:
                ast.parse(f.read(), path)

    def test_locker_state_keeps_exe(self):
        """回归 #17：pc_locker 自己写状态时必须带 exe，
        否则会覆盖 pclock 的 {pid, instance, exe}，导致解锁三重校验永远失败。"""
        with open(os.path.join(ROOT, "pc_locker.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"exe"', src, "pc_locker.write_state 必须写入 exe 字段")


def auth(token: str, cmd: str, nonce: str, challenge: str) -> str:
    msg = f"{cmd}|{nonce}|{challenge}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), msg, hashlib.sha256).hexdigest()


class TestClientProtocol(unittest.TestCase):
    """对内置模拟手机验证：ping 免认证；控制命令走挑战-应答且 proof 与
    cmd/nonce/challenge 绑定；错 token 被拒；客户端未配 token 时拒绝发起控制；
    未认证直接 ok=true 的对端会被客户端拒绝。"""

    @staticmethod
    async def _phone(reader, writer, token: str, immediate_ok: bool = False,
                     unsigned_final: bool = False):
        # 挑战按连接隔离：状态在 handle 连接内部
        session = {"challenge": "", "cmd": "", "nonce": ""}
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                req = json.loads(line.decode("utf-8"))
                cmd = req.get("cmd")
                if immediate_ok and cmd in ("lock", "applock", "unlock"):
                    resp = {"ok": True, "action": cmd}  # 恶意/异常对端：不要求验证直接成功
                elif cmd in ("lock", "applock", "unlock"):
                    if not token:
                        resp = {"ok": False, "error": "手机未设置令牌，拒绝控制命令"}
                    elif not req.get("proof"):
                        session["challenge"] = secrets.token_hex(16)
                        session["cmd"] = cmd
                        session["nonce"] = str(req.get("nonce", ""))
                        resp = {"ok": False, "error": "需要配对验证", "auth": "hmac-sha256",
                                "challenge": session["challenge"]}
                    else:
                        valid = (session["challenge"]
                                 and session["cmd"] == cmd
                                 and session["nonce"] == str(req.get("nonce", "")))
                        want = auth(token, cmd, str(req.get("nonce", "")),
                                    session["challenge"]) if valid else ""
                        ch = session["challenge"]
                        nonce = str(req.get("nonce", ""))
                        session["challenge"] = ""
                        if valid and hmac.compare_digest(want, str(req.get("proof")).lower()):
                            resp = {"ok": True, "action": cmd}
                        else:
                            resp = {"ok": False, "error": "令牌验证失败"}
                        # 失败与成功都签名；unsigned_final 模拟缺失/被剥离签名的响应
                        if not unsigned_final and token:
                            flag = "1" if resp.get("ok") else "0"
                            err = str(resp.get("error", ""))
                            resp["sig"] = hmac.new(
                                token.encode(),
                                f"resp|{cmd}|{nonce}|{ch}|{flag}|{err}".encode(),
                                hashlib.sha256).hexdigest()
                else:
                    resp = {"ok": True, "action": cmd}
                    if cmd == "ping":
                        resp.update({"device": "MockPhone", "battery": 88,
                                     "token_set": bool(token)})
                writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                await writer.drain()
        finally:
            writer.close()

    def test_full_flow(self):
        client_mod = load_module("yuilock_client_test", os.path.join(ROOT, "client.py"))

        async def scenario():
            server = await asyncio.start_server(
                lambda r, w: self._phone(r, w, "TESTTOKEN"), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                good = client_mod.YuiLockClient(
                    host="127.0.0.1", port=port, token="TESTTOKEN", transport="lan")
                ping = await good.ping()
                self.assertTrue(ping.get("ok"), ping)
                self.assertEqual(ping.get("device"), "MockPhone")

                lock = await good.lock_screen()
                self.assertTrue(lock.get("ok"), lock)
                apps = await good.lock_apps()
                self.assertTrue(apps.get("ok"), apps)
                unlock = await good.unlock()
                self.assertTrue(unlock.get("ok"), unlock)

                bad = client_mod.YuiLockClient(
                    host="127.0.0.1", port=port, token="WRONG", transport="lan")
                denied = await bad.lock_screen()
                self.assertFalse(denied.get("ok"), denied)
                self.assertTrue(str(denied.get("error", "")), denied)

                noauth = client_mod.YuiLockClient(
                    host="127.0.0.1", port=port, token="", transport="lan")
                rejected = await noauth.lock_screen()
                self.assertFalse(rejected.get("ok"), rejected)

        asyncio.run(scenario())

    def test_empty_token_phone_rejects(self):
        client_mod = load_module("yuilock_client_test2", os.path.join(ROOT, "client.py"))

        async def scenario():
            server = await asyncio.start_server(
                lambda r, w: self._phone(r, w, ""), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                c = client_mod.YuiLockClient(
                    host="127.0.0.1", port=port, token="TESTTOKEN", transport="lan")
                r = await c.lock_screen()
                # 手机无 token 无法签名 → 第一包不是挑战信封 → 客户端按不可信响应拒绝
                self.assertFalse(r.get("ok"), r)
                self.assertIn("认证流程异常", r.get("error", ""))

        asyncio.run(scenario())

    def test_proof_bound_to_cmd_and_nonce(self):
        """裸协议：用 lock 拿到的 challenge，换成 applock 或错 nonce 的 proof 都必须被拒。"""

        async def raw(reader, writer, payload):
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await writer.drain()
            line = await reader.readline()
            return json.loads(line.decode("utf-8"))

        async def scenario():
            server = await asyncio.start_server(
                lambda r, w: self._phone(r, w, "TESTTOKEN"), "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                r1 = await raw(reader, writer, {"cmd": "lock", "nonce": "N1"})
                self.assertTrue(r1.get("challenge"), r1)
                ch = r1["challenge"]
                # 命令不匹配：拿 lock 的签名去开 applock
                r2 = await raw(reader, writer, {
                    "cmd": "applock", "nonce": "N1",
                    "proof": auth("TESTTOKEN", "lock", "N1", ch)})
                self.assertFalse(r2.get("ok"), r2)
                # nonce 不匹配
                r3 = await raw(reader, writer, {
                    "cmd": "lock", "nonce": "OTHER",
                    "proof": auth("TESTTOKEN", "lock", "N1", ch)})
                self.assertFalse(r3.get("ok"), r3)
                writer.close()

        asyncio.run(scenario())

    def test_direct_success_rejected(self):
        """控制命令第一包就 ok=true 的对端（伪造/劫持），客户端必须拒绝。"""
        client_mod = load_module("yuilock_client_test4", os.path.join(ROOT, "client.py"))

        async def scenario():
            server = await asyncio.start_server(
                lambda r, w: self._phone(r, w, "TESTTOKEN", immediate_ok=True),
                "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                c = client_mod.YuiLockClient(
                    host="127.0.0.1", port=port, token="TESTTOKEN", transport="lan")
                r = await c.lock_screen()
                self.assertFalse(r.get("ok"), r)
                self.assertIn("认证流程异常", r.get("error", ""))

        asyncio.run(scenario())

    def test_unsigned_final_ok_rejected(self):
        """最终成功响应缺 HMAC 签名（被篡改/伪造），客户端必须拒绝采信。"""
        client_mod = load_module("yuilock_client_test5", os.path.join(ROOT, "client.py"))

        async def scenario():
            server = await asyncio.start_server(
                lambda r, w: self._phone(r, w, "TESTTOKEN", unsigned_final=True),
                "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            async with server:
                c = client_mod.YuiLockClient(
                    host="127.0.0.1", port=port, token="TESTTOKEN", transport="lan")
                r = await c.lock_screen()
                self.assertFalse(r.get("ok"), r)
                self.assertIn("响应签名校验失败", r.get("error", ""))

        asyncio.run(scenario())


class TestDiscovery(unittest.TestCase):
    """回归：discover() 必须用非连接 UDP socket 收广播应答。

    历史 bug：用 remote_addr=("255.255.255.255", port) 的 connected socket，
    Windows/Linux 会丢弃源地址不匹配（手机真实 IP）的回包 → 所有设备都发现不了。
    """

    def test_broadcast_targets_include_limited(self):
        client_mod = load_module("yuilock_client_disco_targets", os.path.join(ROOT, "client.py"))
        targets = client_mod.YuiLockClient._broadcast_targets()
        self.assertIn("255.255.255.255", targets)
        self.assertTrue(all(isinstance(t, str) and "." in t for t in targets), targets)

    def test_discover_roundtrip(self):
        client_mod = load_module("yuilock_client_disco", os.path.join(ROOT, "client.py"))

        async def scenario():
            loop = asyncio.get_running_loop()
            token = "TESTTOKEN"

            class MockPhone(asyncio.DatagramProtocol):
                def connection_made(self, transport):
                    self.transport = transport

                def datagram_received(self, data, addr):
                    try:
                        m = json.loads(data.decode("utf-8"))
                    except Exception:
                        return
                    if not m.get("yui_lock_discover"):
                        return
                    nonce = str(m.get("n", ""))
                    sig = hmac.new(token.encode("utf-8"),
                                   ("discover|" + nonce).encode("utf-8"),
                                   hashlib.sha256).hexdigest()
                    reply = {"yui_lock_service": True, "n": nonce, "sig": sig}
                    self.transport.sendto(json.dumps(reply).encode("utf-8"), addr)

            transport, _ = await loop.create_datagram_endpoint(
                MockPhone, local_addr=("0.0.0.0", 0), allow_broadcast=True)
            try:
                port = transport.get_extra_info("socket").getsockname()[1]
                c = client_mod.YuiLockClient(host="auto", port=port, token=token)
                return await c.discover(timeout=5.0)
            finally:
                transport.close()
                await asyncio.sleep(0.05)

        r = asyncio.run(scenario())
        self.assertTrue(r, "discover() 未收到广播应答（connected-socket 回归？）")

    def test_discover_rejects_bad_sig(self):
        client_mod = load_module("yuilock_client_disco_badsig", os.path.join(ROOT, "client.py"))

        async def scenario():
            loop = asyncio.get_running_loop()

            class MockPhone(asyncio.DatagramProtocol):
                def connection_made(self, transport):
                    self.transport = transport

                def datagram_received(self, data, addr):
                    m = json.loads(data.decode("utf-8"))
                    if not m.get("yui_lock_discover"):
                        return
                    # nonce 回显正确，但签名是错的 → 客户端必须不采信
                    reply = {"yui_lock_service": True, "n": str(m.get("n", "")),
                             "sig": "f" * 64}
                    self.transport.sendto(json.dumps(reply).encode("utf-8"), addr)

            transport, _ = await loop.create_datagram_endpoint(
                MockPhone, local_addr=("0.0.0.0", 0), allow_broadcast=True)
            try:
                port = transport.get_extra_info("socket").getsockname()[1]
                c = client_mod.YuiLockClient(host="auto", port=port, token="TESTTOKEN")
                return await c.discover(timeout=1.5)
            finally:
                transport.close()
                await asyncio.sleep(0.05)

        r = asyncio.run(scenario())
        self.assertFalse(r, "签名错误的发现应答不应被采信")


@unittest.skipUnless(sys.platform == "win32", "pclock 状态校验依赖 Windows")
class TestPclockState(unittest.TestCase):
    """回归 #28：坏状态文件不抛错；并发 spawn 只留一个锁进程。"""

    def test_bad_state_and_concurrent_spawn(self):
        import threading

        pclock = load_module("yuilock_pclock_test", os.path.join(ROOT, "pclock.py"))

        # 1) 状态文件 pid 非整数：不抛错、按无效清理
        pclock.STATE_PATH.write_text(
            json.dumps({"pid": "abc", "instance": "x", "exe": "y"}), encoding="utf-8")
        self.assertFalse(pclock.is_locker_alive())
        alive, _msg = pclock.kill_locker()
        self.assertFalse(alive)
        self.assertFalse(pclock.STATE_PATH.exists())

        # 2) 并发 spawn：两线程同时锁定，只能留下一个锁进程
        stub = pclock.STATE_PATH.with_name("yuilock_test_stub_locker.py")
        stub.write_text("import time; time.sleep(10)", encoding="utf-8")
        try:
            results = []

            def worker():
                results.append(pclock.spawn_locker(stub))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            oks = [r for r in results if r.get("ok")]
            self.assertEqual(len(oks), 1, results)
            # 清理：结束测试用的 stub 锁进程
            pclock.kill_locker()
        finally:
            stub.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
