"""WebSocket 桥接层 — 管理 Python 插件与 Minecraft mod 之间的 WebSocket 连接、重连、心跳和收发队列"""

import asyncio
import json
import queue
import threading
import time

import websockets
from websockets.exceptions import ConnectionClosed, InvalidMessage

_DEFAULT_RECV_LOG_LIMIT = 300
_MAID_ACTION_FINISHED_LOG_LIMIT = 4096


def _received_log_preview(raw, message_type):
    """Keep terminal action diagnostics while bounding noisy WS console output."""
    limit = (
        _MAID_ACTION_FINISHED_LOG_LIMIT
        if message_type == "maid_action_finished"
        else _DEFAULT_RECV_LOG_LIMIT
    )
    return str(raw)[:limit]


class WSBridge:
    def __init__(self, ws_url, logger, heartbeat_interval=30, reconnect_interval=5, max_reconnect_interval=60):
        self.ws_url = ws_url
        self._logger = logger
        self._heartbeat_interval = heartbeat_interval
        self._reconnect_interval = max(1, int(reconnect_interval or 5))
        self._max_reconnect_interval = max(self._reconnect_interval, int(max_reconnect_interval or 60))
        self._handshake_retry_interval = min(max(self._reconnect_interval, 10), self._max_reconnect_interval)
        self._loop = None
        self._thread = None
        self._ws = None
        self.connected = False
        self._running = False
        self.last_error_type = ""
        self.last_error_message = ""
        self.last_error_time = 0
        self.next_reconnect_delay = 0
        self._send_queue = queue.Queue()
        self._recv_queue = queue.Queue()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._ws and self._loop and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=10)

    def send(self, data):
        self._send_queue.put(data)

    def drain(self):
        messages = []
        while True:
            try:
                messages.append(self._recv_queue.get_nowait())
            except queue.Empty:
                break
        return messages

    def _record_error(self, error):
        self.last_error_type = type(error).__name__
        self.last_error_message = str(error)
        self.last_error_time = time.time()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            self._logger.error(f"WSBridge thread error: {e}")
        finally:
            self._loop.close()

    async def _connect_loop(self):
        delay = self._reconnect_interval
        while self._running:
            try:
                self._logger.info(f"[WSBridge] Connecting to {self.ws_url}...")
                self._ws = await websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=3,
                )
                self.connected = True
                delay = self._reconnect_interval
                self.next_reconnect_delay = 0
                self.last_error_type = ""
                self.last_error_message = ""
                self.last_error_time = 0
                self._logger.info("[WSBridge] Connected to Minecraft!")
                await self._listen()
            except ConnectionClosed as e:
                self._record_error(e)
                self._logger.info(f"[WSBridge] Connection closed: {e}")
            except InvalidMessage as e:
                self._record_error(e)
                self._logger.warning(
                    f"[WSBridge] Invalid WebSocket response from {self.ws_url}: {e}. "
                    "The port is open, but it did not complete a WebSocket handshake; "
                    "check whether the Minecraft mod server is still starting, the port is wrong, or another program is using it."
                )
            except OSError as e:
                self._record_error(e)
                self._logger.warning(f"[WSBridge] OS error: {e}")
            except Exception as e:
                self._record_error(e)
                self._logger.warning(f"[WSBridge] Error: {type(e).__name__}: {e}")
            finally:
                self.connected = False
                self._ws = None

            if self._running:
                handshake_failed = self.last_error_type == "InvalidMessage"
                reconnect_delay = self._handshake_retry_interval if handshake_failed else delay
                reconnect_delay = min(max(1, reconnect_delay), self._max_reconnect_interval)
                self.next_reconnect_delay = reconnect_delay
                self._logger.info(f"[WSBridge] Reconnecting in {reconnect_delay}s...")
                try:
                    await asyncio.sleep(reconnect_delay)
                    if handshake_failed:
                        delay = self._reconnect_interval
                    else:
                        delay = min(reconnect_delay * 2, self._max_reconnect_interval)
                except asyncio.CancelledError:
                    break

    async def _listen(self):
        ws = self._ws
        if not ws:
            return

        async def recv_loop():
            try:
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                        if data.get("type") == "pong":
                            self._last_pong_time = time.time()
                        if data.get("type") != "pong":
                            preview = _received_log_preview(raw, data.get("type"))
                            if data.get("type") == "game_context":
                                self._logger.debug(f"[WSBridge] recv: {preview}")
                            else:
                                self._logger.info(f"[WSBridge] recv: {preview}")
                        self._recv_queue.put(data)
                    except json.JSONDecodeError:
                        self._logger.warning(f"Invalid JSON: {raw}")
            except ConnectionClosed:
                pass
            except Exception as e:
                self._logger.error(f"[WSBridge] recv error: {type(e).__name__}: {e}")

        async def send_loop():
            while self._running and self.connected:
                try:
                    data = self._send_queue.get_nowait()
                    await ws.send(json.dumps(data))
                except queue.Empty:
                    await asyncio.sleep(0.05)
                except Exception as e:
                    self._logger.error(f"[WSBridge] send error: {type(e).__name__}: {e}")
                    return

        async def heartbeat_loop():
            self._last_pong_time = time.time()
            while self._running and self.connected:
                try:
                    await ws.send(json.dumps({"type": "ping"}))
                    await asyncio.sleep(self._heartbeat_interval)
                    # 检查 pong 超时：如果超过 3 倍心跳间隔没收到 pong，认为应用层卡死
                    if time.time() - self._last_pong_time > self._heartbeat_interval * 3:
                        self._logger.warning("[WSBridge] Pong timeout, closing connection to trigger reconnect")
                        await ws.close()
                        return
                except Exception:
                    return

        tasks = [
            asyncio.create_task(recv_loop()),
            asyncio.create_task(send_loop()),
            asyncio.create_task(heartbeat_loop()),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
