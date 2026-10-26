"""引用链进 prompt 的渲染：必须在扁平文本里保住结构化信息。

旧实现是裸 ``chain.repr`` 拼一行 ``[↑ 昵称 时间: 内容]``，链里已有但被丢掉的两样：

1. **发言人 QQ** —— 只有昵称时，群里重名昵称会让模型分不清"谁在跟谁说话"。
2. **嵌套引用指针** —— B 引用了 C 时，模型看不到"被引用的那条本身也在回应别人"。

这两样都是 ``_build_message_chain`` 递归拉取时**已经付过代价**拿到的
（``Reply.chain``），此前只用于 ``repr`` 就整段丢弃，所以补上不需要额外 API 调用。
"""
from __future__ import annotations

import re
from datetime import datetime
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.enrichment import QQMessageEnricher
from plugin.plugins.qq_auto_reply.message_chain import (
    At,
    MessageChain,
    Reply,
    Text,
)


def _expected_ts(timestamp: int) -> str:
    """按本机本地时间算出期望的头（与生产同一口径，不写死日期）。"""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _chain(sender_name="小明", sender_id="10001", ts=1700000000, elements=None):
    chain = MessageChain(sender_name=sender_name, sender_id=sender_id, timestamp=ts)
    for el in elements or [Text("你好")]:
        chain.add(el)
    return chain


def test_header_carries_the_sender_qq_not_just_the_nickname():
    """昵称之外必须带上 QQ —— 重名昵称下它是唯一能区分人的东西。"""
    out = QQMessageEnricher._format_reply_chains([_chain(sender_name="小明", sender_id="10001")])
    assert "小明" in out
    assert "10001" in out, f"发言人 QQ 丢了: {out!r}"
    assert "你好" in out


def test_header_still_carries_the_timestamp():
    """头里要带时间戳，且按**本机本地时间**渲染（与插件里"当前时间"那段同口径）。

    ⚠️ 断言**不许写死日期**：`1700000000` 在 UTC+8 是 `2023-11-15 06:13:20`，
    在 CI 的 UTC 上是 `2023-11-14 22:13:20`。这条测试原本写的是
    `assert "2023-11-15" in out` —— 于是同一份代码在本机绿、在 CI 红（真红过一次）。
    """
    out = QQMessageEnricher._format_reply_chains([_chain(ts=1700000000)])

    assert _expected_ts(1700000000) in out, f"时间戳丢了或格式变了: {out!r}"
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", out), f"时间戳形态不对: {out!r}"


def test_the_timestamp_assertion_holds_in_any_timezone(monkeypatch):
    """把渲染时钟换成 UTC 再断言一次 —— 等于在本机复现 CI 的时区。

    没有这条，"断言是否依赖本机时区"只能靠人肉推理；有了它，写死日期那种错会在
    本机就红，而不是等 CI。
    """
    import plugin.plugins.qq_auto_reply.enrichment as enrichment

    utc_now = datetime(2023, 11, 14, 22, 13, 20)
    monkeypatch.setattr(
        enrichment, "_dt",
        SimpleNamespace(fromtimestamp=lambda _ts: utc_now),
    )

    out = QQMessageEnricher._format_reply_chains([_chain(ts=1700000000)])

    assert "2023-11-14 22:13:20" in out, f"UTC 下渲染不对: {out!r}"


def test_the_header_uses_local_time_not_utc():
    """源码级：头里的时间用 `fromtimestamp`（本地）而不是 `utcfromtimestamp`。

    这条钉的是"口径"本身 —— 插件其它地方（`_format_current_time`、时间提示段）
    都用本地时间，引用链的头不能自己换一套。

    覆盖**两处**时间头：引用链（`_format_reply_chains`）与转发链
    （`_fetch_forward_content`）。只钉一处的话，另一处偷偷换成 UTC 不会被发现。
    """
    import pathlib

    import plugin.plugins.qq_auto_reply.enrichment as enrichment

    source = pathlib.Path(enrichment.__file__).read_text(encoding="utf-8")

    reply_body = source[source.index("def _format_reply_chains"):]
    reply_body = reply_body[: reply_body.index("def _resolve_reply_sender")]
    assert "_dt.fromtimestamp(" in reply_body, "引用链头里的时间戳没有用 fromtimestamp"
    assert "utcfromtimestamp" not in reply_body, "引用链头里的时间戳换成了 UTC，与插件其它地方不一致"
    assert "timezone.utc" not in reply_body

    forward_body = source[source.index("async def _fetch_forward_content"):]
    forward_body = forward_body[: forward_body.index("async def _build_message_chain")]
    assert "_dt.fromtimestamp(" in forward_body, "转发链头里的时间戳没有用 fromtimestamp"
    assert "utcfromtimestamp" not in forward_body, "转发链头里的时间戳换成了 UTC，与插件其它地方不一致"
    assert "timezone.utc" not in forward_body


