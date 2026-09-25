"""正面情绪动作包（心有涟漪/暖流涌动/满潮欢喜，0.5.5）测试。

覆盖：三个新动作的 apply/到期自动解除/恢复台词分语气/target_lanlan 定向、
主动搭话暂停白名单（新动作生效不写 proactive 开关、存量重度负面仍暂停、
跨角色引用计数兼容）、ripple 期间道歉关键词触发和好提醒、
set_mood/debug_set_mood 接受新动作、行为约束 hint 与默认时长。

依赖 tests/conftest.py 的 ``plugin_factory_full`` / ``plugin_factory`` fixture。
运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio


def run(coro):
    return asyncio.run(coro)


class _HostHttp:
    """宿主 HTTP 桩：current_catgirl 解析 + 主动搭话总开关读写 + 角色名单。"""

    def __init__(self, current="YUI", master=True):
        self.current = current
        self.master = master
        self.calls = []

    async def __call__(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body))
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": self.current}
        if path == "/api/characters":
            return {"猫娘": {self.current: {}}}
        if path == "/api/proactive/settings" and method == "GET":
            return {"settings": {"proactiveChatEnabled": self.master}}
        if path == "/api/proactive/settings" and method == "POST":
            self.master = bool(body.get("proactiveChatEnabled"))
            return {"success": True}
        return None


def _pushes_of(p, message_type):
    return [m for m in p._pushed if m.get("metadata", {}).get("message_type") == f"forever_companion.{message_type}"]


# 新动作 id → 默认时长（分钟）
_NEW_ACTION_DEFAULTS = {"ripple": 15, "warm_current": 30, "spring_tide": 30}


# ---------- 三个新动作：apply / 默认时长 / 标签 ----------


def test_new_actions_apply_with_default_durations(plugin_factory_full, tm, monkeypatch) -> None:
    http = _HostHttp(current="YUI", master=False)
    p = plugin_factory_full(http=http)
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    for action, default_min in _NEW_ACTION_DEFAULTS.items():
        res = run(p.set_mood(action=action, _ctx={"lanlan_name": "YUI"}))
        assert res.value["action"] == action
        shard = p._get_shard("YUI")
        assert shard.mood.action == action
        assert shard.mood.is_active()
        # 未显式指定 minutes 时用各自动作的默认时长（15/30/30），而非全局 20 分钟
        assert shard.mood.expires_at - shard.mood.started_at == default_min * 60
        assert res.value["duration_minutes"] == default_min
        # 显示标签走默认中文（i18n 桩回落 default）
        assert p._action_label(action) == tm._MOOD_ACTION_DEFAULT_LABELS[action]
        # 行为基调已注册且进入注入文本（0.6.8 起去硬锁，措辞为"行为基调"）
        line = p._build_mood_context_line(shard)
        assert "行为基调" in line


def test_ripple_hint_requires_rising_tide_to_recover(tm) -> None:
    # 对称约束：想恢复正常必须先调 mood_rising_tide（与冷战等负面动作一致）
    assert "mood_rising_tide" in tm.ForeverCompanionPlugin._MOOD_BEHAVIOR_HINTS["ripple"]
    for action in ("warm_current", "spring_tide"):
        # 正面动作：兴致回落后自然恢复或调 rising_tide
        assert "mood_rising_tide" in tm.ForeverCompanionPlugin._MOOD_BEHAVIOR_HINTS[action]


# ---------- 到期自动解除 + 恢复台词分语气 + target_lanlan ----------


def test_new_actions_expire_with_gentle_recovery_line(plugin_factory_full, tm, monkeypatch) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", master=False))
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    for action, default_min in _NEW_ACTION_DEFAULTS.items():
        run(p._apply_mood_action(action=action, minutes=None, reason="测试", timed=True, lanlan="小灵"))
        now[0] += (default_min + 1) * 60  # 到期
        run(p._supervise_once())
        shard = p._get_shard("小灵")
        assert shard.mood.action == "", f"{action} 到期应自动解除"
        recovery = [m for m in _pushes_of(p, "mood_recovered") if m["metadata"]["expired_action"] == action]
        assert len(recovery) == 1
        assert recovery[0].get("target_lanlan") == "小灵"
        text = recovery[0]["parts"][0]["text"]
        # 温和回落文案：自然接着相处，不用冲突导向的"打破僵局"
        assert "接着相处" in text
        assert "打破僵局" not in text


def test_heavy_negative_recovery_line_keeps_conflict_tone(plugin_factory_full, tm, monkeypatch) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", master=False))
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._apply_mood_action(action="ebb_tide", minutes=20, reason="", timed=True, lanlan="小灵"))
    now[0] += 21 * 60
    run(p._supervise_once())
    recovery = _pushes_of(p, "mood_recovered")
    assert len(recovery) == 1
    # 重度负面仍用现有"打破僵局"文案，语义不变
    assert "打破僵局" in recovery[0]["parts"][0]["text"]


# ---------- 主动搭话暂停白名单 ----------


def test_new_actions_do_not_pause_proactive(plugin_factory_full) -> None:
    http = _HostHttp(current="YUI", master=True)
    p = plugin_factory_full(http=http)
    for action in _NEW_ACTION_DEFAULTS:
        run(p.set_mood(action=action, _ctx={"lanlan_name": "YUI"}))
        assert http.master is True, f"{action} 生效不应暂停主动搭话"
    # 从未写过开关，引用计数水位为空
    assert all(call[0] != "POST" for call in http.calls)
    assert p._proactive_state["prev"] is None
    assert p._proactive_state["paused_by"] == []
    assert p._should_pause_proactive() is False


def test_pause_whitelist_mixed_lanlan_refcount(plugin_factory_full) -> None:
    """A 角色 spring_tide（不暂停）+ B 角色 ebb_tide（暂停）→ 仍暂停；
    B 解除后即使 A 仍在满潮欢喜，也立即恢复。"""
    http = _HostHttp(current="YUI", master=True)
    p = plugin_factory_full(http=http)
    run(p.set_mood(action="spring_tide", _ctx={"lanlan_name": "YUI"}))
    assert http.master is True  # 正面动作不暂停
    assert p._proactive_state["paused_by"] == []

    run(p.set_mood(action="ebb_tide", minutes=30, _ctx={"lanlan_name": "小灵"}))
    assert http.master is False  # 重度负面仍暂停（存量语义不变）
    assert p._proactive_state["paused_by"] == ["小灵"]

    # B 解除 → 恢复；A 的 spring_tide 不计入引用计数
    run(p.tool_feeling_better(_ctx={"lanlan_name": "小灵"}))
    assert http.master is True
    assert p._proactive_state["prev"] is None
    assert p._proactive_state["paused_by"] == []
    assert p._get_shard("YUI").mood.action == "spring_tide"  # A 的状态不受影响


# ---------- ripple 期间道歉关键词触发和好提醒 ----------


def test_ripple_triggers_reconcile_nudge(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", master=False))
    run(p.set_mood(action="ripple", _ctx={"lanlan_name": "小灵"}))
    run(p._maybe_nudge_reconcile("对不起嘛，别闹别扭了", "小灵"))
    nudges = _pushes_of(p, "reconcile_nudge")
    assert len(nudges) == 1
    assert nudges[0].get("target_lanlan") == "小灵"
    assert nudges[0].get("ai_behavior") == "read"


def test_positive_actions_do_not_trigger_reconcile_nudge(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", master=False))
    for action in ("warm_current", "spring_tide"):
        run(p.set_mood(action=action, _ctx={"lanlan_name": "YUI"}))
        run(p._maybe_nudge_reconcile("对不起，我错了", "YUI"))
    # 正面状态不需要"和好"，道歉关键词不触发提醒
    assert not _pushes_of(p, "reconcile_nudge")


# ---------- set_mood / debug_set_mood 接受新动作 ----------


def test_set_mood_accepts_new_actions(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", master=False))
    for action in _NEW_ACTION_DEFAULTS:
        res = run(p.set_mood(action=action, minutes=10, reason="测试", _ctx={"lanlan_name": "YUI"}))
        assert getattr(res, "value", None), f"set_mood 应接受 {action}"
        assert p._get_shard("YUI").mood.action == action
    # 非法动作仍被拒绝
    res = run(p.set_mood(action="not_a_mood", _ctx={"lanlan_name": "YUI"}))
    assert getattr(res, "error", None) is not None


def test_debug_set_mood_accepts_new_actions(plugin_factory) -> None:
    p = plugin_factory()
    p._proactive_http = _HostHttp(master=False)
    for action in _NEW_ACTION_DEFAULTS:
        res = run(p._debug_set_mood(action=action, duration_minutes=5))
        assert res.value["action"] == action
        assert p._mood_state.action == action
    res = run(p._debug_set_mood(action="not_a_mood"))
    assert getattr(res, "error", None) is not None


# ---------- LLM 工具入口（_ctx 归因 + note 文案） ----------


def test_new_llm_tools_apply_and_attribute_by_ctx(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", master=False))
    res = run(p.tool_ripple(minutes=10, reason="被噎了一下", _ctx={"lanlan_name": "小灵"}))
    assert "心有涟漪" in res.value["note"]
    assert p._get_shard("小灵").mood.action == "ripple"
    assert p._get_shard("YUI").mood.action == ""

    res = run(p.tool_warm_current(reason="想他", _ctx={"lanlan_name": "YUI"}))
    assert "暖流涌动" in res.value["note"]
    assert p._get_shard("YUI").mood.action == "warm_current"

    res = run(p.tool_spring_tide(reason="遇到好事", _ctx={"lanlan_name": "YUI"}))
    assert "满潮欢喜" in res.value["note"]
    assert p._get_shard("YUI").mood.action == "spring_tide"
    # 行为指令推送定向到归属角色
    instr = _pushes_of(p, "mood_instruction")
    assert instr and instr[-1].get("target_lanlan") == "YUI"
