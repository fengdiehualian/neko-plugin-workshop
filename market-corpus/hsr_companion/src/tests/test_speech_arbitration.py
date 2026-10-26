from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime.contracts import CompanionEvent
from plugin.plugins.hsr_companion.runtime.speech import SpeechArbiter
from plugin.plugins.hsr_companion.tests.test_current_observation import (
    analysis,
    plugin_fixture,
)


def event(scene="team_setup", *, now=10, ttl=8):
    return CompanionEvent(f"scene:{scene}", "scene_changed", 4, now,
                          payload={"current": scene}, proactive=True, max_age_seconds=ttl)


def offer(arbiter, scene="team_setup", *, now=10, epoch=1):
    return arbiter.offer(event(scene, now=now), now=now, epoch=epoch,
                         scene=scene, page_type=scene)


def check(arbiter, cue, **changes):
    args = dict(now=13, epoch=1, scene="team_setup", page_type="team_setup",
                available=True, query_busy=False)
    return arbiter.check(cue, **(args | changes))


def test_short_hold_then_single_submission_and_bounded_log():
    arbiter = SpeechArbiter()
    cue = offer(arbiter)
    assert check(arbiter, cue, now=12) == "holding"
    assert check(arbiter, cue) == "ready"
    arbiter.finish(cue, "submitted")
    assert check(arbiter, cue) == "superseded"
    for i in range(100):
        offer(arbiter, now=30 + i)
    assert len(arbiter.snapshot()["recent"]) == 32
    assert arbiter.snapshot()["pending_count"] == 1
    assert arbiter.snapshot()["can_retract_submitted"] is False


def test_new_scene_replaces_old_instead_of_building_a_backlog():
    arbiter = SpeechArbiter()
    old = offer(arbiter)
    new = offer(arbiter, "warp", now=11, epoch=2)
    assert check(arbiter, old) == "superseded"
    assert check(arbiter, new, now=14, epoch=2, scene="warp", page_type="warp") == "ready"
    assert arbiter.snapshot()["counts"]["coalesced_newer"] == 1


def test_query_cancels_pending_and_does_not_catch_up_after_quiet_period():
    arbiter = SpeechArbiter()
    cue = offer(arbiter)
    arbiter.query_started(now=11)
    assert check(arbiter, cue) == "superseded"
    assert offer(arbiter, now=20) is None
    assert arbiter.pending is None  # Not postponed until the answer ends.
    assert offer(arbiter, "warp", now=27, epoch=2) is not None


@pytest.mark.parametrize("changes,reason", [
    ({"now": 18}, "expired"), ({"now": 9}, "expired"),
    ({"available": False}, "unavailable"), ({"query_busy": True}, "recent_query"),
    ({"epoch": 2}, "observation_changed"), ({"scene": "warp"}, "observation_changed"),
    ({"page_type": "team_selection"}, "observation_changed"),
])
def test_release_checks_current_evidence_not_just_original_event(changes, reason):
    arbiter = SpeechArbiter()
    assert check(arbiter, offer(arbiter), **changes) == reason


@pytest.mark.parametrize("fresh,verified", [(False, True), (True, False), (False, False)])
def test_unknown_or_unverified_tool_result_does_not_claim_coverage(fresh, verified):
    arbiter = SpeechArbiter()
    arbiter.covered_by_tool(epoch=1, scene="team_setup", page_type="team_setup",
                            now=10, fresh=fresh, verified=verified)
    assert offer(arbiter, now=11) is not None


def test_tool_coverage_is_scoped_and_expires_without_disabling_companionship():
    arbiter = SpeechArbiter()
    arbiter.covered_by_tool(epoch=1, scene="team_setup", page_type="team_setup",
                            now=10, fresh=True, verified=True)
    assert offer(arbiter, now=26) is None
    assert arbiter.snapshot()["counts"]["covered_by_tool"] == 1
    assert offer(arbiter, "warp", now=27, epoch=2) is not None
    assert offer(arbiter, now=41) is not None


def test_commit_queues_cue_without_awaiting_live_validation(tmp_path, monkeypatch):
    owner, _, sent = plugin_fixture(tmp_path)
    calls = []

    async def slow_validation(_):
        calls.append("validation")
        await asyncio.Future()

    module = importlib.import_module("plugin.plugins.hsr_companion")
    monkeypatch.setattr(module, "validate_before_delivery", slow_validation)

    async def run():
        result = await asyncio.wait_for(owner._apply_local_analysis(
            analysis(roles=False, scene="team_setup"), image_fingerprint="a", source="test"), .5)
        assert result["delivery"]["queued"] is True
        assert calls == []
        assert not any(item["ai_behavior"] == "respond" for item in sent)
        owner._cancel_pending_speech("test_finished")
        await asyncio.sleep(0)

    asyncio.run(run())


def test_passive_commit_reuses_already_synced_context_without_second_push(tmp_path, monkeypatch):
    owner, _, sent = plugin_fixture(tmp_path)
    module = importlib.import_module("plugin.plugins.hsr_companion")

    async def unexpected_validation(_):
        pytest.fail("Passive events must not fetch another frame")

    monkeypatch.setattr(module, "validate_before_delivery", unexpected_validation)
    result = asyncio.run(owner._apply_local_analysis(
        analysis(roles=False, scene="menu"), image_fingerprint="a", source="test"))
    assert result["delivery"]["reason"] == "passive_context_already_synced"
    assert len(sent) == 1
    assert sent[0]["coalesce_key"] == "hsr_current_visual"
    assert sent[0]["ai_behavior"] == "read"