def test_forward_chain_header_carries_local_time():
    """转发链的头 `[转发] [时间] 发送者: 内容` 也要按本机本地时间渲染。

    行为级的那条（`test_the_timestamp_assertion_holds_in_any_timezone`）只走
    `_format_reply_chains`；转发链是另一条独立分支，不测就等于没钉住
    —— 实测把这里的 `fromtimestamp` 换成 `utcfromtimestamp`，只有源码级那条会红。
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from plugin.plugins.qq_auto_reply.connector_seam import OneBotClient

    client = OneBotClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
    client._self_id = "10001"
    enricher = QQMessageEnricher(client)

    ts = 1700000000
    forward_payload = {
        "messages": [{
            "user_id": "20002",
            "message_id": "F-sub-1",
            "time": ts,
            "sender": {"nickname": "小红"},
            "message": [{"type": "text", "data": {"text": "合并转发的内容"}}],
        }],
    }
    message = {"content": "[CQ:forward,id=F123]", "raw": ""}
    with patch.object(client, "get_forward_msg", AsyncMock(return_value=forward_payload)):
        asyncio.run(enricher._fetch_forward_content(message, ["F123"]))

    out = str(message.get("raw_message") or "")
    assert "合并转发的内容" in out, f"转发内容没展开: {out!r}"
    assert _expected_ts(ts) in out, f"转发链头的时间戳丢了或用了 UTC: {out!r}"


def test_forward_chain_timestamp_holds_in_any_timezone(monkeypatch):
    """把渲染时钟换成 UTC 再断言一次转发链的头 —— 在本机复现 CI 的时区。"""
    import asyncio
    from unittest.mock import AsyncMock, patch

    import plugin.plugins.qq_auto_reply.enrichment as enrichment
    from plugin.plugins.qq_auto_reply.connector_seam import OneBotClient

    monkeypatch.setattr(
        enrichment, "_dt",
        SimpleNamespace(fromtimestamp=lambda _ts: datetime(2023, 11, 14, 22, 13, 20)),
    )

    client = OneBotClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
    client._self_id = "10001"
    enricher = QQMessageEnricher(client)

    forward_payload = {
        "messages": [{
            "user_id": "20002",
            "message_id": "F-sub-1",
            "time": 1700000000,
            "sender": {"nickname": "小红"},
            "message": [{"type": "text", "data": {"text": "合并转发的内容"}}],
        }],
    }
    message = {"content": "[CQ:forward,id=F123]", "raw": ""}
    with patch.object(client, "get_forward_msg", AsyncMock(return_value=forward_payload)):
        asyncio.run(enricher._fetch_forward_content(message, ["F123"]))

    out = str(message.get("raw_message") or "")
    assert "2023-11-14 22:13:20" in out, f"UTC 下转发链渲染不对: {out!r}"
    assert "2023-11-15" not in out, "转发链的头写死了本地日期口径"


def test_nested_reply_pointer_is_preserved():
    """被引用的消息本身又引用了别人 —— 那个 ID 必须出现在文本里。

    修之前 ``Reply.repr`` 在 ``chain`` 非空时只返回内层内容，B 在回应 C 这个
    事实对模型完全不可见。
    """
    inner = _chain(sender_name="小红", sender_id="20002", elements=[Text("原始内容")])
    outer = _chain(elements=[Text("我同意"), Reply(message_id="C999", chain=inner)])

    out = QQMessageEnricher._format_reply_chains([outer])
    assert "C999" in out, f"嵌套引用指针丢了: {out!r}"
    assert "我同意" in out
    # 内层内容也应被带出来（Reply.repr 在 chain 非空时返回内层 repr）
    assert "原始内容" in out


def test_nested_ids_are_collected_deduped_and_exclude_the_direct_quote():
    """``_replied_message_ids`` 只收**嵌套**的，且去重、保序。"""
    # A 引用了 B（B 又引用了 C 和 C）。B 的 message_id 是外层，不该出现在这里。
    deep_c = _chain(elements=[Text("最里层")])
    mid = _chain(elements=[
        Text("中间"),
        Reply(message_id="C1", chain=deep_c),
        Reply(message_id="C1", chain=deep_c),   # 重复：必须去重
    ])
    outer = _chain(elements=[Text("外层"), Reply(message_id="B1", chain=mid)])

    ids = QQMessageEnricher._nested_replied_message_ids([outer])
    assert ids == ["B1", "C1"], f"实际={ids}"
    # 直接引用的那条（入站引用的 B1）是外层 chain 的 message_id，不在这里冒充
    assert "fake" not in ids


def test_no_nested_reply_yields_empty_list():
    """没有嵌套引用时不该凭空产出 ID。"""
    chain = _chain(elements=[Text("普通引用"), Reply(message_id="X1")])
    assert QQMessageEnricher._nested_replied_message_ids([chain]) == ["X1"]
    plain = _chain(elements=[Text("没引用任何人")])
    assert QQMessageEnricher._nested_replied_message_ids([plain]) == []


def test_falls_back_to_id_when_nickname_is_missing():
    out = QQMessageEnricher._format_reply_chains([_chain(sender_name="", sender_id="30003")])
    assert "30003" in out
    assert "未知用户" not in out, "有 sender_id 时不该落回「未知用户」"


def test_at_without_nickname_still_renders():
    """链里带 at 段时不能崩 —— 昵称解析是可选的（需要一次 API 调用）。"""
    out = QQMessageEnricher._format_reply_chains([_chain(elements=[At(pid="40004")])])
    assert "40004" in out


# ── 端到端：_fetch_reply_content（此前零覆盖）────────────────────────

def _onebot_msg(mid: str, uid: str, nick: str, text: str, *, reply_to: str = "") -> dict:
    segments: list[dict] = []
    if reply_to:
        segments.append({"type": "reply", "data": {"id": reply_to}})
    segments.append({"type": "text", "data": {"text": text}})
    return {
        "message_id": mid,
        "user_id": uid,
        "time": 1700000000,
        "sender": {"nickname": nick},
        "message": segments,
    }


def test_fetch_reply_content_populates_context_and_nested_ids():
    """入站引用了 B，B 又引用了 C：context 要出内容与发言人，嵌套 ID 要单独列出。

    这是把"解析到的结构化信息真正交到 prompt 手里"的端到端证明。
    修之前 ``_reply_context`` 只有 ``[↑ 昵称 时间: 内容]``：没有发言人 QQ，
    也没有"C999 的存在"。
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from plugin.plugins.qq_auto_reply.connector_seam import OneBotClient

    client = OneBotClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
    client._self_id = "10001"
    enricher = QQMessageEnricher(client)

    b_msg = _onebot_msg("B111", "20002", "小红", "我同意", reply_to="C999")
    c_msg = _onebot_msg("C999", "30003", "小刚", "原始论断")
    with patch.object(client, "get_msg", AsyncMock(side_effect=lambda mid: {
        "B111": b_msg, "C999": c_msg,
    }[mid])):
        message = {"raw_message": "[CQ:reply,id=B111] 我也觉得", "content": ""}
        asyncio.run(enricher._fetch_reply_content(message, ["B111"]))

    ctx = message.get("_reply_context", "")
    assert ctx, "没有产出 _reply_context"
    assert "小红" in ctx and "20002" in ctx, f"被引用者的 QQ 丢了: {ctx!r}"
    assert "我同意" in ctx
    assert "C999" in ctx, f"嵌套引用指针丢了: {ctx!r}"
    # 被引用消息自身引用的 ID 单独列出，供后续需要时使用
    assert message.get("_replied_message_ids") == ["C999"]
    # 裸 CQ 码要从 raw_message 里清掉，别让它进 prompt/history
    assert "CQ:reply" not in str(message.get("raw_message") or "")


