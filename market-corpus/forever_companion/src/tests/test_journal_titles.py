"""日记页标题：写入、续写、翻页、归档与重新载入的真实通道。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from forever_companion.core.journal import journal_write, normalize_journal_title, page_header
from forever_companion.core.state import _JOURNAL_MAX_PAGES, _JOURNAL_PAGE_MAX_ENTRIES

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [None, "", " \n\t ", 123, {}, []])
def test_missing_or_invalid_titles_remain_untitled(value):
    assert normalize_journal_title(value) == ""


def test_title_is_single_line_bounded_without_touching_body():
    pages, _, _, _ = journal_write([], "【我在想】原来的正文\n第二行", False, title="  晚风\n  和你\t ", now=NOW)
    assert pages[0]["title"] == "晚风 和你"
    assert pages[0]["entries"][0]["text"] == "【我在想】原来的正文\n第二行"
    assert page_header(pages[0])["title"] == "晚风 和你"
    assert len(normalize_journal_title("题" * 100)) == 40


def test_continuation_preserves_page_title_and_input_snapshot():
    pages, _, _, _ = journal_write([], "第一段", False, title="第一次看海", now=NOW)
    before = deepcopy(pages)
    continued, _, _, _ = journal_write(pages, "续写", False, title="另一个题目", now=NOW)
    assert continued[0]["title"] == "第一次看海"
    assert pages == before
    omitted, _, _, _ = journal_write(continued, "再续写", False, now=NOW)
    assert omitted[0]["title"] == "第一次看海"


def test_untitled_legacy_page_can_receive_title_on_continuation():
    old = [{"page_no": 8, "entries": [{"ts": "old", "text": "原文"}], "legacy": True}]
    pages, page_no, _, _ = journal_write(old, "续文", False, title="迟来的题目", now=NOW)
    assert page_no == 8 and pages[0]["title"] == "迟来的题目"
    assert pages[0]["entries"][0] == old[0]["entries"][0]
    assert "title" not in old[0]
    assert page_header(old[0])["title"] == ""


@pytest.mark.parametrize("explicit", [True, False])
def test_new_pages_have_independent_titles(explicit):
    pages, _, _, _ = journal_write([], "开篇", False, title="旧页", now=NOW)
    if not explicit:
        for _ in range(_JOURNAL_PAGE_MAX_ENTRIES - 1):
            pages, _, _, _ = journal_write(pages, "续写", False, now=NOW)
    written, page_no, _, _ = journal_write(pages, "另一页", explicit, title="新页", now=NOW)
    assert page_no == 2
    assert [page["title"] for page in written] == ["旧页", "新页"]
    untitled, _, _, _ = journal_write(written, "独立的一页", True, now=NOW)
    assert "title" not in untitled[-1]


def test_tool_title_survives_persistence_restart_and_both_archive_readers(plugin_factory_full, tm):
    plugin = plugin_factory_full(current_lanlan="灵")
    shard = asyncio.run(plugin._ensure_shard("灵"))
    pages = []
    for index in range(_JOURNAL_MAX_PAGES):
        pages, _, _, _ = journal_write(pages, f"原文{index}", True, title=f"题目{index}", now=NOW)
    shard.journal = pages
    asyncio.run(plugin._save_shard_journal("灵", shard))
    result = asyncio.run(plugin.tool_write_journal(events="新的一页", new_page=True, title="晚风里的小事"))
    assert isinstance(result, tm.Ok)
    assert plugin.store.data["journal@灵"][-1]["title"] == "晚风里的小事"
    assert plugin.store.data["journal_archive@灵"][0]["title"] == "题目0"
    assert plugin._pushed == []

    restarted = plugin_factory_full(current_lanlan="灵", store_initial=plugin.store.data)
    current = asyncio.run(restarted.get_journal()).value["pages"]
    assert current[-1]["title"] == "晚风里的小事"
    for reader in (restarted.get_journal(scope="archive"), restarted.get_journal_archive()):
        archived = asyncio.run(reader).value["pages"]
        assert archived[0]["title"] == "题目0"
        assert archived[0]["entries"][0]["text"] == "原文0"
    dashboard = asyncio.run(restarted.dashboard())
    assert dashboard["journal_index"][-1]["title"] == "晚风里的小事"


def test_title_alone_cannot_create_an_empty_diary(plugin_factory, tm):
    plugin = plugin_factory()
    result = asyncio.run(plugin.tool_write_journal(title="只有题目"))
    assert isinstance(result, tm.Err)
    assert plugin._current_shard().journal == []
