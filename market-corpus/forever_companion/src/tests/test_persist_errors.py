"""1.2.2 批次2 回归：写失败传播（入口不再假报成功）与 stats/review 增量即时补刷。

背景：过去所有 _save_shard_* 把宿主 store.set 的真失败（磁盘满/DB 锁返回 Err）
吞成一条 warning、返回 None，调用方面板/工具一律回 Ok——"面板显示成功、盘上没写"；
而语气分布/情绪事件等 stats/review 增量只进内存、等"下一条用户消息"搭车落盘
（注释失真：mood 落盘根本不涵盖 stats@/review@ 两个 key）。批次2 之后：

1. 统一出口 _store_write：not-ready（Ok 空转）仍走"当场生效"降级契约（不算
   失败，批次1 测试锁死），真 Err 记 warning 并原样回传；
2. 用户可见写入口按 Result 传播 Err（persist failed: ...）；
3. _maybe_write_review 落盘失败整体回滚内存（篇目没进、素材没清零、可重试）；
4. tone/mood 动作/和好/first_diary 增量在产生点即时落盘，不再搭车；
5. store.get 返回 Err 与"值不存在"区分开，读失败留痕（行为仍按缺省降级）。

_ERR_STORE 用局部假 store 复刻真失败（指定 fail_keys 的 set/delete/get 返回
tm.Err(tm.SdkError("disk full"))），风格对齐 test_store_readiness._GatedStore：
Ok/Err 类必须用 tm 里插件 isinstance 用的那两个，否则结果判定全部失真。

运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio


def run(coro):
    return asyncio.run(coro)


class _ErrStore:
    """正常读写、对指定 key 注入"真失败"的假 store（不含 enabled 门控——那是批次1 的事）。"""

    def __init__(self, tm, initial=None, *, fail_set=(), fail_delete=(), fail_get=()):
        self._tm = tm
        self.data = dict(initial or {})
        self._fail_set = set(fail_set)
        self._fail_delete = set(fail_delete)
        self._fail_get = set(fail_get)

    async def get(self, key, default=None):
        if key in self._fail_get:
            return self._tm.Err(self._tm.SdkError("db locked"))
        return self._tm.Ok(self.data.get(key, default))

    async def set(self, key, value):
        if key in self._fail_set:
            return self._tm.Err(self._tm.SdkError("disk full"))
        self.data[key] = value
        return self._tm.Ok(None)

    async def delete(self, key):
        if key in self._fail_delete:
            return self._tm.Err(self._tm.SdkError("disk full"))
        return self._tm.Ok(self.data.pop(key, None) is not None)


class _RecLogger:
    """捕获 warning 的 logger 桩（E 项：读失败必须留痕，且与"值不存在"区分）。"""

    def __init__(self):
        self.warnings = []

    def warning(self, *args):
        self.warnings.append(" | ".join(str(a) for a in args))

    def info(self, *_args):
        pass

    debug = error = info


_ENABLED_CYCLE = {
    "enabled": True,
    "anchor_date": "2026-08-01",
    "advance_days": 0,
    "phase_seen": "luteal",
    "params": {},
}


def _boot(tm, boot_factory, store, *, current_lanlan="default", http=None):
    p = boot_factory(store=store, current_lanlan=current_lanlan, http=http)
    run(p.startup())
    return p


# ---------------------------------------------------------------------------
# 1. toggle：写失败向入口传播 Err，内存当场生效不变
# ---------------------------------------------------------------------------


def test_toggle_persist_error_propagates_but_memory_still_applies(tm, boot_factory):
    """cycle@ set 返回 Err → toggle 入口必须回 Err（过去只 warning、假报成功）。

    同时锁死降级契约的另一半：Err 不影响内存——"当场生效"仍成立（与未通电
    静默空转不同，这里是真失败，用户需要知道盘上没写）。回退修复时本测试以
    "入口返回 Ok"失败。
    """
    persisted = dict(_ENABLED_CYCLE)
    store = _ErrStore(
        tm, initial={"lanlan_index": ["灵"], "cycle@灵": persisted}, fail_set={"cycle@灵"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="灵")

    res = run(p.toggle())  # 开 → 关

    assert isinstance(res, tm.Err)
    # i18n 契约第九轮：入口只回稳定码，disk full 细节由 _store_write 统一出口进日志
    assert str(res.error) == "persist_failed"
    shard = p._get_shard("灵")
    assert p._enabled(shard) is False, "内存必须仍当场生效（Err 不回滚状态）"
    assert store.data["cycle@灵"] == persisted, "盘上必须原样（写失败没有半更新）"


# ---------------------------------------------------------------------------
# 2. _maybe_write_review：落盘失败回滚 + 可重试
# ---------------------------------------------------------------------------


def test_write_review_persist_failure_rolls_back_and_retries(tm, boot_factory, monkeypatch):
    """成文成功但 review@ 写失败 → (False, "persist_failed")，内存回滚到快照。

    回退修复（"先改内存、写完不看结果"）时本篇会永久丢失且素材被清零——
    本测试锁死：篇目没进内存、素材没清零、盘上仍是旧内容、first_review
    里程碑未记；写通道恢复后重试必须能成文（一次失败不永久卡死）。
    """
    old_entry = {"ts": "2026-01-01T00:00:00+00:00", "turns": 12, "text": "旧篇"}
    persisted = {"entries": [dict(old_entry)], "stats": {"turns": 2}}
    store = _ErrStore(
        tm, initial={"review@default": persisted}, fail_set={"review@default"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="default")
    shard = p._get_shard("default")
    stats = tm.new_stats()
    for _ in range(60):
        stats = tm.record_turn(stats)
    shard.review_stats = stats
    monkeypatch.setattr(
        p, "_resolve_tone_slot", lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"},
    )
    monkeypatch.setattr(p, "_post_chat_completion", lambda *a, **k: "他最近对她很温柔，几乎每天都来聊天。")

    written, reason = run(p._maybe_write_review("default", shard))

    assert written is False
    assert reason == "persist_failed"
    assert shard.review == [old_entry], "回滚必须还原篇目快照（新篇不能留在内存）"
    assert shard.review_stats["turns"] == 60, "素材清零必须撤销（否则这段素材永久丢失）"
    assert store.data["review@default"] == persisted, "盘上仍是旧内容"
    assert not shard.stats.get("milestones", {}).get("first_review"), "写失败不得记 first_review 里程碑"

    # 写通道恢复 → 重试成功（幂等不因失败永久卡死），这次里程碑也落盘
    store._fail_set.clear()
    written2, reason2 = run(p._maybe_write_review("default", shard))
    assert (written2, reason2) == (True, "written")
    assert len(shard.review) == 2
    assert store.data["review@default"]["entries"][1]["text"].startswith("他最近对她很温柔")
    assert store.data["stats@default"]["milestones"]["first_review"]


def test_write_review_now_queue_reports_persist_failed(tm, boot_factory, monkeypatch):
    """面板队列成文（1.2.3）：persist_failed 不再由入口回 Err——入口只做受理，
    失败结论经 tick 队列消费落到 review_write_result 回流面板（弹"没存住"错误、
    素材保留可重试）。1.2.2 的"如实上报、不假成功、不写脏盘"语义原样带进队列路径。"""
    store = _ErrStore(
        tm,
        initial={"review@default": {"entries": [], "stats": {"turns": 60}}},
        fail_set={"review@default"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="default")
    monkeypatch.setattr(
        p, "_resolve_tone_slot", lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"},
    )
    monkeypatch.setattr(p, "_post_chat_completion", lambda *a, **k: "这阵子他每天都来陪她。")

    res = run(p.write_review_now())
    assert isinstance(res, tm.Ok)
    assert res.value["accepted"] is True

    run(p._drain_pending_review_writes())
    shard = p._get_shard("default")
    result = shard.review_write_result
    assert result is not None and result["written"] is False
    assert result["reason"] == "persist_failed", "写失败必须如实进结果回流，不得假成功"
    assert store.data["review@default"]["entries"] == [], "失败不写脏盘"
    assert shard.review_stats["turns"] == 60, "素材清零随回滚撤销，留着可重试"


# ---------------------------------------------------------------------------
# 3. 情绪动作：一次调用后盘上 stats@/review@ 即含该事件（不依赖下一条消息）
# ---------------------------------------------------------------------------


def test_mood_action_persists_stats_and_review_at_once(plugin_factory, tm):
    """storm_surge 工具跑完后，盘上（不是内存）的 stats@/review@ 必须已带事件。

    过去 _apply_mood_action 只 _save_shard_mood，"随 mood 落盘"是失真注释——
    stats@/review@ 要等下一条用户消息（whisper）才冲刷，中途关软件即丢。
    回退批次2 时 stats@/review@ 根本不会出现在 store.data。
    """
    p = plugin_factory()
    res = run(p.tool_emotional_outburst(reason="积压太久了", minutes=5, _ctx={"lanlan_name": "default"}))
    assert isinstance(res, tm.Ok)

    disk_stats = p.store.data["stats@default"]
    # storm_surge 属重度负面：stats 按天聚合成 cold 计数（动作名本身不入库）；
    # 修复前这条新用户路径不会写 stats@，days 里根本没有今天的桶
    assert any(
        int(day.get("cold") or 0) >= 1 for day in disk_stats.get("days", {}).values()
    ), "情绪事件没随动作即时落 stats@"
    disk_review = p.store.data["review@default"]
    actions = disk_review.get("stats", {}).get("actions", [])
    assert any(item.get("action") == "storm_surge" for item in actions), "动作事件没随动作即时落 review@"


def test_mood_action_persist_failure_propagates_err(tm, boot_factory):
    """动作工具传播链：mood@ 写失败 → 工具回 Err（内存生效照常）。"""
    store = _ErrStore(
        tm, initial={"cycle@灵": dict(_ENABLED_CYCLE)}, fail_set={"mood@灵"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="灵")
    res = run(p.tool_cold_violence(minutes=10, reason="生气了", _ctx={"lanlan_name": "灵"}))
    assert isinstance(res, tm.Err)
    assert str(res.error) == "persist_failed"
    assert p._get_shard("灵").mood.action == "ebb_tide", "内存当场生效契约不变"


# ---------------------------------------------------------------------------
# 4. tone 喂入路径：语气分析收尾即落 stats@/review@（_save_tone_sense_state）
# ---------------------------------------------------------------------------


class _ToneHttp:
    """最小宿主桩：current_catgirl / recent_file / health(CSRF) / emotion.analysis。"""

    def __init__(self):
        self.turns = []

    def set_turns(self, pairs):
        self.turns = []
        for user_text, her_text in pairs:
            self.turns.append({"type": "human", "data": {"content": user_text}})
            self.turns.append({"type": "ai", "data": {"content": her_text}})

    def add_turn(self, user_text, her_text):
        self.turns.append({"type": "human", "data": {"content": user_text}})
        self.turns.append({"type": "ai", "data": {"content": her_text}})

    async def __call__(self, method, path, body=None, headers=None):
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": "灵"}
        if path.startswith("/api/memory/recent_file"):
            return {"content": list(self.turns), "fingerprint": str(len(self.turns))}
        if path == "/health":
            return {"instance_id": "test-token"}
        if path == "/api/emotion/analysis":
            return {"emotion": "happy", "confidence": 0.9}
        return None


def test_tone_analysis_flushes_stats_and_review_without_next_message(tm, boot_factory):
    """_maybe_tone_sense（_feed_stats_tone/_feed_review_tone 的真实驱动路径）跑完
    一轮分析后，盘上 stats@/review@ 必须已含本轮语气增量。

    过去这批增量要等"下一条用户消息"（whisper）冲刷——tick 之后、消息之前的
    窗口里内存与盘不一致。回退修复时 review@ 根本不在 store.data 里。
    """
    http = _ToneHttp()
    http.set_turns([("你好", "你好呀～")])
    store = _ErrStore(tm, initial={"cycle@灵": dict(_ENABLED_CYCLE)})
    p = _boot(tm, boot_factory, store, current_lanlan="灵", http=http)
    shard = run(p._ensure_shard("灵"))

    assert run(p._maybe_tone_sense("灵", shard)) is False  # 首趟只建基线，不分析
    http.add_turn("今天开不开心", "开心！ super好！")
    run(p._maybe_tone_sense("灵", shard))  # 新轮：分析 + 喂入 + 收尾落盘

    assert "happy" in str(store.data["stats@灵"]), "语气增量没随分析收尾落 stats@"
    saved_review = store.data["review@灵"]
    assert saved_review["stats"]["tone"].get("happy") == 1, "素材语气分布没即时落 review@"


# ---------------------------------------------------------------------------
# 5. first_diary 里程碑即时落盘（对齐 first_journal/first_review）
# ---------------------------------------------------------------------------


def test_first_diary_milestone_persisted_with_write(plugin_factory, tm):
    """tool_write_diary 一次调用后，盘上 stats@ 必须已含 milestones.first_diary。

    过去 first_diary 只写内存（first_journal/first_review 都显式落盘，唯独它
    漏了），"第一篇手记"徽章要等下一次任意 stats 写入才可见。
    """
    p = plugin_factory()
    res = run(p.tool_write_diary(entry="今天他给我带了奶茶", mood="开心", _ctx={"lanlan_name": "default"}))
    assert isinstance(res, tm.Ok)

    saved = p.store.data["stats@default"]
    assert saved.get("milestones", {}).get("first_diary"), "first_diary 没随手记落盘"
    assert p.store.data["diary@default"][-1]["entry"] == "今天他给我带了奶茶"


# ---------------------------------------------------------------------------
# 6. 其余入口：delete_diary_item / clear_review / prune_lanlan 的传播
# ---------------------------------------------------------------------------


def test_diary_delete_and_clear_review_propagate_persist_errors(tm, boot_factory):
    """碎片删除/清空我的日记：set 返回 Err → 入口 Err 且盘上数据原样。"""
    diary = [{"ts": "2026-08-10T12:00:00+00:00", "source": "auto", "kind": "like", "quote": "奶茶好喝"}]
    review = {"entries": [{"ts": "2026-01-01T00:00:00+00:00", "text": "旧篇"}], "stats": {"turns": 3}}
    store = _ErrStore(
        tm,
        initial={
            "lanlan_index": ["灵"], "cycle@灵": dict(_ENABLED_CYCLE),
            "diary@灵": diary, "review@灵": review,
        },
        fail_set={"diary@灵", "review@灵"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="灵")

    res = run(p.delete_diary_item(ts="2026-08-10T12:00:00+00:00"))
    assert isinstance(res, tm.Err)
    assert str(res.error) == "persist_failed"
    assert store.data["diary@灵"] == diary, "写失败盘上必须原样"
    assert p._get_shard("灵").diary == [], "内存当场生效（面板即时可见）"

    res2 = run(p.clear_review())
    assert isinstance(res2, tm.Err)
    assert store.data["review@灵"] == review, "写失败盘上必须原样"


def test_prune_lanlan_propagates_delete_failure(tm, boot_factory):
    """prune：store.delete 真失败必须传播 Err（过去只 warning、面板显示"已清除"）。"""

    async def http(method, path, body=None, headers=None):
        if path == "/api/characters/current_catgirl":
            return {"current_catgirl": "小可"}
        if path == "/api/characters":
            return {"猫娘": {"小可": {}}}
        return None

    store = _ErrStore(
        tm, initial={"lanlan_index": ["灵"], "cycle@灵": dict(_ENABLED_CYCLE)},
        fail_delete={"cycle@灵"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="小可", http=http)

    res = run(p.prune_lanlan(lanlan="灵"))

    assert isinstance(res, tm.Err)
    assert str(res.error) == "persist_failed"
    assert "cycle@灵" in store.data, "盘上残留的数据不能被谎称已删"


# ---------------------------------------------------------------------------
# E. 读侧 Err 日志（与"值不存在"区分）
# ---------------------------------------------------------------------------


def test_store_read_error_is_logged_and_degrades_to_default(tm, boot_factory):
    """store.get 返回 Err：必须留 warning（过去完全静默、表现为"数据全空"零线索），
    且行为不变——按缺省继续降级（cycle@ 读失败 → shard.cycle 为空）。"""
    store = _ErrStore(
        tm, initial={"lanlan_index": ["灵"], "cycle@灵": dict(_ENABLED_CYCLE)},
        fail_get={"cycle@灵"},
    )
    p = boot_factory(store=store, current_lanlan="灵")
    logger = _RecLogger()
    p.logger = logger
    run(p.startup())

    # logger 桩不展开 {} 模板，格式串与参数分列——按三段分别断言
    assert any(
        "store read failed" in w and "cycle@灵" in w and "db locked" in w
        for w in logger.warnings
    ), f"读失败必须与值缺失区分留痕，got: {logger.warnings}"
    shard_cycle = p._get_shard("灵").cycle
    # 读失败按缺省降级：盘上那份 cycle 没进来（startup 的 phase_seen 水位除外）
    assert not shard_cycle.get("anchor_date") and "enabled" not in shard_cycle
    # Ok(None)（值不存在）不得混进"读失败"日志
    assert not any("review@灵" in w for w in logger.warnings)


# ---------------------------------------------------------------------------
# 7. 调试注入备份（stats 入口）：读不出来必须中止；旧键名备份必须还能还原
# ---------------------------------------------------------------------------


def test_debug_seed_aborts_when_backup_state_unreadable(tm, boot_factory):
    """两个备份键名都读失败 → 中止注入，绝不覆盖真数据。

    1.3.0 第六轮：过去 seed 只在"备份写失败"时中止，读失败被当成"没有备份"
    继续注入——真数据被假页覆盖后 restore 找不到备份，等于静默丢日记。
    """
    real = dict(tm.new_stats())
    real["first_seen"] = "2026-01-01"
    store = _ErrStore(
        tm,
        initial={"lanlan_index": ["default"], "stats@default": real},
        fail_get={"pre_debug@stats@default", "stats@default|pre-debug"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="default")

    res = run(p._debug_stats(seed=True))

    assert isinstance(res, tm.Err)
    assert "备份状态读不出来" in str(res.error)
    assert store.data["stats@default"]["first_seen"] == "2026-01-01", "读不出来时盘上必须原样"
    assert p._get_shard("default").stats["first_seen"] == "2026-01-01", "内存也不许被假页覆盖"


def test_debug_restore_falls_back_to_legacy_backup_key(tm, boot_factory):
    """旧键名 `<数据键>|pre-debug` 的备份必须还能还原。

    1.3.0 的调试入口正在真机使用中：改键名若不同时保留旧键回退读位，
    "已注入、尚未还原"的升级现场会直接找不到备份——假页留在真 stats 上。
    """
    real = dict(tm.new_stats())
    real["first_seen"] = "2026-02-02"
    demo = dict(tm.new_stats())
    demo["first_seen"] = "2020-03-03"
    store = _ErrStore(
        tm,
        initial={
            "lanlan_index": ["default"],
            "stats@default": demo,
            "stats@default|pre-debug": real,  # 旧键名写入的现场
        },
    )
    p = _boot(tm, boot_factory, store, current_lanlan="default")

    res = run(p._debug_stats(restore=True))

    assert isinstance(res, tm.Ok), res
    assert res.value["restored"] is True
    assert p._get_shard("default").stats["first_seen"] == "2026-02-02"
    assert store.data["stats@default"]["first_seen"] == "2026-02-02"
    # 旧键一并清掉，不留孤儿
    assert "stats@default|pre-debug" not in store.data


# ---------------------------------------------------------------------------
# 8. 调试注入备份（journal 入口）：备份状态读不出来必须中止；旧键名备份必须仍能还原
# ---------------------------------------------------------------------------


def _real_journal_page(page_no: int = 1) -> dict:
    return {
        "page_no": page_no,
        "started_at": "2026-08-01T00:00:00+00:00",
        "entries": [{"ts": "2026-08-01T00:00:00+00:00", "text": "真实的一页"}],
    }


def test_debug_fill_aborts_when_backup_state_unreadable(tm, boot_factory):
    """两个备份键名都读失败 → 必须中止注入，绝不把真日记换成假页。

    1.3.0 第六轮收编：过去 seed 路径只在 `not isinstance(res, Err)` 时补备份，
    读失败等于"跳过备份"却**继续注入**——demo 页当场覆盖 journal@，restore
    再也找不到原数据。备份状态不可知时唯一安全的动作是不动。
    """
    real = [_real_journal_page()]
    store = _ErrStore(
        tm,
        initial={"lanlan_index": ["灵"], "cycle@灵": dict(_ENABLED_CYCLE), "journal@灵": real},
        fail_get={"pre_debug@journal@灵", "journal@灵|pre-debug"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="灵")

    res = run(p._debug_journal_fill(pages=3, lanlan="灵"))

    assert isinstance(res, tm.Err), "备份状态读不出来却仍然注入 = 真数据无从还原"
    assert "备份" in str(res.error)
    assert store.data["journal@灵"] == real, "中止后盘上必须原样"
    assert store.data["journal@灵"][0]["entries"][0]["text"] == "真实的一页"


def test_debug_fill_restore_reads_legacy_backup_key(tm, boot_factory):
    """旧键名 `<数据键>|pre-debug` 的备份必须还能还原（升级不丢现场）。

    1.3.0 的调试入口正在真机使用：有人已经 seed 过、还没 restore。若改键名后
    只扫新键，restore 会报"从未注入过"，把 52 页假数据永久留在真日记位上。
    """
    real = [_real_journal_page()]
    store = _ErrStore(
        tm,
        initial={
            "lanlan_index": ["灵"],
            "cycle@灵": dict(_ENABLED_CYCLE),
            "journal@灵": [_real_journal_page(i) for i in range(1, 53)],
            "journal@灵|pre-debug": {"journal": real, "archive": []},
        },
    )
    p = _boot(tm, boot_factory, store, current_lanlan="灵")

    res = run(p._debug_journal_fill(restore=True, lanlan="灵"))

    assert isinstance(res, tm.Ok), res
    assert res.value["restored"] is True, "旧键名的备份必须能被找到并还原"
    assert [pg["page_no"] for pg in p._get_shard("灵").journal] == [1]
    assert store.data["journal@灵"] == real, "还原必须写回盘"
    assert "journal@灵|pre-debug" not in store.data, "还原后旧键一并作废，不留孤儿"


def test_debug_clear_aborts_when_backup_state_unreadable(tm, boot_factory):
    """clear 档与注入档共用备份通道：备份状态读不出来时同样必须中止清空。

    十六轮 clear 档的差异化契约：清的是她手写的真内容，故与 debug_stats 的
    clear（不备份）不同、清空前必须先备份——备份不可知时唯一安全动作是不动。
    """
    real = [_real_journal_page()]
    store = _ErrStore(
        tm,
        initial={"lanlan_index": ["灵"], "cycle@灵": dict(_ENABLED_CYCLE), "journal@灵": real},
        fail_get={"pre_debug@journal@灵", "journal@灵|pre-debug"},
    )
    p = _boot(tm, boot_factory, store, current_lanlan="灵")

    res = run(p._debug_journal_fill(clear=True, lanlan="灵"))

    assert isinstance(res, tm.Err), "备份状态读不出来却仍然清空 = 真日记无从还原"
    assert "备份" in str(res.error)
    assert store.data["journal@灵"] == real, "中止后盘上必须原样"
