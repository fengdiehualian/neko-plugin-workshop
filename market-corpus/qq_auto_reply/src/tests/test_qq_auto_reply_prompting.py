import pytest
from plugin.plugins.qq_auto_reply.prompting import QQAutoReplyPromptingMixin


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("普通回复", "普通回复"),
        (
            "<think_never_used_51bce0c785ca2f68081bfa7d91973934></think_never_used_51bce0c785ca2f68081bfa7d91973934>我明白啦",
            "我明白啦",
        ),
        (
            "先想想\n</think_never_used_abc123>\n最终回复",
            "最终回复",
        ),
        (
            "<think>内部推理</think>对外回复",
            "对外回复",
        ),
        (
            "<thinking_trace_variant>分析</thinking_trace_variant>结论",
            "结论",
        ),
        (
            "对外回复</think_never_used_trailing>",
            "对外回复",
        ),
    ],
)
def test_sanitize_generated_reply_strips_thinking_variants(raw, expected):
    assert QQAutoReplyPromptingMixin._sanitize_generated_reply(raw) == expected


def _prompt_builder(settings):
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.prompt_builder import QQPromptBuilder

    return QQPromptBuilder(SimpleNamespace(_qq_settings=settings, i18n=None))


def test_group_memory_default_follows_configured_policy():
    """Group requests built without explicit memory flags (retroactive
    review, rapid-fire flush) must inherit the configured group-memory
    policy instead of silently resolving to False — which also flipped the
    shared session's memory_enabled off and blocked idle finalization."""
    on = _prompt_builder({"group_memory_enabled": True})
    off = _prompt_builder({"group_memory_enabled": False})

    assert on.should_use_memory_context(
        is_group=True, permission_level="user", requested=None,
    ) is True
    assert off.should_use_memory_context(
        is_group=True, permission_level="user", requested=None,
    ) is False
    # Explicit values always win over the configured default.
    assert on.should_use_memory_context(
        is_group=True, permission_level="user", requested=False,
    ) is False
    assert off.should_use_memory_context(
        is_group=True, permission_level="user", requested=True,
    ) is True
    # Private-chat defaults are unchanged: admin-only.
    assert on.should_use_memory_context(
        is_group=False, permission_level="admin", requested=None,
    ) is True
    assert on.should_use_memory_context(
        is_group=False, permission_level="user", requested=None,
    ) is False
    # Upgraded configs may lack the key entirely, and _qq_settings itself
    # may be None: both must default safely to off.
    assert _prompt_builder({}).should_use_memory_context(
        is_group=True, permission_level="user", requested=None,
    ) is False
    assert _prompt_builder(None).should_use_memory_context(
        is_group=True, permission_level="user", requested=None,
    ) is False


def test_group_persist_policy_decoupled_from_turn_recall():
    """Group persistence follows the configured policy when unspecified,
    independent of per-turn recall: a proactive turn that explicitly
    disables recall (use=False) must not flip the shared session's
    memory_enabled off and strand buffered opt-in history."""
    on = _prompt_builder({"group_memory_enabled": True})
    off = _prompt_builder({"group_memory_enabled": False})

    assert on.should_persist_memory(
        should_use_memory_context=False, requested=None, is_group=True,
    ) is True
    assert off.should_persist_memory(
        should_use_memory_context=True, requested=None, is_group=True,
    ) is False
    # Explicit values still win.
    assert on.should_persist_memory(
        should_use_memory_context=False, requested=False, is_group=True,
    ) is False
    # Private default unchanged: follows the turn's recall decision.
    assert on.should_persist_memory(
        should_use_memory_context=True, requested=None,
    ) is True
    assert on.should_persist_memory(
        should_use_memory_context=False, requested=None,
    ) is False


def test_open_platform_group_mentions_distinguish_bot_from_other_users():
    from plugin.plugins.qq_auto_reply.connector_seam import QQOpenPlatformConnection

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = "10000"

    bot_only = conn._convert_event(
        "GROUP_AT_MESSAGE_CREATE",
        {
            "id": "m1",
            "group_id": "g1",
            "author": {"id": "u1", "username": "Alice"},
            "content": "<@!10000> hello",
        },
    )
    assert bot_only["mentioned_user_ids"] == ["10000"]
    assert bot_only["mentions_other_user"] is False

    with_other_user = conn._convert_event(
        "GROUP_AT_MESSAGE_CREATE",
        {
            "id": "m2",
            "group_id": "g1",
            "author": {"id": "u1", "username": "Alice"},
            "content": "<@!10000> <@!20000> hello",
        },
    )
    assert with_other_user["mentioned_user_ids"] == ["10000", "20000"]
    assert with_other_user["mentions_other_user"] is True