def test_prepare_marks_verified_return_as_covered_not_spoken(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await owner._apply_local_analysis(analysis(roles=False, scene="menu"),
                                          image_fingerprint="a", source="test")
        result = await owner.hsr_companion_prepare(query="现在是什么界面？")
        assert result["current_visual_evidence"]["fresh"]
        arbiter = owner._get_speech_arbiter()
        assert arbiter._covered[:3] == (getattr(owner, "_observation_epoch", 0), "menu", "activity_menu")
        assert arbiter.snapshot()["ack_scope"] == "sdk_submission_only"
        assert "spoken" not in arbiter.snapshot()["counts"]

    asyncio.run(run())


def test_stopping_observer_awaits_cancelled_speech_and_clears_reference(tmp_path):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._observer_task = None
    owner._assistant_process = SimpleNamespace(stop=lambda: None)

    async def run():
        await owner._apply_local_analysis(analysis(roles=False, scene="team_setup"),
                                          image_fingerprint="a", source="test")
        task = owner._speech_task
        await owner._cancel_observer_task()
        assert task.done()
        assert owner._speech_task is None
        assert owner._get_speech_arbiter().pending is None
        assert owner._observer_status["state"] == "stopped"
        assert not any(item["ai_behavior"] == "respond" for item in sent)

    asyncio.run(run())


def test_cancellation_does_not_disable_a_later_meaningful_scene(tmp_path, monkeypatch):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._speech_arbiter = SpeechArbiter(hold_seconds=0, quiet_seconds=0, coverage_seconds=0)
    module = importlib.import_module("plugin.plugins.hsr_companion")

    async def validate(_):
        return True

    monkeypatch.setattr(module, "validate_before_delivery", validate)

    async def run():
        await owner._apply_local_analysis(analysis(roles=False, scene="team_setup"),
                                          image_fingerprint="a", source="test")
        owner._note_speech_query()
        await asyncio.sleep(0)
        assert not any(item["ai_behavior"] == "respond" for item in sent)
        # Tests skip the quiet window only; production still uses 15 seconds.
        for signature in ("b", "c"):
            await owner._apply_local_analysis(analysis(roles=False, scene="warp"),
                                              image_fingerprint=signature, source="test")
        await owner._speech_task
        responding = [item for item in sent if item["ai_behavior"] == "respond"]
        assert len(responding) == 1
        assert responding[0]["metadata"]["event_id"] == "scene:warp"

    asyncio.run(run())


@pytest.mark.parametrize("action", ["query", "invalidate", "mute"])
def test_runtime_cancellation_paths_do_not_speak_later(tmp_path, action):
    owner, _, sent = plugin_fixture(tmp_path)

    async def run():
        await owner._apply_local_analysis(analysis(roles=False, scene="team_setup"),
                                          image_fingerprint="a", source="test")
        arbiter = owner._get_speech_arbiter()
        assert arbiter.pending is not None
        task = owner._speech_task
        if action == "query":
            await owner.hsr_companion_prepare(query="现在是什么界面？")
        elif action == "invalidate":
            await owner._invalidate_current_observation("page_changed")
        else:
            await owner.set_proactive_companionship(False)
        await asyncio.sleep(0)
        assert arbiter.pending is None
        assert task.done()
        assert not any(item["ai_behavior"] == "respond" for item in sent)

    asyncio.run(run())


@pytest.mark.parametrize("during_validation", ["none", "query", "epoch", "timeout", "failed_push", "push_exception"])
def test_final_async_boundary_cannot_resurrect_cancelled_cue(tmp_path, monkeypatch, during_validation):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._speech_arbiter = SpeechArbiter(hold_seconds=0)
    module = importlib.import_module("plugin.plugins.hsr_companion")

    async def validate(_):
        if during_validation == "query":
            owner._note_speech_query()
        if during_validation == "epoch":
            owner._observation_epoch = getattr(owner, "_observation_epoch", 0) + 1
        if during_validation == "timeout":
            raise TimeoutError()
        return True

    monkeypatch.setattr(module, "validate_before_delivery", validate)
    if during_validation in {"failed_push", "push_exception"}:
        def push(**kwargs):
            sent.append(kwargs)
            if kwargs["ai_behavior"] == "respond":
                if during_validation == "push_exception":
                    raise RuntimeError("transport stopped")
                return {"submitted": False}
            return {"submitted": True}
        owner.push_message = push

    async def run():
        await owner._apply_local_analysis(analysis(roles=False, scene="team_setup"),
                                          image_fingerprint="a", source="test")
        await owner._speech_task
        responding = [item for item in sent if item["ai_behavior"] == "respond"]
        assert len(responding) == (1 if during_validation in {"none", "failed_push", "push_exception"} else 0)
        assert owner._get_speech_arbiter().pending is None
        counts = owner._get_speech_arbiter().snapshot()["counts"]
        if during_validation == "none":
            assert counts["submitted"] == 1
            assert responding[0]["coalesce_key"] == "hsr_proactive_event"
            assert "interrupt_policy" not in responding[0]["metadata"]
        elif during_validation in {"failed_push", "push_exception"}:
            assert counts["submission_failed"] == 1
        elif during_validation == "timeout":
            assert counts["validation_failed"] == 1

    asyncio.run(run())
