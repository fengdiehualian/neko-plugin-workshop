"""插件状态机单测（独立仓库可跑，不依赖宿主）。

覆盖问题清单点名的回归高发区：_MoodState 迁移/过期、_should_inject 的
interval_n 计数语义、非限时动作兜底、supervise 到期恢复与幂等、
主动搭话暂停/恢复原值、日记截断、活动感知与表情联动注入文本。

依赖 tests/conftest.py 注入的宿主 SDK 桩与 ``plugin_factory`` / ``tm`` fixture。
运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio
import types


def run(coro):
    return asyncio.run(coro)


# ---------- _MoodState ----------


def test_mood_state_legacy_action_migration(tm) -> None:
    state = tm._MoodState.from_mapping({"action": "cold_violence", "reason": "r"})
    assert state.action == "ebb_tide"
    assert state.reason == "r"
    # 未知动作原样保留，不做迁移
    state2 = tm._MoodState.from_mapping({"action": "future_action"})
    assert state2.action == "future_action"


def test_timed_action_expiry(tm) -> None:
    state = tm._MoodState.from_mapping(
        {"action": "ebb_tide", "started_at": 100.0, "expires_at": 200.0}
    )
    assert state.is_active(now=150.0) is True
    assert state.is_active(now=200.0) is False
    # 非限时动作 expires_at=0 永不过期（兜底由监督循环负责）
    open_state = tm._MoodState.from_mapping(
        {"action": "seek_harbor", "started_at": 100.0, "expires_at": 0.0}
    )
    assert open_state.is_active(now=10.0**9) is True


# ---------- interval_n 计数语义 ----------


def test_interval_n_gate_does_not_consume_counter(plugin_factory) -> None:
    p = plugin_factory({"inject_mode": "interval_n", "inject_interval_n": 2})

    assert run(p._handle_new_user_message(1.0, "你好")) is False  # count 1 < 2
    assert run(p._handle_new_user_message(2.0, "你好")) is True   # count 2 → 注入并清零
    assert p._user_message_count_since_inject == 0

    # 变化门控拦截：内容与上次注入完全相同 → 不注入、不消耗计数
    state = p._current_phase_state()
    p._last_injected_whisper_key = p._whisper_state_key(state)
    assert run(p._handle_new_user_message(3.0, "你好")) is False  # count 1 < 2
    assert run(p._handle_new_user_message(4.0, "你好")) is False  # count 2 → 尝试但被门控拦截
    assert p._user_message_count_since_inject == 2  # 计数保留，未被白白消耗
    pushed_before = len(p._pushed)

    # 内容一旦变化，下一条消息立即注入（不需要重新攒满）
    p._last_injected_whisper_key = ""
    assert run(p._handle_new_user_message(5.0, "你好")) is True
    assert len(p._pushed) == pushed_before + 1
    assert p._user_message_count_since_inject == 0


def test_duplicate_timestamp_ignored(plugin_factory) -> None:
    p = plugin_factory()
    assert run(p._handle_new_user_message(1.0, "你好")) is True
    # 同水位消息（轮询重复读到同一条记录）不再触发
    assert run(p._handle_new_user_message(1.0, "你好")) is False


# ---------- 非限时动作兜底 ----------


def _patch_clock(monkeypatch, tm, holder):
    monkeypatch.setattr(tm.time, "time", lambda: holder[0])


def test_seek_harbor_fallback_auto_resolves(monkeypatch, tm, plugin_factory) -> None:
    p = plugin_factory(mood_extra={"open_action_timeout_minutes": 120})
    now = [1000.0]
    _patch_clock(monkeypatch, tm, now)

    run(p._apply_mood_action(action="seek_harbor", minutes=None, reason="委屈", timed=False))
    assert p._mood_state.action == "seek_harbor"

    now[0] = 1000.0 + 119 * 60  # 未到阈值
    assert run(p._fallback_open_action_if_due()) == ""
    assert p._mood_state.action == "seek_harbor"

    now[0] = 1000.0 + 121 * 60  # 超阈值：自动解除
    assert run(p._fallback_open_action_if_due()) == "seek_harbor"
    assert p._mood_state.action == ""
    # 限时动作不受兜底影响
    run(p._apply_mood_action(action="ebb_tide", minutes=30, reason="", timed=True))
    now[0] += 400 * 60
    assert run(p._fallback_open_action_if_due()) == ""


def test_seek_harbor_fallback_disabled_by_zero(monkeypatch, tm, plugin_factory) -> None:
    p = plugin_factory(mood_extra={"open_action_timeout_minutes": 0})
    now = [1000.0]
    _patch_clock(monkeypatch, tm, now)
    run(p._apply_mood_action(action="seek_harbor", minutes=None, reason="", timed=False))
    now[0] += 10000 * 60
    assert run(p._fallback_open_action_if_due()) == ""
    assert p._mood_state.action == "seek_harbor"


# ---------- supervise：到期恢复与幂等 ----------


def test_supervise_clears_expired_action_and_speaks(monkeypatch, tm, plugin_factory) -> None:
    p = plugin_factory()
    now = [1000.0]
    _patch_clock(monkeypatch, tm, now)
    run(p._apply_mood_action(action="ebb_tide", minutes=20, reason="", timed=True))
    assert p._mood_state.is_active()

    now[0] = 1000.0 + 21 * 60  # 到期
    run(p._supervise_once())
    assert p._mood_state.action == ""
    recovery = [m for m in p._pushed if m.get("ai_behavior") == "respond"]
    assert recovery, "到期后应推送恢复开口消息"

    # 幂等：再跑一趟不产生第二条恢复消息
    run(p._supervise_once())
    recovery2 = [m for m in p._pushed if m.get("ai_behavior") == "respond"]
    assert len(recovery2) == len(recovery)


# ---------- 主动搭话暂停/恢复 ----------


class _HttpRecorder:
    def __init__(self, master_value=True):
        self.master_value = master_value
        self.calls = []

    async def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/proactive/settings" and method == "GET":
            return {"settings": {"proactiveChatEnabled": self.master_value}}
        if path == "/api/proactive/settings" and method == "POST":
            self.master_value = bool(body.get("proactiveChatEnabled"))
            return {"success": True}
        return None


def test_proactive_pause_records_and_restores_original_value(plugin_factory) -> None:
    p = plugin_factory()
    http = _HttpRecorder(master_value=True)
    p._proactive_http = http

    run(p._pause_host_proactive())
    assert p._proactive_state["prev"] == {"master": True}
    assert ("POST", "/api/proactive/settings", {"proactiveChatEnabled": False}) in http.calls

    run(p._resume_host_proactive())
    assert p._proactive_state["prev"] is None
    assert http.master_value is True  # 原值恢复为开


def test_proactive_pause_when_already_off_does_not_restore_on(plugin_factory) -> None:
    p = plugin_factory()
    http = _HttpRecorder(master_value=False)
    p._proactive_http = http

    run(p._pause_host_proactive())
    assert p._proactive_state["prev"] == {"master": False}
    assert all(call[0] != "POST" for call in http.calls)  # 本来就没开，不写

    run(p._resume_host_proactive())
    assert http.master_value is False  # 结束时也不替用户打开


def test_reassert_proactive_off_when_drifted_back_on(plugin_factory) -> None:
    p = plugin_factory()
    http = _HttpRecorder(master_value=True)
    p._proactive_http = http
    p._proactive_state["prev"] = {"master": True}

    run(p._reassert_proactive_off())
    assert http.master_value is False  # 外部写回 true 后被重新关掉


# ---------- 日记截断语义 ----------


def test_diary_trims_memory_and_disk_consistently(tm, plugin_factory) -> None:
    p = plugin_factory()
    # 0.7.0 起时光日记混排碎片，上限放宽到 120：造 130 条验证截断仍生效
    p._diary = [{"ts": str(i), "entry": f"e{i}"} for i in range(130)]
    run(p._save_diary())
    assert len(p._diary) == tm._DIARY_MAX_ENTRIES
    # 0.5.0 起手记按角色分片落盘（diary@<lanlan>，测试环境角色解析回落 "default"）
    assert len(p.store.data[tm._diary_key("default")]) == tm._DIARY_MAX_ENTRIES
    # 保留的是"最近"的条目
    assert p._diary[-1]["entry"] == "e129"


# ---------- 注入文本：活动感知 / 表情联动 / 阶段×情绪 ----------


def _snapshot(privacy="visible", idle=None, category=None):
    return types.SimpleNamespace(
        privacy_state=privacy, system_idle_seconds=idle, foreground_category=category
    )


def _stub_activity(monkeypatch, snap):
    import sys

    async def fake(_source, **_kwargs):
        return snap

    monkeypatch.setattr(sys.modules["plugin.sdk.plugin"], "get_os_activity_snapshot", fake)


def test_activity_context_long_idle(monkeypatch, tm, plugin_factory) -> None:
    p = plugin_factory()
    _stub_activity(monkeypatch, _snapshot(idle=3600))
    state = tm.compute_phase_state(
        today=tm.parse_anchor_date("2026-08-02"),
        anchor=tm.parse_anchor_date("2026-08-01"),
        cycle_length=28, period_length=5, ovulation_day=14, ovulation_window=3,
    )
    line = run(p._build_activity_context_line(state))
    assert "没在电脑前" in line
    assert p._last_activity_context_key == "idle"


def test_activity_context_private_is_silent(monkeypatch, plugin_factory) -> None:
    p = plugin_factory()
    _stub_activity(monkeypatch, _snapshot(privacy="private", idle=9999, category="work"))
    state = p._current_phase_state()
    assert run(p._build_activity_context_line(state)) == ""


def test_activity_context_focus_work(monkeypatch, plugin_factory) -> None:
    p = plugin_factory()
    _stub_activity(monkeypatch, _snapshot(idle=5, category="work"))
    state = p._current_phase_state()
    line = run(p._build_activity_context_line(state))
    assert "专注" in line
    assert p._last_activity_context_key == "focus:work"


def test_activity_context_disabled_by_config(monkeypatch, plugin_factory) -> None:
    p = plugin_factory({"activity_context": False})
    _stub_activity(monkeypatch, _snapshot(idle=9999))
    assert run(p._build_activity_context_line(p._current_phase_state())) == ""


def test_mood_context_line_aligns_outward_emotion(plugin_factory) -> None:
    p = plugin_factory()
    p._mood_state.action = "ebb_tide"
    p._mood_state.started_at = 1.0
    p._mood_state.expires_at = 10.0**12
    line = p._build_mood_context_line()
    assert "主导情绪清晰可辨" in line
    assert "冷战沉默" in line


def test_whisper_contains_phase_mood_note(tm, plugin_factory) -> None:
    from datetime import date

    p = plugin_factory()
    state = tm.compute_phase_state(
        today=date(2026, 8, 2),
        anchor=date(2026, 8, 1),
        cycle_length=28, period_length=5, ovulation_day=14, ovulation_window=3,
    )
    text = tm.build_body_whisper(p._phases_cfg, state, timezone_name="Asia/Shanghai")
    assert "情绪更敏感" in text


def test_timezone_default_is_auto(plugin_factory) -> None:
    p = plugin_factory()
    p._tide_cfg.pop("timezone", None)
    # 缺省时区不再硬编码 Asia/Shanghai，而是 auto（系统本地）
    phase = p._current_phase_state()
    assert phase.phase in ("menstrual", "follicular", "ovulatory", "luteal")
    assert p._settings_snapshot()["timezone"] == "auto"


# ---------- 总线轮询健壮性 ----------


def test_poll_picks_latest_user_message_among_mixed_records(plugin_factory) -> None:
    """回归：bucket 混入其他类型记录时，旧实现盲取最后一条会永久遮蔽用户消息；
    新实现取最近 10 条倒序找最新 user_message。记录为 SDK v2 的 .payload 结构。"""
    from types import SimpleNamespace

    p = plugin_factory()
    p.ctx.bus = SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: [
        SimpleNamespace(payload={"type": "user_message", "content": "早上的话", "_ts": 1.0}),
        SimpleNamespace(payload={"type": "other_system_record", "_ts": 2.0}),
        SimpleNamespace(payload={"type": "user_message", "content": "刚说的", "_ts": 3.0, "is_voice": True}),
    ]))
    res = run(p._poll_latest_user_message())
    assert res == (3.0, "刚说的", True, "default")  # 第 4 元 = 归属角色（记录无 lanlan 字段时回落当前角色）


def test_poll_empty_bucket_returns_none_and_heartbeats(plugin_factory) -> None:
    from types import SimpleNamespace

    p = plugin_factory()
    p.ctx.bus = SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: []))
    assert p._last_bus_heartbeat_logged < 0  # 哨兵：尚未留痕（-1000.0，兼容 monotonic 0 纪元平台）
    assert run(p._poll_latest_user_message()) is None
    # 心跳已留痕（防"零日志盲区"）；用"二次调用不再推进节流时钟"验证幂等，
    # 不用 > 0 断言：部分平台（如 Linux）time.monotonic() 取值可能恰为 0.0。
    first_mark = p._last_bus_heartbeat_logged
    assert first_mark >= 0
    run(p._poll_latest_user_message())
    assert p._last_bus_heartbeat_logged == first_mark


def test_poll_unwraps_sdk_value_wrapped_records(plugin_factory) -> None:
    """回归（运行时实测）：宿主 MemoryList.dump_records() 给对象序列，SDK 再包成
    payload={"value": <MemoryRecord>}——不解包会吞掉 type，用户消息永远被过滤，
    心跳只剩 latest_type=?。此处模拟该形态：内层对象经 .raw 暴露完整 payload。"""
    from types import SimpleNamespace

    p = plugin_factory()
    inner_old = SimpleNamespace(raw={"type": "user_message", "content": "之前那句", "_ts": 1.0})
    inner_new = SimpleNamespace(
        raw={"type": "user_message", "content": "刚说的", "_ts": 3.0, "is_voice": True, "lanlan": "YUI"}
    )
    p.ctx.bus = SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: [
        SimpleNamespace(payload={"value": inner_old}),
        SimpleNamespace(payload={"value": SimpleNamespace(raw={"type": "other_system_record", "_ts": 2.0})}),
        SimpleNamespace(payload={"value": inner_new}),
    ]))
    res = run(p._poll_latest_user_message())
    assert res == (3.0, "刚说的", True, "YUI")


def test_poll_awaitable_result_is_awaited(plugin_factory) -> None:
    """SDK v2 的 get 可能返回 awaitable（async 会话内），必须 await 后再解析。"""
    from types import SimpleNamespace

    async def _async_get(**kw):
        return [SimpleNamespace(payload={"type": "user_message", "content": "异步路径", "_ts": 9.0})]

    p = plugin_factory()
    p.ctx.bus = SimpleNamespace(memory=SimpleNamespace(get=_async_get))
    res = run(p._poll_latest_user_message())
    assert res == (9.0, "异步路径", False, "default")


def test_poll_missing_bus_is_silent_safe(plugin_factory) -> None:
    p = plugin_factory()
    p.ctx.bus = None
    assert run(p._poll_latest_user_message()) is None  # 不抛异常，只告警一次


# ---------- 和好提醒（沉默情绪 + 道歉关键词） ----------


def test_reconcile_nudge_pushed_during_silent_mood(plugin_factory) -> None:
    """回归：模型常"演"和好却忘调 mood_rising_tide，面板卡冷战；
    沉默情绪期间命中道歉关键词应推一条 read 提醒，且 5 分钟节流。"""
    p = plugin_factory()
    p._proactive_http = _HttpRecorder(master_value=False)
    run(p.set_mood(action="ebb_tide", minutes=30))
    n0 = len(p._pushed)
    run(p._maybe_nudge_reconcile("对不起嘛，别生气了好不好"))
    nudges = [m for m in p._pushed[n0:] if m.get("metadata", {}).get("message_type") == "forever_companion.reconcile_nudge"]
    assert len(nudges) == 1
    assert nudges[0].get("ai_behavior") == "read"
    # 节流：紧接着的下一条道歉不再重复推
    run(p._maybe_nudge_reconcile("真的抱歉，我错了"))
    nudges2 = [m for m in p._pushed[n0:] if m.get("metadata", {}).get("message_type") == "forever_companion.reconcile_nudge"]
    assert len(nudges2) == 1


def test_reconcile_nudge_not_pushed_without_silent_mood(plugin_factory) -> None:
    p = plugin_factory()
    run(p._maybe_nudge_reconcile("对不起"))
    assert not p._pushed  # 无情绪状态时不提醒；日常道歉不该触发噪音


# ---------- 开启开关时立即 prime 注入 ----------


def test_toggle_on_primes_immediate_injection(plugin_factory) -> None:
    """回归：开启总开关的瞬间先注入一次，不用等满 interval_n 条消息；
    关闭时不注入。切角色卡后插件重启场景下避免"开了没反应"的误判。"""
    p = plugin_factory()
    if p._enabled():  # 测试配置默认开启，先关掉再验证"开启瞬间注入"
        run(p.toggle())
    assert not p._enabled()
    run(p.toggle())
    assert p._enabled()
    assert any(m.get("ai_behavior") == "read" for m in p._pushed), "开启后应立即有一次 read 注入"
    n_after_on = len(p._pushed)
    run(p.toggle())  # 关闭
    assert len(p._pushed) == n_after_on


def test_update_settings_enable_primes_injection(plugin_factory) -> None:
    p = plugin_factory()
    run(p.update_settings(enabled=False))
    p._pushed.clear()
    run(p.update_settings(enabled=True))
    assert p._enabled()
    assert any(m.get("ai_behavior") == "read" for m in p._pushed)


# ---------- 工具健康扫描（/api/tools 真实响应结构） ----------


class _ToolsHttp:
    def __init__(self, payload):
        self.payload = payload

    async def __call__(self, method, path, body=None):
        if method == "GET" and path == "/api/tools":
            return self.payload
        return None


def test_tool_health_scan_parses_tools_by_role(plugin_factory) -> None:
    """回归：GET /api/tools 返回 {"ok", "tools_by_role"}，工具嵌在 tools_by_role 下；
    旧实现扫描顶层 values 导致永远误判"全部缺失"、每 5 分钟盲目重注册。"""
    p = plugin_factory()
    p._llm_tools = {"mood_ebb_tide": object(), "mood_sea_fog": object()}
    p._proactive_http = _ToolsHttp({
        "ok": True,
        "tools_by_role": {
            "YUI": [{"name": "mood_ebb_tide"}, {"name": "mood_sea_fog"}],
        },
    })
    reachable, missing = run(p._tool_health_scan())
    assert reachable is True
    assert missing == []  # 全部在位，不再误报


def test_tool_health_scan_detects_real_missing(plugin_factory) -> None:
    p = plugin_factory()
    p._llm_tools = {"mood_ebb_tide": object(), "mood_sea_fog": object()}
    p._proactive_http = _ToolsHttp({
        "ok": True,
        "tools_by_role": {"YUI": [{"name": "mood_ebb_tide"}]},
    })
    reachable, missing = run(p._tool_health_scan())
    assert reachable is True
    assert missing == ["mood_sea_fog"]


# ---------- set_mood 手动触发入口 ----------


def test_set_mood_entry_applies_action(plugin_factory) -> None:
    p = plugin_factory()
    p._proactive_http = _HttpRecorder(master_value=False)
    res = run(p.set_mood(action="storm_surge", minutes=10, reason="测试"))
    assert res.value["action"] == "storm_surge"
    assert p._mood_state.action == "storm_surge"
    # 行为指令已推送（read 通道）
    assert any(m.get("ai_behavior") == "read" for m in p._pushed)


def test_set_mood_entry_rejects_unknown_action(plugin_factory) -> None:
    p = plugin_factory()
    res = run(p.set_mood(action="not_a_mood"))
    # 非法动作返回 Err（带 error，无 value）
    assert getattr(res, "error", None) is not None
    assert p._mood_state.action == ""


# ---------- 调试模式（debug_* 入口） ----------


def test_debug_mode_registers_and_unregisters_entries(plugin_factory) -> None:
    p = plugin_factory({"debug_mode": True})
    p._sync_debug_entries()
    assert "debug_inject" in p._dyn_entries
    assert "debug_check_tools" in p._dyn_entries

    p._tide_cfg["debug_mode"] = False
    p._sync_debug_entries()
    assert p._dyn_entries == {}


def test_debug_mode_off_registers_nothing(plugin_factory) -> None:
    p = plugin_factory()
    p._sync_debug_entries()
    assert p._dyn_entries == {}


def test_debug_force_activity_then_force_inject(plugin_factory) -> None:
    p = plugin_factory()
    res = run(p._debug_force_activity(idle_seconds=3600))
    assert res.value["override"]["idle"] == 3600
    inj = run(p._debug_inject())
    assert inj.value["pushed"] is True
    assert "没在电脑前" in "".join(inj.value["texts"])
    # 清除覆写后回到真实信号（默认桩信号不可用 → 无活动感知行）
    run(p._debug_force_activity())
    inj2 = run(p._debug_inject())
    assert inj2.value["pushed"] is True
    assert "没在电脑前" not in "".join(inj2.value["texts"])


def test_debug_set_mood_accelerates_fallback(plugin_factory) -> None:
    p = plugin_factory(mood_extra={"open_action_timeout_minutes": 120})
    p._proactive_http = _HttpRecorder(master_value=False)
    res = run(p._debug_set_mood(action="seek_harbor", minutes_ago=121))
    assert res.value["action"] == "seek_harbor"
    assert run(p._fallback_open_action_if_due()) == "seek_harbor"


def test_debug_expire_mood_triggers_recovery(plugin_factory) -> None:
    p = plugin_factory()
    p._proactive_http = _HttpRecorder(master_value=False)
    run(p._debug_set_mood(action="ebb_tide", duration_minutes=30))
    res = run(p._debug_expire_mood())
    assert res.value["forced"] is True
    run(p._supervise_once())
    assert p._mood_state.action == ""
    assert any(m.get("ai_behavior") == "respond" for m in p._pushed)


def test_debug_simulate_user_message_advances_counter(plugin_factory) -> None:
    p = plugin_factory({"inject_mode": "interval_n", "inject_interval_n": 2})
    r1 = run(p._debug_simulate_user_message("你好"))
    assert r1.value["injected"] is False and r1.value["message_counter"] == 1
    r2 = run(p._debug_simulate_user_message("你好"))
    assert r2.value["injected"] is True and r2.value["message_counter"] == 0
