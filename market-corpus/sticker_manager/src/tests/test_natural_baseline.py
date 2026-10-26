"""Natural preserves the delivered 0.9.10 eager strategy, including shortcuts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.awareness import build_awareness_text
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.core.eagerness import (
    DECISION_CHECKPOINT,
    EAGER_EXTRA_NOTE,
    TOOL_NOTE,
    TRIGGER_CRITERIA,
    WILL,
    decision_checkpoint,
    effective_inject_interval_n,
    injection_guidance,
    injection_pointer,
    tool_criteria,
)
from sticker_manager.core.quick_candidates import quick_candidates_for_tool
from sticker_manager.core.tool_surface import build_list_tool_description, build_send_tool_description

from .test_reply_latency import _surface


def _baseline():
    path = Path(__file__).parent / "fixtures" / "eager_0910_baseline.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _pool():
    path = Path(__file__).resolve().parents[1] / "tools" / "official_labels_v3.json"
    labels = json.loads(path.read_text(encoding="utf-8"))
    return [Sticker(id=sid, file=f"{sid}.gif", desc="", **patch) for sid, patch in labels["stickers"].items()]


def _natural(text):
    return text.replace("爱发档", "自然档")


def test_natural_preserves_full_0910_strategy_and_both_tool_descriptions():
    old = _baseline()
    actual = {
        "will": injection_guidance("natural"),
        "checkpoint": decision_checkpoint("natural"),
        "criteria": tool_criteria("natural"),
        "tool_note": TOOL_NOTE["natural"],
        "pointer": injection_pointer("natural"),
        "lookup": build_list_tool_description("natural"),
    }
    for key, text in actual.items():
        assert text == _natural(old[key])
    assert effective_inject_interval_n("natural", 0) == old["interval_n"] == 1
    for case in old["send_cases"]:
        assert build_send_tool_description(
            case["catalog"], "natural", quick_candidates=case["quick"],
        ) == _natural(case["description"])
    for event, text in zip((False, True), old["awareness"], strict=True):
        assert build_awareness_text(_pool(), eagerness="natural", event=event) == _natural(text)


def test_reserved_full_tool_surfaces_remain_unchanged():
    for case in _baseline()["reserved_send_cases"]:
        assert build_send_tool_description(
            case["catalog"], "reserved", quick_candidates=case["quick"],
        ) == case["description"]


@pytest.mark.parametrize("tier", ["natural", "eager"])
@pytest.mark.parametrize("case", _baseline()["quick_cases"])
def test_natural_and_eager_use_0910_shortcuts_through_actual_plugin_path(tmp_path, monkeypatch, case, tier):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"send": {"eagerness": tier}}}
    )
    pool = _pool()
    monkeypatch.setattr(plugin._library, "active_pool", lambda: pool)
    monkeypatch.setattr(plugin._library, "group_descs", lambda: {})
    history = [{"id": sid, "ok": True} for sid in case["recent_ids"]]
    monkeypatch.setattr(plugin._library, "read_usage", lambda **_: history)
    assert plugin._send_tool_candidates() == case["description"]
    assert quick_candidates_for_tool(
        pool, recent_ids=set(case["recent_ids"]), eager=True,
    ) == case["description"]


def test_future_eager_tuning_does_not_change_natural(monkeypatch):
    old = _baseline()
    for mapping in (WILL, DECISION_CHECKPOINT, TRIGGER_CRITERIA, TOOL_NOTE):
        monkeypatch.setitem(mapping, "eager", "future eager policy")
    assert injection_guidance("natural") == _natural(old["will"])
    assert build_list_tool_description("natural") == _natural(old["lookup"])
    for case in old["send_cases"]:
        assert build_send_tool_description(
            case["catalog"], "natural", quick_candidates=case["quick"],
        ) == _natural(case["description"])


def test_panel_switch_applies_natural_baseline_without_extra_messages(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"enabled": True, "send": {"eagerness": "reserved"}}}
    )
    plugin._library.load()
    assert not plugin._library.add(data=PNG_BYTES, desc="赞同", tags=["赞同"])[1]
    metas, _ = _surface(plugin)
    result = run_async(plugin.set_eagerness_entry(eagerness="natural"))
    assert result.is_ok() and result.value["eagerness"] == "natural"
    assert host.config.writes == [("sticker_manager.send.eagerness", "natural")]
    assert metas["sticker_list"]["description"] == _natural(_baseline()["lookup"])
    assert "自然档" in metas["sticker_send"]["description"]
    assert plugin._send_tool_candidates() in metas["sticker_send"]["description"]
    state = run_async(plugin.dashboard_context())
    assert state["eagerness"] == "natural" and state["awareness"]["inject_interval_n"] == 1
    assert not host.push.calls and not plugin._library.read_usage()


def test_eager_restores_complete_baseline_with_only_one_added_preference():
    old = _baseline()
    for text, expected in (
        (injection_guidance("eager"), old["will"]),
        (decision_checkpoint("eager"), old["checkpoint"]),
        (build_list_tool_description("eager"), old["lookup"]),
    ):
        assert text.count(EAGER_EXTRA_NOTE) == 1
        assert text.replace(EAGER_EXTRA_NOTE, "") == expected
    assert tool_criteria("eager") == old["criteria"]
    assert TOOL_NOTE["eager"] == old["tool_note"]
    for case in old["send_cases"]:
        text = build_send_tool_description(case["catalog"], "eager", quick_candidates=case["quick"])
        assert text.count(EAGER_EXTRA_NOTE) == 1
        assert text.replace(EAGER_EXTRA_NOTE, "") == case["description"]
        assert len(text) == len(case["description"]) + len(EAGER_EXTRA_NOTE)
        assert "正文只说一次" in text and "若已说完就结束" in text
        assert "text 留空即可" in text and "单回复最多一张" in text
        if case["quick"]:
            assert case["quick"] in text
    for event, expected in zip((False, True), old["awareness"], strict=True):
        text = build_awareness_text(_pool(), eagerness="eager", event=event)
        assert text.count(EAGER_EXTRA_NOTE) == 1
        assert text.replace(EAGER_EXTRA_NOTE, "") == expected
    for case in old["quick_cases"]:
        assert quick_candidates_for_tool(
            _pool(), recent_ids=set(case["recent_ids"]), eager=True,
        ) == case["description"]


def test_panel_switch_applies_full_eager_surface_and_restores_natural(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._library.load()
    assert not plugin._library.add(data=PNG_BYTES, desc="赞同", tags=["赞同"])[1]
    metas, _ = _surface(plugin)
    for tier in ("natural", "eager", "natural"):
        result = run_async(plugin.set_eagerness_entry(eagerness=tier))
        assert result.is_ok() and result.value["eagerness"] == tier
        quick = plugin._send_tool_candidates()
        assert quick
        actual = metas["sticker_send"]["description"]
        expected = build_send_tool_description(plugin._send_tool_catalog(), "natural", quick_candidates=quick)
        if tier == "eager":
            assert actual.count(EAGER_EXTRA_NOTE) == 1
            actual = actual.replace(EAGER_EXTRA_NOTE, "").replace("爱发档", "自然档")
        assert actual == expected
        lookup = metas["sticker_list"]["description"]
        if tier == "eager":
            lookup = lookup.replace(EAGER_EXTRA_NOTE, "").replace("爱发档", "自然档")
        assert lookup == _natural(_baseline()["lookup"])
    assert not host.push.calls and not plugin._library.read_usage()
