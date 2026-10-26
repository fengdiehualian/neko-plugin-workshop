# -*- coding: utf-8 -*-
"""接话反馈闭环（ROUND1 方案 B5）看门狗。

闭环要回答的问题是使用者那句「她插了话没人理，还在那儿待着」的反面：
**她说完之后群里到底有没有人接？** 三档结论各自有后果：

- ``silent``（窗口内 0 条回应）→ 扣注意力（回落加速，别赖在这个群）；
- ``quiet``（1~2 条）→ 不动分数（有人在应，但看不出热度）；
- ``warm``（≥ 阈值条）→ 加注意力（话题在群里是活的，值得多待）。

这份测试刻意分两层：

1. **单元层**（``_settle_feedback`` 直调）断言精确增减量；
2. **时序层**（``decay_all``）断言"由时间触发"这条设计不变量 —— 一个**彻底没人说话**
   的群永远不会有新消息，若结算只挂在消息路径上，「没人接」这个最该被发现的场景
   反而永远结算不了。时序层用**两个群互为对照**来断言，避免把时间衰减也算进去。
"""
from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.attention_service import (
    QQAttentionService,
    QQGroupAttentionState,
)

#: 与线上一致的一组注意力参数（沿用 test_qq_attention_behavior.LIVE 的口径）。
BASE_SETTINGS = {
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_base_rise_rate": 0.08,
    "attention_message_boost": 0.15,
    "attention_keyword_boost_ratio": 1.8,
    "attention_honeymoon_seconds": 60,
    "attention_fall_seconds": 30,
    "attention_fall_rate": 0.015,
    "attention_consume_ratio": 0.1,
    "attention_at_bot_boost": 3.0,
    "attention_question_boost": 1.5,
    "attention_wake_boost_ratio": 0.75,
    "attention_decay_interval_seconds": 5.0,
    "attention_emotion_multipliers": {"calm": 0.0},
    "backlog_labels": [],
    # 反馈闭环的出厂值（也从 settings_schema 读得到同一组数）
    "attention_feedback_enabled": True,
    "attention_feedback_window_seconds": 90.0,
    "attention_feedback_silent_penalty": 0.4,
    "attention_feedback_warm_bonus": 0.4,
    "attention_feedback_warm_count": 3,
}

GROUP_A = "985066274"
GROUP_B = "1048307485"


class _Clock:
    def __init__(self, start: int = 100_000) -> None:
        self.now = start

    def __call__(self) -> int:
        return self.now


