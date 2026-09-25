"""QQ机器人桥接 v0.1 (QQ Bot Bridge)

在 N.E.K.O 里连接官方 QQ 机器人（q.qq.com / api.sgroup.qq.com），实现双向桥接：
  - 接收 QQ 群@消息、私聊、频道@消息
  - 把消息交给「回复源」生成回复，再发回 QQ
  - 回复源可配置：echo（原样返回）/ webhook（POST 到 N.E.K.O Agent 端点）/ none（只接收）
  - 提供 qq_send 入口，让猫娘/其他插件主动给 QQ 发消息

网络协议仅用 Python 标准库实现（含一个迷你 WebSocket 客户端），零第三方依赖。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
)
from plugin.sdk.plugin.ui import action as ui_action
from plugin.sdk.plugin.ui import context as ui_context

from .qq_core import DEFAULT_INTENTS, QqBotClient

_PLUGIN_ID = "qqbot_bridge"

_DEFAULTS: dict[str, Any] = {
    "app_id": "",
    "client_secret": "",
    "bot_token": "",
    "intents": DEFAULT_INTENTS,
    "reply_mode": "webhook",  # echo | webhook | none
    "webhook_url": "",
    "webhook_auth": "",
    "auto_reply": True,
    "max_recent": 50,
}


def _read_cfg(ctx: Any, key: str, default: Any) -> Any:
    """安全读取 plugin.toml 的 [qqbot_bridge] 配置段字段。"""
    try:
        section = getattr(ctx.config, _PLUGIN_ID, None)
        if section is not None:
            val = getattr(section, key, None)
            if val is not None:
                return val
    except Exception:
        pass
    return default


@neko_plugin
class QqbotBridgePlugin(NekoPluginBase):
    """QQ机器人桥接 - N.E.K.O 插件入口"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self.client: QqBotClient | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._state: dict[str, Any] = {
            "connected": False,
            "app_id": "",
            "session_id": "",
            "reply_mode": _DEFAULTS["reply_mode"],
            "webhook_url": "",
            "auto_reply": True,
            "recent": [],  # [{ts, direction, channel, author, text}]
            "error": "",
            "started_at": 0,
        }

    # ── 配置 ───────────────────────────────────────────────────
    def _cfg(self, key: str, default: Any = None) -> Any:
        if default is None:
            default = _DEFAULTS.get(key)
        return _read_cfg(self.ctx, key, default)

    def _build_client(self) -> QqBotClient:
        app_id = str(self._cfg("app_id") or "")
        secret = str(self._cfg("client_secret") or "")
        token = str(self._cfg("bot_token") or "")
        intents = self._cfg("intents", DEFAULT_INTENTS)
        try:
            intents = int(intents)
        except Exception:
            intents = DEFAULT_INTENTS
        return QqBotClient(
            app_id=app_id,
            client_secret=secret,
            token=token,
            intents=intents,
            on_event=self._on_qq_event,
            on_disconnect=self._on_client_disconnect,
            logger=self.logger,
        )

    # ── 生命周期 ───────────────────────────────────────────────
    @lifecycle(id="startup")
    async def on_startup(self) -> None:
        self.logger.info("[qqbot_bridge] 启动")
        self._state["reply_mode"] = str(self._cfg("reply_mode", "webhook"))
        self._state["webhook_url"] = str(self._cfg("webhook_url") or "")
        self._state["auto_reply"] = bool(self._cfg("auto_reply", True))
        self._state["app_id"] = str(self._cfg("app_id") or "")
        app_id = self._state["app_id"]
        if not app_id:
            self._state["error"] = "未配置 app_id，无法连接。请在 plugin.toml 的 [qqbot_bridge] 段填写 app_id / client_secret。"
            self.logger.warning("[qqbot_bridge] %s", self._state["error"])
            return
        self._running = True
        self._task = asyncio.create_task(self._run_client())

    @lifecycle(id="shutdown")
    async def on_shutdown(self) -> None:
        self._running = False
        self._state["connected"] = False
        if self.client is not None:
            try:
                await self.client.stop()
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None:
            self._task.cancel()

    async def _run_client(self) -> None:
        while self._running:
            try:
                client = self._build_client()
                self.client = client
                await client.start()
                self._state["connected"] = True
                self._state["error"] = ""
                self._state["started_at"] = time.time()
                self.logger.info("[qqbot_bridge] 已连接 QQ 机器人网关")
                # 等待断开
                while self._running and client.connected:
                    await asyncio.sleep(2)
                if not self._running:
                    break
                self.logger.warning("[qqbot_bridge] 连接断开，5 秒后重连")
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                self._state["connected"] = False
                self._state["error"] = f"连接失败：{exc}"
                self.logger.error("[qqbot_bridge] 连接 QQ 失败: %s", exc)
                await asyncio.sleep(10)
        self._state["connected"] = False

    async def _on_client_disconnect(self, reason: str) -> None:
        self._state["connected"] = False
        self.logger.info("[qqbot_bridge] 客户端断开（%s）", reason)

    # ── 事件处理 ───────────────────────────────────────────────
    async def _on_qq_event(self, event: dict[str, Any]) -> None:
        from .qq_core import normalize_qq_event

        etype = event.get("type", "")
        data = event.get("data", {}) or {}
        norm = normalize_qq_event(etype, data)
        self._push_recent(
            ts=int(time.time()),
            direction="in",
            channel=norm["channel"],
            author=norm["author"],
            text=norm["text"],
        )
        self.logger.info("[qq] %s from %s: %s", etype, norm["author"], norm["text"])

        if not bool(self._cfg("auto_reply", True)):
            return
        reply = await self._produce_reply(norm["text"], norm)
        if reply and norm["target_id"]:
            try:
                await self.client.send_raw(norm["channel"], norm["target_id"], reply, norm["msg_id"])
                self._push_recent(
                    ts=int(time.time()),
                    direction="out",
                    channel=norm["channel"],
                    author="bot",
                    text=reply,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[qq] 回复发送失败: %s", exc)

    async def _produce_reply(self, text: str, meta: dict[str, Any]) -> str | None:
        mode = self._state["reply_mode"]
        if mode == "none":
            return None
        if mode == "echo":
            return text or "（收到空消息）"
        if mode == "webhook":
            url = self._state["webhook_url"]
            if not url:
                return f"（未配置 webhook_url，无法回复）你说了：{text}"
            try:
                return await self._call_webhook(url, text, meta)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("[qq] webhook 回复失败: %s", exc)
                return f"（回复失败：{exc}）"
        return text

    async def _call_webhook(self, url: str, text: str, meta: dict[str, Any]) -> str:
        import urllib.request

        payload = json.dumps({"message": text, "meta": meta}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        auth = str(self._cfg("webhook_auth") or "")
        if auth:
            headers["Authorization"] = f"Bearer {auth}"

        def _do() -> str:
            req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")

        raw = await asyncio.to_thread(_do)
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj.get("reply") or obj.get("response") or obj.get("content") or raw
        except Exception:  # noqa: BLE001
            pass
        return raw

    def _push_recent(self, *, ts: int, direction: str, channel: str, author: str, text: str) -> None:
        self._state.setdefault("recent", []).append(
            {"ts": ts, "direction": direction, "channel": channel, "author": author, "text": text}
        )
        max_recent = int(self._cfg("max_recent", 50))
        if len(self._state["recent"]) > max_recent:
            self._state["recent"] = self._state["recent"][-max_recent:]

    # ── 面板状态 ───────────────────────────────────────────────
    @ui_context(id="main")
    async def get_ui_context(self) -> dict[str, Any]:
        return dict(self._state)

    # ── 动作：发送消息 ────────────────────────────────────────
    @ui_action(
        id="qq_send",
        label="发送消息",
        icon="📤",
        group="send",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="qq_send",
        name="发送 QQ 消息",
        description="通过官方 QQ 机器人向指定群 / 用户 / 频道发送一条文本消息。",
        input_schema={
            "type": "object",
            "properties": {
                "target_type": {
                    "type": "string",
                    "description": "目标类型：group（群）/ c2c（私聊用户）/ channel（频道）",
                    "enum": ["group", "c2c", "channel"],
                },
                "target_id": {
                    "type": "string",
                    "description": "目标 ID：group_openid / user_openid / channel_id",
                },
                "content": {"type": "string", "description": "要发送的文本"},
                "msg_id": {
                    "type": "string",
                    "description": "可选，被回复消息的 id（被动回复，5 分钟内有效）",
                },
            },
        },
    )
    async def qq_send(self, **kwargs):
        target_type = str(kwargs.get("target_type") or "group")
        target_id = str(kwargs.get("target_id") or "").strip()
        content = str(kwargs.get("content") or "").strip()
        msg_id = kwargs.get("msg_id") or None
        if not target_id or not content:
            return Err(SdkError("需要提供 target_id 与 content"))
        if self.client is None or not self.client.connected:
            return Err(SdkError("QQ 机器人未连接，请检查配置与网络"))
        try:
            result = await self.client.send_raw(target_type, target_id, content, msg_id)
            self._push_recent(ts=int(time.time()), direction="out", channel=target_type, author="bot", text=content)
            return Ok({"ok": True, "result": result})
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"发送失败：{exc}"))

    # ── 动作：重连 ────────────────────────────────────────────
    @ui_action(
        id="qq_reconnect",
        label="重连",
        icon="🔄",
        group="manage",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="qq_reconnect",
        name="重连 QQ 机器人",
        description="断开并重新连接官方 QQ 机器人网关。",
    )
    async def qq_reconnect(self, **kwargs):
        if self.client is not None:
            try:
                await self.client.stop()
            except Exception:  # noqa: BLE001
                pass
        self.client = None
        self._state["connected"] = False
        self._state["error"] = ""
        if self._task is not None:
            self._task.cancel()
        self._running = True
        self._task = asyncio.create_task(self._run_client())
        return Ok({"ok": True, "message": "已触发重连"})

    # ── 动作：状态 ────────────────────────────────────────────
    @plugin_entry(
        id="qq_status",
        name="QQ 机器人连接状态",
        description="返回当前 QQ 机器人的连接状态、回复模式与最近消息摘要。",
    )
    async def qq_status(self, **kwargs):
        recent = self._state.get("recent", [])[-10:]
        return Ok(
            {
                "connected": self._state["connected"],
                "app_id": self._state["app_id"],
                "reply_mode": self._state["reply_mode"],
                "webhook_url": self._state["webhook_url"],
                "error": self._state["error"],
                "recent_count": len(self._state.get("recent", [])),
                "recent": recent,
            }
        )