def test_fetch_reply_content_degrades_when_get_msg_fails():
    """get_msg 失败必须自降级：抛不出去，且不产出半截 context。"""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from plugin.plugins.qq_auto_reply.connector_seam import OneBotClient

    client = OneBotClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
    client._self_id = "10001"
    enricher = QQMessageEnricher(client)

    with patch.object(client, "get_msg", AsyncMock(side_effect=RuntimeError("not connected"))):
        message = {"raw_message": "[CQ:reply,id=B111] hi", "content": ""}
        asyncio.run(enricher._fetch_reply_content(message, ["B111"]))   # 不得抛出

    assert not message.get("_reply_context")
    assert not message.get("_replied_message_ids")
    # 拿不到内容时 raw_message 保持原样（不静默删掉那条 CQ 码）
    assert "CQ:reply" in str(message.get("raw_message") or "")


# ── 非数字消息 ID：CQ 码清洗必须对它生效 ─────────────────────────────

def test_non_numeric_message_ids_are_sanitized():
    """CQ 清洗正则不能假设消息 ID 是纯数字。

    实测 backlog 里 120 个 message_id 有 14 个非纯数字
    （``poke_1048307485_3281414178_1790149679`` 这类），开放平台/Lagrange 更甚。
    旧正则 ``id=\\d+`` 对它们整体不匹配，于是：

    - ``_sanitize_message_text`` 里替换不掉 —— **用户会在聊天里看到裸 CQ 码**；
    - ``_fetch_reply_content`` 里清不掉 —— 裸 CQ 码漏进 prompt 与会话历史。
    """
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    for mid in ("B111", "poke_1048307485_3281414178_1790149679", "1604095307"):
        out = QQAutoReplyPlugin._sanitize_message_text(
            f"[CQ:reply,id={mid}] 你好", is_reply_to_bot=False,
        )
        assert "CQ:reply" not in out, f"id={mid} 没被替换: {out!r}"
        assert out == "[回复他人的消息] 你好"

        out_bot = QQAutoReplyPlugin._sanitize_message_text(
            f"[CQ:reply,id={mid}] 你好", is_reply_to_bot=True,
        )
        assert out_bot == "[回复你的消息] 你好"