def _service(groups=(GROUP_A, GROUP_B), **overrides):
    settings = dict(BASE_SETTINGS)
    settings.update(overrides)
    plugin = SimpleNamespace(
        group_permission_mgr=SimpleNamespace(list_groups=lambda: [{"group_id": g} for g in groups]),
        _qq_settings=settings,
        backlog_store=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    clock = _Clock()
    svc._current_time = clock
    return svc, clock


def _seed(svc, gid, score):
    st = svc._load_state(gid)
    st.attention_score = score
    svc._write_state(st)


def _score(svc, gid) -> float:
    return float(svc._load_state(gid).attention_score)


def _reply(svc, gid):
    asyncio.run(svc.update_on_reply(gid))


def _message(svc, gid, clock, *, sender="u1", text="接一句"):
    asyncio.run(svc.update_on_message({
        "group_id": gid, "user_id": sender, "content": text,
        "timestamp": clock.now, "is_at_bot": False,
    }))


def _tick(svc):
    asyncio.run(svc.decay_all())


# ── 1. 周期开始：她发言后计数清零、且这一轮还没结算 ──────────────────────

def test_reply_opens_a_new_feedback_cycle():
    svc, clock = _service()
    _seed(svc, GROUP_A, 5.0)
    # 上一轮残留的计数（3 条）不该带进新一轮
    st = svc._load_state(GROUP_A)
    st.msgs_after_reply = 3
    st.feedback_settled_at = clock.now - 500
    svc._write_state(st)

    _reply(svc, GROUP_A)
    st = svc._load_state(GROUP_A)

    assert st.msgs_after_reply == 0, "她发言之后，上一轮的回应计数必须清零"
    assert st.feedback_settled_at < st.last_reply_at, "新一轮必须是「未结算」状态"


# ── 2. 计数：别人说的每一条都算回应 ────────────────────────────────

def test_others_messages_after_reply_are_counted():
    svc, clock = _service()
    _reply(svc, GROUP_A)
    for _ in range(4):
        clock.now += 10
        _message(svc, GROUP_A, clock)
    assert svc._load_state(GROUP_A).msgs_after_reply == 4


def test_another_groups_messages_do_not_leak():
    """B 群的消息不能算进 A 群的「她说完之后」。"""
    svc, clock = _service()
    _reply(svc, GROUP_A)
    clock.now += 10
    _message(svc, GROUP_B, clock)
    assert svc._load_state(GROUP_A).msgs_after_reply == 0
    assert svc._load_state(GROUP_B).msgs_after_reply == 1


# ── 3. 三档结论的精确增减量（单元层）────────────────────────────────

def _state_for_settlement(score=5.0, *, replied_at, count):
    st = QQGroupAttentionState(group_id=GROUP_A)
    st.attention_score = score
    st.last_reply_at = replied_at
    st.msgs_after_reply = count
    return st


def test_silent_settlement_subtracts_the_penalty():
    svc, _ = _service()
    st = _state_for_settlement(replied_at=1000, count=0)
    tier = svc._settle_feedback(st, 1000 + 90)

    assert tier == "silent"
    assert st.attention_score == 5.0 - 0.4
    assert st.feedback_tier == "silent" and st.feedback_msgs == 0
    assert st.last_focus_reason == "feedback:silent"


def test_warm_settlement_adds_the_bonus():
    svc, _ = _service()
    st = _state_for_settlement(replied_at=1000, count=3)
    tier = svc._settle_feedback(st, 1000 + 90)

    assert tier == "warm"
    assert st.attention_score == 5.0 + 0.4
    assert st.feedback_msgs == 3


def test_quiet_settlement_does_not_move_the_score():
    svc, _ = _service()
    for count in (1, 2):
        st = _state_for_settlement(replied_at=1000, count=count)
        tier = svc._settle_feedback(st, 1000 + 90)
        assert tier == "quiet"
        assert st.attention_score == 5.0, f"{count} 条回应不该改变分数"
        assert st.last_focus_reason != "feedback:quiet", "quiet 不该覆写焦点原因"


def test_warm_threshold_comes_from_settings():
    svc, _ = _service(attention_feedback_warm_count=5)
    st = _state_for_settlement(replied_at=1000, count=4)
    assert svc._settle_feedback(st, 1000 + 90) == "quiet"
    st2 = _state_for_settlement(replied_at=1000, count=5)
    assert svc._settle_feedback(st2, 1000 + 90) == "warm"


def test_score_never_goes_below_zero():
    svc, _ = _service()
    st = _state_for_settlement(score=0.2, replied_at=1000, count=0)
    svc._settle_feedback(st, 1000 + 90)
    assert st.attention_score == 0.0


# ── 4. 时间窗口：不到点不下结论 ───────────────────────────────────

def test_not_settled_before_the_window():
    svc, _ = _service()
    st = _state_for_settlement(replied_at=1000, count=0)
    assert svc._settle_feedback(st, 1000 + 89) == ""
    assert st.attention_score == 5.0, "窗口没到就扣分 = 把「还没打完字」当成没人理"
    assert st.feedback_settled_at == 0 and st.feedback_tier == ""


def test_settlement_is_idempotent():
    """同一轮结算两次不该扣两次 —— decay 循环与消息路径都会调它。"""
    svc, _ = _service()
    st = _state_for_settlement(replied_at=1000, count=0)
    assert svc._settle_feedback(st, 1090) == "silent"
    after_first = st.attention_score
    assert svc._settle_feedback(st, 1100) == ""
    assert st.attention_score == after_first


def test_new_reply_reopens_settlement():
    svc, _ = _service()
    st = _state_for_settlement(replied_at=1000, count=0)
    assert svc._settle_feedback(st, 1090) == "silent"
    st.last_reply_at = 1200          # 她又说了一句
    st.msgs_after_reply = 3
    assert svc._settle_feedback(st, 1300) == "warm"


def test_no_reply_yet_means_nothing_to_settle():
    svc, _ = _service()
    st = QQGroupAttentionState(group_id=GROUP_A)
    st.attention_score = 5.0
    st.msgs_after_reply = 9          # 她从没说过话，这些不是「对她的回应」
    assert svc._settle_feedback(st, 10_000) == ""
    assert st.attention_score == 5.0


# ── 5. 时序层：由时间触发，且两个群互为对照 ────────────────────────

def test_decay_loop_settles_a_silent_group_without_any_new_message():
    """**最要紧的一条**：彻底没人说话的群永远不会有新消息。

    结算若只挂在消息路径上，这个最该被发现的场景反而永远结算不了。
    """
    svc, clock = _service()
    _seed(svc, GROUP_A, 5.0)
    _reply(svc, GROUP_A)

    clock.now += 120                  # 期间一条消息都没有
    _tick(svc)

    st = svc._load_state(GROUP_A)
    assert st.feedback_tier == "silent"
    assert st.feedback_msgs == 0


def test_silent_and_warm_groups_diverge_by_penalty_plus_bonus():
    """对照实验：两个群同分起步、同一时刻结算，差值只能是反馈量本身。"""
    svc, clock = _service()
    _seed(svc, GROUP_A, 5.0)
    _seed(svc, GROUP_B, 5.0)
    _reply(svc, GROUP_A)
    _reply(svc, GROUP_B)

    for _ in range(3):                # 只有 B 群有人接
        clock.now += 10
        _message(svc, GROUP_B, clock)

    clock.now += 120
    _tick(svc)

    a, b = svc._load_state(GROUP_A), svc._load_state(GROUP_B)
    assert (a.feedback_tier, b.feedback_tier) == ("silent", "warm")
    assert b.attention_score - a.attention_score > 0.7, (
        f"没人接的群必须落后于接起来的群：A={a.attention_score} B={b.attention_score}"
    )


def test_settlement_fires_once_per_cycle_through_the_decay_loop():
    svc, clock = _service()
    _seed(svc, GROUP_A, 5.0)
    _reply(svc, GROUP_A)

    clock.now += 120
    _tick(svc)
    first = svc._load_state(GROUP_A)
    assert first.feedback_tier == "silent"

    clock.now += 120                  # 又过两分钟，但这一轮早已结算
    _tick(svc)
    second = svc._load_state(GROUP_A)
    assert second.feedback_tier == "silent"
    assert second.feedback_settled_at == first.feedback_settled_at, "一轮只结算一次"


# ── 6. 开关与持久化 ──────────────────────────────────────────────

def test_disabled_kill_switch_restores_old_behaviour():
    svc, clock = _service(attention_feedback_enabled=False)
    st = _state_for_settlement(replied_at=1000, count=0)
    assert svc._settle_feedback(st, 10_000) == ""
    assert st.attention_score == 5.0
    assert svc.feedback_line(GROUP_A) == ""

    _seed(svc, GROUP_A, 5.0)
    _reply(svc, GROUP_A)
    clock.now += 300
    _tick(svc)
    assert svc._load_state(GROUP_A).feedback_tier == ""


def test_feedback_fields_survive_a_save_load_round_trip():
    st = QQGroupAttentionState(group_id=GROUP_A)
    st.msgs_after_reply = 4
    st.feedback_settled_at = 1234
    st.feedback_tier = "warm"
    st.feedback_msgs = 3
    back = QQGroupAttentionState.from_dict(st.to_dict(), group_id=GROUP_A)
    assert (back.msgs_after_reply, back.feedback_settled_at, back.feedback_tier, back.feedback_msgs) == (
        4, 1234, "warm", 3,
    )


def test_legacy_save_without_feedback_fields_is_neutral():
    """老存档没有这几个键 → 当作「她还没发过言」，不能凭空扣分或加分。"""
    legacy = {"group_id": GROUP_A, "attention_score": 6.0, "phase": "rise"}
    st = QQGroupAttentionState.from_dict(legacy, group_id=GROUP_A)
    assert (st.msgs_after_reply, st.feedback_settled_at, st.feedback_tier, st.feedback_msgs) == (0, 0, "", 0)
    assert st.attention_score == 6.0


# ── 7. 提示词那半边：让她知道刚才那句有没有被接住 ────────────────────

def test_feedback_line_covers_all_four_states():
    svc, clock = _service()
    assert svc.feedback_line(GROUP_A) == "", "还没发过言 → 不注入任何东西"

    _reply(svc, GROUP_A)
    assert "还没有人回应" in svc.feedback_line(GROUP_A, now=clock.now + 5)

    clock.now += 10
    _message(svc, GROUP_A, clock)
    assert "有人接着说" in svc.feedback_line(GROUP_A)

    for _ in range(2):
        clock.now += 10
        _message(svc, GROUP_A, clock)
    assert "接着聊了 3 条" in svc.feedback_line(GROUP_A)

    svc2, clock2 = _service()
    _reply(svc2, GROUP_A)
    clock2.now += 120
    assert "一直没人接话" in svc2.feedback_line(GROUP_A)


def test_attention_context_carries_the_feedback_line():
    """注入路径必须是**真的**接上了（get_attention_context 是提示词的入口）。"""
    svc, clock = _service()
    _seed(svc, GROUP_A, 5.0)
    _reply(svc, GROUP_A)
    clock.now += 120

    context = svc.get_attention_context(GROUP_A)
    assert "一直没人接话" in context


def test_feedback_settings_are_declared_in_the_schema():
    """配置键必须在单一真相表里，否则「面板能存、代码读不到」那类静默失效会重演。"""
    from plugin.plugins.qq_auto_reply import settings_schema

    root = pathlib.Path(__file__).resolve().parents[1]
    schema_text = (root / "settings_schema.py").read_text(encoding="utf-8")
    service_text = (root / "attention_service.py").read_text(encoding="utf-8")
    defaults = settings_schema.defaults()

    for key in (
        "attention_feedback_enabled",
        "attention_feedback_window_seconds",
        "attention_feedback_silent_penalty",
        "attention_feedback_warm_bonus",
        "attention_feedback_warm_count",
    ):
        assert key in defaults, f"{key} 没进 settings_schema.defaults()"
        assert f'"{key}"' in schema_text, f"{key} 不在 settings_schema 里"
        assert f'"{key}"' in service_text, f"{key} 没有被 attention_service 真的读"
