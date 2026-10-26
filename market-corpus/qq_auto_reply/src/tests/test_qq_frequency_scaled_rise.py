"""自然增速按本群发言频率缩放：热群涨得快，冷群涨得慢。

``attention_base_rise_rate`` 原本是个对所有群一视同仁的常数，"活跃度"只体现在
每条消息那一次 ``attention_message_boost``（0.15）上。结果是：一个安静但持续有人
偶尔说话的群，和一个每分钟十条的群，自然增长速率完全相同。

现在 rise 相位的速率乘上 ``目标间隔 / 距上一条消息的间隔``（钳到 [min, max]）。
判据复用既有的 ``last_message_at``，不引入滑动窗口或计数器。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.attention_service import (
    QQAttentionService,
    QQGroupAttentionState,
)

BASE_CFG = {
    "attention_max_score": 10.0,
    "attention_focus_threshold": 4.0,
    "attention_focus_hold_threshold": 2.0,
    "attention_min_threshold": 1.0,
    "attention_base_rise_rate": 0.08,
    "attention_message_boost": 0.15,
    "attention_keyword_boost_ratio": 1.8,
    "attention_heat_warm_gap_seconds": 120,
    "attention_fall_rate": 0.015,
    "attention_consume_ratio": 0.1,
    "attention_at_bot_boost": 3.0,
    "attention_question_boost": 1.5,
    "attention_wake_boost_ratio": 0.75,
    "attention_decay_interval_seconds": 5.0,
    "attention_frequency_target_gap": 30.0,
    "attention_frequency_min_multiplier": 0.15,
    # 刻意用 5.0 而不是出厂默认值：本文件测的是**机制**，不该因为出厂默认上限
    # 被调整（3.0 → 1.8）而整体变红。出厂默认值另有一条测试单独钉。
    "attention_frequency_max_multiplier": 5.0,
    "attention_emotion_multipliers": {"calm": 0.0},
    "backlog_labels": [],
}


def _service(**overrides) -> QQAttentionService:
    cfg = dict(BASE_CFG)
    cfg.update(overrides)
    plugin = SimpleNamespace(
        _qq_settings=cfg,
        backlog_store=None,
        group_permission_mgr=None,
        permission_mgr=None,
        _emit_log=lambda *a, **k: None,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )
    svc = QQAttentionService(plugin)
    svc._current_time = lambda: 0
    return svc


def _state(last_message_at: int) -> QQGroupAttentionState:
    st = QQGroupAttentionState(group_id="g1", attention_score=0.0)
    st.last_message_at = last_message_at
    return st


def test_hot_group_rises_faster_than_cold_group():
    """核心不变量：间隔越短，倍率越高。"""
    svc = _service()
    hot = svc._frequency_scale(_state(1000 - 10), 1000)    # 10 秒一条
    warm = svc._frequency_scale(_state(1000 - 30), 1000)   # 30 秒一条（=目标）
    cold = svc._frequency_scale(_state(1000 - 300), 1000)  # 5 分钟一条
    assert hot > warm > cold, f"热={hot} 温={warm} 冷={cold}"


def test_target_gap_gives_exactly_one():
    """间隔恰好等于目标间隔时必须是 1.0×，否则"目标"这个词就没意义了。"""
    svc = _service()
    assert svc._frequency_scale(_state(1000 - 30), 1000) == pytest.approx(1.0)
    assert svc._frequency_scale(_state(1000 - 60), 1000) == pytest.approx(0.5)


def test_multipliers_are_clamped():
    """两个方向都必须封顶，否则刷屏群会瞬间打满、冷群会彻底冻结。"""
    svc = _service()   # 本文件上限 = 5.0
    # 5 秒一条 → 原始 30×，必须封到 max=5.0
    assert svc._frequency_scale(_state(1000 - 5), 1000) == pytest.approx(5.0)
    # 一小时一条 → 原始 1/120，必须抬到 min=0.15
    assert svc._frequency_scale(_state(1000 - 3600), 1000) == pytest.approx(0.15)


def test_min_multiplier_is_not_zero():
    """冷群下限不得为 0（否则那些群的自然增速彻底冻结）。"""
    svc = _service()
    assert svc._frequency_min_multiplier() > 0.0
    assert svc._frequency_scale(_state(1000 - 99999), 1000) > 0.0


def test_never_messaged_group_falls_back_to_the_floor():
    """从未发过言的群（last_message_at=0）按冷群处理，且不得抛异常/出 inf。"""
    svc = _service()
    scale = svc._frequency_scale(_state(0), 1000)
    assert scale == pytest.approx(0.15)


def test_same_second_burst_takes_the_ceiling():
    """同一秒内连发：间隔为 0，按最热处理（而不是除零）。"""
    svc = _service()
    assert svc._frequency_scale(_state(1000), 1000) == pytest.approx(5.0)


def test_frequency_scales_the_rate_not_the_message_boost():
    """端到端：同样的推进时长，间隔越短拿到的分数越高（**都在热聊窗口内**）。

    同时钉住"只影响热聊档的速率、不动 ``attention_message_boost``"这个边界。

    设定：``now = t + 10``（推进 dt=10 秒），``last_message_at = t - gap``，
    于是**发言间隔 = gap + 10**。断言里先把 ``_frequency_scale`` 的实算值也
    钉一遍，避免"间隔"和"dt"两个量再次互相污染（踩过一次，见下）。

    ⚠️ 三个 gap 都必须落在 ``attention_heat_warm_gap_seconds``（本文件 120s）以内：
    跨过那条线就是**冷却档**（分数往下掉），那时"频率缩放"根本轮不到上场 ——
    这条边界由 `test_a_group_quiet_longer_than_the_warm_gap_cools_down` 单独钉。
    """
    svc = _service()
    t = 100_000
    dt = 10

    def advance(gap_seconds: int) -> tuple[float, float]:
        st = _state(t - gap_seconds)
        st.last_decay_at = t
        svc._write_state(st)
        score = svc._apply_decay(svc._load_state("g1"), t + dt).attention_score
        # 用实算的倍率反推期望值，间隔由构造保证是 gap + dt
        scale = min(max(svc._frequency_target_gap() / (gap_seconds + dt),
                        svc._frequency_min_multiplier()),
                    svc._frequency_max_multiplier())
        return score, scale

    hot, hot_scale = advance(10)      # 间隔 20s → 1.5×
    warm, warm_scale = advance(30)    # 间隔 40s → 0.75×
    cold, cold_scale = advance(100)   # 间隔 110s → 0.27×（仍在热聊窗口内）

    assert hot_scale == pytest.approx(1.5)
    assert warm_scale == pytest.approx(0.75)
    assert cold_scale == pytest.approx(30 / 110)
    # 分数 = rise_rate(0.08) × scale × dt(10)
    assert hot == pytest.approx(0.08 * hot_scale * dt, rel=1e-2)
    assert warm == pytest.approx(0.08 * warm_scale * dt, rel=1e-2)
    assert cold == pytest.approx(0.08 * cold_scale * dt, rel=1e-2)
    assert hot > warm > cold


def test_a_group_quiet_longer_than_the_warm_gap_cools_down():
    """静默超过热聊窗口 → 冷却档：分数往下掉，频率缩放完全不参与。

    这是 2026-09-29 换掉 rise/fall 相位机之后的**核心语义**：分数只回答
    "这个群现在还热吗"，判据只有本群最后一条消息的时刻。
    """
    svc = _service()
    t = 100_000

    def advance(actual_gap: int) -> tuple[float, str]:
        st = _state(t + 10 - actual_gap)
        st.attention_score = 5.0
        st.last_decay_at = t
        svc._write_state(st)
        after = svc._apply_decay(svc._load_state("g1"), t + 10)
        return after.attention_score, after.heat

    score, heat = advance(200)          # 200 秒没人说话 > 120s 窗口
    assert heat == "cooling"
    # fall_rate 0.015 × 10 = 0.15，与发言间隔无关
    assert score == pytest.approx(5.0 - 0.15, rel=1e-3)

    hot_score, hot_heat = advance(5)    # 5 秒前刚有人说话
    assert hot_heat == "warm"
    assert hot_score > 5.0, "热聊中的群该涨分"


def test_the_prompt_and_settings_keys_are_consistent_after_the_removal():
    """（占位：心跳档的键与提示词一致性由 test_qq_attention_heat.py 覆盖。）"""


def test_a_burst_of_messages_shortens_the_gap_and_raises_the_rate():
    """两个群在**同一时刻**推进相同的时间，刚说过话的那个必须涨得更快。

    这是"频率影响增速"最贴近现场的表述：不需要比较绝对值，只要比较同一
    ``now`` 下间隔不同的两个群（两个间隔都落在热聊窗口内）。
    """
    svc = _service()
    t = 100_000

    def score_with_gap(actual_gap: int) -> float:
        st = _state(t + 10 - actual_gap)   # 间隔恰好 = actual_gap
        st.last_decay_at = t
        svc._write_state(st)
        return svc._apply_decay(svc._load_state("g1"), t + 10).attention_score

    chatty = score_with_gap(5)      # 5 秒前刚有人说话
    quiet = score_with_gap(100)     # 100 秒没人说话（还没到 120s 的冷却线）
    assert chatty > quiet
    assert chatty == pytest.approx(0.08 * 5.0 * 10, rel=1e-2)   # 封顶（本文件取 5.0）
    assert quiet == pytest.approx(0.08 * (30 / 100) * 10, rel=1e-2)


def test_frequency_has_no_effect_in_the_cooling_tier():
    """冷却档不该被频率缩放影响 —— 那里是回落，不是"慢速上升"。"""
    svc = _service()
    for gap in (200, 3000):
        st = _state(1000 - gap)          # 都超过 120s 的热聊窗口
        st.attention_score = 5.0
        st.last_decay_at = 1000
        svc._write_state(st)
        after = svc._apply_decay(svc._load_state("g1"), 1010)
        # fall_rate 0.015 * 10 = 0.15，与频率无关
        assert after.attention_score == pytest.approx(5.0 - 0.15, rel=1e-3)
        assert after.heat == "cooling"


def test_shipped_ceiling_is_the_moderated_value():
    """出厂上限必须仍是收敛过的 1.8，防止有人把它改回激进的 3.0。

    单独一条：本文件其它测试用的是自己声明的上限（5.0），所以它们不会因为
    出厂默认变动而变红 —— 那条信息就只由这里承载。

    为什么是 1.8：``rise_rate``（0.08）与上限相乘才是热群的实际增速，3.0 时
    热点群 20 秒夺冠、焦点会随刷屏频繁跳；1.8 把它压到约 1.8×（每 12 秒以内
    的群吃满），保住"越热闹越快"的排序又不至于黏住刷屏的那个。
    """
    from plugin.plugins.qq_auto_reply import settings_schema

    spec = settings_schema.BY_KEY["attention_frequency_max_multiplier"]
    assert spec.default == pytest.approx(1.8)
    # 下限/目标间隔一并钉住，避免三个值被人单独改动后失去平衡
    assert settings_schema.BY_KEY["attention_frequency_min_multiplier"].default == pytest.approx(0.15)
    assert settings_schema.BY_KEY["attention_frequency_target_gap"].default == pytest.approx(30.0)
    # 上限必须 >=1（否则"热群"会比基准还慢），下限必须 >0（否则冷群彻底冻结）
    assert spec.floor is not None and spec.floor >= 1.0
    assert settings_schema.BY_KEY["attention_frequency_min_multiplier"].default > 0.0
