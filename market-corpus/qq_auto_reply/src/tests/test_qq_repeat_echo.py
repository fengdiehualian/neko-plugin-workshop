"""群里复读时，猫娘跟着复读一次 —— 使用者拍板的规则。

规则原文：「让她跟着复读一次，只有复读的人大于5并且这个群是焦点的时候才会触发」。

所以这里钉的是**三个必要条件 + 一次**：

1. **>5 个不同的人**（≥6 人）复读同一句 —— 按**人**不按条：一个人刷 6 条不算
2. **这个群自己在聊** —— 原话是"这个群是焦点"，跨群取舍 2026-09-29 删除后读作
   "本群分数过保持线"；注意力关掉则不跟
3. **一句只跟一次** —— 同一句在同一个群里有冷却，免得群一直刷她就一直跟

以及三条工程约束：

* 跟的是**那句原文**（CQ 码与协议标签剥掉后逐字发出），不是让模型重写 ——
  "跟着复读"本身就是照抄，交给模型每次都可能变成别的意思
* **发送成功才落冷却**：发失败不该白白消耗这一轮的机会
* 失败不得把入站消息处理带崩（调用点在 `handle_group_message` 里）

背景实测（同日，20 次抽样，见 docs/SESSION-HANDOFF.md §4.0v）：不加这个服务时，
群里复读她**一次都没跟着复读** —— 所以这是新增行为，不是修 bug。
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.repeat_echo_service import QQRepeatEchoService

PLUGIN_DIR = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (PLUGIN_DIR / "repeat_echo_service.py").read_text(encoding="utf-8")
DISPATCHER = (PLUGIN_DIR / "message_dispatcher.py").read_text(encoding="utf-8")
BUFFER = (PLUGIN_DIR / "reply_buffer_service.py").read_text(encoding="utf-8")

GROUP = "1048307485"
OTHER_GROUP = "985066274"
LINE = "一江大气喵"


class _FakeAttention:
    """只出「本群在不在聊」这一个判据，并记下**问的是哪个群**。

    真实现是 `attention_service.is_in_conversation()`（本群分数 ≥ 保持线）；
    2026-09-29 之前这里问的是"全局焦点群是不是它"，跨群取舍删掉后换成每群各自判定。
    """

    def __init__(self, *, in_conversation: bool = True, enabled: bool = True) -> None:
        self.in_conversation = in_conversation
        self.enabled = enabled
        self.asked: list[str] = []

    def _enabled(self) -> bool:
        return self.enabled

    def is_in_conversation(self, group_id: str) -> bool:
        self.asked.append(str(group_id))
        return bool(self.in_conversation)


class _FakeQQClient:
    needs_attention = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = False

    def is_connected(self) -> bool:
        return True

    async def send_group_message(self, group_id: str, message: str):
        if self.fail:
            raise RuntimeError("transport down")
        self.sent.append((group_id, message))
        return "mid-1"


class _FakeGate:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def on_reply_sent(self, group_id: str) -> None:
        self.replies.append(group_id)


class _FakeSession:
    def __init__(self) -> None:
        self._conversation_history: list = []


def _service(*, in_conversation=True, enabled=True, fail=False, with_session=True) -> QQRepeatEchoService:
    client = _FakeQQClient()
    client.fail = fail
    gate = _FakeGate()
    session = _FakeSession()
    plugin = SimpleNamespace(
        logger=logging.getLogger("qq.test"),
        _emit_log=lambda *a, **k: None,
        qq_client=client,
        attention_service=_FakeAttention(in_conversation=in_conversation, enabled=enabled),
        attention_gate_service=gate,
        _qq_settings={},
        _user_sessions={"group:" + GROUP: {"session": session}} if with_session else {},
    )
    plugin._ensure_qq_client_connected = lambda: None
    plugin._validate_group_id = lambda gid: str(gid)
    plugin._validate_outbound_message = lambda text: str(text).strip()
    service = QQRepeatEchoService(plugin)
    service.fakes = SimpleNamespace(client=client, gate=gate, session=session)  # type: ignore[attr-defined]
    return service


def _repeat(service, *, senders: int, text: str = LINE, group: str = GROUP, start=1000.0) -> list[str]:
    """模拟 `senders` 个不同的人依次发同一句，返回实际跟着复读的内容。"""
    echoed: list[str] = []
    for index in range(senders):
        result = service.observe(
            group_id=group, sender_id=f"u{index}", text=text, now=start + index,
        )
        if result:
            echoed.append(result)
    return echoed


# ── 必要条件 1：>5 个不同的人 ─────────────────────────────────────

def test_five_people_do_not_trigger():
    """5 个人不够 —— 规则是**大于** 5。"""
    service = _service()

    assert _repeat(service, senders=5) == []


def test_six_people_trigger():
    service = _service()

    assert _repeat(service, senders=6) == [LINE]


def test_one_person_spamming_is_not_a_repeat():
    """按**人**算：一个人连发 6 条不算复读。"""
    service = _service()

    echoed = [
        service.observe(group_id=GROUP, sender_id="only", text=LINE, now=1000.0 + i)
        for i in range(6)
    ]

    assert [e for e in echoed if e] == []


def test_same_person_does_not_count_twice():
    """同一个人重复出现只算一个人头。"""
    service = _service()

    echoed = []
    for index in range(6):
        echoed.append(service.observe(
            group_id=GROUP, sender_id="dup" if index < 3 else f"u{index}", text=LINE,
            now=1000.0 + index,
        ))

    # 人头是 {dup, u3, u4, u5} = 4 个 → 不够
    assert [e for e in echoed if e] == []


# ── 必要条件 2：这个群自己在聊 ────────────────────────────────────

def test_a_group_that_is_not_in_conversation_does_not_trigger():
    """复读的人够多，但这个群自己没在聊（分数没过保持线）→ 不跟。"""
    service = _service(in_conversation=False)

    assert _repeat(service, senders=8) == []


def test_the_check_is_about_this_group_not_another_one():
    """判据必须问**本群**，不能去问别的群（跨群取舍的回归）。"""
    service = _service()

    assert _repeat(service, senders=6) == [LINE]

    asked = service.plugin.attention_service.asked
    assert asked and set(asked) == {GROUP}
    assert OTHER_GROUP not in asked


def test_attention_disabled_does_not_trigger():
    """注意力关掉时"这个群在聊"不成立，规则里的前置条件不成立。"""
    service = _service(enabled=False)

    assert _repeat(service, senders=8) == []


def test_triggering_requires_the_group_to_be_in_conversation():
    """同一个群先没在聊、后来聊起来了：聊起来之后才算。"""
    service = _service(in_conversation=False)

    assert _repeat(service, senders=6) == []
    service.plugin.attention_service.in_conversation = True
    result = service.observe(group_id=GROUP, sender_id="u6", text=LINE, now=1010.0)

    assert result == LINE


# ── 必要条件 3：一句只跟一次 ──────────────────────────────────────

def test_only_echoes_once_per_episode():
    """跟过一次之后，后面再来多少人都不再跟。"""
    service = _service()

    first = _repeat(service, senders=6)
    more = _repeat(service, senders=6, start=1010.0)

    assert first == [LINE]
    assert more == []


def test_cooldown_is_per_text():
    """冷却按"句子"算：另一句凑够人照样能跟。"""
    service = _service()

    assert _repeat(service, senders=6) == [LINE]
    assert _repeat(service, senders=6, text="哈哈哈哈") == ["哈哈哈哈"]


def test_a_new_round_after_the_cooldown_can_echo_again():
    """冷却过了，同一句再被复读还能再跟一次（不是永久拉黑）。"""
    service = _service()

    assert _repeat(service, senders=6, start=1000.0) == [LINE]
    # 冷却 600s：+301s 还在冷却里 → 不跟；+700s 过了冷却 → 可以再跟一次
    assert _repeat(service, senders=6, start=1000.0 + 301) == []
    assert _repeat(service, senders=6, start=1000.0 + 700) == [LINE]


def test_old_senders_do_not_carry_into_a_new_round():
    """人头按时间窗重数：5 个人刷完，隔很久再来 1 个人不算第 6 个。"""
    service = _service()

    assert _repeat(service, senders=5, start=1000.0) == []  # 最后一条在 1004
    late = service.observe(group_id=GROUP, sender_id="u9", text=LINE, now=1004.0 + 181)

    assert late is None


# ── 跟的是"那一句原文" ────────────────────────────────────────────

def test_echo_text_is_verbatim():
    service = _service()

    assert _repeat(service, senders=6, text="  一江大气喵！ ") == ["一江大气喵！"]


def test_echo_strips_cq_codes():
    """`[CQ:at,qq=…]` 不能原样发出去 —— 那会变成 @某人。"""
    service = _service()

    assert _repeat(service, senders=6, text="[CQ:at,qq=12345] 一江大气喵") == ["一江大气喵"]


def test_echo_strips_protocol_tags():
    service = _service()

    assert _repeat(service, senders=6, text="<msg>一江大气喵</msg>") == ["一江大气喵"]


def test_punctuation_and_space_variants_are_the_same_repeat():
    """同一句的不同标点/空格写法算同一句（否则真人复读永远凑不够人头）。"""
    service = _service()
    variants = [LINE, LINE + "！", "一江 大气喵", "一江大气喵。", "[CQ:at,qq=1] " + LINE]

    echoed = []
    for index, variant in enumerate(variants):
        echoed.append(service.observe(
            group_id=GROUP, sender_id=f"u{index}", text=variant, now=1000.0 + index,
        ))
    echoed.append(service.observe(
        group_id=GROUP, sender_id="u5", text=LINE + "～", now=1006.0,
    ))

    assert [e for e in echoed if e] == [LINE]


def test_symbol_only_messages_do_not_trigger():
    service = _service()

    assert _repeat(service, senders=8, text="！！！") == []


def test_overlong_text_does_not_trigger():
    """太长的不当复读跟：误跟的代价是以她的名义广播一大段别人的文本。"""
    service = _service()

    assert _repeat(service, senders=8, text="喵" * 41) == []


# ── 只有"一句文本"才跟 ────────────────────────────────────────────
#
# 实测踩到的（线上钩子观察到）：图片消息经 VLM 描述后正文是
# `[Image 这是mc风格的精致Q版…]`，而"六个人连发同一张图"是很常见的复读。
# 不挡的话她会把这段内部标记（连同别人的图描述）当成复读原文发进群。

def test_image_messages_do_not_trigger():
    service = _service()

    assert _repeat(service, senders=8, text="[Image 这是mc风格的精致Q版动漫女孩]") == []


def test_poke_notices_do_not_trigger():
    service = _service()

    assert _repeat(service, senders=8, text="戳一戳qq用户1561615") == []


def test_file_and_record_segments_do_not_trigger():
    service = _service()

    assert _repeat(service, senders=8, text="[CQ:file,file=a.md,file_id=53d3e03]") == []
    assert _repeat(service, senders=8, text="[CQ:record,file=x.silk]") == []


def test_cq_hidden_message_without_text_does_not_trigger():
    """只剩 CQ 段（没有描述）：剥完什么都不剩，没有"原文"可跟。"""
    service = _service()

    assert _repeat(service, senders=8, text="[CQ:at,qq=123]") == []


def test_at_prefix_on_plain_text_still_triggers():
    """带 @ 的纯文本仍然算 —— @ 只是前缀，剥掉就是那句原文。"""
    service = _service()

    assert _repeat(service, senders=6, text="[CQ:at,qq=12345] 一江大气喵") == ["一江大气喵"]


# ── 发送侧 ──────────────────────────────────────────────────────

def test_maybe_echo_sends_exactly_one_message():
    service = _service()

    sent = []
    for index in range(6):
        result = asyncio.run(service.maybe_echo(
            group_id=GROUP, sender_id=f"u{index}", text=LINE,
        ))
        if result:
            sent.append(result)

    assert sent == [LINE]
    assert service.fakes.client.sent == [(GROUP, LINE)]


def test_echo_records_history_and_attention():
    """跟一次也算一次回复：记进会话历史（她得记得自己跟了）+ 注意力簿记。"""
    service = _service()
    for index in range(5):
        asyncio.run(service.maybe_echo(group_id=GROUP, sender_id=f"u{index}", text=LINE))

    asyncio.run(service.maybe_echo(group_id=GROUP, sender_id="u5", text=LINE))

    history = service.fakes.session._conversation_history
    assert [getattr(row, "content", None) for row in history] == [LINE]
    assert service.fakes.gate.replies == [GROUP]


def test_echo_works_without_a_live_session():
    """没有活跃会话时也要能跟（消息照样发出去，只是没历史行可记）。"""
    service = _service(with_session=False)

    for index in range(5):
        asyncio.run(service.maybe_echo(group_id=GROUP, sender_id=f"u{index}", text=LINE))
    result = asyncio.run(service.maybe_echo(group_id=GROUP, sender_id="u5", text=LINE))

    assert result == LINE
    assert service.fakes.client.sent == [(GROUP, LINE)]


def test_send_failure_does_not_raise_and_does_not_consume_the_cooldown():
    """发送失败：不炸调用方，也不落冷却 —— 下一句复读还有机会跟上。"""
    service = _service(fail=True)

    for index in range(5):
        asyncio.run(service.maybe_echo(group_id=GROUP, sender_id=f"u{index}", text=LINE))
    failed = asyncio.run(service.maybe_echo(group_id=GROUP, sender_id="u5", text=LINE))

    assert failed is None
    service.fakes.client.fail = False
    retried = asyncio.run(service.maybe_echo(group_id=GROUP, sender_id="u6", text=LINE))

    assert retried == LINE


# ── 缓冲侧：不重复回应 ────────────────────────────────────────────

def test_batch_of_the_echoed_repeat_skips_the_summary():
    service = _service()
    for index in range(6):
        service.observe(group_id=GROUP, sender_id=f"u{index}", text=LINE, now=1000.0 + index)
    service.mark_echoed(group_id=GROUP, text=LINE)

    assert service.should_skip_batch_summary(
        group_id=GROUP, texts=[LINE] * 6, now=1006.0,
    ) is True


def test_batch_of_a_long_past_echo_is_still_summarized():
    """冷却早过了（新一轮）→ 该批照常总结，不该整批沉掉。"""
    service = _service()
    for index in range(6):
        service.observe(group_id=GROUP, sender_id=f"u{index}", text=LINE, now=1000.0 + index)
    service.mark_echoed(group_id=GROUP, text=LINE)

    assert service.should_skip_batch_summary(
        group_id=GROUP, texts=[LINE] * 6, now=1006.0 + 601,
    ) is False


def test_mixed_batch_is_still_summarized():
    """批里混了别的内容就得正常总结 —— 那才是有话要说的一批。"""
    service = _service()
    for index in range(6):
        service.observe(group_id=GROUP, sender_id=f"u{index}", text=LINE, now=1000.0 + index)
    service.mark_echoed(group_id=GROUP, text=LINE)

    assert service.should_skip_batch_summary(
        group_id=GROUP, texts=[LINE, LINE, "你们在聊什么"], now=1006.0,
    ) is False


def test_batch_is_summarized_when_the_echo_never_got_sent():
    """判定过但**没真发出去**（发送失败）→ 不该压掉这一批的总结：
    群里什么都没收到，再不说一句就真的没反应了。"""
    service = _service()
    for index in range(6):
        service.observe(group_id=GROUP, sender_id=f"u{index}", text=LINE, now=1000.0 + index)
    service.release_echo(group_id=GROUP, text=LINE)

    assert service.should_skip_batch_summary(
        group_id=GROUP, texts=[LINE] * 6, now=1006.0,
    ) is False


# ── 接线（源码级） ────────────────────────────────────────────────

def test_dispatcher_calls_the_echo_after_the_gate() -> None:
    """钩子必须在 `evaluate()` 之后、`ignore` 判断之前。

    之后：那条 evaluate 刚把消息计进注意力，"这个群是不是焦点"才反映现在。
    之前：回复频率闸拦的是普通回复，不该顺带把复读也拦掉（否则焦点群刷得越快
    越跟不了）。
    """
    gate_pos = DISPATCHER.index("gate_decision = await self.plugin.attention_gate_service.evaluate(")
    echo_pos = DISPATCHER.index("self.plugin.repeat_echo_service.maybe_echo(")
    ignore_pos = DISPATCHER.index('if gate_decision.action == "ignore":', gate_pos)
    assert gate_pos < echo_pos < ignore_pos, "复读钩子位置不对"


def test_buffer_asks_before_summarizing() -> None:
    """缓冲的多条总结前必须问一句"这批是不是刚跟过的复读"。"""
    body = BUFFER[BUFFER.index("# 多条缓冲 → 走 pipeline 生成总结"):]
    body = body[: body.index("async def _generate_ack")]
    assert "should_skip_batch_summary" in body, "缓冲没有问复读判定，会连着回两条"


def test_the_threshold_is_strictly_greater_than_five() -> None:
    """规则是"大于 5"，所以默认阈值必须是 6 —— 不是 5 也不是 10。"""
    assert QQRepeatEchoService.DEFAULT_MIN_SENDERS == 6


@pytest.mark.parametrize("needle", ["_is_active_group", "is_in_conversation", "min_senders"])
def test_source_keeps_the_two_conditions(needle: str) -> None:
    assert needle in SOURCE
