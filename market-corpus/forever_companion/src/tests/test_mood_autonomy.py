"""Emotion records describe choices without imposing a reply or reconciliation."""

import asyncio
from copy import deepcopy

import pytest
from forever_companion.core.state import _MoodState


def run(coro):
    return asyncio.run(coro)


def choose(plugin, action="ebb_tide", *, role="A", origin="self", minutes=10, reason=""):
    return run(plugin._apply_mood_action(
        action=action, minutes=minutes, reason=reason, timed=action != "seek_harbor",
        lanlan=role, origin=origin,
    ))


def test_choice_switch_release_and_restart_are_per_character(plugin_factory_full, tm, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    p = plugin_factory_full(current_lanlan="A")
    choose(p, reason="private-reason")
    now[0] += 1
    choose(p, "shallow_reef", origin="user")
    choose(p, "spring_tide", role="B")
    a = p._get_shard("A").mood
    assert a.origin == "user"
    assert [item["kind"] for item in a.history] == ["choose", "switch"]
    assert a.history[-1]["previous_action"] == "ebb_tide"
    run(p.tool_feeling_better(reason="my choice", _ctx={"lanlan_name": "A"}))
    assert not p._get_shard("A").mood.action
    assert p._get_shard("A").mood.history[-1]["origin"] == "self"
    restored = plugin_factory_full(current_lanlan="A", store_initial=deepcopy(p.store.data))
    a2 = run(restored._ensure_shard("A")).mood
    b2 = run(restored._ensure_shard("B")).mood
    assert a2.to_mapping() == p._get_shard("A").mood.to_mapping()
    assert b2.action == "spring_tide"
    assert len(b2.history) == 1
    # A's old private reasons are panel-only, never appended to current hints.
    assert all("private-reason" not in msg["parts"][0]["text"] for msg in p._pushed[-1:])


def test_old_snapshot_has_no_invented_history_or_source():
    mood = _MoodState.from_mapping({"action": "cold_violence", "reason": "old"})
    assert mood.action == "ebb_tide"
    assert mood.origin == ""
    assert mood.history == []


def test_manual_debug_is_not_recorded_as_character_choice(plugin_factory_full):
    p = plugin_factory_full(current_lanlan="A")
    run(p._debug_set_mood(action="ripple", lanlan="A"))
    mood = p._get_shard("A").mood
    assert mood.origin == mood.history[-1]["origin"] == "user"


@pytest.mark.parametrize("bad", [None, [], {}, "bad", float("nan"), float("inf"), True, -1, 0, 10**400])
def test_history_ignores_invalid_timestamps(bad):
    item = {"ts": bad, "kind": "choose", "origin": "self", "action": "ripple"}
    assert _MoodState.from_mapping({"history": [item]}).history == []


@pytest.mark.parametrize("field", ["action", "previous_action", "kind", "origin"])
def test_history_handles_unhashable_fields(field):
    item = {"ts": 1, "kind": "choose", "origin": "self", "action": "ripple", "previous_action": ""}
    item[field] = ["malformed"]
    mood = _MoodState.from_mapping({"history": [item]})
    assert len(mood.history) == (1 if field == "previous_action" else 0)
    if mood.history:
        assert mood.history[0]["previous_action"] == ""


def test_history_is_bounded_and_mapping_copies_are_independent():
    mood = _MoodState()
    for index in range(50):
        mood.record_event("choose", "ripple", "self", str(index) + "x" * 250)
    saved = mood.to_mapping()
    restored = _MoodState.from_mapping(saved)
    assert len(saved["history"]) == len(restored.history) == 40
    assert saved["history"][0]["reason"].startswith("10")
    assert all(len(item["reason"]) == 200 for item in restored.history)
    saved["history"][0]["reason"] = "changed"
    assert mood.history[0]["reason"] != "changed"
    assert restored.history[0]["reason"] != "changed"


@pytest.mark.parametrize("action", ["ebb_tide", "warm_current", "seek_harbor"])
def test_expiry_records_once_without_reconciliation_or_model_turn(plugin_factory_full, tm, monkeypatch, action):
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    p = plugin_factory_full(current_lanlan="A", mood_extra={"open_action_timeout_minutes": 10})
    choose(p, action)
    now[0] += 11 * 60
    run(p._supervise_once())
    run(p._supervise_once())
    mood = p._get_shard("A").mood
    assert mood.action == mood.origin == ""
    assert [item["kind"] for item in mood.history] == ["choose", "expire"]
    assert mood.history[-1]["origin"] == "timer"
    notices = [msg for msg in p._pushed if msg.get("metadata", {}).get("message_type") == "forever_companion.mood_recovered"]
    assert len(notices) == 1
    assert notices[0]["ai_behavior"] == "read"
    assert notices[0]["visibility"] == []
    assert "不代表你已经消气、原谅或改变感受" in notices[0]["parts"][0]["text"]
    assert p.store.data["mood@A"]["history"] == mood.history


def test_overdue_choice_records_expiry_before_new_choice(plugin_factory_full, tm, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tm.time, "time", lambda: now[0])
    p = plugin_factory_full(current_lanlan="A")
    choose(p)
    now[0] += 601
    choose(p, "ripple")
    assert [item["kind"] for item in p._get_shard("A").mood.history] == ["choose", "expire", "choose"]


def test_manual_end_preserves_observation_and_is_not_self_reconciliation(plugin_factory_full):
    p = plugin_factory_full(current_lanlan="A")
    choose(p, reason="private-history")
    mood = p._get_shard("A").mood
    affect = (mood.valence, mood.arousal, mood.affect_updated_at)
    result = run(p.lift_mood(expected_lanlan="A", expected_started_at=mood.started_at))
    assert result.value["cleared"] is True
    assert (mood.valence, mood.arousal, mood.affect_updated_at) == affect
    assert mood.history[-1]["kind"] == "release"
    assert mood.history[-1]["origin"] == "user"
    assert "不是你自主决定和好" in p._pushed[-1]["parts"][0]["text"]
    assert "private-history" not in p._pushed[-1]["parts"][0]["text"]
    run(p.lift_mood())
    assert len(mood.history) == 2


@pytest.mark.parametrize("expected", [{"expected_lanlan": "B"}, {"expected_started_at": -1}])
def test_manual_end_rejects_stale_character_or_choice(plugin_factory_full, expected):
    p = plugin_factory_full(current_lanlan="A")
    choose(p)
    before = p._get_shard("A").mood.to_mapping()
    result = run(p.lift_mood(**expected))
    assert str(result.error) == "mood_changed"
    assert p._get_shard("A").mood.to_mapping() == before


def test_manual_end_reports_persist_failure(plugin_factory_full, tm, monkeypatch):
    p = plugin_factory_full(current_lanlan="A")
    choose(p)

    async def fail_save(*_args):
        return tm.Err(tm.SdkError("persist_failed"))

    monkeypatch.setattr(p, "_save_shard_mood", fail_save)
    result = run(p.lift_mood())
    assert str(result.error) == "persist_failed"


def test_observation_and_apology_never_change_choice(plugin_factory_full):
    p = plugin_factory_full(current_lanlan="A")
    choose(p)
    shard = p._get_shard("A")
    before = shard.mood.to_mapping()
    run(p._maybe_nudge_reconcile("对不起", "A"))
    p._emotion_sense_cfg["window_turns"] = 2
    p._tone_correction_check("A", shard, "happy")
    p._tone_correction_check("A", shard, "happy")
    assert shard.mood.to_mapping() == before
    text = p._pushed[-1]["parts"][0]["text"]
    assert "温和表达不代表矛盾已经解决" in text
    assert p._pushed[-1]["ai_behavior"] == "read"


def test_dashboard_history_is_bounded_local_and_source_is_accurate(plugin_factory_full):
    p = plugin_factory_full(current_lanlan="A")
    for _ in range(15):
        choose(p, "ripple", origin="user", reason="local-private-history")
    state = run(p.dashboard())
    assert state["mood"]["origin"] == "user"
    assert len(state["mood"]["history"]) == 12
    p._get_shard("A").mood.reason = "current-reason"
    line = p._build_mood_context_line(p._get_shard("A"))
    assert "用户明确要求" in line
    assert "current-reason" in line
    assert "local-private-history" not in line
    # Turning the system off hides active behavior, but does not erase local history.
    p._mood_cfg["enabled"] = False
    state = run(p.dashboard())
    assert not state["mood"]["active"]
    assert len(state["mood"]["history"]) == 12
