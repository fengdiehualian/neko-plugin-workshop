"""19:01 failure contracts: retain validated candidates; don't lose obligations.

Synthetic controlled clocks/SDK failures reproduce coordination, not live OCR.
"""
from __future__ import annotations

import asyncio
import importlib
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.fact_lifecycle import late_read_proved
from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp
from plugin.plugins.hsr_companion.runtime.review_policy import producer_receipt
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, request
from plugin.plugins.hsr_companion.tests.test_query_reuse import REGIONS, picture
from plugin.plugins.hsr_companion.tests.test_resident_observer import configure_loop, health, sample
from plugin.sdk.plugin import NekoPluginBase


@pytest.mark.parametrize("change", ["epoch", "pid", "stopped", "unverified_page", "no_controls", "no_guard", "future", "nan"])
def test_candidate_retention_still_rejects_invalid_lifecycle_or_proof(change):
    owner = SimpleNamespace(_observation_epoch=2, _analysis_game_pid=7,
        _next_frame_page_regions=[{}], _next_frame_query_guard=([{}], (1,)))
    value = {"scene": {"verified": True}, "_candidate_validation": {
        "epoch": 2, "game_pid": 7, "checked_frame_at": time.time() - 4}}
    if change == "epoch":
        owner._observation_epoch = 3
    elif change == "pid":
        owner._analysis_game_pid = 8
    elif change == "stopped":
        owner._observer_stop_requested = True
    elif change == "unverified_page":
        value["scene"]["verified"] = False
    elif change == "no_controls":
        owner._next_frame_page_regions = []
    elif change == "no_guard":
        owner._next_frame_query_guard = None
    else:
        value["_candidate_validation"]["checked_frame_at"] = time.time() + 1 if change == "future" else float("nan")
    assert not late_read_proved(owner, value)


def test_real_guarded_producer_keeps_candidate_when_question_changes_during_validation(tmp_path, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import live_refresh
    owner, _, _ = plugin_fixture(tmp_path)
    payload = picture()
    owner._vision = SimpleNamespace(analyze=lambda image: analysis(roles=False, scene="menu"))
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: sample(payload))
    original = live_refresh.stable_screen_content

    def validate(*args, **kwargs):
        result = original(*args, **kwargs)
        owner._query_refresh_generation = getattr(owner, "_query_refresh_generation", 0) + 1
        return result

    monkeypatch.setattr(live_refresh, "stable_screen_content", validate)
    result = asyncio.run(live_refresh.guarded_analysis(owner, payload, helper=True))
    assert result is not None
    assert result["_candidate_validation"]["epoch"] == getattr(owner, "_observation_epoch", 0)
    assert result["_candidate_validation"]["game_pid"] == 1
    assert "_live_validation" not in result


@pytest.mark.parametrize("changed_query", [False, True])
def test_slow_validated_candidate_is_committed_without_minting_fresh_receipt(tmp_path, monkeypatch, changed_query):
    owner, values, _ = plugin_fixture(tmp_path)
    module = importlib.import_module(type(owner).__module__)
    payload = picture()
    configure_loop(owner, SimpleNamespace(health=health, fetch_frame=lambda seq: sample(payload)))
    owner._query_refresh_generation = 0
    owner._answer_query_generation = 0
    clock = [100.0]

    async def completed(plugin, image, **kwargs):
        clock[0] += 36.0
        value = analysis(roles=False, scene="menu")
        plugin._next_frame_page_regions = REGIONS[:1]
        plugin._next_frame_query_guard = (REGIONS, region_stamp(payload, REGIONS))
        plugin._next_frame_delivery_guard = plugin._next_frame_query_guard
        value["_candidate_validation"] = {
            "epoch": getattr(plugin, "_observation_epoch", 0),
            "game_pid": 1, "checked_frame_at": time.time() - 4,
        }
        value["_live_validation"] = {
            "epoch": getattr(plugin, "_observation_epoch", 0),
            "query_generation": 0, "answer_generation": 0,
            "checked_frame_at": time.time() - 4,
        }
        if changed_query:
            plugin._query_refresh_generation = 1
            plugin._answer_query_generation = 1
            value.pop("_live_validation")
        return value

    async def stop(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(module, "guarded_analysis", completed)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(to_thread=asyncio.to_thread,
        sleep=stop, CancelledError=asyncio.CancelledError))
    owner._wait_observer_tick = stop
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(owner._observer_loop())
    assert values.get("last_local_vision"), "A successful producer candidate was thrown away after validation"
    assert owner._committed_observation
    # Retention is not permission to answer from a four-second-old proof.
    evidence = {"fresh": True, "screen_facts": {"observation_serial": owner._observation_serial}}
    assert producer_receipt(owner, evidence) is None


