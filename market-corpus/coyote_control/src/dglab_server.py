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
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse


class DglabServerV4:
    """DG-Lab SocketControl V4 WebSocket 服务器"""

    def __init__(self, host: str = "0.0.0.0", port: int = 9998, logger: Optional[logging.Logger] = None):
        self.host = host
        self.port = port
        self.logger = logger or logging.getLogger(__name__)

        self.server = None
        self.running = False

        # 控制方 clientId（我们的插件）
        self.controller_id = str(uuid.uuid4())

        # 所有连接: clientId -> {"ws": websocket, "type": "controller"|"client", "controller_id": ...}
        self.connections: Dict[str, Dict[str, Any]] = {}

        # 被控方信息: client_id -> {battery, strength_a, strength_b, ...}
        self.clients_info: Dict[str, Dict] = {}

        # 被控方连接/断开回调
        self.on_client_attached = None
        self.on_client_detached = None

        # 心跳任务
        self.heartbeat_interval = 30.0
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动 WebSocket 服务器"""
        try:
            import websockets

            self.running = True
            
            # V4 协议需要在 handle_client 中解析 query string
            async def handler(websocket):
                await self.handle_client(websocket)
            
            self.server = await websockets.serve(
                handler,
                self.host,
                self.port,
                # 禁用协议级 ping：DG-Lab APP 不响应 PONG 时会被默认
                # ping_timeout(20s) 踢掉导致每 ~40s 断连。保活交给
                # 插件自身的业务心跳(30s) + 应用层心跳即可。
                ping_interval=None,
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
                    except Exception:
                        pass
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
        """处理收到的消息"""
        msg_type = message.get("type")

        if msg_type == "message":
            # 被控方发送给控制方的消息，或控制方发送给被控方的消息
            data = message.get("data", {})
            
            conn_info = self.connections.get(sender_id)
            if not conn_info:
                return

            if conn_info["type"] == "client":
                # 被控方发来的数据，解析状态信息
                self._parse_client_feedback(sender_id, data)
            
        elif msg_type == "heartbeat":
            # 心跳回复，忽略
            pass

    def _parse_client_feedback(self, client_id: str, data: Dict):
        """解析被控方反馈的数据"""
        if client_id not in self.clients_info:
            return

        # V4 协议中 APP 可能返回的数据结构（根据实际情况调整）
        if "battery" in data:
            self.clients_info[client_id]["battery"] = data["battery"]
        
        if "strength" in data:
            strength = data["strength"]
            if isinstance(strength, dict):
                self.clients_info[client_id]["strength_a"] = strength.get("a", 0)
                self.clients_info[client_id]["strength_b"] = strength.get("b", 0)

    async def send_to_client(self, client_id: str, data: Dict) -> bool:
        """向被控方发送数据"""
        return await self._send_to_client(client_id, data)

    async def set_strength(self, client_id: str, channel: str, strength: int) -> bool:
        """
        设置强度
        V4 协议：data 中包含设备指令
        """
        channel_num = 1 if channel.upper() == "A" else 2
        return await self._send_to_client(client_id, {
            "type": "strength",
            "channel": channel_num,
            "mode": 2,  # 2 = 设为指定值
            "value": strength
        })

    async def send_pulse(self, client_id: str, channel: str, waveform_hex_list: list) -> bool:
        """
        发送波形
        V4 协议：data 中包含波形数据
        """
        channel_name = channel.upper()
        return await self._send_to_client(client_id, {
            "type": "pulse",
            "channel": channel_name,
            "waveform": waveform_hex_list
        })

    async def clear_pulse(self, client_id: str, channel: str = "both") -> bool:
        """清空波形队列"""
        ok = True
        if channel in ("A", "both"):
            ok = await self._send_to_client(client_id, {
                "type": "clear",
                "channel": 1
            }) and ok
        if channel in ("B", "both"):
            ok = await self._send_to_client(client_id, {
                "type": "clear",
                "channel": 2
            }) and ok
        return ok

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
            ok = await self.send_pulse(client_id, channel_name, waveform_frames) and ok
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
    ) -> bool:
        """
        设置强度并下发波形（兼容 V3 API）
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
            ok = await self.set_strength(client_id, ch, intensity) and ok
            await asyncio.sleep(0.05)
            ok = await self.send_pulse(client_id, ch, waveform_hex_list) and ok
        return ok
