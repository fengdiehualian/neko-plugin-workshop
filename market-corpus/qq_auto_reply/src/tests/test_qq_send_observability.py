# -*- coding: utf-8 -*-
"""发送成功必须留一行文件日志（真机验收发现的观测缺口）。

由来：2026-09-27 真机盘点"哪些机制在用"时发现 —— `reply_delivery_node` **只在失败/
跳过时写日志**，成功时只进 `_emit_log` 的内存环形缓冲（UI 面板）。于是日志文件里
"她今天到底发出去几条"**查不到**，只能靠"生成了几轮/回溯回了几条"间接推断：

    16 轮生成、[RetroReview] 4 条已发送 —— 正常路径那 12 轮到底发了没有？无从判断。

补一行 `[Send]` 之后，"发了什么、发到哪里、多少字"就能直接数出来。
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.pipeline_models import QQDeliveryPlan, QQMessageBlock
from plugin.plugins.qq_auto_reply.reply_delivery_node import QQReplyDeliveryNode

GROUP = "1048307485"


class _Client:
    needs_attention = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_group_message(self, group_id, text, **kw):
        self.sent.append((str(group_id), str(text)))
        return {"status": "ok"}

    async def send_message(self, target_id, text, **kw):
        self.sent.append((str(target_id), str(text)))
        return {"status": "ok"}


def _node(*, fail: bool = False):
    client = _Client()

    async def _send(group_id, text, **kw):
        if fail:
            return None            # 未确认送达（与真实客户端的 None 语义一致）
        client.sent.append((str(group_id), str(text)))
        return {"status": "ok"}

    client.send_group_message = _send  # type: ignore[assignment]
    plugin = SimpleNamespace(
        qq_client=client,
        logger=logging.getLogger("qq.delivery.send"),
        _emit_log=lambda *a, **k: None,
        _get_reply_mode=lambda: "text",
        _qq_settings={},
        voice_reply_service=SimpleNamespace(synthesize_reply_voice_file=lambda t: None),
        i18n=SimpleNamespace(t=lambda key, default=None: default or key),
    )
    return QQReplyDeliveryNode(plugin), client


def _plan(text: str = "一句话") -> QQDeliveryPlan:
    return QQDeliveryPlan(
        target_type="group", target_id=GROUP,
        blocks=[QQMessageBlock(text=text)],
    )


def test_successful_send_logs_one_line(caplog):
    node, client = _node()
    with caplog.at_level(logging.INFO):
        result = asyncio.run(node.deliver(_plan("你好呀")))

    assert result is not None and result.delivered is True
    assert client.sent == [(GROUP, "你好呀")]
    hits = [r.message for r in caplog.records if "[Send]" in r.message]
    assert len(hits) == 1, f"成功发送应恰好留一行 [Send] 日志，实际 {hits}"
    assert GROUP in hits[0] and "3 字" in hits[0], hits[0]


def test_failed_send_does_not_log_success(caplog):
    """未确认送达时不许记"已发送"——那正是这条日志要防的谎报。"""
    node, _client = _node(fail=True)
    with caplog.at_level(logging.INFO):
        result = asyncio.run(node.deliver(_plan("你好呀")))

    assert result is not None and result.delivered is False
    assert not [r for r in caplog.records if "已发送" in r.message], "发送失败却记了已发送"


def test_failed_send_says_why_it_was_not_delivered(caplog):
    """**未投递也要留一行**（2026-09-27 真机缺口：生成了 24 字却查不到为什么没发）。

    只写"发了"的那一半，等于下次出问题还是只能猜。三个分量都要落进日志。
    """
    node, _client = _node(fail=True)
    with caplog.at_level(logging.INFO):
        asyncio.run(node.deliver(_plan("你好呀")))

    hits = [r.message for r in caplog.records if "未投递" in r.message]
    assert len(hits) == 1, f"未投递应恰好留一行日志，实际 {hits}"
    assert GROUP in hits[0] and "blocks=1" in hits[0], hits[0]
    assert "有正文块=True" in hits[0] and "正文确认=False" in hits[0], hits[0]


def test_plan_without_any_content_is_reported_as_not_delivered(caplog):
    """计划里根本没有正文（纯装饰/被过滤光）时必须留痕，不能静默 return。"""
    node, client = _node()
    plan = QQDeliveryPlan(
        target_type="group", target_id=GROUP,
        blocks=[QQMessageBlock(reply_to="12345")],   # 只有引用、没有正文
    )
    with caplog.at_level(logging.INFO):
        result = asyncio.run(node.deliver(plan))

    assert result is not None and result.delivered is False
    assert client.sent == [], "只有引用没有正文的块不该发出去"
    hits = [r.message for r in caplog.records if "未投递" in r.message]
    assert hits and "有正文块=False" in hits[0], hits


def test_private_send_is_logged_too(caplog):
    """私聊同样要留痕（她今天就 0 条私聊，正因为看不到才无从确认）。"""
    node, client = _node()
    plan = QQDeliveryPlan(target_type="private", target_id="820040531",
                          blocks=[QQMessageBlock(text="在的")])
    with caplog.at_level(logging.INFO):
        asyncio.run(node.deliver(plan))
    assert client.sent == [("820040531", "在的")]
    hits = [r.message for r in caplog.records if "[Send]" in r.message]
    assert hits and "private" in hits[0], hits


def test_a_voice_reply_is_not_logged_as_zero_characters(caplog):
    """**没有正文的投递不许写成「0 字」** —— 真机 15:00:54 就是这么误导读日志的人的。

    `[Send] group … 已发送（0 字, blocks=1）` 看上去像"发了一条空消息"，而实际是
    语音（`reply_mode=both`）或表情/poke。日志要按块里**真正有什么**说。
    """
    node, _client = _node()
    plan = QQDeliveryPlan(
        target_type="group", target_id=GROUP,
        blocks=[QQMessageBlock(record="file:///tmp/x.amr")],
    )
    with caplog.at_level(logging.INFO):
        result = asyncio.run(node.deliver(plan))

    assert result is not None and result.delivered is True
    hits = [r.message for r in caplog.records if "[Send]" in r.message]
    assert hits and "语音" in hits[0], hits
    assert "0 字" not in hits[0], f"语音被写成 0 字：{hits[0]}"


def test_a_question_ending_reply_is_marked_in_the_log(caplog):
    """**问句率要可数**（2026-09-27 给她加了问句预算，就得能验证）。

    群聊里她的话不落盘（宿主记忆库只有私聊/本体对话），所以群侧只能靠日志：
    `grep -c '问句结尾' / grep -c '已发送'`。
    """
    node, _client = _node()
    with caplog.at_level(logging.INFO):
        asyncio.run(node.deliver(_plan("这个我不太懂，你能给我讲讲吗？")))

    hits = [r.message for r in caplog.records if "[Send]" in r.message]
    assert hits and "问句结尾" in hits[0], hits


def test_a_statement_is_not_marked_as_a_question(caplog):
    node, _client = _node()
    with caplog.at_level(logging.INFO):
        asyncio.run(node.deliver(_plan("我懂了，谢谢。")))

    hits = [r.message for r in caplog.records if "[Send]" in r.message]
    assert hits and "问句结尾" not in hits[0], hits


def test_question_detection_ignores_tags_and_wrapping():
    """判据落在"真正发出去的正文"上：标签剥掉、空白去掉，再看最后一个字符。"""
    node, _client = _node()
    ends = node._ends_with_question

    assert ends([QQMessageBlock(text="<text>是吗？</text>")]) is True
    assert ends([QQMessageBlock(text="是吗?  ")]) is True
    assert ends([QQMessageBlock(text="是吗。")]) is False
    assert ends([QQMessageBlock(text="")]) is False, "空正文不该被算成问句"
    assert ends([QQMessageBlock(poke="123")]) is False, "纯装饰块没有问句可言"


# ── 三、发送内容摘要 ───────────────────────────────────────────────

def test_a_sticker_only_send_is_described_as_a_sticker():
    """摘要按块里**真正有什么**写（fake 客户端没有 sticker/poke 能力，这里直接测摘要）。"""
    node, _client = _node()
    describe = node._describe_blocks

    assert describe([QQMessageBlock(sticker="1")]) == "表情"
    assert describe([QQMessageBlock(poke="820040531")]) == "戳一戳"
    assert describe([QQMessageBlock(emoji="14")]) == "表情回应"
    assert describe([QQMessageBlock(record="a.amr")]) == "语音"
    assert describe([QQMessageBlock(poke="820040531"), QQMessageBlock(text="在的")]) == "2 字/戳一戳", (
        "首块是装饰、正文在后面时必须报出正文字数（旧写法只看首块 → 0 字）"
    )
    assert describe([QQMessageBlock()]) == "无内容"
