"""Manual-start connection to the local game server; no import-time I/O."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from collections.abc import Awaitable, Callable
from typing import cast

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from .events import GameEvents
from .orders import CATEGORIES
from .process import game_running

ADDRESS = "ws://127.0.0.1:48930/neko"
PendingRequests = dict[str, tuple[str, asyncio.Future[dict]]]


class ProtocolError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class NekoSousClient:
    def __init__(self, logger=None, *, probe: Callable[[], Awaitable[bool]] = game_running,
                 on_event: Callable[[dict], Awaitable[bool | None]] | None = None,
                 on_state: Callable[[dict], None] | None = None, on_reset: Callable[[], None] | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.probe = probe
        self.on_event = on_event
        self.on_state = on_state
        self.on_reset = on_reset
        self.event_options = dict.fromkeys(CATEGORIES, True)
        self.events_enabled = True
        self.state_supported = False
        self.orders_supported = False
        self.kitchen_supported = False
        self.mixing_supported = False
        self.subscription_pending = False
        self._subscription_lock = asyncio.Lock()
        self._pending: PendingRequests | None = None
        self._events: GameEvents | None = None
        self.events_supported = False
        self.events_subscribed = False
        self.last_event: dict | None = None
        self.state = "disconnected"
        self.game_state = "unknown"
        self.desired = False
        self.session_id = ""
        self.server_version = ""
        self.last_error: dict | None = None
        self.retry_in = 0
        self.mode = "companion"
        self.mode_supported = False
        self.pending_mode: str | None = None
        self._mode_future: asyncio.Future[bool] | None = None
        self._commands: asyncio.Queue[tuple[str, asyncio.Future[bool]]] = asyncio.Queue()
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def kitchen_pending(self) -> bool:
        return self._events is not None and self._events.kitchen_pending()

    async def close_connection(self) -> None:
        """关闭当前连接，保留现有的自动重连意图。"""
        if self._ws:
            await self._ws.close()

    def snapshot(self) -> dict:
        return {
            "address": ADDRESS, "game_state": self.game_state, "connection_state": self.state,
            "desired": self.desired, "session_id": self.session_id, "server_version": self.server_version,
            "last_error": self.last_error, "retry_in": self.retry_in,
            "mode": self.mode, "mode_supported": self.mode_supported, "pending_mode": self.pending_mode,
            "events_supported": self.events_supported, "events_subscribed": self.events_subscribed,
            "last_event": self.last_event,
            "events_enabled": self.events_enabled, "state_supported": self.state_supported,
            "subscription_pending": self.subscription_pending,
            "orders_supported": self.orders_supported,
            "kitchen_supported": self.kitchen_supported,
            "mixing_supported": self.mixing_supported,
        }

    def _reset_mode(self) -> None:
        self.events_supported = False
        self.state_supported = False
        self.orders_supported = False
        self.kitchen_supported = False
        self.mixing_supported = False
        self.events_subscribed = False
        self.last_event = None
        if self.on_reset:
            self.on_reset()
        self.mode = "companion"
        self.mode_supported = False
        self.pending_mode = None
        if self._mode_future and not self._mode_future.done():
            self._mode_future.set_result(False)
        self._mode_future = None
        while not self._commands.empty():
            self._commands.get_nowait()

    async def set_mode(self, mode: str) -> dict:
        if mode not in {"companion", "playmate"}:
            self.report_error("invalid_mode", f"不支持的模式：{mode}")
        elif self.state != "connected":
            self.report_error("not_connected", "请先连接游戏服务端")
        elif not self.mode_supported:
            self.report_error("mode_not_supported", "当前游戏端未声明模式切换能力，请更新游戏端插件")
        elif self.pending_mode is None and mode != self.mode:
            future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self._mode_future = future
            self.pending_mode = mode
            self.last_error = None
            self._commands.put_nowait((mode, future))
            try:
                await asyncio.wait_for(asyncio.shield(future), 12)
            except TimeoutError:
                future.cancel()
                self.report_error("mode_timeout", "等待游戏端确认模式超时，将断开并重新同步")
                if self._ws:
                    await self._ws.close()
            except asyncio.CancelledError:
                future.cancel()
                # An in-flight request may already have reached the server. Closing
                # the session makes its reset authoritative instead of guessing.
                if self._ws:
                    await self._ws.close()
                raise
            finally:
                if self._mode_future is future:
                    self._mode_future = None
                    self.pending_mode = None
        return self.snapshot()

    def report_error(self, code: str, detail: str) -> None:
        if self.last_error and self.last_error["code"] == code and self.last_error["detail"] == detail:
            return
        self.last_error = {"id": uuid.uuid4().hex, "code": code, "detail": detail}
        self.logger.warning(f"[NekoSous] {code}：{detail}")

    async def refresh_game(self) -> bool | None:
        try:
            running = await self.probe()
            self.game_state = "running" if running else "stopped"
            return running
        except Exception as exc:
            self.game_state = "unknown"
            self.report_error("game_check_failed", f"检测游戏进程失败：{type(exc).__name__}: {exc}")
            return None

    async def start(self) -> dict:
        async with self._lock:
            if self._task and not self._task.done():
                return self.snapshot()
            running = await self.refresh_game()
            if not running:
                self.state = "disconnected"
                if running is False:
                    self.report_error("game_not_running", "请先启动《胡闹厨房 2》后再试")
                return self.snapshot()
            self.last_error = None
            self.desired = True
            self.state = "connecting"
            self._task = asyncio.create_task(self._run(), name="neko-sous-connection")
            return self.snapshot()

    async def stop(self) -> dict:
        async with self._lock:
            self.desired = False
            task, self._task = self._task, None
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.state = "disconnected"
            self.session_id = ""
            self.server_version = ""
            self.retry_in = 0
            self.last_error = None
            self._reset_mode()
            return self.snapshot()

    @staticmethod
    def _message(kind: str, payload: dict | None = None) -> dict:
        return {"version": 1, "type": kind, "id": uuid.uuid4().hex, "payload": payload or {}}

    @staticmethod
    async def _receive(ws: ClientConnection, timeout: float | None, *, check_error: bool = True) -> dict:
        raw = await asyncio.wait_for(ws.recv(), timeout)
        if not isinstance(raw, str):
            raise ProtocolError("protocol_error", "服务端发送了二进制消息")
        try:
            message = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ProtocolError("protocol_error", "服务端返回了无效 JSON") from exc
        if (not isinstance(message, dict) or type(message.get("version")) is not int
                or message["version"] != 1 or not isinstance(message.get("payload"), dict)
                or not isinstance(message.get("id"), str) or not message["id"]):
            raise ProtocolError("protocol_error", "服务端消息结构或协议版本不匹配")
        if check_error:
            NekoSousClient._check_error(message)
        return message

    @staticmethod
    def _check_error(message: dict) -> None:
        if message.get("type") == "error":
            payload = message["payload"]
            code = payload.get("code")
            known = {
                "server_busy", "invalid_frame", "invalid_json", "invalid_message", "unsupported_version",
                "handshake_required", "invalid_hello", "handshake_timeout", "heartbeat_timeout",
                "already_connected", "unknown_type", "invalid_mode",
            }
            raise ProtocolError(code if isinstance(code, str) and code in known else "server_error",
                                str(payload.get("message", code)))

    async def _dispatch(self, ws: ClientConnection, pending: PendingRequests, events: GameEvents) -> None:
        while True:
            message = await self._receive(ws, None, check_error=False)
            if message.get("type") == "game_state":
                if self.events_subscribed and self.state_supported:
                    state = message["payload"]
                    # 使用事件校验规则检查快照字段，但不把快照放进播报队列。
                    sample = {**state, "event": "scene_ready", "event_id": message["id"]}
                    if GameEvents.valid(sample):
                        events.update_state(state)
                        if self.on_state:
                            self.on_state(state)
                continue
            if message.get("type") == "game_event":
                if not self.events_supported:
                    raise ProtocolError("protocol_error", "服务端未声明事件能力却推送游戏事件")
                if not self.events_subscribed:
                    continue
                if events.accept(message["payload"]):
                    self.last_event = message["payload"]
                    if self.on_state:
                        self.on_state(message["payload"])
                else:
                    self.logger.debug("[NekoSous] 已忽略重复、过期或无效的游戏事件")
                continue
            reply_to = message.get("reply_to")
            item = pending.get(reply_to) if isinstance(reply_to, str) else None
            if item is None:
                self._check_error(message)
                raise ProtocolError("protocol_error", "服务端响应的请求 ID 不匹配")
            expected, future = item
            if future.done():
                continue
            try:
                self._check_error(message)
                if message.get("type") != expected:
                    raise ProtocolError("protocol_error", f"服务端响应类型不匹配，预期：{expected}")
            except ProtocolError as exc:
                future.set_exception(exc)
            else:
                if expected in {"events_subscribed", "events_unsubscribed"}:
                    self.events_subscribed = expected == "events_subscribed"
                    events.clear()
                    self.last_event = None
                    if self.on_reset:
                        self.on_reset()
                future.set_result(message)

    async def _request(self, ws: ClientConnection, pending: PendingRequests, kind: str, expected: str, timeout: float,
                       payload: dict | None = None) -> dict:
        request = self._message(kind, payload)
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        pending[request["id"]] = (expected, future)
        try:
            await ws.send(json.dumps(request))
            return await asyncio.wait_for(future, timeout)
        finally:
            pending.pop(request["id"], None)
            if not future.done():
                future.cancel()

    async def _heartbeat(self, ws: ClientConnection, pending: PendingRequests, interval: float, timeout: float) -> None:
        while True:
            try:
                await self._request(ws, pending, "ping", "pong", min(interval, timeout - interval))
            except TimeoutError as exc:
                raise ProtocolError("heartbeat_timeout", "等待 pong 心跳响应超时") from exc
            await asyncio.sleep(interval)

    async def _control(self, ws: ClientConnection, pending: PendingRequests, timeout: float) -> None:
        if self.events_supported and self.events_enabled:
            await self._apply_subscription(True)
        self.state = "connected"
        self.last_error = None
        self.logger.info(f"[NekoSous] 握手成功，会话：{self.session_id}，事件订阅：{self.events_subscribed}")
        while True:
            mode, future = await self._commands.get()
            if future.done():
                continue
            try:
                confirmation = await self._request(ws, pending, "set_mode", "mode_changed", timeout, {"mode": mode})
            except TimeoutError as exc:
                raise ProtocolError("mode_timeout", "等待 mode_changed 确认超时") from exc
            except ProtocolError as exc:
                if exc.code not in {"invalid_mode", "unknown_type"}:
                    raise
                self.report_error(exc.code, str(exc))
                if not future.done():
                    future.set_result(False)
                continue
            if confirmation["payload"].get("mode") != mode:
                raise ProtocolError("protocol_error", "模式确认的模式不匹配")
            self.mode = mode
            self.logger.info(f"[NekoSous] 游戏端已确认模式：{mode}")
            if not future.done():
                future.set_result(True)

    async def _apply_subscription(self, enabled: bool) -> None:
        async with self._subscription_lock:
            self.subscription_pending = True
            try:
                kind = "subscribe_events" if enabled else "unsubscribe_events"
                expected = "events_subscribed" if enabled else "events_unsubscribed"
                payload = {"include_state": True} if enabled and self.state_supported else {}
                if enabled and self.orders_supported:
                    payload["include_orders"] = True
                if enabled and self.kitchen_supported:
                    payload["include_kitchen"] = True
                if enabled and self.mixing_supported:
                    payload["include_mixing"] = True
                # 仅在会话建立后调用；显式标注由会话初始化保证的非空对象。
                await self._request(cast(ClientConnection, self._ws), cast(PendingRequests, self._pending),
                                    kind, expected, 5, payload)
            except TimeoutError as exc:
                raise ProtocolError("subscription_timeout", "等待游戏事件订阅切换确认超时") from exc
            finally:
                self.subscription_pending = False

    def configure_events(self, options: dict[str, bool]) -> None:
        self.event_options = dict(options)
        if self._events is not None:
            self._events.configure(options)

    async def set_subscription(self, enabled: bool) -> None:
        if self.desired and self.state != "connected":
            raise ProtocolError("settings_busy", "连接建立或恢复中，请稍后再修改事件订阅")
        if self.state == "connected" and not self.events_supported:
            raise ProtocolError("events_not_supported", "当前游戏端不支持事件订阅，请更新游戏端插件")
        if self.state == "connected" and enabled != self.events_subscribed:
            try:
                await self._apply_subscription(enabled)
            except (ProtocolError, OSError, ConnectionClosed):
                # 超时后无法判断游戏端是否已应用，重连以旧偏好重新建立基线。
                if self._ws:
                    await self._ws.close()
                raise
        self.events_enabled = enabled

    async def _announce(self, events: GameEvents) -> None:
        while True:
            event = await events.next()
            if self.events_subscribed and self.on_event is not None:
                try:
                    await self.on_event(event)
                except Exception as exc:
                    self.report_error("announcement_failed", f"提交游戏播报失败：{type(exc).__name__}: {exc}")

    async def _session(self) -> None:
        async with connect(
            ADDRESS, proxy=None, compression=None, open_timeout=5, close_timeout=2,
            max_size=65536, ping_interval=20, ping_timeout=10,
        ) as ws:
            self._ws = ws
            hello = self._message("hello", {"client_name": "neko_sous", "client_version": "0.1.0"})
            await ws.send(json.dumps(hello))
            try:
                welcome = await self._receive(ws, 5)
            except TimeoutError as exc:
                raise ProtocolError("handshake_timeout", "等待 welcome 握手响应超时") from exc
            payload = welcome["payload"]
            interval = payload.get("heartbeat_interval_seconds")
            timeout = payload.get("heartbeat_timeout_seconds")
            session = payload.get("session_id")
            if (welcome.get("type") != "welcome" or welcome.get("reply_to") != hello["id"]
                    or not isinstance(session, str) or not session
                    or type(interval) not in (int, float) or type(timeout) not in (int, float)
                    or not math.isfinite(interval) or not math.isfinite(timeout)
                    or not 1 <= interval < timeout <= 60):
                raise ProtocolError("protocol_error", "welcome 响应关联、会话或心跳参数无效")
            self.session_id = session
            self.server_version = str(payload.get("server_version", ""))
            capabilities = payload.get("capabilities", [])
            self.mode_supported = isinstance(capabilities, list) and "mode_switch" in capabilities
            self.events_supported = isinstance(capabilities, list) and "game_events" in capabilities
            self.state_supported = isinstance(capabilities, list) and "game_state" in capabilities
            self.orders_supported = isinstance(capabilities, list) and "order_events" in capabilities
            self.kitchen_supported = isinstance(capabilities, list) and "kitchen_events" in capabilities
            self.mixing_supported = isinstance(capabilities, list) and "mixing_events" in capabilities
            if self.mode_supported:
                if payload.get("mode") not in {"companion", "playmate"}:
                    raise ProtocolError("protocol_error", "welcome 缺少有效的当前模式")
                self.mode = payload["mode"]
            pending: PendingRequests = {}
            events = GameEvents()
            events.configure(self.event_options)
            self._pending = pending
            self._events = events
            tasks = [
                asyncio.create_task(self._dispatch(ws, pending, events)),
                asyncio.create_task(self._heartbeat(ws, pending, interval, timeout)),
                asyncio.create_task(self._control(ws, pending, min(5, timeout - interval))),
                asyncio.create_task(self._announce(events)),
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                events.clear()
                self._pending = None
                self._events = None

    async def _run(self) -> None:
        delay = 1
        try:
            while self.desired:
                try:
                    running = await self.refresh_game()
                    if not running:
                        self.state = "waiting_game"
                        if running is False:
                            self.report_error("game_not_running", "游戏已退出，等待游戏重新启动")
                    else:
                        self.state = "connecting" if delay == 1 else "reconnecting"
                        await self._session()
                except asyncio.CancelledError:
                    raise
                except ProtocolError as exc:
                    self.report_error(exc.code, str(exc))
                except OSError as exc:
                    self.report_error("connection_refused", f"无法连接 {ADDRESS}：{type(exc).__name__}: {exc}")
                except ConnectionClosed as exc:
                    self.report_error("connection_lost", f"WebSocket 连接关闭：{exc}")
                except Exception as exc:
                    self.logger.exception("[NekoSous] WebSocket 客户端异常")
                    self.report_error("connection_failed", f"{type(exc).__name__}: {exc}")
                finally:
                    self._ws = None
                    self._reset_mode()
                    was_connected = bool(self.session_id)
                    self.session_id = ""
                    self.server_version = ""
                if not self.desired:
                    break
                if was_connected:
                    delay = 1
                if self.state != "waiting_game":
                    self.state = "reconnecting"
                self.retry_in = delay
                await asyncio.sleep(delay)
                self.retry_in = 0
                delay = min(delay * 2, 10)
        finally:
            self.session_id = ""
            self.server_version = ""
            self.retry_in = 0
            self.desired = False
            self.state = "disconnected"
