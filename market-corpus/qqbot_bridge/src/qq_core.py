"""Official QQ 机器人 (q.qq.com / api.sgroup.qq.com) client.

Implements the public QQ bot protocol using only the Python standard library:

  * App access token  : POST https://bots.qq.com/app/getAppAccessToken
  * Gateway discovery  : GET  https://api.sgroup.qq.com/gateway
  * Event stream       : WebSocket wss://api.sgroup.qq.com/websocket/ (op codes)
  * Send message       : POST https://api.sgroup.qq.com/v2/{groups|users|channels}/.../messages

Reference: https://bot.q.qq.com/wiki/  (QQ 机器人开放平台)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from .qq_ws import WebSocketClient, WebSocketError

__all__ = [
    "QqBotClient",
    "normalize_qq_event",
    "strip_mention",
    "DEFAULT_INTENTS",
    "INTENT_GROUP_AT_MESSAGE",
    "INTENT_C2C_MESSAGE",
    "INTENT_AT_MESSAGE",
]

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL = "https://api.sgroup.qq.com/gateway"
API_BASE = "https://api.sgroup.qq.com"

# Intent bitmasks (see QQ bot docs: https://bot.q.qq.com/wiki/develop/api/gateway/intents.html)
INTENT_GROUP_AT_MESSAGE = 1 << 25
INTENT_C2C_MESSAGE = 1 << 27
INTENT_AT_MESSAGE = 1 << 25  # channel @ (shares the bit with group @)
DEFAULT_INTENTS = INTENT_GROUP_AT_MESSAGE | INTENT_C2C_MESSAGE

_MENTION_RE = re.compile(r"<@!?[^>\s]+>|\[@!\d+\]", re.IGNORECASE)


def strip_mention(text: str) -> str:
    """Remove @bot mention tokens and surrounding whitespace from a message."""
    if not text:
        return ""
    cleaned = _MENTION_RE.sub("", text)
    return cleaned.strip()


def normalize_qq_event(event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a QQ gateway dispatch into a flat dict the plugin can use.

    Returns keys: channel, author, text, target_id, msg_id, guild_id.
      - channel: "group" | "c2c" | "channel"
      - target_id: where to send the reply (group_openid / user_openid / channel_id)
    """
    if event_type == "GROUP_AT_MESSAGE_CREATE":
        author = (data.get("author") or {}).get("user_openid") or (data.get("author") or {}).get("id") or "unknown"
        return {
            "channel": "group",
            "author": author,
            "text": strip_mention(data.get("content", "")),
            "target_id": data.get("group_openid", ""),
            "msg_id": data.get("id", ""),
            "guild_id": "",
        }
    if event_type == "C2C_MESSAGE_CREATE":
        return {
            "channel": "c2c",
            "author": data.get("user_openid", "unknown"),
            "text": strip_mention(data.get("content", "")),
            "target_id": data.get("user_openid", ""),
            "msg_id": data.get("id", ""),
            "guild_id": "",
        }
    if event_type in ("AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
        author = (data.get("author") or {}).get("username") or (data.get("author") or {}).get("id") or "unknown"
        return {
            "channel": "channel",
            "author": author,
            "text": strip_mention(data.get("content", "")),
            "target_id": data.get("channel_id", ""),
            "msg_id": data.get("id", ""),
            "guild_id": data.get("guild_id", ""),
        }
    if event_type == "DIRECT_MESSAGE_CREATE":
        return {
            "channel": "c2c",
            "author": data.get("author", {}).get("user_openid", "unknown") if isinstance(data.get("author"), dict) else "unknown",
            "text": strip_mention(data.get("content", "")),
            "target_id": data.get("guild_id", "") or data.get("author", {}).get("user_openid", ""),
            "msg_id": data.get("id", ""),
            "guild_id": data.get("guild_id", ""),
        }
    return {
        "channel": "unknown",
        "author": "unknown",
        "text": strip_mention(data.get("content", "")),
        "target_id": "",
        "msg_id": data.get("id", ""),
        "guild_id": "",
    }


def _build_opener():
    proxies: Dict[str, str] = {}
    for env_key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        val = os.environ.get(env_key)
        if val:
            proxies["http"] = val
            proxies["https"] = val
            break
    if proxies:
        handler = urllib.request.ProxyHandler(proxies)
        return urllib.request.build_opener(handler)
    return urllib.request.build_opener()


def _http_json(url: str, method: str = "GET", data: Optional[bytes] = None,
              headers: Optional[Dict[str, str]] = None, timeout: int = 15):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    opener = _build_opener()
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def get_app_access_token(app_id: str, client_secret: str, timeout: int = 15) -> tuple[str, int]:
    payload = json.dumps({"appId": app_id, "clientSecret": client_secret}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    resp = _http_json(TOKEN_URL, method="POST", data=payload, headers=headers, timeout=timeout)
    token = resp.get("access_token")
    if not token:
        raise WebSocketError(f"获取 access_token 失败: {resp}")
    return token, int(resp.get("expires_in", 7200))


def get_gateway(token: str, app_id: str, timeout: int = 15) -> str:
    headers = {"Authorization": f"QQBot {token}", "X-Bot-Appid": app_id}
    resp = _http_json(GATEWAY_URL, method="GET", headers=headers, timeout=timeout)
    return resp.get("url") or "wss://api.sgroup.qq.com/websocket/"


class QqBotClient:
    """Connects to the official QQ bot gateway and dispatches incoming events."""

    def __init__(
        self,
        app_id: str,
        client_secret: str = "",
        token: str = "",
        intents: int = DEFAULT_INTENTS,
        *,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
        on_disconnect: Optional[Callable[[str], Any]] = None,
        logger=None,
    ):
        self.app_id = app_id
        self.client_secret = client_secret
        self.token = token
        self.intents = intents
        self.on_event = on_event
        self.on_disconnect = on_disconnect
        self.logger = logger
        self.ws: Optional[WebSocketClient] = None
        self.connected = False
        self.session_id: Optional[str] = None
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval = 30.0
        self._last_seq: Optional[int] = None

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if not self.token and self.client_secret:
            tok, _ = await asyncio.to_thread(get_app_access_token, self.app_id, self.client_secret)
            self.token = tok
        if not self.token:
            raise WebSocketError("缺少 token 或 client_secret，无法连接 QQ 网关")
        gateway = await asyncio.to_thread(get_gateway, self.token, self.app_id)
        self._running = True
        self.ws = WebSocketClient(gateway, on_message=self._on_ws_message, on_close=self._on_ws_close, logger=self.logger)
        await self.ws.connect()

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        if self.ws is not None:
            await self.ws.close()
        self.connected = False

    # --- websocket handlers ------------------------------------------------

    async def _on_ws_message(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except Exception:  # noqa: BLE001
            return
        op = msg.get("op")
        if op == 10:  # Hello
            interval_ms = (msg.get("d") or {}).get("heartbeat_interval", 30000)
            self._heartbeat_interval = max(1.0, interval_ms / 1000.0)
            await self._identify()
            self._start_heartbeat()
        elif op == 11:  # Heartbeat ACK
            pass
        elif op == 7:  # Reconnect (server asks us to drop & reconnect)
            if self.logger is not None:
                self.logger.info("[qq] 收到服务器重连指令")
            await self.stop()
            if self.on_disconnect is not None:
                await self.on_disconnect("reconnect")
        elif op == 9:  # Invalid session
            await self._identify()
        elif op == 0:  # Dispatch
            self._last_seq = msg.get("s")
            t = msg.get("t")
            d = msg.get("d") or {}
            if t == "READY":
                self.session_id = d.get("session_id")
                self.connected = True
                if self.logger is not None:
                    self.logger.info("[qq] READY session=%s", self.session_id)
            elif t in (
                "GROUP_AT_MESSAGE_CREATE",
                "C2C_MESSAGE_CREATE",
                "AT_MESSAGE_CREATE",
                "GROUP_MESSAGE_CREATE",
                "DIRECT_MESSAGE_CREATE",
            ):
                if self.on_event is not None:
                    await self.on_event({"type": t, "data": d})

    async def _on_ws_close(self) -> None:
        self.connected = False
        if self.on_disconnect is not None and self._running:
            await self.on_disconnect("closed")

    async def _identify(self) -> None:
        assert self.ws is not None
        payload = json.dumps(
            {
                "op": 2,
                "d": {
                    "token": f"QQBot {self.token}",
                    "intents": self.intents,
                    "properties": {"os": "linux", "browser": "neko", "device": "neko"},
                },
            }
        )
        await self.ws.send_text(payload)

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        try:
            while self._running and self.ws is not None and not self.ws._closed:
                await self.ws.send_text(json.dumps({"op": 1, "d": self._last_seq}))
                await asyncio.sleep(self._heartbeat_interval)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            if self.logger is not None:
                self.logger.warning("[qq] heartbeat error: %s", exc)

    # --- send --------------------------------------------------------------

    async def send_group_message(self, group_openid: str, content: str, msg_id: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"content": content, "msg_type": 0}
        if msg_id:
            body["msg_id"] = msg_id
        return await self._rest_post(f"{API_BASE}/v2/groups/{group_openid}/messages", body)

    async def send_c2c_message(self, user_openid: str, content: str, msg_id: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"content": content, "msg_type": 0}
        if msg_id:
            body["msg_id"] = msg_id
        return await self._rest_post(f"{API_BASE}/v2/users/{user_openid}/messages", body)

    async def send_channel_message(self, channel_id: str, content: str, msg_id: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"content": content, "msg_type": 0}
        if msg_id:
            body["msg_id"] = msg_id
        return await self._rest_post(f"{API_BASE}/v2/channels/{channel_id}/messages", body)

    async def send_raw(self, channel: str, target_id: str, content: str, msg_id: Optional[str] = None) -> Dict[str, Any]:
        if channel == "group":
            return await self.send_group_message(target_id, content, msg_id)
        if channel == "c2c":
            return await self.send_c2c_message(target_id, content, msg_id)
        return await self.send_channel_message(target_id, content, msg_id)

    async def _rest_post(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        def _do():
            data = json.dumps(body).encode("utf-8")
            headers = {
                "Authorization": f"QQBot {self.token}",
                "X-Bot-Appid": self.app_id,
                "Content-Type": "application/json",
            }
            return _http_json(url, method="POST", data=data, headers=headers, timeout=15)

        return await asyncio.to_thread(_do)


def now_ms() -> int:
    return int(time.time() * 1000)
