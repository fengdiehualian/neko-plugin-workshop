"""Preserve the observed 0.9.8 strategy while other tiers can evolve."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.awareness import build_awareness_text
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.core.eagerness import (
    DECISION_CHECKPOINT,
    TOOL_NOTE,
    TRIGGER_CRITERIA,
    WILL,
    decision_checkpoint,
    effective_inject_interval_n,
    injection_guidance,
    tool_criteria,
)

from .test_reply_latency import _surface


def _baseline():
    path = Path(__file__).parent / "fixtures" / "eager_098_baseline.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_reserved_matches_frozen_098_strategy_except_tier_name():
    old = _baseline()
    actual = {
        "will": injection_guidance("reserved"),
        "checkpoint": decision_checkpoint("reserved"),
        "criteria": tool_criteria("reserved"),
        "tool_note": TOOL_NOTE["reserved"],
    }
    for key, text in actual.items():
        assert text == old[key].replace("爱发档", "矜持档")
    assert effective_inject_interval_n("reserved", 0) == old["interval_n"]


def test_future_eager_changes_cannot_rewrite_reserved_baseline(monkeypatch):
    before = (
        injection_guidance("reserved"), decision_checkpoint("reserved"),
        tool_criteria("reserved"), TOOL_NOTE["reserved"],
    )
    for mapping in (WILL, DECISION_CHECKPOINT, TRIGGER_CRITERIA, TOOL_NOTE):
        monkeypatch.setitem(mapping, "eager", "future eager policy")
    assert before == (
        injection_guidance("reserved"), decision_checkpoint("reserved"),
        tool_criteria("reserved"), TOOL_NOTE["reserved"],
    )


def test_panel_switch_applies_baseline_to_both_tools_and_dashboard(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"enabled": True, "send": {"eagerness": "eager"}}}
    )
    plugin._library.load()
    assert not plugin._library.add(data=PNG_BYTES, desc="开心", tags=["开心"])[1]
    metas, _ = _surface(plugin)
    result = run_async(plugin.set_eagerness_entry(eagerness="reserved"))
    assert result.is_ok() and result.value["eagerness"] == "reserved"
    assert host.config.writes == [("sticker_manager.send.eagerness", "reserved")]
    expected = _baseline()["checkpoint"].replace("爱发档", "矜持档")
    for name in ("sticker_list", "sticker_send"):
        assert metas[name]["description"].startswith(expected)
        assert "爱发档" not in metas[name]["description"]
    reminder = build_awareness_text(plugin._library.active_pool(), eagerness="reserved")
    assert _baseline()["will"].replace("爱发档", "矜持档") in reminder
    state = run_async(plugin.dashboard_context())
    assert state["eagerness"] == "reserved" and state["awareness"]["inject_interval_n"] == 3
    assert not host.push.calls and not plugin._library.read_usage()