def test_focus_score_tracks_raw_attention_scalar():
    """Under the periodic model attention is a scalar: get_focus_score returns the raw score with
    no extra bonus; phase advance is idempotent on last_decay_at and leaves attention unchanged when unset."""
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService, QQGroupAttentionState

    plugin = SimpleNamespace(
        _qq_settings={
            "enable_group_attention": True,
            "attention_focus_threshold": 4,
            "attention_min_threshold": 1,
        },
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *args, **kwargs: None,
    )
    service = QQAttentionService(plugin)
    service._current_time = lambda: 100
    service._cache = {
        "focus": QQGroupAttentionState(
            group_id="focus",
            attention_score=8.0,
            focus_acquired_at=95,
        ).to_dict(),
        "other": QQGroupAttentionState(
            group_id="other",
            attention_score=6.5,
        ).to_dict(),
    }

    assert service.get_focus_score() == pytest.approx(8.0, rel=1e-3)
    assert service.get_snapshot()["focus_group_id"] == "focus"

    # 时间推进但 last_decay_at 未置 → 幂等推进，注意力保持原始分。
    service._current_time = lambda: 106
    assert service.get_focus_score() == pytest.approx(8.0, rel=1e-3)
    assert service.get_snapshot()["focus_group_id"] == "focus"


def test_attention_context_speaks_about_this_group_only():
    """注入提示词的注意力上下文只说**这个群自己**的状态。

    2026-09-29 跨群取舍删除前，这句话是"这是你当前关注的焦点群 / 这不是你当前关注的群"
    —— 而门控那时真的按它决定搭不搭话。取舍删掉后每个群各自判定，还留着那句"你不是
    焦点"只会让模型以为自己不该开口（它其实已经被放行到 LLM 了）。
    """
    from types import SimpleNamespace as _NS

    from plugin.plugins.qq_auto_reply.attention_service import (
        QQAttentionService,
        QQGroupAttentionState,
    )

    plugin = _NS(
        _qq_settings={"attention_focus_threshold": 4, "attention_min_threshold": 1},
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *args, **kwargs: None,
    )
    service = QQAttentionService(plugin)
    service._current_time = lambda: 100
    service._cache = {
        "focus": QQGroupAttentionState(group_id="focus", attention_score=8.0).to_dict(),
        "other": QQGroupAttentionState(group_id="other", attention_score=6.5).to_dict(),
    }

    hottest = service.get_attention_context("focus")
    colder = service.get_attention_context("other")

    for text, score in ((hottest, "8.0"), (colder, "6.5")):
        assert "焦点" not in text, f"提示词还在讲「焦点群」这套跨群话术：{text!r}"
        assert score in text, f"没报出这个群自己的分数：{text!r}"
    assert hottest != colder, "两个群拿到的上下文一模一样 —— 说明报的不是本群的状态"


def test_rise_phase_does_not_clamp_above_focus_line():
    """The rise phase must not clamp scores above the focus line.

    Old logic `min(focus_line, score + rate*dt)` shaved an @bot-won 8.0 back to
    the focus line (4.0); decay_all runs every 5s, so high attention evaporated
    almost instantly. Now rise grows only below the line, keeps higher scores
    unchanged, and records focus_acquired_at.
    """
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService, QQGroupAttentionState

    plugin = SimpleNamespace(
        _qq_settings={
            "enable_group_attention": True,
            "attention_base_rise_rate": 0.02,
            "attention_heat_warm_gap_seconds": 120,
            "attention_fall_rate": 0.015,
            "attention_max_score": 10.0,
            "attention_focus_threshold": 4.0,
            "attention_min_threshold": 1.0,
        },
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
    )
    service = QQAttentionService(plugin)
    now = [100]
    service._current_time = lambda: now[0]

    state = QQGroupAttentionState(group_id="g1", attention_score=8.0)
    state.last_decay_at = 90  # 10 秒前，保证 dt=10 正常推进
    state.last_message_at = 90  # 刚刚还有人说话 → 热聊档
    service._write_state(state)

    # 推进 10 秒：热聊档不应把 8.0 砍回档位线，且现在可以继续往上长
    # （自然增长上限是 max_score，见 _advance_heat 的注释）。
    #
    # 期望值只断言**方向与上限**，不钉具体数值：频率缩放的 dt 组合容易手算错，
    # 而本测试要钉的行为是「高于档位线时不回砍、且还能继续涨」。
    now[0] += 10
    after = service._apply_decay(service._load_state("g1"), now[0])

    assert after.attention_score >= 8.0, "热聊档绝不能把高分砍回档位线"
    assert after.attention_score > 8.0, (
        f"热聊档应当继续增长（上限是 max_score={service._max_attention()}），"
        f"实际停在 {after.attention_score:.2f} —— 档位线封顶会让到线后零余量"
    )
    assert after.attention_score <= service._max_attention()
    assert after.focus_acquired_at == 110  # 本来就高于线，夺冠计时已记录
    assert after.heat == "warm"


def test_zero_attention_settings_are_honored():
    """Saved 0 values must be honored, not fall back to defaults via or-default.

    Old ``get(key, d) or d`` treated 0 as falsy: attention_consume_ratio=0
    (disable reply consumption) read back 0.10, attention_fall_rate=0 read back 0.015.
    """
    from types import SimpleNamespace

    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

    plugin = SimpleNamespace(
        _qq_settings={
            "enable_group_attention": True,
            "attention_consume_ratio": 0,      # 禁用回复消耗
            "attention_fall_rate": 0,          # 禁用回落
            "attention_heat_warm_gap_seconds": 0,   # 永不算凉（一直 warm）
            "attention_base_rise_rate": 0,     # 禁用时间自然上升
        },
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
    )
    service = QQAttentionService(plugin)

    assert service._consume_ratio() == 0.0
    assert service._fall_rate() == 0.0
    assert service._heat_warm_gap_seconds() == 0
    assert service._rise_rate() == 0.0
