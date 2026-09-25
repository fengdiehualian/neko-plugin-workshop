"""多角色（per-lanlan）分片状态测试（0.5.0 重构的核心语义）。

覆盖：两角色周期/情绪/手记隔离、旧版单份数据一次性迁移（含 proactive_prev
水位搬运与幂等）、per-shard interval_n 计数独立、push_message 定向
（target_lanlan）、主动搭话暂停的引用计数、LLM 工具的 _ctx.lanlan_name 归因、
面板自动跟随宿主当前角色（dashboard 的 lanlan / lanlan_list 字段）。

依赖 tests/conftest.py 的 ``plugin_factory_full`` fixture（可指定宿主 HTTP 桩
与 ctx._current_lanlan）。运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio


def run(coro):
    return asyncio.run(coro)


class _HostHttp:
    """宿主 HTTP 桩：current_catgirl 解析 + 主动搭话总开关读写 + 角色名单。

    known：宿主现存角色名单（["猫娘"] keys）；None = /api/characters 返回 None
    （模拟名单不可达，孤儿判定应按"未知"处理）。
    """

    def __init__(self, current="YUI", master=True, known=...):
        self.current = current
        self.master = master
        # 缺省（...）：名单可用且只含当前角色；显式 None 模拟不可达
        self.known = {current} if known is ... else known
        self.calls = []

    async def __call__(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body))
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": self.current}
        if path == "/api/characters":
            if self.known is None:
                return None
            return {"猫娘": {name: {} for name in self.known}}
        if path == "/api/proactive/settings" and method == "GET":
            return {"settings": {"proactiveChatEnabled": self.master}}
        if path == "/api/proactive/settings" and method == "POST":
            self.master = bool(body.get("proactiveChatEnabled"))
            return {"success": True}
        return None


def _pushes_of(p, message_type):
    return [m for m in p._pushed if m.get("metadata", {}).get("message_type") == f"forever_companion.{message_type}"]


# ---------- 两角色状态隔离 ----------


def test_two_lanlan_cycle_mood_diary_isolated(plugin_factory_full, tm) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI"))
    run(p._ensure_shard("小灵"))
    assert set(p._lanlan_index) == {"YUI", "小灵"}
    yui = p._get_shard("YUI")
    ling = p._get_shard("小灵")

    # 周期：快进天数各自独立，落盘 key 分开
    yui.cycle["advance_days"] = 3
    run(p._save_shard_cycle("YUI", yui))
    assert not ling.cycle.get("advance_days")
    assert p.store.data[tm._cycle_key("YUI")]["advance_days"] == 3
    assert not p.store.data.get(tm._cycle_key("小灵"), {}).get("advance_days")

    # 情绪：工具按 _ctx 归因，只动归属角色的 shard
    res = run(p.tool_cold_violence(minutes=30, reason="测试", _ctx={"lanlan_name": "YUI"}))
    assert getattr(res, "value", None)
    assert yui.mood.action == "ebb_tide"
    assert ling.mood.action == ""

    # 手记：写小灵的，不进 YUI 的；镜像推送定向到归属角色
    run(p.tool_write_diary(entry="小灵的手记", mood="开心", _ctx={"lanlan_name": "小灵"}))
    assert len(ling.diary) == 1
    assert len(yui.diary) == 0
    assert p.store.data[tm._diary_key("小灵")][0]["entry"] == "小灵的手记"
    mirror = _pushes_of(p, "diary_entry")
    assert mirror and mirror[-1].get("target_lanlan") == "小灵"

    # 开关：关 YUI（面板入口跟随宿主当前角色，HTTP 桩解析为 YUI）不影响小灵
    run(p.toggle())
    assert p._enabled(yui) is False
    assert p._enabled(ling) is True


# ---------- 快进与潮汐首日（锚点）互相干扰回归 ----------


def test_set_anchor_clears_advance_days(plugin_factory_full, tm) -> None:
    """快进后再设首日：首日必须落在所设日期上，快进天数清零。

    1.1.3 的 bug：set_anchor 只改锚点不清 advance_days，而阶段计算用
    effective = today + advance_days 与 anchor 比对，日历上的首日标记
    因此落在「所设日期 - 快进天数」，用户看到的就是"刚设的首日也被
    快进了一天"。
    """
    p = plugin_factory_full(http=_HostHttp(current="YUI"))
    shard = p._get_shard("YUI")
    shard.cycle["advance_days"] = 1
    run(p._save_shard_cycle("YUI", shard))

    res = run(p.set_anchor(date="2026-09-03"))
    value = getattr(res, "value", None)
    assert value and value["anchor_date"] == "2026-09-03"
    assert value["advance_days"] == 0
    # 落盘数据同步清零，锚点就是所设日期本身
    saved = p.store.data[tm._cycle_key("YUI")]
    assert saved["anchor_date"] == "2026-09-03"
    assert not saved.get("advance_days")
    assert shard.cycle["advance_days"] == 0


def test_advance_after_set_anchor_still_shifts_clock(plugin_factory_full) -> None:
    """清零不伤快进本身：重设首日之后再快进，身体时钟照常前移。"""
    p = plugin_factory_full(http=_HostHttp(current="YUI"))
    run(p.set_anchor(date="2026-09-03"))
    res = run(p.advance_days_entry(days=1))
    value = getattr(res, "value", None)
    assert value and value["advance_days"] == 1


# ---------- 旧版单角色数据迁移 ----------


def test_legacy_state_migrates_to_current_lanlan(plugin_factory_full, tm) -> None:
    legacy_cycle = {
        "enabled": True,
        "anchor_date": "2026-08-01",
        "advance_days": 2,
        "phase_seen": "follicular",
        "settings": {"tide": {"inject_mode": "interval_n"}, "mood": {"enabled": True}},
    }
    legacy_mood = {"action": "ebb_tide", "reason": "旧情绪", "started_at": 1.0, "expires_at": 10.0**12}
    legacy_diary = [{"ts": "2026-08-01T00:00:00+00:00", "mood": "委屈", "entry": "旧手记"}]
    # 宿主 HTTP 不可达（http=None 默认桩），经 ctx._current_lanlan 回落解析当前角色
    p = plugin_factory_full(
        current_lanlan="小灵",
        store_initial={
            "cycle_state": legacy_cycle,
            "mood_state": legacy_mood,
            "mood_diary": legacy_diary,
        },
    )
    shard = p._get_shard("小灵")
    assert shard.cycle["anchor_date"] == "2026-08-01"
    assert shard.cycle["advance_days"] == 2
    assert shard.cycle["enabled"] is True
    assert shard.mood.action == "ebb_tide"
    assert shard.mood.reason == "旧情绪"
    assert shard.diary[0]["entry"] == "旧手记"
    # settings 抽进全局覆盖层
    assert p._settings_override["tide"]["inject_mode"] == "interval_n"
    assert p.store.data[tm._STORE_SETTINGS]["mood"]["enabled"] is True
    assert p._lanlan_index == ["小灵"]
    # 新 key 已落盘；旧 key 保留作备份、内容不变
    assert p.store.data[tm._cycle_key("小灵")]["anchor_date"] == "2026-08-01"
    assert p.store.data["cycle_state"]["anchor_date"] == "2026-08-01"
    assert p.store.data["mood_diary"][0]["entry"] == "旧手记"
    # 幂等：再次载入不重复迁移、不丢数据
    run(p._load_state())
    assert p._lanlan_index == ["小灵"]
    assert p._get_shard("小灵").cycle["advance_days"] == 2


def test_legacy_proactive_prev_migrates_to_proactive_state(plugin_factory_full, tm) -> None:
    """旧 mood_state 带 proactive_prev 说明升级瞬间正处于"情绪暂停主动搭话"中：
    水位必须搬进 proactive_state，否则升级后总开关永远卡死。"""
    legacy_mood = {
        "action": "ebb_tide",
        "reason": "",
        "started_at": 1.0,
        "expires_at": 10.0**12,
        "proactive_prev": {"master": True},
    }
    p = plugin_factory_full(
        current_lanlan="YUI",
        store_initial={
            "cycle_state": {"enabled": True, "anchor_date": "2026-08-01"},
            "mood_state": legacy_mood,
        },
    )
    assert p._proactive_state["prev"] == {"master": True}
    assert p._proactive_state["paused_by"] == ["YUI"]
    assert p.store.data[tm._STORE_PROACTIVE]["prev"] == {"master": True}
    # _MoodState 不再携带 proactive_prev 字段
    assert not hasattr(p._get_shard("YUI").mood, "proactive_prev")


def test_fresh_install_writes_no_migration(plugin_factory_full, tm) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI"))
    # 全新安装：无旧 key、无 settings 残留，索引只含当前角色
    assert "cycle_state" not in p.store.data
    assert p._settings_override == {}
    assert p._lanlan_index == ["YUI"]


# ---------- per-shard interval_n 计数独立 ----------


def test_interval_n_counters_are_per_shard(plugin_factory_full) -> None:
    p = plugin_factory_full(
        http=_HostHttp(current="YUI"),
        tide_extra={"inject_mode": "interval_n", "inject_interval_n": 2},
    )
    assert run(p._handle_new_user_message(1.0, "早", "YUI")) is False      # YUI count=1
    assert run(p._handle_new_user_message(2.0, "在吗", "小灵")) is False   # 小灵 count=1（独立计）
    assert run(p._handle_new_user_message(3.0, "第二句", "YUI")) is True   # YUI count=2 → 注入并清零
    yui = p._get_shard("YUI")
    ling = p._get_shard("小灵")
    assert yui.user_message_count_since_inject == 0
    assert ling.user_message_count_since_inject == 1
    assert run(p._handle_new_user_message(4.0, "第二句", "小灵")) is True
    assert ling.user_message_count_since_inject == 0
    # 水位也各自独立：YUI 已推进到 3.0，小灵只到 4.0，互不回退
    assert yui.last_injected_message_ts == 3.0
    assert ling.last_injected_message_ts == 4.0


# ---------- push_message 定向（target_lanlan） ----------


def test_body_whisper_and_mood_instruction_carry_target_lanlan(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI"))
    run(p._handle_new_user_message(1.0, "你好", "YUI"))  # every_user_message → 立即注入
    whispers = _pushes_of(p, "body_whisper")
    assert whispers and all(m.get("target_lanlan") == "YUI" for m in whispers)

    run(p.tool_cold_violence(minutes=30, _ctx={"lanlan_name": "小灵"}))
    instr = _pushes_of(p, "mood_instruction")
    assert instr and instr[-1].get("target_lanlan") == "小灵"


def test_recovery_line_targets_owning_lanlan(plugin_factory_full, tm, monkeypatch) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI"))
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    run(p._apply_mood_action(action="ebb_tide", minutes=20, reason="", timed=True, lanlan="小灵"))
    now[0] += 21 * 60  # 到期
    run(p._supervise_once())
    assert p._get_shard("小灵").mood.action == ""
    recovery = _pushes_of(p, "mood_recovered")
    assert len(recovery) == 1
    assert recovery[0].get("target_lanlan") == "小灵"


def test_reconcile_nudge_targets_owning_lanlan(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", master=False))
    run(p.set_mood(action="shallow_reef", minutes=30, _ctx={"lanlan_name": "小灵"}))
    n0 = len(p._pushed)
    run(p._maybe_nudge_reconcile("对不起嘛，别生气了", "小灵"))
    nudges = _pushes_of(p, "reconcile_nudge")[0:]
    assert len(nudges) == 1
    assert nudges[0].get("target_lanlan") == "小灵"
    assert len(p._pushed) > n0


# ---------- 主动搭话引用计数 ----------


def test_proactive_refcount_pause_and_resume_single_lanlan(plugin_factory_full) -> None:
    http = _HostHttp(current="YUI", master=True)
    p = plugin_factory_full(http=http)
    run(p.set_mood(action="ebb_tide", minutes=30, _ctx={"lanlan_name": "YUI"}))
    assert http.master is False  # 暂停
    assert p._proactive_state["prev"] == {"master": True}
    assert p._proactive_state["paused_by"] == ["YUI"]
    run(p.tool_feeling_better(_ctx={"lanlan_name": "YUI"}))
    assert http.master is True  # 全部解除 → 恢复原值
    assert p._proactive_state["prev"] is None
    assert p._proactive_state["paused_by"] == []


def test_proactive_refcount_two_lanlan_partial_release_stays_paused(plugin_factory_full) -> None:
    http = _HostHttp(current="YUI", master=True)
    p = plugin_factory_full(http=http)
    run(p.set_mood(action="ebb_tide", minutes=30, _ctx={"lanlan_name": "YUI"}))
    run(p.set_mood(action="sea_fog", minutes=30, _ctx={"lanlan_name": "小灵"}))
    assert sorted(p._proactive_state["paused_by"]) == ["YUI", "小灵"]
    assert http.master is False
    posts = len([c for c in http.calls if c[0] == "POST"])

    # A 解除：B 仍有生效情绪 → 仍暂停，且不重复写开关
    run(p.tool_feeling_better(_ctx={"lanlan_name": "YUI"}))
    assert p._proactive_state["paused_by"] == ["小灵"]
    assert http.master is False
    assert len([c for c in http.calls if c[0] == "POST"]) == posts

    # B 也解除 → 恢复
    run(p.tool_feeling_better(_ctx={"lanlan_name": "小灵"}))
    assert http.master is True
    assert p._proactive_state["prev"] is None
    assert p._proactive_state["paused_by"] == []


# ---------- LLM 工具 _ctx 归因 ----------


def test_llm_tools_attribute_by_ctx_lanlan_name(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI"))
    run(p.tool_want_comfort(reason="委屈", _ctx={"lanlan_name": "小灵"}))
    assert p._get_shard("小灵").mood.action == "seek_harbor"
    assert p._get_shard("YUI").mood.action == ""
    # 无 _ctx：归因宿主当前角色（HTTP 权威解析 = YUI）
    run(p.tool_perfunctory(minutes=5))
    assert p._get_shard("YUI").mood.action == "shallow_reef"


# ---------- 面板跟随宿主当前角色 / 设置拆分 ----------


def test_dashboard_follows_current_lanlan(plugin_factory_full) -> None:
    http = _HostHttp(current="YUI")
    p = plugin_factory_full(http=http)
    run(p._ensure_shard("小灵"))  # 已知角色进只读列表，但面板不看它
    ctx = run(p.dashboard())
    assert ctx["lanlan"] == "YUI"
    assert "is_current" not in ctx  # 无手动选中态，不再返回该字段
    assert {item["name"] for item in ctx["lanlan_list"]} == {"YUI", "小灵"}

    # 宿主切卡后面板自动跟上（清掉 15s 解析缓存模拟下一次轮询）
    http.current = "小灵"
    p._current_lanlan_cache = ("", -1000.0)
    ctx2 = run(p.dashboard())
    assert ctx2["lanlan"] == "小灵"
    assert "is_current" not in ctx2

    # 无 select_lanlan 入口（手动切换已取消）
    assert not hasattr(p, "select_lanlan")


def test_update_settings_splits_per_char_and_global(plugin_factory_full, tm) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI"))
    res = run(p.update_settings(cycle_length=30, inject_interval_n=5))
    assert getattr(res, "value", None)
    yui = p._get_shard("YUI")
    # per-character 字段进 shard.params，全局字段进 settings 覆盖层
    assert yui.cycle["params"]["cycle_length"] == 30
    assert p._settings_override["tide"]["inject_interval_n"] == 5
    assert p._tide_cfg["inject_interval_n"] == 5
    assert p.store.data[tm._STORE_SETTINGS]["tide"]["inject_interval_n"] == 5
    # 其他角色不受影响
    ling = run(p._ensure_shard("小灵"))
    assert not ling.cycle.get("params")
    # enabled 走 shard 且 prime 注入定向
    run(p.update_settings(enabled=False))
    assert yui.cycle["enabled"] is False
    assert p._enabled(yui) is False
    run(p.update_settings(enabled=True))
    whispers = _pushes_of(p, "body_whisper")
    assert whispers and whispers[-1].get("target_lanlan") == "YUI"


# ---------- 角色数据轻量管理：摘要列表 / 孤儿标注 / 清除孤儿 ----------


def _summary_of(ctx, name):
    return next(item for item in ctx["lanlan_list"] if item["name"] == name)


def test_dashboard_lanlan_list_summary_and_orphan(plugin_factory_full, tm) -> None:
    """lanlan_list 升级为摘要对象列表；在索引但不在宿主名单的角色标 orphan=True。"""
    http = _HostHttp(current="YUI", known={"YUI", "小灵"})
    p = plugin_factory_full(http=http)
    run(p._ensure_shard("小灵"))
    run(p._ensure_shard("幽灵"))  # 已从宿主删除的残留角色
    run(p.tool_cold_violence(minutes=30, _ctx={"lanlan_name": "小灵"}))

    ctx = run(p.dashboard())
    yui = _summary_of(ctx, "YUI")
    assert yui["enabled"] is True
    assert yui["phase"] in ("menstrual", "follicular", "ovulatory", "luteal", "before_start")
    assert yui["mood_active"] is False
    assert yui["orphan"] is False
    ling = _summary_of(ctx, "小灵")
    assert ling["mood_active"] is True
    assert ling["orphan"] is False
    ghost = _summary_of(ctx, "幽灵")
    assert ghost["orphan"] is True
    assert ghost["enabled"] is True  # 无 shard 覆写时回落全局 enabled

    # 名单 60s 缓存：两次面板轮询只打一次 /api/characters
    run(p.dashboard())
    chars_calls = [c for c in http.calls if c[1] == "/api/characters"]
    assert len(chars_calls) == 1


def test_dashboard_orphan_unknown_when_roster_unavailable(plugin_factory_full) -> None:
    """宿主名单不可达（None=未知）时一律标 False，绝不可误标孤儿。"""
    p = plugin_factory_full(http=_HostHttp(current="YUI", known=None))
    run(p._ensure_shard("小灵"))
    ctx = run(p.dashboard())
    assert all(item["orphan"] is False for item in ctx["lanlan_list"])


def _make_orphan(p, tm, name="幽灵"):
    """造一个带完整残留数据的孤儿 shard（索引 + 三个 Store key + 内存 shard）。"""
    shard = run(p._ensure_shard(name))
    shard.cycle["advance_days"] = 2
    shard.mood.action = "ebb_tide"
    shard.diary.append({"ts": "t", "mood": "委屈", "entry": "残留手记"})
    run(p._save_shard_cycle(name, shard))
    run(p._save_shard_mood(name, shard))
    run(p._save_shard_diary(name, shard))
    return shard


def test_prune_lanlan_success(plugin_factory_full, tm) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", known={"YUI"}))
    _make_orphan(p, tm)

    res = run(p.prune_lanlan(lanlan="幽灵"))
    assert getattr(res, "value", None) == {"pruned": "幽灵"}
    # 三个分片 key 全部删除
    for key in (tm._cycle_key("幽灵"), tm._mood_key("幽灵"), tm._diary_key("幽灵")):
        assert key not in p.store.data
    # 索引移除并落盘；内存 shard 丢弃
    assert "幽灵" not in p._lanlan_index
    assert p.store.data[tm._STORE_LANLAN_INDEX] == ["YUI"]
    assert "幽灵" not in p._shards


def test_prune_lanlan_sweeps_debug_backups(plugin_factory_full, tm) -> None:
    """prune 必须扫掉调试注入备份：新键与 1.3.0 旧 `|pre-debug` 键名都算。

    1.3.0 第六轮之前 prune 完全不碰这些键——删掉角色后备份永久孤儿，且同名
    重建角色会被上一世的注入档污染（restore 还原出别人的日记）。
    """
    p = plugin_factory_full(http=_HostHttp(current="YUI", known={"YUI"}))
    _make_orphan(p, tm)
    ghost = "幽灵"
    for key in (
        tm._debug_backup_key("journal", ghost), tm._debug_backup_key("review", ghost),
        tm._debug_backup_key("stats", ghost),
        tm._legacy_debug_backup_key(tm._journal_key(ghost)),
    ):
        p.store.data[key] = {"journal": [], "archive": []}

    res = run(p.prune_lanlan(lanlan=ghost))
    assert getattr(res, "value", None) == {"pruned": ghost}
    for key in (
        tm._debug_backup_key("journal", ghost), tm._debug_backup_key("review", ghost),
        tm._debug_backup_key("stats", ghost),
        tm._legacy_debug_backup_key(tm._journal_key(ghost)),
        tm._legacy_debug_backup_key(tm._review_key(ghost)),
        tm._legacy_debug_backup_key(tm._stats_key(ghost)),
    ):
        assert key not in p.store.data, f"prune 后残留孤儿备份键：{key}"


def test_prune_lanlan_rejects_still_existing(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", known={"YUI", "小灵"}))
    run(p._ensure_shard("小灵"))
    res = run(p.prune_lanlan(lanlan="小灵"))
    assert getattr(res, "error", None) is not None
    assert "小灵" in p._lanlan_index
    assert "小灵" in p._shards


def test_prune_lanlan_rejects_when_roster_unavailable(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", known=None))
    run(p._ensure_shard("小灵"))
    res = run(p.prune_lanlan(lanlan="小灵"))
    assert getattr(res, "error", None) is not None
    assert "小灵" in p._lanlan_index


def test_prune_lanlan_rejects_current_lanlan(plugin_factory_full) -> None:
    # 当前角色恰为孤儿（宿主名单为空但 current_catgirl 仍是它）：仍拒绝清除
    p = plugin_factory_full(http=_HostHttp(current="YUI", known=set()))
    assert "YUI" in p._lanlan_index
    res = run(p.prune_lanlan(lanlan="YUI"))
    assert getattr(res, "error", None) is not None
    assert "YUI" in p._lanlan_index
    assert "YUI" in p._shards


def test_prune_lanlan_rejects_unknown_name(plugin_factory_full) -> None:
    p = plugin_factory_full(http=_HostHttp(current="YUI", known={"YUI"}))
    res = run(p.prune_lanlan(lanlan="不存在"))
    assert getattr(res, "error", None) is not None



# ---------- 随机化默认锚点（0.9.1） ----------





def test_fresh_lanlan_gets_randomized_luteal_anchor(plugin_factory_full, tm) -> None:

    """新角色无锚点时：默认锚点随机落在平稳期（不是今天=潮汐日第一天的旧默认）。"""

    p = plugin_factory_full(

        http=_HostHttp(current="YUI"),

        tide_extra={"anchor_date": ""},  # 配置层锚点留空 -> 走随机默认

    )

    shard = p._get_shard("YUI")

    anchor_raw = str(shard.cycle.get("anchor_date") or "").strip()

    assert anchor_raw, "randomized anchor should be assigned on refresh"

    from datetime import date as date_cls



    anchor = tm.parse_anchor_date(anchor_raw)

    state = p._current_phase_state(shard)

    assert state.phase == "luteal", f"fresh anchor landed on {state.phase}"

    assert state.days_until_next_period >= 3, "should keep tail days before next tide"

    # 锚点通常在过去（周期循环反推）；单极小概率同日也不该触发（luteal 已保证）

    assert anchor <= date_cls.today() or anchor_raw





def test_randomized_anchor_persists_across_refresh(plugin_factory_full, tm) -> None:

    """随机锚点固化：再次 refresh 不重新随机（重启语义）。"""

    p = plugin_factory_full(

        http=_HostHttp(current="YUI"),

        tide_extra={"anchor_date": ""},

    )

    shard = p._get_shard("YUI")

    first = str(shard.cycle.get("anchor_date") or "")

    assert first

    # 二次 refresh（模拟重启后的配置重载）：锚点已写入 shard.cycle，不再变化

    run(p._refresh_config())

    second = str(shard.cycle.get("anchor_date") or "")

    assert first == second

