"""
DG-Lab SocketControl V4 WebSocket 服务器
实现官方 DG-Lab V4 协议，支持 1 控制方 : N 被控方

协议参考: https://github.com/dungeonlab-open/dglab-websocket-server

V4 连接流程:
1. 控制方连接 ws://host:port，获得 clientId
2. 被控方通过 ws://host:port?tid=控制方clientId 接入
3. 控制方收到 client_attached 通知
4. 控制方通过 {"type": "message", "clientId": "被控方ID", "data": {...}} 发送指令
5. 被控方可通过 {"type": "message", "data": {...}} 上报数据

V4 二维码格式:
https://dungeon-lab.cn/s/?v=1&action=socket&url=ws://IP:PORT?tid=控制方ID
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse


class DglabServerV4:
    """DG-Lab SocketControl V4 WebSocket 服务器"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9998,
        logger: Optional[logging.Logger] = None,
        controller_id: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.logger = logger or logging.getLogger(__name__)

        self.server = None
        self.running = False
        # 服务器所属事件循环（跨线程调用需 run_coroutine_threadsafe 投递到这里）
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        # 控制方 clientId（我们的插件）；外部传入则持久复用，否则每次新建
        self.controller_id = controller_id or str(uuid.uuid4())

        # 所有连接: clientId -> {"ws": websocket, "type": "controller"|"client", "controller_id": ...}
        self.connections: Dict[str, Dict[str, Any]] = {}

        # 被控方信息: client_id -> {battery, strength_a, strength_b, ...}
        self.clients_info: Dict[str, Dict] = {}

        # 每个被控方的设备 slotId（从 devices.snapshot 获取，V4 指令定位设备用）
        self.slot_ids: Dict[str, Optional[str]] = {}

        # 被控方连接/断开回调
        self.on_client_attached = None
        self.on_client_detached = None

        # RPC reqId 自增计数（保证同一被控方的未完成请求不重复）
        self._req_counter = 0

        # 心跳任务
        self.heartbeat_interval = 30.0
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动 WebSocket 服务器"""
        try:
            import websockets

            self.running = True
            self.loop = asyncio.get_running_loop()
            
            # V4 协议需要在 handle_client 中解析 query string
            # websockets>=10 的 handler 只接收 websocket 一个参数（path 移入 websocket.path）
            async def handler(websocket):
                await self.handle_client(websocket)
            
            self.server = await websockets.serve(
                handler,
                self.host,
                self.port,
                # 对齐官方 v4-server：原生协议 ping 每 10s 一次，
                # 容忍 30s 无 pong（官方为连续 3 次 miss 后 terminate）。
                # APP 端会正常响应原生 ping，同时也依赖服务器的
                # 下行流量判活，禁用后 APP 会概率性强杀连接。
                ping_interval=10,
                ping_timeout=30,
            )

            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            self.logger.info(
                f"DG-Lab V4 WebSocket server started on ws://{self.host}:{self.port}, "
                f"controller_id={self.controller_id}"
            )

        except Exception as error:
            self.logger.error(f"Failed to start WebSocket server: {error}")
            raise

    async def stop(self):
        """停止 WebSocket 服务器"""
        self.running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        self.logger.info("DG-Lab V4 WebSocket server stopped")

    async def _heartbeat_loop(self):
        """定时向所有连接发送心跳"""
        while self.running:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                for client_id, conn_info in list(self.connections.items()):
                    try:
                        ws = conn_info["ws"]
                        await ws.send(json.dumps({"type": "heartbeat"}))
                        # websockets>=12 会在原生 ping/pong 往返后更新 latency
                        latency = getattr(ws, "latency", None)
                        if latency:
                            self.logger.info(
                                "Heartbeat sent to %s, link latency=%.0fms",
                                client_id, latency * 1000,
                            )
                    except Exception as error:
                        self.logger.warning(
                            "Heartbeat to %s failed: %s", client_id, error
                        )
            except asyncio.CancelledError:
                break
            except Exception as error:
                self.logger.warning(f"Heartbeat loop error: {error}")

    async def handle_client(self, websocket):
        """处理客户端连接"""
        remote = "unknown"
        try:
            remote = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        except Exception:
            pass

        # 解析 query string 判断是控制方还是被控方
        parsed = urlparse(websocket.request.path)
        query_params = parse_qs(parsed.query)
        target_controller_id = query_params.get("tid", [None])[0]

        is_client = target_controller_id is not None
        client_id = str(uuid.uuid4())

        if is_client:
            # 被控方连接
            if target_controller_id != self.controller_id:
                self.logger.warning(
                    f"Client {client_id} tried to connect with wrong controller_id: {target_controller_id}"
                )
                await websocket.close(code=4001, reason="controller_not_found")
                return

            self.connections[client_id] = {
                "ws": websocket,
                "type": "client",
                "controller_id": target_controller_id
            }

            self.clients_info[client_id] = {
                "id": client_id,
                "connected_at": datetime.now(),
                "battery": None,
                "strength_a": 0,
                "strength_b": 0,
            }

            self.logger.info(f"Client connected: {client_id} from {remote}")

            # 发送 hello
            await self._send(websocket, {
                "type": "hello",
                "clientId": client_id
            })

            # 通知被控方：控制方已连接
            await self._send(websocket, {
                "type": "controller_attached",
                "clientId": self.controller_id
            })

            # 通知控制方（如果有回调）
            if self.on_client_attached:
                try:
                    self.on_client_attached(client_id)
                except Exception as callback_error:
                    self.logger.error(f"on_client_attached callback failed: {callback_error}")

        else:
            # 不带 tid 参数的连接 - 拒绝（NEKO 插件本身就是控制方）
            self.logger.warning(
                f"Rejected connection without tid from {remote}. "
                f"All clients must connect with ?tid={self.controller_id}"
            )
            await websocket.close(code=4000, reason="tid_required")
            return

        try:
            async for raw in websocket:
                try:
                    data = json.loads(raw)
                    await self._handle_message(client_id, data)
                except json.JSONDecodeError:
                    self.logger.warning(f"Invalid JSON from {client_id}: {raw}")

        except Exception as error:
            self.logger.error(f"Client {client_id} error: {error}")

        finally:
            conn_info = self.connections.pop(client_id, None)
            
            if conn_info and conn_info["type"] == "client":
                self.clients_info.pop(client_id, None)
                
                if self.on_client_detached:
                    try:
                        self.on_client_detached(client_id)
                    except Exception as callback_error:
                        self.logger.error(f"on_client_detached callback failed: {callback_error}")

            self.logger.info(f"Client disconnected: {client_id}")

    async def _send(self, websocket, message: Dict):
        """发送 JSON 消息"""
        await websocket.send(json.dumps(message))

    async def _send_to_client(self, client_id: str, data: Dict) -> bool:
        """向指定被控方发送消息"""
        conn_info = self.connections.get(client_id)
        if not conn_info or conn_info["type"] != "client":
            self.logger.warning(f"Client {client_id} not found or not a client")
            return False

        try:
            ws = conn_info["ws"]
            await ws.send(json.dumps({
                "type": "message",
                "data": data
            }))
            return True
        except Exception as error:
            self.logger.error(f"Send to client {client_id} failed: {error}")
            return False

    async def _handle_message(self, sender_id: str, message: Dict):
        """处理收到的消息（V4 应用层：t=req / resp / ev）"""
        msg_type = message.get("type")
        # ping/pong 每 2s 一次，降为 debug 防止刷屏；业务消息保持 info
        if msg_type in ("ping", "pong", "heartbeat"):
            self.logger.debug("Recv from %s: type=%s", sender_id, msg_type)
        else:
            self.logger.info("Recv from %s: type=%s", sender_id, msg_type)

        if msg_type == "ping":
            # 应用层 ping 探测：必须回 pong，否则 APP 等待 8s(responseTimeout) 后断开
            conn_info = self.connections.get(sender_id)
            if conn_info:
                try:
                    ws = conn_info["ws"]
                    await self._send(ws, {
                        "type": "pong",
                        "ts": int(time.time() * 1000),
                    })
                except Exception as error:
                    self.logger.warning("Send pong failed: %s", error)
            return
        elif msg_type == "pong":
            # 服务器不需要回应 pong
            return
        elif msg_type == "message":
            # 被控方(APP)上报的应用层消息
            conn_info = self.connections.get(sender_id)
            if not conn_info:
                return

            if conn_info["type"] == "client":
                data = message.get("data", {})
                if isinstance(data, dict):
                    self._parse_client_feedback(sender_id, data)
            
        elif msg_type == "heartbeat":
            # 心跳回复，忽略
            pass

    def _parse_client_feedback(self, client_id: str, data: Dict):
        """解析被控方上报的 V4 应用层消息（ev 事件 / resp 响应）"""
        if client_id not in self.clients_info:
            return

        t = data.get("t")
        if t == "ev":
            # APP 主动上报的事件
            ev = data.get("ev")
            if ev == "devices.snapshot":
                devices = data.get("devices") or []
                self._update_slot_from_devices(client_id, devices)
            elif ev == "devices.patch":
                added = data.get("added") or []
                self._update_slot_from_devices(client_id, added)
                removed = data.get("removed") or []
                for slot_id in removed:
                    if self.slot_ids.get(client_id) == slot_id:
                        self.slot_ids[client_id] = None
            elif ev == "slots.patch":
                slots = data.get("slots") or []
                for slot in slots:
                    if not isinstance(slot, dict):
                        continue
                    slot_id = slot.get("slotId")
                    if slot_id and self.slot_ids.get(client_id) == slot_id:
                        props = slot.get("props") or {}
                        self._apply_slot_props(client_id, props)
                        slot_state = slot.get("slotState") or {}
                        self._apply_slot_state(client_id, slot_state)
            elif ev == "custom.action":
                self.clients_info[client_id]["action"] = data.get("action")
        elif t == "resp":
            # RPC 响应（任务完成/被清理等），记录最近一次结果供面板展示
            result = data.get("result") or {}
            self.clients_info[client_id]["last_resp"] = {
                "reqId": data.get("reqId"),
                "result": result,
                "error": data.get("error"),
            }

    def _update_slot_from_devices(self, client_id: str, devices: list):
        """从设备列表里记录 slotId，并缓存设备属性"""
        if not devices:
            return
        first = devices[0]
        if not isinstance(first, dict):
            return
        slot_id = first.get("slotId")
        if slot_id:
            self.slot_ids[client_id] = slot_id
        self._apply_slot_props(client_id, first.get("props") or {})
        self._apply_slot_state(client_id, first.get("slotState") or {})

    def _apply_slot_props(self, client_id: str, props: Dict):
        """把设备 props（电量/强度等）写入 clients_info"""
        info = self.clients_info[client_id]
        if "power" in props:
            info["battery"] = props["power"]
        if "intensityA" in props:
            info["strength_a"] = props["intensityA"]
        if "intensityB" in props:
            info["strength_b"] = props["intensityB"]

    def _apply_slot_state(self, client_id: str, slot_state: Dict):
        """解析 APP 上报的 slotState（含通道强度上限 intensityMax）"""
        if not isinstance(slot_state, dict):
            return
        info = self.clients_info[client_id]
        for key, field in (("channelA", "intensity_max_a"), ("channelB", "intensity_max_b")):
            ch = slot_state.get(key)
            if isinstance(ch, dict) and "intensityMax" in ch:
                info[field] = ch["intensityMax"]

    def _new_req_id(self) -> str:
        self._req_counter += 1
        return f"neko-{self._req_counter}"

    async def send_to_client(self, client_id: str, data: Dict) -> bool:
        """向被控方发送数据（外层自动包装为 V4 message 帧）"""
        return await self._send_to_client(client_id, data)

    # ---------- V4 RPC 控制指令（device.op） ----------

    @staticmethod
    def _v4_channel(channel: str) -> int:
        """V4 通道值：A=0, B=1"""
        return 0 if channel.upper() == "A" else 1

    async def _send_rpc(
        self,
        client_id: str,
        method: str,
        data: Optional[Dict] = None,
    ) -> bool:
        """发送 V4 RPC 请求：{"t":"req","reqId":...,"m":method,"data":{...}}"""
        rpc = {
            "t": "req",
            "reqId": self._new_req_id(),
            "m": method,
        }
        if data:
            rpc["data"] = data
        return await self._send_to_client(client_id, rpc)

    def _require_slot(self, client_id: str) -> Optional[str]:
        """取被控方 slotId；APP 上报 devices.snapshot 前不可控"""
        slot_id = self.slot_ids.get(client_id)
        if not slot_id:
            self.logger.warning(
                "Client %s has no slotId yet (waiting devices.snapshot), skip control",
                client_id,
            )
        return slot_id

    async def set_strength(
        self,
        client_id: str,
        channel: str,
        strength: int,
        duration_ms: int = 5000,
    ) -> bool:
        """
        设置强度。
        V4 只接受绝对归零(SetIntensity v=0)；非零强度用 SetTempIntensity 临时强度。
        """
        slot_id = self._require_slot(client_id)
        if not slot_id:
            return False
        channel_num = self._v4_channel(channel)
        if strength == 0:
            # t:7 SetIntensity —— 只能设为 0（归零/复位）
            return await self._send_rpc(client_id, "device.op", {
                "s": slot_id,
                "t": 7,
                "c": channel_num,
                "p": 1,
                "v": 0,
            })
        # t:4 SetTempIntensity —— 临时强度，任务结束自动回 0
        return await self._send_rpc(client_id, "device.op", {
            "s": slot_id,
            "t": 4,
            "c": channel_num,
            "p": 1,
            "d": duration_ms,
            "v": strength,
        })

    async def send_pulse(
        self,
        client_id: str,
        channel: str,
        waveform_hex_list: list,
        duration_ms: int = 0,
    ) -> bool:
        """下发波形帧（t:0 AppendPulseData）"""
        slot_id = self._require_slot(client_id)
        if not slot_id:
            return False
        return await self._send_rpc(client_id, "device.op", {
            "s": slot_id,
            "t": 0,
            "c": self._v4_channel(channel),
            "p": 1,
            "d": duration_ms,
            "v": list(waveform_hex_list),
        })

    async def clear_pulse(self, client_id: str, channel: str = "both") -> bool:
        """清空波形/任务（device.op.clear）"""
        slot_id = self._require_slot(client_id)
        if not slot_id:
            return False
        if channel in ("A", "B"):
            return await self._send_rpc(client_id, "device.op.clear", {
                "s": slot_id,
                "c": self._v4_channel(channel),
            })
        return await self._send_rpc(client_id, "device.op.clear", {
            "s": slot_id,
        })

    def get_connected_clients(self) -> list:
        """获取所有已连接的被控方 ID 列表"""
        return [
            cid for cid, info in self.connections.items()
            if info["type"] == "client"
        ]

    def get_clients_detail(self) -> list:
        """获取所有已连接被控方的详细信息（供 Web 面板使用）"""
        result = []
        for cid in self.get_connected_clients():
            info = self.clients_info.get(cid, {})
            result.append({
                "client_id": cid,
                "battery": info.get("battery"),
                "strength_a": info.get("strength_a", 0),
                "strength_b": info.get("strength_b", 0),
                "intensity_max_a": info.get("intensity_max_a"),
                "intensity_max_b": info.get("intensity_max_b"),
            })
        return result

    def get_client_info(self, client_id: str) -> Optional[Dict]:
        """获取被控方信息"""
        return self.clients_info.get(client_id)

    def get_qrcode_url(self, ws_url: str = None) -> str:
        """
        生成 V4 二维码 URL
        ws_url: WebSocket 地址，如 ws://192.168.1.100:9998 或 wss://your-domain.com/v4
        """
        if not ws_url:
            ws_url = f"ws://{self.host}:{self.port}"
        
        full_ws_url = f"{ws_url}?tid={self.controller_id}"
        from urllib.parse import quote
        qrcode_url = f"https://dungeon-lab.cn/s/?v=1&action=socket&url={quote(full_ws_url)}"
        return qrcode_url
    
    def get_qrcode_url_v3(self, ws_url: str = None) -> str:
        """
        生成 V3 兼容二维码 URL（给 DG-Lab 3 APP 使用）
        ws_url: WebSocket 地址，如 ws://192.168.1.100:9998
        """
        if not ws_url:
            ws_url = f"ws://{self.host}:{self.port}"
        
        full_ws_url = f"{ws_url}?tid={self.controller_id}"
        # V3 格式：https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#ws://...
        qrcode_url = f"https://www.dungeon-lab.com/app-download.php#DGLAB-SOCKET#{full_ws_url}"
        return qrcode_url

    def get_qr_content(self, local_ip: str) -> str:
        """
        生成二维码内容（默认使用 V4，兼容 V3 API）
        local_ip: 本机 IP 地址
        """
        ws_url = f"ws://{local_ip}:{self.port}"
        return self.get_qrcode_url(ws_url)

    def is_running(self) -> bool:
        """服务器是否正在运行"""
        return self.running

    def has_bound_app(self) -> bool:
        """是否有连接的被控方"""
        return len(self.get_connected_clients()) > 0

    async def reset_all(self) -> bool:
        """重置所有被控方（清空波形并归零强度）"""
        ok = True
        for client_id in self.get_connected_clients():
            ok = await self.clear_pulse(client_id, "both") and ok
            ok = await self.set_strength(client_id, "A", 0) and ok
            ok = await self.set_strength(client_id, "B", 0) and ok
        return ok

    async def add_pulses(
        self,
        channel: str,
        waveform_frames: list,
        duration_ms: int,
        client_id: str = None,
    ) -> bool:
        """
        添加波形脉冲（兼容插件主逻辑调用）
        
        Args:
            channel: 通道 A / B / both
            waveform_frames: 波形帧列表（hex 字符串）
            duration_ms: 持续时间（毫秒），用于日志记录
            client_id: 可选，指定被控方 ID，不填则发送给第一个连接的设备
        
        Returns:
            是否成功
        """
        if not client_id:
            clients = self.get_connected_clients()
            if not clients:
                self.logger.warning("No clients connected, cannot add pulses")
                return False
            client_id = clients[0]

        channels = ["A", "B"] if channel.lower() in ("both", "ab") else [channel.upper()]
        ok = True
        for channel_name in channels:
            ok = await self.send_pulse(
                client_id, channel_name, waveform_frames, duration_ms=duration_ms
            ) and ok
            await asyncio.sleep(0.05)
        
        self.logger.info(
            f"Added pulses to {client_id}: channel={channel}, frames={len(waveform_frames)}, duration_ms={duration_ms}"
        )
        return ok

    async def apply_pulse(
        self,
        channel: str,
        intensity: int,
        waveform_hex_list: list,
        client_id: str = None,
        duration_ms: int = 5000,
    ) -> bool:
        """
        设置强度并下发波形
        channel 可为 A / B / both
        """
        if not client_id:
            clients = self.get_connected_clients()
            if not clients:
                return False
            client_id = clients[0]

        channels = ["A", "B"] if channel.lower() in ("both", "ab") else [channel.upper()]
        ok = True
        for ch in channels:
            ok = await self.set_strength(client_id, ch, intensity, duration_ms=duration_ms) and ok
            await asyncio.sleep(0.05)
            ok = await self.send_pulse(client_id, ch, waveform_hex_list, duration_ms=duration_ms) and ok
        return ok
