"""1.3.0 藏书阁（journal_archive@）回归：淘汰不再静默丢弃，而是搬进合订本。

锁死四条纪律：
1. 工具路径：写满活架后 journal_write 带出的 evicted 页必须落进
   journal_archive@<角色>（只追加、不入当前书 blob）；
2. 载入路径：_save_shard_journal 兜住一切截断（旧版磁盘残留 / 迁移超长），
   截掉的溢出页同样入阁；
3. 阁内再满才从最旧一页真删（翻阅按"距最近一本"倒计数，删旧不挪位）；
4. archive_brief 只显存储里现成的事实（页码/日期），不派生会随淘汰平移的序号。

运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio
from datetime import datetime, timedelta, timezone


def run(coro):
    return asyncio.run(coro)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _pages(count: int, *, start_no: int = 1) -> list[dict]:
    return [
        {
            "page_no": start_no + i,
            "started_at": (NOW + timedelta(days=i)).isoformat(timespec="seconds"),
            "entries": [{"ts": (NOW + timedelta(days=i)).isoformat(timespec="seconds"), "text": f"内容{i}"}],
        }
        for i in range(count)
    ]


def _write_page(p, tm, lanlan: str, text: str, *, new_page: bool = True):
    res = run(p.tool_write_journal(
        events=text, new_page=new_page, _ctx={"lanlan_name": lanlan},
    ))
    assert isinstance(res, tm.Ok), f"tool write failed: {res.error}"
    return res


# ---------------------------------------------------------------------------
# 1. 工具路径：写满活架 → 淘汰页入阁并落盘
# ---------------------------------------------------------------------------


def test_tool_eviction_archives_page(tm, plugin_factory):
    p = plugin_factory()
    lanlan = "灵"
    # 写满 52 页 + 再写 1 页：第 53 次写入把第 1 页挤下活架
    for i in range(53):
        _write_page(p, tm, lanlan, f"第{i}页的故事")
    shard = p._get_shard(lanlan)
    assert len(shard.journal) == 52
    assert shard.journal[0]["page_no"] == 2, "活架最旧应为第 2 页"
    # 被淘汰的第 1 页没有消失：进阁 + 落盘
    assert [pg["page_no"] for pg in shard.journal_archive] == [1]
    archived = shard.journal_archive[0]
    assert "第0页的故事" in archived["entries"][0]["text"]
    assert p.store.data.get("journal_archive@灵"), "合订本必须落盘（独立 key）"
    # 当前书 blob 不含阁页：两本分开，写热路径不重写阁
    assert all(pg["page_no"] != 1 for pg in p.store.data["journal@灵"])


def test_get_journal_archive_entry(tm, plugin_factory_full):
    p = plugin_factory_full(current_lanlan="灵")
    shard = p._get_shard("灵")
    shard.journal = _pages(2, start_no=1)
    run(p._append_journal_archive("灵", shard, _pages(1)))
    res = run(p.get_journal_archive())
    assert isinstance(res, tm.Ok), res
    payload = res.value
    assert len(payload["pages"]) == 1
    page = payload["pages"][0]
    assert page["page_no"] == 1
    assert page["entry_count"] == 1
    assert page["entries"][0]["text"] == "内容0"


def test_get_journal_scope_archive_channel(tm, plugin_factory_full):
    """1.3.0 实机 403 踩坑后的主翻阅通道：get_journal(scope=archive) 与
    get_journal_archive 同数据（前者走旧入口静态白名单，运行中覆盖导入也可达）。"""
    p = plugin_factory_full(current_lanlan="灵")
    shard = p._get_shard("灵")
    shard.journal = _pages(2, start_no=1)
    run(p._append_journal_archive("灵", shard, _pages(1)))
    res = run(p.get_journal(scope="archive"))
    assert isinstance(res, tm.Ok), res
    assert res.value["scope"] == "archive"
    assert [pg["page_no"] for pg in res.value["pages"]] == [1]
    # 缺省仍翻活架，不串数据
    res2 = run(p.get_journal())
    assert isinstance(res2, tm.Ok)
    assert res2.value["scope"] == "shelf"
    assert [pg["page_no"] for pg in res2.value["pages"]] == [1, 2]


# ---------------------------------------------------------------------------
# 6. 我的日记调试注入：假卷宗上架/还原（档案架验收用）
# ---------------------------------------------------------------------------


def test_fabricate_demo_reviews_shape(tm):
    entries = tm.fabricate_demo_reviews(4, now=NOW)
    assert len(entries) == 4
    assert [e["ts"] for e in entries] == sorted(e["ts"] for e in entries), "时间正序"
    assert all(e.get("demo") for e in entries)
    for e in entries:
        assert e["turns"] > 0 and "~" in e["span"] and "\n" in e["text"]
        # 1.3.0 完善：假卷宗同步带「本卷依据」快照三栏，验卷宗页各栏渲染
        assert set(e) == {"ts", "turns", "span", "self_action_count", "tone", "mood_avg", "quotes", "text", "demo"}


def test_debug_review_fill_seed_and_restore(tm, plugin_factory):
    p = plugin_factory()
    shard = p._get_shard("灵")
    real = {"ts": "2026-01-01T00:00:00+00:00", "turns": 3, "span": "", "self_action_count": 0, "text": "真实篇"}
    shard.review = [dict(real)]
    shard.review_stats = {"turns": 9}
    res = run(p._debug_review_fill(entries=4, lanlan="灵"))
    assert isinstance(res, tm.Ok), res
    assert res.value["entries_total"] == 4
    assert all(pg.get("demo") for pg in shard.review)
    backup = p.store.data["pre_debug@review@灵"]
    assert backup["entries"][0]["text"] == "真实篇" and backup["stats"]["turns"] == 9
    assert backup["archive"] == [], "1.3.0 档案室一并入备份"
    assert len(p.store.data["review@灵"]["entries"]) == 4

    res2 = run(p._debug_review_fill(restore=True, lanlan="灵"))
    assert isinstance(res2, tm.Ok) and res2.value["restored"] is True
    assert [pg["text"] for pg in shard.review] == ["真实篇"]
    assert shard.review_stats["turns"] == 9
    assert res2.value["archive_total"] == 0
    assert p.store.data.get("pre_debug@review@灵") is None


# ---------------------------------------------------------------------------
# 2. 载入/保存路径：溢出截断同样入阁（旧 blob 超长的兜底）
# ---------------------------------------------------------------------------


def test_save_shard_journal_archives_overflow(tm, plugin_factory):
    p = plugin_factory()
    lanlan = "灵"
    shard = p._get_shard(lanlan)
    shard.journal = _pages(55)
    res = run(p._save_shard_journal(lanlan, shard))
    assert isinstance(res, tm.Ok), res
    assert len(shard.journal) == 52
    assert [pg["page_no"] for pg in shard.journal_archive] == [1, 2, 3]
    assert [pg["page_no"] for pg in shard.journal] == list(range(4, 56))
    assert len(p.store.data["journal@灵"]) == 52
    assert len(p.store.data["journal_archive@灵"]) == 3


def test_save_shard_journal_without_overflow_skips_archive_write(plugin_factory):
    """零淘汰的常态写不多花一次存储写：archive key 不被触碰。"""
    p = plugin_factory()
    shard = p._get_shard("灵")
    shard.journal = _pages(3)
    run(p._save_shard_journal("灵", shard))
    assert "journal_archive@灵" not in p.store.data


# ---------------------------------------------------------------------------
# 3. 阁内再满：从最旧一页真删（新页永远在末尾，倒计数不挪位）
# ---------------------------------------------------------------------------


def test_archive_cap_drops_oldest(tm, plugin_factory):
    p = plugin_factory()
    shard = p._get_shard("灵")
    shard.journal_archive = _pages(100)
    run(p._append_journal_archive("灵", shard, _pages(6, start_no=101)))
    archive = shard.journal_archive
    assert len(archive) == tm._JOURNAL_ARCHIVE_MAX_PAGES
    assert archive[0]["page_no"] == 3, "106 进 104 出：最旧两页被裁"
    assert archive[-1]["page_no"] == 106, "新入阁页永远在末尾"


# ---------------------------------------------------------------------------
# 4. brief 只显事实 + 载入回读
# ---------------------------------------------------------------------------


def test_archive_brief_reports_facts_only(tm):
    empty = tm.archive_brief([])
    assert empty == {"pages": 0}
    pages = _pages(3)
    brief = tm.archive_brief(pages)
    assert brief["pages"] == 3
    assert brief["first_ts"] == pages[0]["started_at"]
    assert brief["last_ts"] == pages[-1]["entries"][-1]["ts"]


def test_archive_loaded_with_shard(plugin_factory):
    """重开（新实例）后合订本原样回来：加载路径读 journal_archive@。"""
    p = plugin_factory()
    shard = p._get_shard("灵")
    run(p._append_journal_archive("灵", shard, _pages(2)))
    initial = dict(p.store.data)
    p2 = plugin_factory()
    p2.store.data.update(initial)
    shard2 = p2._get_shard("灵")
    shard2.loaded = False
    run(p2._ensure_shard("灵"))
    assert [pg["page_no"] for pg in shard2.journal_archive] == [1, 2]


# ---------------------------------------------------------------------------
# 5. 调试入口 debug_journal_fill：注入走生产淘汰链路，restore 整包回滚
# ---------------------------------------------------------------------------


def test_debug_journal_fill_seed_and_restore(tm, plugin_factory):
    p = plugin_factory()
    shard = p._get_shard("灵")
    shard.journal = _pages(1)  # 先垫一页"真实"日记
    res = run(p._debug_journal_fill(pages=3, lanlan="灵"))
    assert isinstance(res, tm.Ok), res
    data = res.value
    assert data["seeded"] is True and data["flipped"] == 3
    assert data["shelf_pages"] == 52
    assert data["archive"]["pages"] == 3
    assert shard.journal[0]["page_no"] == 4, "翻 3 页应挤下最旧 3 页"
    assert [pg["page_no"] for pg in shard.journal_archive] == [1, 2, 3]
    # 注入前状态已整包备份（含从未入阁的空档）
    backup = p.store.data["pre_debug@journal@灵"]
    assert [pg["page_no"] for pg in backup["journal"]] == [1]
    assert backup["archive"] == []
    # 落盘同步：两键都在盘上
    assert len(p.store.data["journal@灵"]) == 52
    assert len(p.store.data["journal_archive@灵"]) == 3

    res2 = run(p._debug_journal_fill(restore=True, lanlan="灵"))
    assert isinstance(res2, tm.Ok), res2
    assert res2.value["restored"] is True
    assert [pg["page_no"] for pg in shard.journal] == [1], "真实日记原样回来"
    assert shard.journal_archive == []
    assert p.store.data.get("pre_debug@journal@灵") is None, "还原后备份作废"

    # --- 十六轮 clear 档：单独清空（备份在清、restore 找回、互斥闸） ---
    p3 = plugin_factory()
    shard3 = p3._get_shard("灵")
    shard3.journal = _pages(2)
    run(p3._append_journal_archive("灵", shard3, _pages(1, start_no=9)))
    res3 = run(p3._debug_journal_fill(clear=True, lanlan="灵"))
    assert isinstance(res3, tm.Ok), res3
    assert res3.value["cleared"] == 2 and res3.value["archive_cleared"] == 1
    assert shard3.journal == [] and shard3.journal_archive == []
    # 两键盘面同步清空（空列表 blob，clear_review 同口径）
    assert p3.store.data["journal@灵"] == []
    assert p3.store.data["journal_archive@灵"] == []
    # 清空前已整包备份：误清可 restore 找回
    backup3 = p3.store.data["pre_debug@journal@灵"]
    assert [pg["page_no"] for pg in backup3["journal"]] == [1, 2]
    assert [pg["page_no"] for pg in backup3["archive"]] == [9]

    res4 = run(p3._debug_journal_fill(restore=True, lanlan="灵"))
    assert isinstance(res4, tm.Ok), res4
    assert [pg["page_no"] for pg in shard3.journal] == [1, 2], "清掉的手记原样回来"
    assert [pg["page_no"] for pg in shard3.journal_archive] == [9]
    assert p3.store.data.get("pre_debug@journal@灵") is None

    # restore / clear 互斥（同传当场拒绝，不动任何数据）
    res5 = run(p3._debug_journal_fill(restore=True, clear=True, lanlan="灵"))
    assert isinstance(res5, tm.Err)


def test_debug_journal_clear_entry_delegates(tm, plugin_factory):
    """十八轮：独立入口 debug_journal_clear 必须真走 fill 的 clear 档（转发非复刻）。"""
    p = plugin_factory()
    shard = p._get_shard("灵")
    shard.journal = _pages(3)
    res = run(p._debug_journal_clear(lanlan="灵"))
    assert isinstance(res, tm.Ok), res
    assert res.value["cleared"] == 3
    assert shard.journal == [] and p.store.data["journal@灵"] == []
    # 备份在位：独立入口同样受"先备后清"契约保护
    backup = p.store.data["pre_debug@journal@灵"]
    assert [pg["page_no"] for pg in backup["journal"]] == [1, 2, 3]
    # 找回走 fill restore（两入口共用一条备份通道）
    res2 = run(p._debug_journal_fill(restore=True, lanlan="灵"))
    assert [pg["page_no"] for pg in shard.journal] == [1, 2, 3]
    assert isinstance(res2, tm.Ok) and res2.value["restored"] is True
