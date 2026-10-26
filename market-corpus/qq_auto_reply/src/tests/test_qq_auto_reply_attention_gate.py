"""Per-group attention floor: each group is judged by **its own** score, not by a global focus.

Pins the gating order of `attention_gate_service.evaluate()` (2026-09-29, after the
cross-group focus selection was removed -- user's words: 「每个群自己管自己的注意力」):

1. @bot direct mention -- or **her name in the text** (`named_bot`, 2026-09-29
   user's words: 「叫猫娘名字也等于@她」) -- the only bypass, force-replies
   in any group
2. blacklist -> ignored everywhere
3. groups outside the attention competition -> pass through to the downstream permission layer
4. keyword / reply-to-bot -> force reply **before** the attention floor, so being
   mentioned in a cold group still gets an answer
5. otherwise the floor: the group's own score below the conversation line -> ignore
   (`low_attention`), at/above it -> hand the message to the LLM (`in_conversation`)

The former `non_focus` branch (drop everything outside the single focus group) is gone;
these tests exist so it cannot quietly come back.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_gate_service import QQAttentionGateService


class _FakeAttention:
    """Attention stub with **per-group** scores; `is_in_conversation` mirrors the real rule.

    Deliberately implements the same pair as `attention_service`: a group is "in
    conversation" when its own score is at or above the conversation line. A stub that
    let the caller pass the answer in by hand would not catch a regression where the
    gate reads some *other* group's score.
    """

    THRESHOLD = 2.0

    def __init__(self, *, scores: dict[str, float] | None = None, enabled: bool = True):
        self._scores = dict(scores or {})
        self._enabled_flag = enabled
        self.calls: list[str] = []
        self._now = 1000

    def _enabled(self) -> bool:
        return self._enabled_flag

    def _current_time(self) -> int:
        return self._now

    def get_focus_group(self) -> str | None:
        # 跨群取舍已删除：门控**不该**再问"全局焦点是谁"。真到这里就记一笔，
        # 由 `test_gate_never_asks_for_a_global_focus` 钉住。
        self.calls.append("get_focus_group")
        return None

    def _score_for(self, group_id: str) -> float:
        return float(self._scores.get(str(group_id or "").strip(), 5.0))

    def get_state(self, group_id: str):
        self.calls.append(f"get_state:{group_id}")
        return SimpleNamespace(attention_score=self._score_for(group_id))

    def is_in_conversation(self, group_id: str) -> bool:
        self.calls.append(f"is_in_conversation:{group_id}")
        if not self._enabled_flag:
            return False
        return self._score_for(group_id) >= self.THRESHOLD

    def conversation_threshold(self) -> float:
        return self.THRESHOLD

    def _minimum_threshold(self) -> float:
        return 1.0

    def _focus_threshold(self) -> float:
        return 4.0

    async def update_on_message(self, message: dict) -> None:
        self.calls.append(f"update_on_message:{message.get('group_id')}")

    def mark_focus(self, group_id: str) -> None:
        self.calls.append(f"mark_focus:{group_id}")

    def lock_group(self, group_id: str) -> None:
        # 被 @ 会加锁（期内该群独占）——桩必须跟上，否则门控在这一步就炸。
        self.calls.append(f"lock_group:{group_id}")

    def wake_boost(self, group_id: str) -> None:
        self.calls.append(f"wake_boost:{group_id}")


def _plugin(attention) -> SimpleNamespace:
    return SimpleNamespace(
        attention_service=attention,
        qq_client=SimpleNamespace(needs_attention=True, _sent_message_ids={}),
        permission_mgr=None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _qq_settings={"backlog_labels": []},
        _emit_log=lambda *a, **k: None,
        _run_with_session_lock=None,
        reply_buffer_service=None,
        session_memory_service=None,
        reply_pipeline=None,
        runtime_service=None,
        _admin_qq="0",
        _build_session_key=lambda **k: "",
    )


async def _evaluate(plugin, **kwargs) -> tuple:
    gate = QQAttentionGateService(plugin)
    default = dict(
        group_id="g1",
        sender_id="u1",
        is_at_bot=False,
        message_text="hello",
        quoted_message_id="",
        timestamp=1000,
    )
    default.update(kwargs)
    decision = await gate.evaluate(**default)
    return decision, gate


# ── 叫她的名字 = @她（2026-09-29 使用者口径）────────────────────────


def test_named_bot_is_as_good_as_an_at():
    """凉群里叫她的名字 → 一样强制回复（从"打分里 +40"升级成门控层的等同 @）。

    判据来自 `addressing` 的 `named_bot` 档：没人被 @、也没引用谁，只是文本里有她的
    名字/别名。它会走 @ 那条一模一样的旁路（锁群 + 抬分 + 清退避），只是 reason
    分开写，日志里能分辨这一轮是怎么被叫起来的。
    """
    attention = _FakeAttention(scores={"g1": 0.2})       # 凉群：普通消息会被拦
    plugin = _plugin(attention)
    plugin._qq_settings = {"addressee_names": ["猫娘"], "backlog_labels": []}

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1", message_text="猫娘今天怎么样"))

    assert decision.action == "reply"
    assert decision.reason == "named_bot", decision.reason
    assert decision.force_reply is True
    assert "lock_group:g1" in attention.calls, "@ 与叫名字该走同一条锁群路径"
    assert "mark_focus:g1" in attention.calls
    assert "wake_boost:g1" in attention.calls


def test_a_plain_message_without_her_name_is_still_blocked_in_a_cold_group():
    """反面：没叫她的名字就还是走注意力闸（别把这一档放宽成"什么都能过"）。"""
    attention = _FakeAttention(scores={"g1": 0.2})
    plugin = _plugin(attention)
    plugin._qq_settings = {"addressee_names": ["猫娘"], "backlog_labels": []}

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1", message_text="今天天气不错"))

    assert decision.action == "ignore"
    assert decision.reason.startswith("low_attention")


def test_a_message_atting_someone_else_is_not_saved_by_her_name():
    """既 @ 了别人、又提她名字 → 仍然算"冲着别人说的"，名字救不回来。

    判据顺序没变：`named_bot` 是第 6 档，排在 `at_other` 之后。这一条防的是"把名字
    判据提到 @别人 之前"，那会让"@张三 猫娘你看呢"这种插话式的消息也变成点名她。
    """
    attention = _FakeAttention(scores={"g1": 0.2})
    plugin = _plugin(attention)
    plugin._qq_settings = {"addressee_names": ["猫娘"], "backlog_labels": []}

    decision, _ = asyncio.run(_evaluate(
        plugin, group_id="g1", message_text="猫娘你看呢",
        mentioned_user_ids=["someone-else"],
    ))

    assert decision.action == "ignore", decision.reason
    assert decision.reason.startswith("low_attention")


# ── 注意力闸：只看本群 ─────────────────────────────────────────────


def test_cold_group_plain_message_blocked_and_attention_accumulated():
    """本群分数低于保持线 → 不搭话；但消息**照样**计分（下次才可能热起来）。"""
    attention = _FakeAttention(scores={"g1": 0.5})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason.startswith("low_attention")
    assert not decision.force_reply
    assert "update_on_message:g1" in attention.calls
    assert "mark_focus:g1" not in attention.calls


def test_a_hot_other_group_does_not_rescue_a_cold_group():
    """别的群再热也不改变本群的判定——这正是删掉跨群取舍要钉住的性质。"""
    attention = _FakeAttention(scores={"g1": 0.4, "g2": 9.5})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason.startswith("low_attention")


def test_a_cold_other_group_does_not_block_a_warm_group():
    """反向同理：本群热着就交给 LLM，哪怕同时有别的群凉着。"""
    attention = _FakeAttention(scores={"g1": 5.0, "g2": 0.1})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "reply"
    assert decision.reason == "in_conversation"
    assert decision.force_reply is False


def test_gate_never_asks_for_a_global_focus():
    """门控里不许再出现"全局焦点是谁"这个问题。"""
    attention = _FakeAttention(scores={"g1": 5.0})
    plugin = _plugin(attention)

    asyncio.run(_evaluate(plugin, group_id="g1"))

    assert "get_focus_group" not in attention.calls
    assert "is_in_conversation:g1" in attention.calls


def test_warm_group_plain_message_passes_to_llm():
    """本群在聊的普通消息不强制回复，交给 LLM 自行判断。"""
    attention = _FakeAttention(scores={"g1": 5.0})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "reply"
    assert decision.reason == "in_conversation"
    assert decision.force_reply is False


def test_warm_group_above_send_gate_not_focus_line():
    """本群分数过了保持线（2.0）但没到焦点线（4.0）→ 照样放行。

    回归：发送门控以前误用焦点线，2.1 < 4.0 就被拦，"保住注意力"形同虚设。
    """
    attention = _FakeAttention(scores={"g1": 2.1})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "reply"
    assert decision.reason == "in_conversation"
    assert decision.force_reply is False


def test_cold_group_below_conversation_line_logged():
    """低于保持线时给出可读原因（带分数），否则事后查不到"为什么没回"。"""
    attention = _FakeAttention(scores={"g1": 0.5})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason == "low_attention(0.5)"


# ── 点名优先：关键词 / 引用她 排在注意力闸**之前** ────────────────


def test_cold_group_reply_to_bot_force_replies():
    """引用她 = 点名叫她，凉群里也要回（以前这里返回 non_focus 直接丢掉）。"""
    attention = _FakeAttention(scores={"g1": 0.2})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(
        plugin,
        group_id="g1",
        quoted_message_id="m1",
        is_reply_to_bot=True,  # 连接层已经判定这是回复猫娘
    ))

    assert decision.action == "reply"
    assert decision.reason == "reply_to_bot"
    assert decision.force_reply is True
    assert "update_on_message:g1" in attention.calls
    assert "mark_focus:g1" in attention.calls


def test_cold_group_keyword_force_replies():
    """关键词（issue 标签）同样优先于注意力闸。"""
    attention = _FakeAttention(scores={"g1": 0.2})
    plugin = _plugin(attention)
    plugin._qq_settings = {
        "backlog_labels": [{
            "id": "issue", "label": "Issue",
            "keywords": ["error"], "priority": 100,
        }],
    }

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1", message_text="has error"))

    assert decision.action == "reply"
    assert decision.reason == "keyword:issue"
    assert decision.force_reply is True


def test_warm_group_reply_to_bot_force_replies():
    """本群在聊时引用她 → 强制回复。"""
    attention = _FakeAttention(scores={"g1": 5.0})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(
        plugin,
        group_id="g1",
        quoted_message_id="m1",
        is_reply_to_bot=True,
    ))

    assert decision.action == "reply"
    assert decision.reason == "reply_to_bot"
    assert decision.force_reply is True


def test_warm_group_keyword_force_replies():
    """本群在聊时命中关键词 → 强制回复。"""
    attention = _FakeAttention(scores={"g1": 5.0})
    plugin = _plugin(attention)
    plugin._qq_settings = {
        "backlog_labels": [{
            "id": "issue", "label": "Issue",
            "keywords": ["error"], "priority": 100,
        }],
    }

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1", message_text="has error"))

    assert decision.action == "reply"
    assert decision.reason == "keyword:issue"
    assert decision.force_reply is True


# ── @ 她：唯一的旁路 ───────────────────────────────────────────────


def test_at_bot_bypasses_the_attention_floor():
    """纯粹的 @她（非引用）在任何群都必回（唯一旁路）。"""
    attention = _FakeAttention(scores={"g1": 0.0})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1", is_at_bot=True))

    assert decision.action == "reply"
    assert decision.force_reply is True
    assert "mark_focus:g1" in attention.calls
    assert "wake_boost:g1" in attention.calls


def test_at_and_reply_combined_is_treated_as_a_reply():
    """同时带 @ 与引用时按**引用**处理（用户确认），于是走强制回复那条路。"""
    attention = _FakeAttention(scores={"g1": 0.0})
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(
        plugin,
        group_id="g1",
        is_at_bot=True,
        is_reply_to_bot=True,
    ))

    assert decision.action == "reply"
    assert decision.reason == "reply_to_bot"
    assert decision.force_reply is True


def test_attention_disabled_blanks_plain_messages():
    """注意力整个关掉时这道闸不搭话（@ 那条路仍然必回，见上一条）。"""
    attention = _FakeAttention(scores={"g1": 0.0}, enabled=False)
    plugin = _plugin(attention)

    decision, _ = asyncio.run(_evaluate(plugin, group_id="g1"))

    assert decision.action == "ignore"
    assert decision.reason == "attention_disabled"
