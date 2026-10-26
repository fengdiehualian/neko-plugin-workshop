"""Preloaded candidates remove a lookup, not model judgment or send gates."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.core.quick_candidates import (
    QUICK_CANDIDATE_CHARS,
    QUICK_CANDIDATE_LIMIT,
    QUICK_CANDIDATE_TITLE,
    quick_candidates_for_tool,
)
from sticker_manager.core.tool_surface import build_list_tool_description, build_send_tool_description, catalog_for_tool
from sticker_manager.services.library import Library

from .test_reply_latency import _surface


def _ids(text):
    return re.findall(r"^\[([^\]]+)\]", text, re.MULTILINE)


def test_full_caption_and_tags_are_preserved_without_guessing_from_group():
    caption = "自己开心地笑，但不表示已经收到礼物，也不用于嘲笑对方。"
    sticker = Sticker("smile", "private.gif", "笑", caption=caption, tags=["开心", "单纯笑脸"], group="表情")
    unnamed = Sticker("unnamed", "another.gif", "", group="开心")
    text = quick_candidates_for_tool([sticker, unnamed], {"开心": "开心"})
    assert _ids(text) == ["smile"]
    assert caption in text and "开心/单纯笑脸" in text
    assert "private.gif" not in text and "another.gif" not in text
    description = build_send_tool_description("・表情", "eager", quick_candidates=text)
    assert QUICK_CANDIDATE_TITLE in description
    assert "不用先查图" in description
    assert description.index(QUICK_CANDIDATE_TITLE) < description.index("需要更多候选")
    assert "正文只说一次" in description
    assert "暂时还没有可发" not in build_send_tool_description("", "eager", quick_candidates=text)


@pytest.mark.parametrize("eager", [False, True])
def test_shortlist_is_bounded_unique_and_skips_whole_overlong_rows(eager):
    pool = [
        Sticker(str(i), f"{i}.png", "", caption="自己开心。", tags=["开心", "喜欢", "轻松"])
        for i in range(40)
    ]
    long = Sticker("long", "long.png", "", caption="开心" * (QUICK_CANDIDATE_CHARS + 1))
    text = quick_candidates_for_tool([long, *pool], eager=eager)
    ids = _ids(text)
    assert 0 < len(ids) <= QUICK_CANDIDATE_LIMIT
    assert len(ids) == len(set(ids)) and "long" not in ids
    assert len(text) <= QUICK_CANDIDATE_CHARS
    assert len(_ids(quick_candidates_for_tool(pool, limit=1))) == 1
    assert quick_candidates_for_tool(pool, limit=0) == ""
    assert quick_candidates_for_tool([]) == ""


def test_recently_used_representative_yields_to_an_unused_equivalent():
    used = Sticker("a", "a.png", "开心", last_used_at=200, use_count=1)
    unused = Sticker("b", "b.png", "开心")
    disabled = Sticker("c", "c.png", "难过", disabled=True)
    assert _ids(quick_candidates_for_tool([used, unused, disabled])) == ["b"]
    assert quick_candidates_for_tool([used, unused]) == quick_candidates_for_tool([unused, used])


@pytest.mark.parametrize("tier", ["reserved", "natural", "eager"])
def test_active_zone_shortlist_sends_once_without_lookup_or_extra_text(tmp_path, run_async, tier):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(
        enabled=True, send=SendSettings(eagerness=tier, cooldown_sec=0),
    )
    plugin._library.load()
    first, error = plugin._library.add(data=PNG_BYTES, desc="开心", tags=["开心"], caption="自己开心地笑。")
    assert not error
    second, error = plugin._library.add(
        data=PNG_BYTES + b"second", desc="开心", tags=["开心"], caption="自己开心地笑。",
    )
    assert not error
    away, error = plugin._library.create_zone("away", "")
    assert not error
    hidden, error = plugin._library.add(data=PNG_BYTES + b"away", desc="难过", tags=[], zone=away)
    assert not error
    quick = plugin._send_tool_candidates()
    assert hidden.id not in _ids(quick)
    chosen = _ids(quick)[0]
    plugin.tool_sticker_list = AsyncMock(side_effect=AssertionError("unnecessary lookup"))
    metas, _ = _surface(plugin)
    assert plugin._refresh_send_tool_surface()
    assert quick in metas["sticker_send"]["description"]
    assert "不用先查图" in metas["sticker_send"]["description"]
    result = run_async(plugin.tool_sticker_send(sticker_id=chosen, _ctx={"lanlan_name": "K"}))
    assert result["sent"] == chosen
    assert len(host.push.calls) == 1
    assert all(part["type"] == "image" for part in host.push.calls[0]["parts"])
    assert plugin._surface_dirty
    assert chosen not in _ids(plugin._send_tool_candidates())
    assert set(_ids(plugin._send_tool_candidates())) <= {first.id, second.id}
    again = run_async(plugin.tool_sticker_send(sticker_id=chosen, _ctx={"lanlan_name": "K"}))
    assert again["reason"] == "recent_repeat" and len(host.push.calls) == 1
    plugin.tool_sticker_list.assert_not_awaited()
    assert len([row for row in plugin._library.read_usage() if row.get("ok")]) == 1
    assert plugin._runstats.sent == 1
    assert "若已说完就结束" in result["note"]
    assert plugin._refresh_send_tool_surface(force=False)
    assert plugin._send_tool_candidates() in metas["sticker_send"]["description"]
    assert plugin._library.activate_zone(away)[1] == ""
    assert _ids(plugin._send_tool_candidates()) == [hidden.id]


def test_recent_singleton_disappears_from_shortcut_but_other_role_can_still_lookup(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(
        enabled=True, send=SendSettings(eagerness="eager", cooldown_sec=0),
    )
    plugin._library.load()
    sticker, error = plugin._library.add(data=PNG_BYTES, desc="开心", tags=["开心"])
    assert not error and _ids(plugin._send_tool_candidates()) == [sticker.id]
    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    assert result["sent"] == sticker.id and plugin._send_tool_candidates() == ""
    plugin._library = Library(plugin._library.root)
    plugin._library.load()
    assert plugin._send_tool_candidates() == ""
    blocked = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    assert blocked["reason"] == "recent_repeat" and len(host.push.calls) == 1
    # Shared shortcuts are deliberately stricter; role-specific lookup is unchanged.
    available = run_async(plugin.tool_sticker_list(query="开心", _ctx={"lanlan_name": "Other"}))
    assert available["candidate_ids"] == [sticker.id]


def test_shortcut_history_counts_successful_distinct_images_and_respects_setting(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(send=SendSettings(recent_dedup_count=1))
    plugin._library.load()
    a, error = plugin._library.add(data=PNG_BYTES, desc="开心", tags=["开心"])
    assert not error
    b, error = plugin._library.add(data=PNG_BYTES + b"b", desc="难过", tags=["难过"])
    assert not error
    for sid, ok in ((a.id, True), (a.id, True), (b.id, False)):
        plugin._library.append_usage({"id": sid, "ok": ok, "lanlan": "K"}, keep=50)
    assert _ids(plugin._send_tool_candidates()) == [b.id]
    plugin._settings = StickerManagerSettings(send=SendSettings(recent_dedup_count=0))
    assert set(_ids(plugin._send_tool_candidates())) == {a.id, b.id}


def test_standing_context_budget_with_all_reviewed_official_captions():
    reviewed = json.loads((Path(__file__).resolve().parents[1] / "tools/official_labels_v3.json").read_text("utf-8"))
    pool = [Sticker(id=sid, file=f"{sid}.gif", desc="", **patch) for sid, patch in reviewed["stickers"].items()]
    # Keep every category in the prompt, even in the worst case of full group text.
    groups = {f"group{i}": "分类适用情境。" * 4 for i in range(22)}
    catalog = catalog_for_tool(
        [Sticker(str(i), f"{i}.gif", "说明", group=name) for i, name in enumerate(groups)], groups,
    )
    quick = quick_candidates_for_tool(pool, eager=True)
    send = build_send_tool_description(catalog, "eager", quick_candidates=quick)
    lookup = build_list_tool_description("eager")
    assert len(_ids(quick)) == QUICK_CANDIDATE_LIMIT
    assert len(send) < 3000 and len(lookup) < 600
    assert "轻松聊天默认配一张贴切图" in send
    assert "优先查候选" not in send and "优先查候选" not in lookup


def test_eager_expands_coverage_after_recent_exclusion_without_changing_baseline():
    reviewed = json.loads((Path(__file__).resolve().parents[1] / "tools/official_labels_v3.json").read_text("utf-8"))
    pool = [Sticker(id=sid, file=f"{sid}.gif", desc="", **patch) for sid, patch in reviewed["stickers"].items()]
    recent = {"089", "007", "100", "004", "102"}
    baseline = quick_candidates_for_tool(pool, recent_ids=recent)
    eager = quick_candidates_for_tool(pool, recent_ids=recent, eager=True)
    assert _ids(baseline) == ["031", "139", "150", "115", "117", "047", "160", "072", "013"]
    assert len(_ids(eager)) == QUICK_CANDIDATE_LIMIT > len(_ids(baseline))
    assert not (set(_ids(eager)) & recent)
    assert {"005", "012", "145"} <= set(_ids(eager))  # Approval, playfulness, drowsiness.
    for sid in _ids(eager):
        assert reviewed["stickers"][sid]["caption"] in eager
    assert quick_candidates_for_tool(list(reversed(pool)), recent_ids=recent, eager=True) == eager
    assert len(eager) <= QUICK_CANDIDATE_CHARS


def test_eager_drowsy_browse_does_not_use_a_puzzled_representative():
    puzzled = Sticker("a", "a.gif", "困惑", caption="自己困惑地看着对方。", tags=["困惑"])
    sleepy = Sticker("z", "z.gif", "犯困", caption="自己犯困打哈欠。", tags=["犯困"])
    assert _ids(quick_candidates_for_tool([puzzled, sleepy])) == ["a"]
    assert _ids(quick_candidates_for_tool([puzzled, sleepy], eager=True)) == ["z"]


def test_switching_eager_shortcuts_does_not_change_reserved_profile(tmp_path, run_async):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._library.load()
    for data, desc in (
        (PNG_BYTES, "困惑"), (PNG_BYTES + b"sleepy", "犯困"), (PNG_BYTES + b"approve", "赞同"),
    ):
        assert not plugin._library.add(data=data, desc=desc, tags=[desc])[1]
    metas, _ = _surface(plugin)
    run_async(plugin.set_eagerness_entry(eagerness="reserved"))
    baseline = plugin._send_tool_candidates()
    run_async(plugin.set_eagerness_entry(eagerness="eager"))
    eager = plugin._send_tool_candidates()
    assert eager != baseline and eager in metas["sticker_send"]["description"]
    run_async(plugin.set_eagerness_entry(eagerness="reserved"))
    assert plugin._send_tool_candidates() == baseline
    assert baseline in metas["sticker_send"]["description"]
