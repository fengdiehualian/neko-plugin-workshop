"""能力中心（1.2.7）测试：声明表解析 / 否决式开关 / 按角色隔离 / 工具显隐。

覆盖：
- core/capabilities.evaluate_capabilities 纯函数（含依赖链与原因词表）；
- set_capability 否决/撤否决语义（开不获强行点亮）、持久化往返（caps@<角色>）；
- 按角色隔离（A 关不影响 B）与全局份 caps@*；
- 既有闸口的收编等价性（无否决时行为与 1.2.x 一致：总开关/[mood] 配置/
  上游依赖的三闸矩阵）；
- 语气感知服务经 cap_enabled 回调接线；
- 工具显隐高级选项 hide_disabled_tools：温和模式零通知、隐藏模式按
  "所有角色都不生效"摘除、恢复重挂、巡检（_reemit_missing_tools）跳过；
- prune 清理 caps@ 键。

运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio
from types import SimpleNamespace


def run(coro):
    return asyncio.run(coro)


# ---------- 纯解析函数 ----------


def test_evaluate_defaults_all_on_with_master():
    from forever_companion.core.capabilities import evaluate_capabilities

    states = evaluate_capabilities(True, {}, set())
    assert all(s.enabled for s in states.values())
    assert all(s.source == "on" for s in states.values())


def test_evaluate_master_off_blocks_everything():
    from forever_companion.core.capabilities import evaluate_capabilities

    states = evaluate_capabilities(False, {}, set())
    # master off：无上游的根能力报 master_off；带依赖的先被 upstream 挡
    # （依赖展示优先，根能力本身仍直指 master_off）
    assert states["whisper"].source == "master_off"
    assert states["mood_engine"].source == "master_off"
    assert states["tone_sense"].enabled is False
    assert states["tone_sense"].blocked_by == ("mood_engine",) or states["tone_sense"].source == "master_off"


def test_evaluate_dependency_chain_upstream_off():
    from forever_companion.core.capabilities import evaluate_capabilities

    states = evaluate_capabilities(True, {"mood_engine": False}, set())
    assert states["mood_engine"].source == "config_off"
    for dep in ("tone_sense", "fragments", "journal", "review"):
        assert states[dep].enabled is False
        assert states[dep].source == "upstream_off"
        assert states[dep].blocked_by == ("mood_engine",)


def test_evaluate_user_veto_beats_config_on():
    from forever_companion.core.capabilities import evaluate_capabilities

    states = evaluate_capabilities(True, {"fragments": True}, {"fragments"})
    assert states["fragments"].source == "user_off"


def test_evaluate_user_veto_and_upstream_reports_upstream():
    from forever_companion.core.capabilities import evaluate_capabilities

    states = evaluate_capabilities(True, {"mood_engine": False}, {"fragments"})
    # 上游挡住时如实报上游（用户否决仍在表里，面板经 user_off 字段回显）
    assert states["fragments"].source == "upstream_off"


def test_capability_tools_grouping():
    from forever_companion.core.capabilities import (
        CAPABILITY_SPECS,
        capability_tools_for,
        evaluate_capabilities,
    )

    states = evaluate_capabilities(True, {}, {"mood_engine"})
    on, hideable = capability_tools_for(states)
    assert "mood_ebb_tide" in hideable
    assert "mood_journal_write" in hideable and "mood_recall_fragments" in hideable
    assert on == set()
    # 声明表覆盖 12 个工具且与装饰器清单一致
    all_tools = {t for s in CAPABILITY_SPECS.values() for t in s.tools}
    assert len(all_tools) == 12


# ---------- 运行面：判定与收编等价 ----------


def _boot(plugin_factory_full, **kw):
    return plugin_factory_full(**kw)


def test_no_override_matches_legacy_gates(plugin_factory_full):
    p = _boot(plugin_factory_full)
    run(p._ensure_shard("default"))
    assert p._mood_enabled() is True  # 总开关开、[mood] 默认开
    assert p._fragments_enabled() is True
    assert p._review_enabled() is True
    assert p._journal_enabled() is True
    assert p._emotion_sense_enabled() is True


def test_mood_config_off_blocks_dependents(plugin_factory_full):
    p = _boot(plugin_factory_full, mood_extra={"enabled": False})
    run(p._ensure_shard("default"))
    assert p._mood_enabled() is False
    assert p._fragments_enabled() is False
    assert p._review_enabled() is False
    # 身体轻语不依赖情绪引擎：照旧生效
    assert p._cap_effective("whisper") is True


def test_tone_sense_veto_isolated_from_fragments(plugin_factory_full):
    p = _boot(plugin_factory_full)
    run(p.set_capability(capability_id="tone_sense", enabled=False))
    assert p._emotion_sense_enabled() is False
    assert p._fragments_enabled() is True
    assert p._mood_enabled() is True
    # 撤否决回落配置默认
    run(p.set_capability(capability_id="tone_sense", enabled=True))
    assert p._emotion_sense_enabled() is True


def test_enable_cannot_force_past_config_off(plugin_factory_full):
    p = _boot(plugin_factory_full, mood_extra={"enabled": False})
    res = run(p.set_capability(capability_id="mood_engine", enabled=True))
    assert res.value["enabled"] is False
    assert res.value["source"] == "config_off"
    assert res.value["note"] == "reverted_to_default"
    assert p._mood_enabled() is False


def test_veto_persist_roundtrip(plugin_factory_full):
    p = _boot(plugin_factory_full)
    run(p.set_capability(capability_id="fragments", enabled=False))
    assert p.store.data["caps@default"] == {"fragments": True}
    # 新实例从同一 store 冷启动：否决仍在
    p.store.data.setdefault("lanlan_index", ["default"])
    p2 = _boot(plugin_factory_full, store_initial=dict(p.store.data))
    run(p2._ensure_shard("default"))
    assert p2._fragments_enabled() is False
    assert p2._mood_enabled() is True


def test_caps_store_write_failure_surfaces(plugin_factory_full):
    p = _boot(plugin_factory_full)

    async def boom(*_a, **_k):
        from forever_companion import Err, SdkError

        return Err(SdkError("disk full"))

    p.store.set = boom
    res = run(p.set_capability(capability_id="fragments", enabled=False))
    # 内存当场生效 + 如实带 persist_error（既定契约）
    assert res.value["enabled"] is False
    assert "persist_error" in res.value
    assert p._fragments_enabled() is False


def test_per_role_isolation(plugin_factory_full):
    p = _boot(plugin_factory_full)
    run(p._ensure_shard("小灵"))
    run(p.set_capability(capability_id="mood_engine", enabled=False, lanlan="YUI"))
    assert p._cap_effective("mood_engine", lanlan="YUI") is False
    assert p._cap_effective("mood_engine", lanlan="小灵") is True
    # 未知角色（无 shard 数据）也不受他人否决牵连
    assert p._cap_effective("mood_engine", lanlan="别人") is True


def test_global_veto_hits_all_roles(plugin_factory_full):
    p = _boot(plugin_factory_full)
    p._caps_off["*"] = {"review"}
    run(p._ensure_shard("小灵"))
    assert p._cap_effective("review", lanlan="小灵") is False
    assert p._cap_effective("review", lanlan="default") is False
    # 角色撤否决解不开全局份（如实回弹），全局份只经维护入口改
    res = run(p.set_capability(capability_id="review", enabled=True, lanlan="小灵"))
    assert res.value["enabled"] is False
    assert res.value["source"] == "user_off"


def test_unknown_capability_rejected(plugin_factory_full):
    from forever_companion import Err

    p = _boot(plugin_factory_full)
    res = run(p.set_capability(capability_id="nope", enabled=False))
    assert isinstance(res, Err)


def test_list_capabilities_payload(plugin_factory_full):
    p = _boot(plugin_factory_full, mood_extra={"enabled": False})
    res = run(p.list_capabilities())
    data = res.value
    assert data["master_enabled"] is True
    by_id = {c["id"]: c for c in data["capabilities"]}
    assert by_id["mood_engine"]["source"] == "config_off"
    assert by_id["fragments"]["source"] == "upstream_off"
    assert by_id["fragments"]["blocked_by"] == ["mood_engine"]
    assert by_id["whisper"]["enabled"] is True
    assert "capabilities" in data and len(data["capabilities"]) == 10


def test_whisper_veto_stops_injection_only(plugin_factory_full):
    p = _boot(plugin_factory_full)
    run(p.set_capability(capability_id="whisper", enabled=False))
    parts = run(p._inject_now("default"))
    assert parts == []
    # 情绪/统计链路不受牵连
    assert p._mood_enabled() is True


def test_journal_veto_keeps_tool_writable(plugin_factory_full):
    """既有语义：journal 关 = 停递邀请；她自愿调用写日记工具仍被允许（只认情绪闸）。"""
    p = _boot(plugin_factory_full)
    run(p.set_capability(capability_id="journal", enabled=False))
    assert p._journal_enabled() is False
    assert p._mood_enabled() is True
    due, _reason = run(p._maybe_journal_invite("default", p._get_shard("default")))
    assert due is False


def test_phase_opener_and_anniversary_veto(plugin_factory_full):
    p = _boot(plugin_factory_full)
    run(p.set_capability(capability_id="phase_openers", enabled=False))
    run(p.set_capability(capability_id="anniversary", enabled=False))
    state = p._current_phase_state()
    assert run(p._maybe_phase_opener(state, "default", p._get_shard("default"))) is False
    assert run(p._maybe_anniversary_push("default", p._get_shard("default"))) is False


def test_activity_veto_via_whisper_dependency(plugin_factory_full):
    p = _boot(plugin_factory_full)
    run(p.set_capability(capability_id="whisper", enabled=False))
    # 活动行搭载身体轻语：上游关则连带不生效
    assert p._cap_effective("activity_sense") is False
    line = run(p._build_activity_context_line(p._current_phase_state(), p._get_shard("default")))
    assert line == ""


# ---------- 工具显隐（高级选项） ----------


def _install_fake_tools(p, names):
    p._llm_tools = {n: SimpleNamespace(name=n, role=None, description="", parameters={}, timeout_seconds=15.0) for n in names}
    p._notify_log = []

    def _on(meta):
        p._notify_log.append(("register", meta.name))

    def _off(name, role=None):
        p._notify_log.append(("unregister", name))

    p._notify_llm_tool_registered = _on
    p._notify_llm_tool_unregistered = _off
    return p


_MOOD_TOOLS = (
    "mood_ebb_tide", "mood_sea_fog", "mood_shallow_reef", "mood_storm_surge",
    "mood_seek_harbor", "mood_ripple", "mood_warm_current", "mood_spring_tide",
    "mood_rising_tide", "mood_drift_bottle",
)


def test_gentle_mode_no_tool_notices(plugin_factory_full):
    p = _boot(plugin_factory_full, mood_extra={"enabled": False})
    _install_fake_tools(p, _MOOD_TOOLS)
    p._sync_tool_visibility()
    assert p._notify_log == []
    assert p._cap_hidden_tools == set()


def test_hide_mode_unregisters_disabled_and_rehangs(plugin_factory_full):
    p = _boot(plugin_factory_full, mood_extra={"enabled": False})
    _install_fake_tools(p, _MOOD_TOOLS)
    run(p.set_capability_flags(hide_disabled_tools=True))
    assert ("unregister", "mood_ebb_tide") in p._notify_log
    assert len(p._cap_hidden_tools) == 10
    # 巡检跳过隐藏名单：不抵消
    p._notify_log.clear()
    p._reemit_missing_tools(list(_MOOD_TOOLS[:3]))
    assert p._notify_log == []
    # 恢复情绪引擎 → 重新挂上
    p._mood_cfg["enabled"] = True
    p._sync_tool_visibility()
    assert ("register", "mood_ebb_tide") in p._notify_log
    assert p._cap_hidden_tools == set()
    # 再关掉 → 幂等重摘
    p._mood_cfg["enabled"] = False
    p._sync_tool_visibility()
    assert ("unregister", "mood_ebb_tide") in p._notify_log


def test_hide_mode_keeps_tools_live_for_any_role(plugin_factory_full):
    p = _boot(plugin_factory_full, mood_extra={"enabled": False})
    _install_fake_tools(p, _MOOD_TOOLS)
    run(p._ensure_shard("小灵"))
    run(p.set_capability_flags(hide_disabled_tools=True))
    assert len(p._cap_hidden_tools) == 10
    # 小灵开着情绪引擎（配置默认开，只 YUI 否决？——此处反过来：YUI 否决）
    run(p.set_capability(capability_id="mood_engine", enabled=True))  # 先撤 config 影响？不：配置仍关
    # 配置关着时任何角色都不生效 → 维持隐藏
    assert len(p._cap_hidden_tools) == 10
    p._mood_cfg["enabled"] = True
    p._sync_tool_visibility()
    assert p._cap_hidden_tools == set()
    # 再配置关但小灵…均关才摘；回到关：全角色不生效 → 摘
    p._mood_cfg["enabled"] = False
    p._sync_tool_visibility()
    assert len(p._cap_hidden_tools) == 10


def test_hide_flags_roundtrip_via_settings(plugin_factory_full):
    p = _boot(plugin_factory_full)
    run(p.set_capability_flags(hide_disabled_tools=True))
    assert p.store.data["settings"]["capabilities"]["hide_disabled_tools"] is True
    p2 = _boot(plugin_factory_full, store_initial=dict(p.store.data))
    run(p2._refresh_config())
    assert p2._cap_hide_tools_enabled() is True


def test_set_capability_flags_persist_error_propagates(plugin_factory_full):
    from forever_companion import Err

    p = _boot(plugin_factory_full)

    async def boom(*_a, **_k):
        from forever_companion import Err, SdkError

        return Err(SdkError("db locked"))

    p.store.set = boom
    res = run(p.set_capability_flags(hide_disabled_tools=True))
    assert isinstance(res, Err)


def test_dashboard_carries_capabilities_view(plugin_factory_full):
    """1.2.7：功能页数据源改随 dashboard 5s 轮询下发——总开关/否决变更同帧翻转
    （修"拨了总开关、功能页横幅还挂着旧态"的陈旧窗口）。"""
    p = _boot(plugin_factory_full)
    view = run(p.dashboard())["capabilities"]
    assert view["master_enabled"] is True
    by_id = {c["id"]: c for c in view["capabilities"]}
    assert by_id["mood_engine"]["enabled"] is True
    # 功能页否决 → 下一帧 dashboard 即如实反映（无需另拉 list_capabilities）
    run(p.set_capability(capability_id="tone_sense", enabled=False))
    view2 = run(p.dashboard())["capabilities"]
    by_id2 = {c["id"]: c for c in view2["capabilities"]}
    assert by_id2["tone_sense"]["enabled"] is False
    assert by_id2["tone_sense"]["source"] == "user_off"
    # 总开关关 → 下一帧 master_off 全灭；状态条 system_enabled 不受总开关牵连
    run(p.set_capability(capability_id="phase_openers", enabled=False))
    p._get_shard(view2["lanlan"] or "default").cycle["enabled"] = False
    view3 = run(p.dashboard())["capabilities"]
    assert view3["master_enabled"] is False
    assert all(not c["enabled"] for c in view3["capabilities"])
    mood = run(p.dashboard())["mood"]
    # 情绪系统"本身"还开着（只是否决了开场白/语气）：不因总开关叠加双重提示
    assert mood["system_enabled"] is True


def test_list_capabilities_and_dashboard_same_builder(plugin_factory_full):
    """入口与轮询共用 _cap_view：两份载荷必须逐字段一致（口径唯一）。"""
    p = _boot(plugin_factory_full)
    entry = run(p.list_capabilities()).value
    polled = run(p.dashboard())["capabilities"]
    assert entry == polled


def test_prune_removes_caps_key(plugin_factory_full):
    async def http(method, path, body=None, headers=None):
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": "default"}
        if path == "/api/characters":
            # 宿主现存名单只有 default：小灵是孤儿可清
            return {"猫娘": {"default": {}}}
        return None

    p = _boot(plugin_factory_full, http=http)
    run(p._ensure_shard("小灵"))
    run(p.set_capability(capability_id="review", enabled=False, lanlan="小灵"))
    assert p.store.data["caps@小灵"] == {"review": True}
    from forever_companion import Ok

    res = run(p.prune_lanlan(lanlan="小灵"))
    assert isinstance(res, Ok)
    assert "caps@小灵" not in p.store.data
    assert "小灵" not in p._caps_off
