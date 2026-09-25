"""验证「其它插件 ↔ QQ」通信链路（方案 A）。

- 发送：其它插件经 ``self.plugins.call_entry("qq_auto_reply:send_group_proactive_message", ...)``
  调 qq_auto_reply 的入口发送。
- 接收：qq_auto_reply 把每条入站消息广播成 ``bus.messages`` 里一条
  ``metadata.kind == "qq_message"`` 的记录（``metadata.qq_inbound`` 为结构化消息），
  其它插件用 ``bus.messages.get(plugin_id="qq_auto_reply", filter={"kind": "qq_message"})``
  订阅。

本测试用最小 mock（不连 NapCat、不起 message_plane broker），钉死这两条链路的契约。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
from plugin.sdk.shared.core.decorators import EVENT_META_ATTR


def _new_plugin() -> QQAutoReplyPlugin:
    p = QQAutoReplyPlugin.__new__(QQAutoReplyPlugin)
    # plugin_id 是只读 property（读 self.ctx.plugin_id），先铺一个最小 ctx
    p.ctx = SimpleNamespace(plugin_id="qq_auto_reply")
    # 广播经 _spawn_push_ui_event → _push_ui_event（真实 HTTP），单测 stub 掉后者避免打真宿主。
    p._push_ui_event = lambda *a, **k: None
    return p


# ── 接收：入站消息 → SSE 推送（type=qq_message, data=qq_inbound）─────────

async def test_broadcast_qq_inbound_pushes_sse_event():
    """qq_auto_reply 收到消息后，应经 SSE 推送一条 type=qq_message 事件，data 带结构化消息。"""
    plugin = _new_plugin()
    calls: list = []
    plugin._spawn_push_ui_event = lambda *a, **k: calls.append((a, k))

    await plugin._broadcast_qq_inbound({
        "message_type": "group",
        "user_id": "111",
        "group_id": "222",
        "content": "在吗",
        "message_id": "m1",
        "timestamp": 123,
        "is_at_bot": True,
        "is_reply_to_bot": False,
    })

    assert len(calls) == 1
    args, _kws = calls[0]
    assert args[0] == "qq_message"            # type
    assert args[1] == "在吗"                  # text
    qi = _kws["data"]
    # 发件人 / 收件人 / 消息文本（其它插件消费的核心字段）
    assert qi["sender"] == "111"             # 发件人
    assert qi["sender_nickname"] == ""       # 发件人昵称（本例未提供）
    assert qi["recipient"] == "222"          # 收件人（群=群号）
    assert qi["recipient_type"] == "group"
    assert qi["text"] == "在吗"              # 消息文本
    assert qi["user_id"] == "111"
    assert qi["group_id"] == "222"
    assert qi["is_at_bot"] is True


async def test_broadcast_private_recipient_is_bot_self():
    """私聊消息：收件人应为 bot 自己的 id（有 qq_client.self_id 时）。"""
    plugin = _new_plugin()
    plugin.qq_client = SimpleNamespace(self_id="bot-999")
    calls: list = []
    plugin._spawn_push_ui_event = lambda *a, **k: calls.append((a, k))
    await plugin._broadcast_qq_inbound({"message_type": "private", "user_id": "111", "content": "hi"})
    qi = calls[0][1]["data"]
    assert qi["recipient"] == "bot-999"
    assert qi["recipient_type"] == "private"
    assert qi["sender"] == "111"


async def test_broadcast_swallows_errors():
    """SSE 推送失败绝不能把消息管线带下去。"""
    plugin = _new_plugin()
    plugin._spawn_push_ui_event = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SSE down"))
    await plugin._broadcast_qq_inbound({"message_type": "group", "content": "hi"})  # 不抛


# ── 发送：其它插件 call_entry 到 qq_auto_reply 的发送入口 ─────────────────

def test_send_entry_is_exposed_with_the_four_actions():
    """其它插件 call_entry 的是**一个** `send` 入口，动作由 action 参数选。"""
    assert callable(getattr(QQAutoReplyPlugin, "send"))
    meta = getattr(QQAutoReplyPlugin.send, EVENT_META_ATTR)
    actions = meta.input_schema["properties"]["action"]["enum"]
    assert set(actions) == {"private", "group", "backlog_reply", "backlog_review"}
    # 老入口必须彻底消失，不留兼容别名
    for gone in ("send_group_proactive_message", "send_private_proactive_message",
                 "send_backlog_reply_direct"):
        assert not hasattr(QQAutoReplyPlugin, gone), gone


async def test_send_group_invokes_service():
    """call_entry("qq_auto_reply:send", {"action": "group", ...}) → 真的调群发服务。"""
    plugin = _new_plugin()
    plugin.proactive_message_service = SimpleNamespace(
        send_group_message=AsyncMock(return_value={"message_id": "sent-1"}),
    )
    result = await plugin.send(action="group", group_id="123", message="你好")

    plugin.proactive_message_service.send_group_message.assert_awaited_once_with(
        group_id="123", message="你好", verbatim=False)
    # 该入口把发送服务的结果原样透传（其它插件的 call_entry 拿到它）
    assert result == {"message_id": "sent-1"}


async def test_send_private_invokes_service():
    plugin = _new_plugin()
    plugin.proactive_message_service = SimpleNamespace(
        send_private_message=AsyncMock(return_value={}),
    )
    await plugin.send(action="private", target="888", message="私聊你好")

    plugin.proactive_message_service.send_private_message.assert_awaited_once_with(
        target="888", message="私聊你好", verbatim=False)


async def test_send_group_verbatim_forwards_flag():
    """verbatim=true 时应把开关透传给发送服务（原文直发，不经 LLM 生成）。"""
    plugin = _new_plugin()
    plugin.proactive_message_service = SimpleNamespace(
        send_group_message=AsyncMock(return_value={"status": "sent", "verbatim": True, "message_id": "m-1"}),
    )
    result = await plugin.send(action="group", group_id="123", message="原文直发", verbatim=True)

    plugin.proactive_message_service.send_group_message.assert_awaited_once_with(
        group_id="123", message="原文直发", verbatim=True)
    assert result == {"status": "sent", "verbatim": True, "message_id": "m-1"}


async def test_send_verbatim_string_is_coerced():
    """JSON 来的 verbatim 可能是字符串 —— "false" 是真值，必须统一转 bool。"""
    plugin = _new_plugin()
    plugin.proactive_message_service = SimpleNamespace(
        send_group_message=AsyncMock(return_value={}),
    )
    await plugin.send(action="group", group_id="1", message="x", verbatim="false")

    assert plugin.proactive_message_service.send_group_message.await_args.kwargs["verbatim"] is True  # 非空字符串 → True，与旧行为一致


async def test_send_rejects_missing_required_params():
    plugin = _new_plugin()
    plugin.proactive_message_service = SimpleNamespace(send_group_message=AsyncMock())

    r = await plugin.send(action="group", group_id="", message="")
    assert r.is_err() and "INVALID_INPUT" in str(r.error)
    plugin.proactive_message_service.send_group_message.assert_not_awaited()


async def test_send_rejects_unknown_action():
    plugin = _new_plugin()
    r = await plugin.send(action="broadcast")
    assert r.is_err() and "BAD_ACTION" in str(r.error)
