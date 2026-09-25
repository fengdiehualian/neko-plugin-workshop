from __future__ import annotations

import asyncio

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, ui


@neko_plugin
class QQSendTestPlugin(NekoPluginBase):
    """验证「其它插件 ↔ QQ」链路的最小测试插件（收发一体）。

    - 发送：``self.plugins.call_entry(f"{_SOURCE_PLUGIN}:send", {"action": "group", ..., "verbatim": True})``
      原文直发（走 qq_auto_reply 那条已建立的连接，无需再开第二条 WS）。
    - 接收链路（唯一）：启动时连 SSE 流 ``GET /plugin/{_SOURCE_PLUGIN}/ui-api/events``，收
      ``type=qq_message`` 帧（``data`` 即 qq_inbound），存进 ``_received``，用 ``get_received`` 查看。
    """

    #: 目标/源插件 id（唯一硬编码处）；换成别的桥接插件即可复测该插件↔其它插件。
    _SOURCE_PLUGIN = "qq_auto_reply"

    @lifecycle(id="startup")
    async def startup(self, **_):
        # 托管 static/（含 index.html），供 UI panel 使用。
        # no-cache：否则默认 max-age=3600，浏览器会把旧版 index.html 缓存一小时，
        # 改代码后用户仍看到旧前端。和 qq_auto_reply 的 register_static_ui 一致。
        self.register_static_ui("static", cache_control="no-cache")
        self._received: list[dict] = []
        # 唯一接收链路：订阅 SSE 流，实时收 type=qq_message 帧。
        self._sse_task = None
        try:
            self._sse_task = asyncio.get_running_loop().create_task(self._watch_qq_sse())
        except Exception:
            self._sse_task = None
        return Ok({"status": "ready"})

    async def _watch_qq_sse(self) -> None:
        """连接 /plugin/qq_auto_reply/ui-api/events 的 SSE 流，收集 type=qq_message 帧。"""
        import json as _json

        import httpx as _httpx
        try:
            from config import USER_PLUGIN_BASE
        except Exception:
            USER_PLUGIN_BASE = "http://127.0.0.1:48916"
        url = f"{USER_PLUGIN_BASE}/plugin/{self._SOURCE_PLUGIN}/ui-api/events"
        async with _httpx.AsyncClient(timeout=None) as c:
            async with c.stream("GET", url) as r:
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = _json.loads(line[len("data: "):])
                    except Exception:
                        continue
                    if ev.get("type") == "qq_message" and isinstance(ev.get("data"), dict):
                        self._received.append(ev["data"])
        # 流自然断开或被取消 → 返回

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        try:
            if getattr(self, "_sse_task", None) is not None and not self._sse_task.done():
                self._sse_task.cancel()
        except Exception:
            pass
        return Ok({"status": "shutdown"})

    @ui.action(label="向 QQ 群发测试消息", refresh_context=True)
    @plugin_entry(
        id="send_test",
        name="向 QQ 群发测试消息",
        description="向指定 QQ 群原文直发（verbatim）一条测试消息，验证其它插件 ↔ QQ 链路。需传 group_id（收件人或群号）与 text。",
        input_schema={
            "type": "object",
            "properties": {
                "group_id": {"type": "string", "description": "目标群号/收件人"},
                "text": {"type": "string", "description": "要逐字发送的内容"},
            },
            "required": ["group_id", "text"],
            "additionalProperties": False,
        },
    )
    async def send_test(self, group_id: str = "", text: str = "", **_):
        gid = str(group_id or "").strip()
        msg = str(text or "").strip()
        if not gid:
            return Err(SdkError("INVALID_INPUT: group_id 不能为空"))
        if not msg:
            return Err(SdkError("INVALID_INPUT: text 不能为空"))

        # plugins.exists 返回 Result[bool]（Ok(True/False)/Err），不是裸 bool——
        # Ok/Err 没有 __bool__，恒为真值，直接 `not exists(...)` 会让守卫永不触发。
        existed = await self.plugins.exists(self._SOURCE_PLUGIN)
        if hasattr(existed, "is_err") and existed.is_err():
            return Err(SdkError(f"PLUGIN_MISSING: {getattr(existed, 'error', 'unknown')}"))
        if not getattr(existed, "value", True):
            # Ok(False)：目标插件未启用 → 明确报错，不再往下沉。
            return Err(SdkError(f"PLUGIN_MISSING: 找不到 {self._SOURCE_PLUGIN}，请先启用它"))

        # 主动发送 + verbatim=true：源插件原文直发（不经 LLM 生成），确定性快。
        sent = await self.plugins.call_entry(
            f"{self._SOURCE_PLUGIN}:send",
            {"action": "group", "group_id": gid, "message": msg, "verbatim": True},
            timeout=30.0,
        )
        if isinstance(sent, Err):
            return Err(SdkError(f"SEND_FAILED: {sent.error}"))
        # call_entry 返回 Ok(response)，而 response 是目标入口的 Ok/Err——需解包一层，
        # 否则入口失败（如 NOT_READY）会被外层 Ok 吞掉，误报「已发送」。
        response = getattr(sent, "value", None)
        if hasattr(response, "is_err") and response.is_err():
            return Err(SdkError(f"SEND_FAILED: {getattr(response, 'error', 'unknown')}"))
        if isinstance(response, Err):
            return Err(SdkError(f"SEND_FAILED: {response.error}"))
        value = getattr(response, "value", response) if isinstance(response, Ok) else response
        return Ok({"sent": True, "group_id": gid, "text": msg, "result": value})

    @ui.action(label="查看收到的 QQ 消息", refresh_context=True)
    @plugin_entry(
        id="get_received",
        name="查看收到的 QQ 消息",
        description="列出 qq_send_test 启动以来，经 SSE 流(ui-api/events) 收到的 type=qq_message 事件。",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    async def get_received(self, **_):
        # 唯一接收链路：_received 只由 SSE watcher 追加（按序），新在前返回。
        received = list(getattr(self, "_received", None) or [])
        received.reverse()
        return Ok({"count": len(received), "received": received[:50]})

    @ui.action(label="探测 qq_auto_reply 跨插件调用耗时", refresh_context=True)
    @plugin_entry(
        id="ping_qq",
        name="探测 qq_auto_reply 跨插件调用耗时",
        description="调用 qq_auto_reply 的一个快速读入口（get_dashboard_state），返回耗时，用于定位跨插件链路是否通。",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    async def ping_qq(self, **_):
        import time as _t
        t0 = _t.time()
        r = await self.plugins.call_entry(
            f"{self._SOURCE_PLUGIN}:query", {"action": "dashboard"}, timeout=15.0)
        elapsed = round(_t.time() - t0, 2)
        if isinstance(r, Err):
            return Err(SdkError(f"PING_ERR: {r.error}"))
        val = getattr(r, "value", None) if isinstance(r, Ok) else None
        return Ok({"elapsed_s": elapsed, "onebot_connected": (val or {}).get("onebot_connected") if isinstance(val, dict) else None})
