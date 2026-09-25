"""1.3.0 「我的日记」档案室（review_archive@）+ 素材快照回归——藏书阁同款纪律。

锁死五条纪律：
1. 成文路径：活架攒满 52 篇后 append_review 带出的淘汰卷必须搬进
   review_archive@<角色>（只追加、不入 review@ blob）；
2. 载入路径：_save_shard_review 兜住一切截断（磁盘残留/调试注入超长），
   截掉的溢出卷同样入阁；阁内再满才从最旧一卷真删；
3. 双通道不串架：get_review(scope=archive) 与 get_review_archive 同数据，
   缺省仍翻活架；
4. 素材快照进卷宗：review_record 固化 tone/mood_avg/quotes（stats 清零后
   卷宗页仍能回看「本卷依据」）；
5. review_archive_brief 只显存储里现成的事实，不派生会随淘汰平移的序号。

运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


def run(coro):
    return asyncio.run(coro)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _reviews(count: int, *, start_i: int = 0) -> list[dict]:
    return [
        {
            "ts": (NOW + timedelta(days=i)).isoformat(timespec="seconds"),
            "turns": 10 + i,
            "span": "",
            "self_action_count": 0,
            "text": f"卷文{i}",
        }
        for i in range(start_i, start_i + count)
    ]


# ---------------------------------------------------------------------------
# 1. 成文路径：满架追加 → 淘汰卷入阁并落盘
# ---------------------------------------------------------------------------


def _stub_compose(p, monkey, text="他最近很温柔。"):
    monkey.setattr(
        p, "_resolve_tone_slot",
        lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"},
    )
    monkey.setattr(p, "_post_chat_completion", lambda *a, **k: text)


def _fill_stats(p, tm, turns: int):
    shard = p._get_shard("default")
    stats = tm.new_stats()
    for _ in range(turns):
        stats = tm.record_turn(stats)
    shard.review_stats = stats
    return shard


def test_compose_eviction_archives_oldest_review(tm, plugin_factory) -> None:
    p = plugin_factory()
    shard = _fill_stats(p, tm, 60)
    # 活架先装满 52 卷
    shard.review = _reviews(tm._REVIEW_MAX_ENTRIES)
    monkey = pytest.MonkeyPatch()
    try:
        _stub_compose(p, monkey)
        written, reason = run(p._maybe_write_review("default", shard))
        assert written is True, reason
        assert len(shard.review) == tm._REVIEW_MAX_ENTRIES, "活架不涨"
        # 被淘汰的最旧一卷没有消失：进阁 + 落盘（独立 key）
        assert len(shard.review_archive) == 1
        assert shard.review_archive[0]["text"] == "卷文0"
        assert p.store.data["review_archive@default"][0]["text"] == "卷文0"
        # 当前 blob 不含阁卷：两本分开，成文热路径不重写阁
        saved_texts = [e["text"] for e in p.store.data["review@default"]["entries"]]
        assert "卷文0" not in saved_texts and "他最近很温柔。" in saved_texts
    finally:
        monkey.undo()


def test_compose_without_eviction_skips_archive_write(tm, plugin_factory) -> None:
    """零淘汰的常态成文不多花一次存储写：archive key 不被触碰。"""
    p = plugin_factory()
    shard = _fill_stats(p, tm, 60)
    shard.review = _reviews(3)
    monkey = pytest.MonkeyPatch()
    try:
        _stub_compose(p, monkey)
        written, _reason = run(p._maybe_write_review("default", shard))
        assert written is True
        assert "review_archive@default" not in p.store.data
    finally:
        monkey.undo()


# ---------------------------------------------------------------------------
# 2. 载入/保存路径：溢出截断入阁 + 阁满裁旧 + 重开回读
# ---------------------------------------------------------------------------


def test_save_shard_review_archives_overflow(tm, plugin_factory) -> None:
    p = plugin_factory()
    shard = p._get_shard("灵")
    shard.review = _reviews(55)
    res = run(p._save_shard_review("灵", shard))
    assert isinstance(res, tm.Ok), res
    assert len(shard.review) == 52
    assert [e["text"] for e in shard.review_archive] == ["卷文0", "卷文1", "卷文2"]
    assert len(p.store.data["review@灵"]["entries"]) == 52
    assert len(p.store.data["review_archive@灵"]) == 3


def test_review_archive_cap_drops_oldest(tm, plugin_factory) -> None:
    p = plugin_factory()
    shard = p._get_shard("灵")
    shard.review_archive = _reviews(100)
    run(p._append_review_archive("灵", shard, _reviews(6, start_i=100)))
    archive = shard.review_archive
    assert len(archive) == tm._REVIEW_ARCHIVE_MAX_ENTRIES
    assert archive[0]["text"] == "卷文2", "106 进 104 出：最旧两卷被裁"
    assert archive[-1]["text"] == "卷文105", "新入阁卷永远在末尾"


def test_review_archive_loaded_with_shard(plugin_factory) -> None:
    """重开（新实例）后档案室原样回来：加载路径读 review_archive@。"""
    p = plugin_factory()
    shard = p._get_shard("灵")
    shard.review_archive = _reviews(2)
    run(p._store_write("review_archive@灵", list(shard.review_archive), "seed"))
    initial = dict(p.store.data)
    p2 = plugin_factory()
    p2.store.data.update(initial)
    shard2 = p2._get_shard("灵")
    shard2.loaded = False
    run(p2._ensure_shard("灵"))
    assert [e["text"] for e in shard2.review_archive] == ["卷文0", "卷文1"]


# ---------------------------------------------------------------------------
# 3. 双通道不串架 + 清空/角色清理覆盖档案室
# ---------------------------------------------------------------------------


def test_get_review_scope_archive_channel(tm, plugin_factory_full) -> None:
    p = plugin_factory_full(current_lanlan="灵")
    shard = p._get_shard("灵")
    shard.review = _reviews(2)
    run(p._append_review_archive("灵", shard, _reviews(1)))
    res = run(p.get_review(scope="archive"))
    assert isinstance(res, tm.Ok), res
    assert res.value["scope"] == "archive"
    assert [e["text"] for e in res.value["entries"]] == ["卷文0"]
    # 缺省仍翻活架，不串数据（倒序：最新在前）
    res2 = run(p.get_review())
    assert res2.value["scope"] == "shelf"
    assert [e["text"] for e in res2.value["entries"]] == ["卷文1", "卷文0"]
    # 规范入口与 scope 通道同数据
    res3 = run(p.get_review_archive())
    assert [e["text"] for e in res3.value["entries"]] == ["卷文0"]


def test_clear_review_also_clears_archive(tm, plugin_factory) -> None:
    p = plugin_factory()
    shard = p._get_shard("default")
    shard.review = _reviews(2)
    run(p._append_review_archive("default", shard, _reviews(3)))
    res = run(p.clear_review())
    assert isinstance(res, tm.Ok), res
    assert res.value["cleared"] == 2
    assert res.value["archive_cleared"] == 3
    assert shard.review == [] and shard.review_archive == []
    assert p.store.data["review_archive@default"] == []


def test_prune_removes_review_archive_key(tm) -> None:
    """角色清理 key 清单必须含档案室键——从源头钉死，不留孤儿卷。"""
    import inspect

    from forever_companion import ForeverCompanionPlugin

    source = inspect.getsource(ForeverCompanionPlugin.prune_lanlan)
    assert "_review_archive_key(name)" in source


# ---------------------------------------------------------------------------
# 4. 素材快照进卷宗 + 进度双门槛 + brief 只显事实
# ---------------------------------------------------------------------------


def test_review_record_snapshots_material(tm) -> None:
    stats = tm.new_stats()
    stats = tm.record_turn(stats, valence=0.4)
    stats = tm.record_turn(stats, valence=-0.2)
    stats = tm.record_tone(stats, "happy")
    stats = tm.record_tone(stats, "happy")
    stats = tm.record_tone(stats, "sad")
    stats = tm.record_fragment(stats, "like", "「" + "很长的一句话" * 20 + "」")
    stats = tm.record_fragment(stats, "important", "下周体检记得早睡")
    rec = tm.review_record(NOW.isoformat(timespec="seconds"), stats, "正文")
    # 语气分布：只留计数>0，按计数降序
    assert list(rec["tone"].items()) == [("happy", 2), ("sad", 1)]
    # 心情均值：与 prompt 素材块同款口径
    assert rec["mood_avg"] == 0.1
    # 原话摘录：至多 6 条、单条截 40 字
    assert len(rec["quotes"]) == 2
    assert rec["quotes"][0]["kind"] == "like"
    assert len(rec["quotes"][0]["quote"]) <= 40
    assert rec["quotes"][1]["quote"] == "下周体检记得早睡"


def test_get_review_progress_has_days_dimension(tm, plugin_factory) -> None:
    p = plugin_factory()
    shard = p._get_shard("default")
    stats = tm.new_stats()
    # 起点回拨 9 天（真实时钟）、只攒 3 轮：轮数门槛未到、天数门槛已过
    stats = tm.record_turn(stats, now=tm._now_utc() - timedelta(days=9))
    shard.review_stats = stats
    res = run(p.get_review())
    prog = res.value["progress"]
    assert prog["days"] == 9
    assert prog["days_threshold"] == 7
    assert prog["due"] is True
    assert prog["due_reason"] == "due_days"


def test_review_elapsed_days_bad_data(tm) -> None:
    assert tm.elapsed_days(tm.new_stats()) == 0
    assert tm.elapsed_days({"started_at": "not-a-date"}) == 0
    now = tm._now_utc()
    started = (now - timedelta(days=3, hours=1)).isoformat(timespec="seconds")
    assert tm.elapsed_days({"started_at": started}, now=now) == 3


def test_review_archive_brief_reports_facts_only(tm) -> None:
    empty = tm.review_archive_brief([])
    assert empty == {"entries": 0}
    items = _reviews(3)
    brief = tm.review_archive_brief(items)
    assert brief["entries"] == 3
    assert brief["first_ts"] == items[0]["ts"]
    assert brief["last_ts"] == items[-1]["ts"]


def test_dashboard_carries_review_archive_brief(tm, plugin_factory) -> None:
    p = plugin_factory()
    shard = run(p._ensure_shard("default"))
    shard.review_archive = _reviews(2)
    dash = run(p.dashboard())
    brief = dash["review_archive_brief"]
    assert brief["entries"] == 2
    assert brief["first_ts"] == _reviews(1)[0]["ts"]


# ---------------------------------------------------------------------------
# 5. 调试注入：超 52 卷走真实落盘链入阁，restore 连档案室一并回滚
# ---------------------------------------------------------------------------


def test_debug_review_fill_overflow_archives_and_restores(tm, plugin_factory) -> None:
    p = plugin_factory()
    shard = p._get_shard("灵")
    shard.review = _reviews(1)  # 垫一卷"真实"卷宗
    shard.review_stats = {"turns": 9}
    res = run(p._debug_review_fill(entries=55, lanlan="灵"))
    assert isinstance(res, tm.Ok), res
    assert res.value["entries_total"] == tm._REVIEW_MAX_ENTRIES, "活架封顶 52"
    assert res.value["archive_total"] == 3, "溢出的 3 卷搬进档案室"
    assert all(e.get("demo") for e in shard.review)
    assert all(e.get("demo") for e in shard.review_archive)
    assert len(p.store.data["review@灵"]["entries"]) == 52
    assert len(p.store.data["review_archive@灵"]) == 3

    res2 = run(p._debug_review_fill(restore=True, lanlan="灵"))
    assert isinstance(res2, tm.Ok), res2
    assert res2.value["restored"] is True
    assert [e["text"] for e in shard.review] == ["卷文0"], "真实卷宗原样回来"
    assert shard.review_archive == [], "注入期入阁的假卷随还原退场"
    assert p.store.data.get("pre_debug@review@灵") is None, "还原后备份作废"


# ---------------------------------------------------------------------------
# pytest.MonkeyPatch 直接复用顶部 import（与 test_review.py 同款姿势）
# ---------------------------------------------------------------------------