@pytest.mark.parametrize("where", ["before_result", "before_submission"])
def test_three_transient_activity_failures_do_not_cancel_registered_answer(monkeypatch, where):
    owner, sent = followup_owner(monkeypatch)
    calls = []

    async def activity(*args, **kwargs):
        calls.append(True)
        if (1 <= len(calls) <= 3 if where == "before_result" else 2 <= len(calls) <= 4):
            return "unavailable", None
        return "available", None

    monkeypatch.setattr(followup, "latest_user_activity", activity)
    req = replace(request(), expires_at=time.monotonic() + 4)
    owner._pending_answer = req
    asyncio.run(followup.deliver_followup(owner, req))
    assert len(sent) == 1, "Transient guard failure discarded the answer instead of retrying inside its original deadline"
    assert sent[0]["metadata"]["outcome"] == "ready"
    assert len(calls) >= 4


def test_persistent_activity_failure_never_authorizes_blind_answer(monkeypatch):
    owner, sent = followup_owner(monkeypatch)

    async def activity(*args, **kwargs):
        return "unavailable", None

    monkeypatch.setattr(followup, "latest_user_activity", activity)
    req = replace(request(), expires_at=time.monotonic() + .05)
    owner._pending_answer = req
    asyncio.run(followup.deliver_followup(owner, req))
    assert not sent and owner._pending_answer is None
    assert owner._followup_status["reason"] == "activity_unavailable_after_retries"


@pytest.mark.parametrize("after_recovery", ["same_question", "new_user_message"])
def test_real_sdk_wrapper_and_activity_reader_recover_without_bypassing_user_guard(monkeypatch, after_recovery):
    from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity
    owner, sent = followup_owner(monkeypatch)
    req = replace(request(), expires_at=time.monotonic() + 4)
    calls = []

    async def host_get(**kwargs):
        calls.append(kwargs)
        if len(calls) <= 3:
            raise TimeoutError("injected IPC failure")
        return [{"type": "user_message", "lanlan": "猫娘",
                 "_ts": req.asked_at + (1 if after_recovery == "new_user_message" else 0)}]

    # Public SDK wrapper -> real threaded activity reader -> real retries ->
    # real final guard. No replacement of latest_user_activity with a result.
    sdk = NekoPluginBase(SimpleNamespace(plugin_id="hsr_activity_test", metadata={},
        bus=SimpleNamespace(memory=SimpleNamespace(get=host_get))))
    owner.bus = sdk.bus
    monkeypatch.setattr(followup, "latest_user_activity", latest_user_activity)
    owner._pending_answer = req
    asyncio.run(followup.deliver_followup(owner, req))
    assert len(calls) >= 4
    # Followup IPC has a separate bounded budget; proactive reads still keep
    # their original .25s default. This assertion follows the new IPC contract.
    assert all(0 < c["timeout"] <= followup.ACTIVITY_TRANSPORT_SECONDS and c["limit"] == 8 for c in calls)
    if after_recovery == "same_question":
        assert len(sent) == 1 and sent[0]["metadata"]["outcome"] == "ready"
    else:
        assert not sent and owner._followup_status["reason"] == "message_relation_unresolved"
