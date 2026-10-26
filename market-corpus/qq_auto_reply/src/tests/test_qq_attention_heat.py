"""热度档（warm / cooling）：每个群按**自己的**静默时长涨落。

2026-09-29：删掉跨群取舍之后，rise/fall 相位机（蜜月 + 让位）整套退役，换成两个
热度档。判据**只有本群自己的时间戳**：

- ``warm``：``now - last_message_at < attention_heat_warm_gap_seconds`` → 增长
- ``cooling``：静默超过那个窗口 → 回落

（原本还有第三档 ``dormant``（休眠）：它随**主动破冰**一起删除 —— 休眠的唯一触发源
就是"她主动开口没人接"。见 `docs/SESSION-HANDOFF.md` §42。）

这些用例钉住"档位怎么定、每种档怎么动、以及旧存档还读不读得进来"。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply import settings_schema
from plugin.plugins.qq_auto_reply.attention_service import (
    QQAttentionService,
    QQGroupAttentionState,
)

SETTINGS = {
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_base_rise_rate": 0.05,
    "attention_message_boost": 0.15,
    "attention_heat_warm_gap_seconds": 120,
    "attention_fall_rate": 0.02,
    "attention_consume_ratio": 0.1,
    "attention_frequency_target_gap": 30.0,
    "attention_frequency_min_multiplier": 0.15,
    "attention_frequency_max_multiplier": 1.8,
    "attention_emotion_multipliers": {"calm": 0.0},
    "backlog_labels": [],
}


def _service(**overrides) -> QQAttentionService:
    plugin = SimpleNamespace(
        _qq_settings={**SETTINGS, **overrides},
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    svc._current_time = lambda: 1000
    return svc


# ── 档位怎么定 ──────────────────────────────────────────────────────


def test_a_group_that_just_spoke_is_warm():
    svc = _service()
    st = QQGroupAttentionState(group_id="g1", last_message_at=1000 - 10)
    assert svc._heat_tier(st, 1000) == "warm"


def test_a_group_quiet_longer_than_the_gap_is_cooling():
    svc = _service()
    st = QQGroupAttentionState(group_id="g1", last_message_at=1000 - 121)
    assert svc._heat_tier(st, 1000) == "cooling"


def test_the_gap_boundary_belongs_to_cooling():
    """恰好静默满窗口就算凉（判据是 `now - last < gap` 才算热）。

    边界写死在一处、由这条钉住：含糊的话，真机行为会随 tick 相位抖动。
    """
    svc = _service()
    st = QQGroupAttentionState(group_id="g1", last_message_at=1000 - 120)
    assert svc._heat_tier(st, 1000) == "cooling"
    st.last_message_at = 1000 - 119
    assert svc._heat_tier(st, 1000) == "warm"


def test_a_group_that_never_spoke_is_cooling():
    svc = _service()
    st = QQGroupAttentionState(group_id="g1", last_message_at=0)
    assert svc._heat_tier(st, 1000) == "cooling"


def test_zero_gap_means_never_cools():
    """`attention_heat_warm_gap_seconds=0` = 永不算凉（显式取值，不用 `or` 兜底）。"""
    svc = _service(attention_heat_warm_gap_seconds=0)
    st = QQGroupAttentionState(group_id="g1", last_message_at=1)
    assert svc._heat_warm_gap_seconds() == 0
    assert svc._heat_tier(st, 10_000_000) == "warm"


def test_an_old_archive_cannot_freeze_a_group_forever():
    """旧存档里的休眠标记在**启动时**被清掉，不会把群冻住。

    `from_dict` 只读 `dormant` 这一个布尔量（`_load_state` 每次都经它重建），所以
    清理放在 `load_cached_state` —— 那是插件启动时的一次性迁移。这里直接验清完之后不冻。
    """
    svc = _service()
    st = QQGroupAttentionState(
        group_id="g1", attention_score=5.0, last_message_at=1000 - 600, dormant=True,
    )
    st.last_decay_at = 1000

    # 启动迁移做的事（见 load_cached_state）
    st.dormant = False

    after = svc._apply_decay(st, 1010)
    assert after.heat == "cooling", "旧存档把群冻住了"
    assert after.attention_score < 5.0, "冷群该往下掉"


# ── 每种档怎么动 ────────────────────────────────────────────────────


def test_warm_grows_and_is_capped_by_max_score():
    svc = _service()
    # 推进区间里一直有人说话（last_message_at 落在窗口内）
    st = QQGroupAttentionState(group_id="g1", attention_score=9.99, last_message_at=1590)
    st.last_decay_at = 1000
    after = svc._apply_decay(st, 1600)      # 热聊档：涨
    assert after.heat == "warm"
    assert after.attention_score == pytest.approx(svc._max_attention())


def test_cooling_decays_at_the_fall_rate():
    svc = _service()
    st = QQGroupAttentionState(group_id="g1", attention_score=5.0, last_message_at=1000 - 600)
    st.last_decay_at = 1000
    after = svc._apply_decay(st, 1010)
    assert after.heat == "cooling"
    assert after.attention_score == pytest.approx(5.0 - svc._fall_rate() * 10)


def test_cooling_never_goes_below_zero():
    svc = _service()
    st = QQGroupAttentionState(group_id="g1", attention_score=0.1, last_message_at=1000 - 600)
    st.last_decay_at = 1000
    after = svc._apply_decay(st, 100_000)
    assert after.attention_score == 0.0


def test_a_positive_emotion_slows_the_cooling():
    """正向情绪跌得慢（`1 - emo`），负向情绪跌得快 —— 与相位机的口径一致。"""
    svc = _service(attention_emotion_multipliers={"calm": 0.0, "playful": 0.5, "annoyed": -0.5})
    scores = {}
    for emo in ("calm", "playful", "annoyed"):
        st = QQGroupAttentionState(
            group_id="g1", attention_score=5.0, last_message_at=1000 - 600, emotion=emo,
        )
        st.last_decay_at = 1000
        scores[emo] = svc._apply_decay(st, 1010).attention_score
    assert scores["playful"] > scores["calm"] > scores["annoyed"]


# ── 写入路径上标签必须自洽 ──────────────────────────────────────────


def test_the_label_is_refreshed_on_every_write():
    """`_normalize_state` 重算标签：否则"她刚回了话"这种不碰时间戳的写入会留下过期档位。"""
    svc = _service()
    st = QQGroupAttentionState(group_id="g1", attention_score=3.0, last_message_at=1000 - 600)
    svc._write_state(st)

    svc._current_time = lambda: 1000
    asyncio.run(svc.update_on_reply("g1"))

    assert svc._load_state("g1").heat == "cooling"


def test_advancing_with_an_explicit_now_does_not_get_overwritten_by_the_clock():
    """`_apply_decay(state, now)` 的档位必须按传进来的 `now` 算，不能被服务时钟覆盖。"""
    svc = _service()
    svc._current_time = lambda: 0            # 一个"看起来很早"的服务时钟
    st = QQGroupAttentionState(group_id="g1", attention_score=5.0, last_message_at=1000 - 600)
    st.last_decay_at = 1000
    after = svc._apply_decay(st, 1010)
    assert after.heat == "cooling"


# ── 存档兼容 ────────────────────────────────────────────────────────


def test_heat_round_trips_through_the_archive():
    st = QQGroupAttentionState(group_id="g1", attention_score=3.5, heat="cooling")
    restored = QQGroupAttentionState.from_dict(st.to_dict(), group_id="g1")
    assert restored.heat == "cooling"
    assert restored.attention_score == pytest.approx(3.5)


def test_a_phase_era_archive_still_loads():
    """相位时代的存档（`phase` / `phase_started_at` / `steady_since`）必须还能读。

    分数保留；热度不读旧值 —— 它由 `_advance_heat` 按本群时间戳重算。
    """
    legacy = {
        "group_id": "g1",
        "attention_score": 6.0,
        "phase": "fall",
        "phase_started_at": 900,
        "steady_since": 800,
        "last_message_at": 1000 - 600,
        "last_decay_at": 1000,
    }
    st = QQGroupAttentionState.from_dict(legacy, group_id="g1")
    assert st.attention_score == pytest.approx(6.0)
    assert st.heat == "warm"          # 默认值，随后由推进重算

    svc = _service()
    after = svc._apply_decay(st, 1010)
    assert after.heat == "cooling", "旧存档里的 fall 不该被当成新模型的冷却档"


# ── 设置项：旧键退役、新键在位 ──────────────────────────────────────


def test_the_retired_phase_keys_are_zombies():
    from plugin.plugins.qq_auto_reply.config_store import QQAutoReplyConfigStore

    for key in (
        "attention_honeymoon_seconds",
        "attention_fall_seconds",
        "attention_fall_boost_attenuation",
    ):
        assert key not in settings_schema.BY_KEY, f"{key} 还留在真源表里"
        assert key not in settings_schema.defaults(), f"{key} 还写进默认值里"
        assert key in QQAutoReplyConfigStore._LEGACY_ZOMBIE_KEYS, (
            f"{key} 不在僵尸键名单里 —— 老配置里的残留值会被原样传下去"
        )
    # 新模型的键在位（删旧的不是把功能删掉）
    assert settings_schema.BY_KEY["attention_heat_warm_gap_seconds"].default == 120
    assert settings_schema.BY_KEY["attention_fall_rate"].default == pytest.approx(0.015)


def test_the_prompt_talks_about_heat_not_phases():
    """注入提示词的那段话必须说"热度"，且不许再出现"相位"。"""
    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

    plugin = SimpleNamespace(
        _qq_settings=dict(SETTINGS),
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
    )
    svc = QQAttentionService(plugin)
    svc._current_time = lambda: 1000
    svc._cache = {
        "g1": QQGroupAttentionState(
            group_id="g1", attention_score=3.5, last_message_at=990,
        ).to_dict(),
    }

    text = svc.get_attention_context("g1")

    assert "相位" not in text, f"提示词还在讲相位：{text!r}"
    assert "热聊中" in text, f"没报出这个群自己的热度档：{text!r}"


def test_the_snapshot_exposes_heat_for_the_ui():
    """面板要能显示"热度"而不是"相位"。"""
    from plugin.plugins.qq_auto_reply.attention_service import QQAttentionService

    plugin = SimpleNamespace(
        _qq_settings=dict(SETTINGS),
        backlog_store=None,
        group_permission_mgr=SimpleNamespace(list_groups=lambda: [{"group_id": "g1"}]),
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
    )
    svc = QQAttentionService(plugin)
    svc._current_time = lambda: 1000
    svc._cache = {
        "g1": QQGroupAttentionState(
            group_id="g1", attention_score=3.5, last_message_at=990,
        ).to_dict(),
    }

    snapshot = svc.get_snapshot()
    group = next(g for g in snapshot["groups"] if g["group_id"] == "g1")
    assert group["heat"] == "warm"
    assert group["dominant_dimension"] if "dominant_dimension" in group else True
