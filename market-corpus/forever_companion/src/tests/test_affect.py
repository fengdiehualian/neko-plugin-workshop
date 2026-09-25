"""连续心情（valence/arousal 二维 + 余波惰性衰减）测试。

覆盖：惰性衰减数学（arousal/valence 两速 τ、Δt=0 不变、未写入过=(0, 静息基线)）、
arousal 静息基线 homeostasis（高于基线回落/低于基线回升，[mood].arousal_baseline
默认 0.35；baseline=0 回到旧语义；配置越界 clamp）、动作→冲量表覆盖全部 8 个动作
且先衰减再叠加、rising_tide 缓解冲量（负 valence 减半、arousal ×0.6、正 valence
不动）、语气信号单轮限幅（confidence 加权，v ≤0.10 / a ≤0.12，neutral 的 valence
向 0、arousal 向基线微拉不越过）、域 clamp、from_mapping 旧数据零迁移兼容、
dashboard payload 含 affect（两位小数）、语气感知两种模式（筛选/校正）都喂
连续心情并随 mood@<角色> 落盘。

依赖 tests/conftest.py 的 ``plugin_factory_full`` / ``tm`` fixture。
运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio
import math

import pytest


def run(coro):
    return asyncio.run(coro)


class _HostHttp:
    """宿主 HTTP 桩：角色解析 + 主动搭话开关 + health(CSRF) + recent_file + emotion/analysis。"""

    def __init__(self, current="YUI", emotion=("happy", 1.0)):
        self.current = current
        self.master = True
        self.turns: list[dict] = []
        self.emotion = emotion
        self.analysis_calls: list[dict] = []

    def set_turns(self, pairs: list[tuple[str, str]]) -> None:
        self.turns = []
        for user_text, her_text in pairs:
            self.turns.append({"type": "human", "data": {"content": user_text}})
            self.turns.append({"type": "ai", "data": {"content": her_text}})

    def add_turn(self, user_text: str, her_text: str) -> None:
        self.turns.append({"type": "human", "data": {"content": user_text}})
        self.turns.append({"type": "ai", "data": {"content": her_text}})

    async def __call__(self, method, path, body=None, headers=None):
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": self.current}
        if path == "/api/characters":
            return {"猫娘": {self.current: {}}}
        if path == "/health":
            return {"status": "ok", "instance_id": "test-csrf-token"}
        if path.startswith("/api/memory/recent_file"):
            # 同 test_emotion_sense 桩：模拟宿主 filename 格式校验（recent_<角色>.json）
            import re as _re
            from urllib.parse import parse_qs as _parse_qs
            from urllib.parse import urlparse as _urlparse

            fn = (_parse_qs(_urlparse(path).query).get("filename") or [""])[0]
            if not _re.match(r"^recent_(.+)\.json$", fn):
                return None
            return {"content": list(self.turns), "fingerprint": str(len(self.turns))}
        if path == "/api/emotion/analysis":
            self.analysis_calls.append({"body": body, "headers": headers})
            return {"emotion": self.emotion[0], "confidence": self.emotion[1]}
        if path == "/api/proactive/settings" and method == "GET":
            return {"settings": {"proactiveChatEnabled": self.master}}
        if path == "/api/proactive/settings" and method == "POST":
            self.master = bool(body.get("proactiveChatEnabled"))
            return {"success": True}
        return None


# ---------- 惰性衰减数学 ----------


def test_decay_two_speeds_and_zero_dt(plugin_factory_full, tm) -> None:
    p = plugin_factory_full(http=_HostHttp())
    shard = p._get_shard("YUI")
    shard.mood.valence = 0.8
    shard.mood.arousal = 0.6
    shard.mood.affect_updated_at = 10_000.0
    # Δt=0 不变
    v, a = p._current_affect(shard, now=10_000.0)
    assert v == pytest.approx(0.8)
    assert a == pytest.approx(0.6)
    # Δt=30min：arousal 恰好一个 τ（向基线 0.35 回归），valence 只过了 1/8 个 τ（τ=4h）
    v, a = p._current_affect(shard, now=10_000.0 + 1800)
    assert a == pytest.approx(0.35 + (0.6 - 0.35) * math.exp(-1))
    assert v == pytest.approx(0.8 * math.exp(-1800 / (4 * 3600)))
    # 读取只折算不回写：存储值保持原样（惰性语义）
    assert shard.mood.arousal == pytest.approx(0.6)
    # 从未写入过心情事件的旧状态（affect_updated_at=0）：初始态即静息水平 (0, 基线)
    fresh = p._get_shard("小灵")
    assert p._current_affect(fresh, now=99_999.0) == (0.0, 0.35)


def test_arousal_decays_toward_baseline_both_directions(plugin_factory_full, tm) -> None:
    """静息基线 homeostasis：高于基线回落、低于基线回升；纯函数路径默认 baseline=0 保持旧语义。"""
    p = plugin_factory_full(http=_HostHttp())
    shard = p._get_shard("YUI")
    # 高于基线：0.8 → 30min 后向 0.35 回落（不贴底）
    shard.mood.arousal = 0.8
    shard.mood.affect_updated_at = 1000.0
    _, a = p._current_affect(shard, now=1000.0 + 1800)
    assert a == pytest.approx(0.35 + 0.45 * math.exp(-1))
    assert 0.35 < a < 0.8
    # 低于基线（如 shallow_reef -0.05 叠加后 0.30）：30min 后向 0.35 回升
    shard.mood.arousal = 0.30
    _, a = p._current_affect(shard, now=1000.0 + 1800)
    assert a == pytest.approx(0.35 - 0.05 * math.exp(-1))
    assert 0.30 < a < 0.35
    # 纯函数路径不传 baseline（默认 0.0）：回归到完全平静、初始态 (0, 0)，旧语义不变
    fresh = tm._MoodState()
    assert tm._current_affect(fresh, now=1000.0) == (0.0, 0.0)
    fresh.arousal = 0.8
    fresh.affect_updated_at = 1000.0
    assert tm._current_affect(fresh, now=1000.0 + 1800)[1] == pytest.approx(0.8 * math.exp(-1))


def test_baseline_zero_keeps_legacy_semantics(plugin_factory_full, tm) -> None:
    """[mood].arousal_baseline = 0：完全回到旧语义（向 0 衰减、初始态 (0,0)、neutral 向 0 拉）。"""
    p = plugin_factory_full(http=_HostHttp(), mood_extra={"arousal_baseline": 0})
    shard = p._get_shard("YUI")
    assert p._current_affect(shard, now=1000.0) == (0.0, 0.0)
    shard.mood.arousal = 0.5
    shard.mood.affect_updated_at = 1000.0
    _, a = p._current_affect(shard, now=1000.0 + 1800)
    assert a == pytest.approx(0.5 * math.exp(-1))
    p._feed_tone_affect(shard, "neutral", 1.0, now=1000.0)
    assert shard.mood.arousal == pytest.approx(0.5 - 0.12)


def test_baseline_clamped_to_domain(plugin_factory_full) -> None:
    """配置越界 clamp 到 [0,1]：1.5 → 1.0；-0.2 → 0.0（等价旧语义）。"""
    p = plugin_factory_full(http=_HostHttp(), mood_extra={"arousal_baseline": 1.5})
    assert p._current_affect(p._get_shard("YUI"), now=1.0) == (0.0, 1.0)
    p2 = plugin_factory_full(http=_HostHttp(), mood_extra={"arousal_baseline": -0.2})
    assert p2._current_affect(p2._get_shard("YUI"), now=1.0) == (0.0, 0.0)


# ---------- 动作冲量表 ----------


def test_action_impulses_cover_all_eight(plugin_factory_full, tm, monkeypatch) -> None:
    expected = {
        "ebb_tide": (-0.45, 0.25),
        "sea_fog": (-0.35, 0.10),
        "shallow_reef": (-0.20, -0.05),
        "storm_surge": (-0.55, 0.60),
        "seek_harbor": (-0.30, 0.35),
        "ripple": (0.15, 0.20),
        "warm_current": (0.35, 0.15),
        "spring_tide": (0.55, 0.45),
    }
    assert dict(tm.ForeverCompanionPlugin._MOOD_AFFECT_IMPULSES) == expected
    for action, (dv, da) in expected.items():
        p = plugin_factory_full(http=_HostHttp())
        now = [1000.0]
        monkeypatch.setattr(tm.time, "time", lambda: now[0])
        run(p._apply_mood_action(
            action=action, minutes=10, reason="测试",
            timed=action in tm._TIMED_ACTIONS, lanlan="YUI",
        ))
        shard = p._get_shard("YUI")
        assert shard.mood.valence == pytest.approx(dv), action
        # arousal 从静息基线 0.35 起叠加：shallow_reef 的 -0.05 → 0.30（低于基线，之后自然回升）
        assert shard.mood.arousal == pytest.approx(max(0.0, min(1.0, 0.35 + da))), action
        assert shard.mood.affect_updated_at == now[0]
        # 沿用 mood@<角色> key 落盘，不加新 key
        saved = p.store.data["mood@YUI"]
        assert saved["valence"] == pytest.approx(dv)
        assert saved["arousal"] == pytest.approx(max(0.0, min(1.0, 0.35 + da)))


def test_impulse_decays_before_stacking(plugin_factory_full, tm, monkeypatch) -> None:
    """第二次冲量先把上一次惰性衰减到当前值，再叠加。"""
    p = plugin_factory_full(http=_HostHttp())
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._apply_mood_action(action="ripple", minutes=10, reason="", timed=True, lanlan="YUI"))
    now[0] += 1800  # 30 分钟后
    run(p._apply_mood_action(action="warm_current", minutes=10, reason="", timed=True, lanlan="YUI"))
    shard = p._get_shard("YUI")
    assert shard.mood.valence == pytest.approx(0.15 * math.exp(-0.125) + 0.35)
    # arousal 余波先向基线 0.35 回落，再叠加新冲量
    assert shard.mood.arousal == pytest.approx(0.35 + 0.20 * math.exp(-1) + 0.15)
    assert shard.mood.affect_updated_at == now[0]


def test_mood_expiry_adds_no_counter_impulse(plugin_factory_full, tm, monkeypatch) -> None:
    """动作到期不加反向冲量：余波靠自然衰减散去。"""
    p = plugin_factory_full(http=_HostHttp())
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._apply_mood_action(action="ebb_tide", minutes=20, reason="", timed=True, lanlan="YUI"))
    now[0] += 21 * 60
    run(p._supervise_once())
    shard = p._get_shard("YUI")
    assert shard.mood.action == ""  # 动作已自动解除
    assert shard.mood.valence == pytest.approx(-0.45)  # 存储值不动，读取时才衰减
    v, a = p._current_affect(shard)
    assert v == pytest.approx(-0.45 * math.exp(-1260 / (4 * 3600)))
    assert a == pytest.approx(0.35 + 0.25 * math.exp(-1260 / 1800))  # 向基线回落而非贴底


# ---------- rising_tide 缓解冲量 ----------


def test_rising_tide_relief_impulse(plugin_factory_full, tm, monkeypatch) -> None:
    p = plugin_factory_full(http=_HostHttp())
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._apply_mood_action(action="ebb_tide", minutes=20, reason="", timed=True, lanlan="YUI"))
    res = run(p.tool_feeling_better(_ctx={"lanlan_name": "YUI"}))
    assert res.value["previous_action"] == "ebb_tide"
    shard = p._get_shard("YUI")
    assert shard.mood.action == ""  # 不落动作状态
    # 负 valence 减半、arousal ×0.6（含基线的当前值 0.60 × 0.6）
    assert shard.mood.valence == pytest.approx(-0.225)
    assert shard.mood.arousal == pytest.approx(0.36)


def test_rising_tide_keeps_positive_valence(plugin_factory_full, tm, monkeypatch) -> None:
    """正 valence 不缓解（保持原值），arousal 照常 ×0.6。"""
    p = plugin_factory_full(http=_HostHttp())
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._apply_mood_action(action="warm_current", minutes=10, reason="", timed=True, lanlan="YUI"))
    run(p.tool_feeling_better(_ctx={"lanlan_name": "YUI"}))
    shard = p._get_shard("YUI")
    assert shard.mood.valence == pytest.approx(0.35)
    assert shard.mood.arousal == pytest.approx((0.35 + 0.15) * 0.6)  # 基线 0.35 + 冲量 0.15，再 ×0.6


# ---------- 语气信号：单轮限幅 ----------


def test_tone_feed_single_turn_capped(plugin_factory_full, tm) -> None:
    p = plugin_factory_full(http=_HostHttp())
    shard = p._get_shard("YUI")
    # angry：-v 满格、++a 满格（arousal 从静息基线 0.35 起积分）
    p._feed_tone_affect(shard, "angry", 1.0, now=1000.0)
    assert shard.mood.valence == pytest.approx(-0.10)
    assert shard.mood.arousal == pytest.approx(0.35 + 0.12)
    # happy：+v 满格、+a 0.75 格
    p._feed_tone_affect(shard, "happy", 1.0, now=1000.0)
    assert shard.mood.valence == pytest.approx(0.0)
    assert shard.mood.arousal == pytest.approx(0.35 + 0.12 + 0.09)
    # surprised：+v 小、++a
    p._feed_tone_affect(shard, "surprised", 1.0, now=1000.0)
    assert shard.mood.valence == pytest.approx(0.04)
    assert shard.mood.arousal == pytest.approx(0.35 + 0.12 + 0.09 + 0.12)
    # confidence 线性加权：0.5 → 半步
    p._feed_tone_affect(shard, "sad", 0.5, now=1000.0)
    assert shard.mood.valence == pytest.approx(0.04 - 0.05)
    assert shard.mood.arousal == pytest.approx(0.35 + 0.12 + 0.09 + 0.12 - 0.045)
    # 未知 label 不动心情
    before = (shard.mood.valence, shard.mood.arousal)
    p._feed_tone_affect(shard, "unknown_label", 1.0, now=1000.0)
    assert (shard.mood.valence, shard.mood.arousal) == before


def test_tone_feed_neutral_pulls_toward_targets(plugin_factory_full, tm) -> None:
    """neutral 微拉：valence 向 0、arousal 向静息基线（双向），最多一格且不越过目标。"""
    p = plugin_factory_full(http=_HostHttp())
    shard = p._get_shard("YUI")
    shard.mood.valence = 0.05
    shard.mood.arousal = 0.5
    shard.mood.affect_updated_at = 1000.0
    p._feed_tone_affect(shard, "neutral", 1.0, now=1000.0)
    # |v| < 一格：拉回 0 而不越过；arousal 高于基线：向基线减一格
    assert shard.mood.valence == pytest.approx(0.0)
    assert shard.mood.arousal == pytest.approx(0.38)
    p._feed_tone_affect(shard, "neutral", 1.0, now=1000.0)
    assert shard.mood.valence == pytest.approx(-0.0)  # 已在 0 不再动
    # 距基线不足一格（0.38 - 0.35 = 0.03）：拉到基线为止，不越过
    assert shard.mood.arousal == pytest.approx(0.35)
    # 低于基线时向上拉（双向 homeostasis），同样不越过
    shard.mood.arousal = 0.30
    p._feed_tone_affect(shard, "neutral", 1.0, now=1000.0)
    assert shard.mood.arousal == pytest.approx(0.35)


# ---------- 域 clamp ----------


def test_impulse_clamped_to_domain(plugin_factory_full, tm) -> None:
    p = plugin_factory_full(http=_HostHttp())
    shard = p._get_shard("YUI")
    shard.mood.affect_updated_at = 1000.0
    shard.mood.valence = 0.95
    shard.mood.arousal = 0.95
    p._apply_affect_impulse(shard, 0.55, 0.60, now=1000.0)
    assert shard.mood.valence == 1.0
    assert shard.mood.arousal == 1.0
    p._apply_affect_impulse(shard, -2.0, -2.0, now=1000.0)
    assert shard.mood.valence == -1.0
    assert shard.mood.arousal == 0.0


# ---------- from_mapping 旧数据兼容 ----------


def test_from_mapping_legacy_data_tolerant(tm) -> None:
    # 旧版存储没有连续心情三字段：缺省回落 0，零迁移
    state = tm._MoodState.from_mapping({
        "action": "ebb_tide", "reason": "旧数据", "started_at": 1.0, "expires_at": 2.0,
    })
    assert state.action == "ebb_tide"
    assert state.valence == 0.0
    assert state.arousal == 0.0
    assert state.affect_updated_at == 0.0
    # to_mapping 带上三字段（下次落盘即完成升级）
    saved = state.to_mapping()
    assert saved["valence"] == 0.0
    assert saved["arousal"] == 0.0
    assert saved["affect_updated_at"] == 0.0
    # 空/非 dict 输入也不炸
    empty = tm._MoodState.from_mapping(None)
    assert empty.valence == 0.0 and empty.affect_updated_at == 0.0


# ---------- dashboard payload ----------


def test_dashboard_payload_carries_affect(plugin_factory_full, tm, monkeypatch) -> None:
    p = plugin_factory_full(http=_HostHttp())
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._apply_mood_action(action="spring_tide", minutes=10, reason="开心", timed=True, lanlan="YUI"))
    result = run(p.dashboard())
    # Δt=0：冲量原值下发（arousal 含静息基线 0.35），保留两位小数；附带基线供面板画刻度
    assert result["mood"]["affect"] == {"valence": 0.55, "arousal": 0.8, "arousal_baseline": 0.35}
    # 30 分钟后：两速衰减后再下发（arousal 向基线回落；惰性衰减，不回写存储）
    now[0] += 1800
    result = run(p.dashboard())
    affect = result["mood"]["affect"]
    assert affect["valence"] == round(0.55 * math.exp(-0.125), 2)
    assert affect["arousal"] == round(0.35 + 0.45 * math.exp(-1), 2)
    assert p._get_shard("YUI").mood.valence == pytest.approx(0.55)  # 存储不被读取改动


def test_dashboard_affect_defaults_baseline(plugin_factory_full) -> None:
    """初始平静状态：valence 居中 0，arousal 显示静息基线（不再是贴底的 0）。"""
    p = plugin_factory_full(http=_HostHttp())
    result = run(p.dashboard())
    assert result["mood"]["affect"] == {"valence": 0.0, "arousal": 0.35, "arousal_baseline": 0.35}


# ---------- 语气感知两种模式都喂连续心情 ----------


def test_tone_sense_feeds_affect_screen_mode(plugin_factory_full, tm, monkeypatch) -> None:
    http = _HostHttp(emotion=("happy", 1.0))
    http.set_turns([("你好", "你好呀")])
    p = plugin_factory_full(http=http)
    shard = p._get_shard("YUI")
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))  # 基线
    http.add_turn("今天好开心", "太好啦")
    run(p._maybe_tone_sense("YUI", shard))
    # happy @1.0：她 +0.10 v / +0.09 a（0.75 格，从静息基线 0.35 起积分）；
    # 用户消息同 label 以权重 0.25 同向传导再 +0.025 v / +0.0225 a
    assert shard.mood.valence == pytest.approx(0.125)
    assert shard.mood.arousal == pytest.approx(0.35 + 0.1125)
    # 随 mood@<角色> 落盘
    assert p.store.data["mood@YUI"]["valence"] == pytest.approx(0.125)
    # 分析请求不带 lanlan_name（纯静默分析，不触发宿主前端表情更新）
    assert "lanlan_name" not in http.analysis_calls[-1]["body"]


def test_tone_sense_feeds_affect_correction_mode(plugin_factory_full, tm, monkeypatch) -> None:
    http = _HostHttp(emotion=("happy", 1.0))
    http.set_turns([("你好", "你好呀")])
    p = plugin_factory_full(http=http)
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p.set_mood(action="ebb_tide", minutes=30, _ctx={"lanlan_name": "YUI"}))
    shard = p._get_shard("YUI")
    assert shard.mood.valence == pytest.approx(-0.45)  # 动作冲量先落
    run(p._maybe_tone_sense("YUI", shard))  # 基线（不推进分析节流时间戳）
    http.add_turn("对不起嘛", "哼，这次就原谅你")
    run(p._maybe_tone_sense("YUI", shard))
    # 校正模式同样积分：-0.45 + 0.10 / 基线 0.35 + 冲量 0.25 + 0.09
    assert shard.mood.valence == pytest.approx(-0.35)
    assert shard.mood.arousal == pytest.approx(0.35 + 0.25 + 0.09)


# ---------- 持续极端心情邀请 ----------


def _extreme_invites(p):
    return [m for m in p._pushed if m.get("metadata", {}).get("message_type") == "forever_companion.affect_extreme_invite"]


def test_extreme_invite_after_sustained_low_valence(plugin_factory_full, tm, monkeypatch) -> None:
    """valence 持续低于阈值满 20 分钟推一次动作邀请（她仍可自主决定）；冷却期不重复。"""
    p = plugin_factory_full(http=_HostHttp())
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    shard = p._get_shard("YUI")
    p._apply_affect_impulse(shard, dv=-0.6, da=0.0, now=now[0])
    assert p._maybe_extreme_affect_invite("YUI", shard) is False  # 刚进极端档：只开始计时
    now[0] += 10 * 60
    assert p._maybe_extreme_affect_invite("YUI", shard) is False  # 未满 20 分钟
    now[0] += 11 * 60  # 满 21 分钟；衰减后 valence≈-0.55 仍在档内
    assert p._maybe_extreme_affect_invite("YUI", shard) is True
    invites = _extreme_invites(p)
    assert len(invites) == 1
    assert invites[0]["metadata"]["side"] == "low"
    assert "mood_drift_bottle" in invites[0]["parts"][0]["text"]  # 轻型动作排前引导
    now[0] += 20 * 60
    assert p._maybe_extreme_affect_invite("YUI", shard) is False  # 冷却 30 分钟内不重复
    assert len(_extreme_invites(p)) == 1


def test_extreme_invite_high_side_and_reset_on_normal(plugin_factory_full, tm, monkeypatch) -> None:
    """高涨侧对称邀请；回到常态清零计时、换侧重新计时。"""
    p = plugin_factory_full(http=_HostHttp())
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    shard = p._get_shard("YUI")
    p._apply_affect_impulse(shard, dv=1.2, da=0.0, now=now[0])
    assert p._maybe_extreme_affect_invite("YUI", shard) is False  # 刚进档，建立计时起点
    now[0] += 21 * 60
    assert p._maybe_extreme_affect_invite("YUI", shard) is True
    assert _extreme_invites(p)[0]["metadata"]["side"] == "high"
    assert "mood_warm_current" in _extreme_invites(p)[0]["parts"][0]["text"]
    # 拉回常态：计时与侧别清零
    p._apply_affect_impulse(shard, dv=-0.8, da=0.0, now=now[0])  # 0.92-0.8≈0.12，跌回常态
    assert p._maybe_extreme_affect_invite("YUI", shard) is False
    assert shard.affect_extreme_since == 0.0
    assert shard.affect_extreme_side == 0


def test_extreme_invite_skipped_during_active_action(plugin_factory_full, tm, monkeypatch) -> None:
    """动作生效中跳过邀请（她已经在表达了），且不计时。"""
    p = plugin_factory_full(http=_HostHttp())
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p.set_mood(action="ebb_tide", minutes=30, _ctx={"lanlan_name": "YUI"}))
    shard = p._get_shard("YUI")
    assert shard.mood.valence == pytest.approx(-0.45)  # 动作冲量已入极端档
    now[0] += 25 * 60  # 动作仍在（30 分钟限时），且持续超 20 分钟
    assert p._maybe_extreme_affect_invite("YUI", shard) is False
    assert shard.affect_extreme_since == 0.0
    assert not _extreme_invites(p)


def test_extreme_invite_disabled_by_zero_threshold(plugin_factory_full, tm, monkeypatch) -> None:
    """[mood].extreme_invite_threshold = 0 时整个邀请机制关闭。"""
    p = plugin_factory_full(http=_HostHttp())
    p._mood_cfg = dict(p._mood_cfg, extreme_invite_threshold=0)
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    shard = p._get_shard("YUI")
    p._apply_affect_impulse(shard, dv=-0.9, da=0.0, now=now[0])
    now[0] += 60 * 60
    assert p._maybe_extreme_affect_invite("YUI", shard) is False
    assert not _extreme_invites(p)


# ---------- 用户侧情绪同向传导（低权重） ----------


class _DualHttp(_HostHttp):
    """按文本来源返回不同情绪的宿主桩：用户消息与她的话给不同 label（验证两侧权重）。"""

    def __init__(self, user_emotion=("happy", 1.0), her_emotion=("sad", 1.0)):
        super().__init__(emotion=her_emotion)
        self.user_emotion = user_emotion

    async def __call__(self, method, path, body=None, headers=None):
        if path == "/api/emotion/analysis":
            text = str((body or {}).get("text") or "")
            user_texts = {t["data"]["content"] for t in self.turns if t.get("type") == "human"}
            if text in user_texts:
                self.analysis_calls.append({"body": body, "headers": headers})
                return {"emotion": self.user_emotion[0], "confidence": self.user_emotion[1]}
        return await super().__call__(method, path, body, headers)


def test_user_tone_feeds_affect_with_low_weight(plugin_factory_full, tm, monkeypatch) -> None:
    """用户侧同向传导：她 sad（权重 1.0，-0.10 v / -0.09 a）、用户 happy（权重 0.25，
    +0.025 v / +0.0225 a）→ 净 -0.075 v；两侧各一次独立分析。"""
    http = _DualHttp(user_emotion=("happy", 1.0), her_emotion=("sad", 1.0))
    http.set_turns([("你好", "你好呀")])
    p = plugin_factory_full(http=http)
    shard = p._get_shard("YUI")
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))  # 基线
    http.add_turn("今天过得特别棒！", "……哦，挺好的。")
    run(p._maybe_tone_sense("YUI", shard))
    assert len(http.analysis_calls) == 2  # 她 + 用户各一次独立分析
    assert shard.mood.valence == pytest.approx(-0.10 + 0.025)
    assert shard.mood.arousal == pytest.approx(0.35 - 0.09 + 0.0225)


def test_user_tone_skipped_in_correction_mode(plugin_factory_full, tm, monkeypatch) -> None:
    """动作生效中（校正模式）只分析她——校正看她的表现，用户侧不喂、不多一次分析。"""
    http = _DualHttp(user_emotion=("happy", 1.0), her_emotion=("happy", 1.0))
    http.set_turns([("你好", "你好呀")])
    p = plugin_factory_full(http=http)
    run(p.set_mood(action="ebb_tide", minutes=30, _ctx={"lanlan_name": "YUI"}))
    shard = p._get_shard("YUI")
    assert shard.mood.valence == pytest.approx(-0.45)  # 动作冲量先落
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))  # 基线
    http.add_turn("别难过啦，抱一下", "哼，这次就原谅你")
    run(p._maybe_tone_sense("YUI", shard))
    assert len(http.analysis_calls) == 1  # 只有她的回复被分析
    assert shard.mood.valence == pytest.approx(-0.45 + 0.10)  # 用户侧 happy 未计入


def test_user_tone_disabled_by_zero_weight(plugin_factory_full, tm, monkeypatch) -> None:
    """[emotion_sense].user_affect_weight = 0：用户侧整个关闭（不分析、不喂入）。"""
    http = _DualHttp(user_emotion=("happy", 1.0), her_emotion=("sad", 1.0))
    http.set_turns([("你好", "你好呀")])
    p = plugin_factory_full(http=http)
    p._emotion_sense_cfg = dict(p._emotion_sense_cfg, user_affect_weight=0)
    shard = p._get_shard("YUI")
    now = [5000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._maybe_tone_sense("YUI", shard))  # 基线
    http.add_turn("今天过得特别棒！", "……哦，挺好的。")
    run(p._maybe_tone_sense("YUI", shard))
    assert len(http.analysis_calls) == 1  # 只有她
    assert shard.mood.valence == pytest.approx(-0.10)
