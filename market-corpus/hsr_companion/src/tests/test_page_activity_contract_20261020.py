"""Fixed scope: trace page proof loss; separate result progress from speech IPC.

Controlled box/transport inputs are not the historical screenshot or live chat.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity
from plugin.plugins.hsr_companion.tests.test_diagnostics import inputs
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box
from plugin.plugins.hsr_companion.tests.test_exploration_fields import classify, hud
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, request


@pytest.mark.parametrize("weak,expected", [(None, "accepted"), ("chat", "chat_control"),
                                         ("location", "accepted"), ("nav", "navigation")])
def test_social_control_trace_identifies_condition_without_relaxing_it(weak, expected):
    from plugin.plugins.hsr_companion.detectors.exploration import exploration_control_evidence
    boxes = hud(place="观景车厢")
    boxes.pop(1)
    boxes.append(box("Enter", 45, 720))
    if weak == "chat":
        boxes[-1]["score"] = .89
    elif weak == "location":
        boxes[0]["score"] = .89
    elif weak == "nav":
        boxes[-2]["score"] = .84
    controls, trace = exploration_control_evidence(boxes, width=1280, height=800)
    assert trace["condition"] == expected
    assert bool(controls) is (expected == "accepted")
    assert (classify(boxes)["page_type"] == "exploration_hud") is (expected == "accepted")
    assert "Enter" not in json.dumps(trace) and "观景车厢" not in json.dumps(trace, ensure_ascii=False)


def test_control_trace_is_bounded_and_cannot_export_text_or_geometry():
    from plugin.plugins.hsr_companion.runtime.page_control_trace import public_control_trace, record_controls
    owner = SimpleNamespace(_observation_epoch=1, _observation_serial=2)
    for index in range(20):
        owner._observation_serial = index
        record_controls(owner, "staged", analysis={"page": {"page_type": "exploration_hud"},
            "recognition": {"scene_regions": [{"text": "PRIVATE", "region": {"left": .1}}],
                            "control_trace": {"origin": "native_layout", "condition": "chat_control",
                                              "raw": "PRIVATE"}}})
    result = public_control_trace(owner)
    assert len(result) == 8 and result[-1]["control_count"] == 1
    assert result[-1]["condition"] == "chat_control"
    assert "PRIVATE" not in json.dumps(result) and "region" not in json.dumps(result)


def test_empty_snapshot_rejection_records_its_own_proof_source():
    from plugin.plugins.hsr_companion.runtime.delivery_scope import scoped_guard
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    owner = SimpleNamespace(_observation_epoch=2, _observation_serial=9,
                            _accepted_frame_page_regions=[{"text": "PRIVATE"}])
    evidence = {"page_type": "exploration_hud", "screen_facts": {"observation_serial": 8}}
    assert scoped_guard(owner, evidence, build_query_plan("当前页面", "read_screen", screen_fields=["page"]),
                        [], proof_source={"page_controls": []}) is None
    row = owner._page_control_trace[-1]
    assert row["stage"] == "delivery_rejected" and row["control_count"] == 0
    assert row["selection_serial"] == 8 and row["revision"] == 9


def test_followup_reads_current_result_even_when_activity_transport_fails(monkeypatch):
    owner, sent = followup_owner(monkeypatch)
    reads = []
    original = owner._current_visual_evidence
    async def current():
        reads.append(True)
        return await original()
    async def unavailable(*args, **kwargs):
        return "unavailable", None
    owner._current_visual_evidence = current
    monkeypatch.setattr(followup, "latest_user_activity", unavailable)
    req = replace(request(), expires_at=time.monotonic() + .05)
    owner._pending_answer = req
    asyncio.run(followup.deliver_followup(owner, req))
    assert reads, "Speech IPC must not prevent discovering whether the answer recovered"
    assert not sent and owner._followup_status["reason"] == "activity_unavailable_after_retries"
    assert owner._followup_status["result_ready"] is True


def test_followup_activity_budget_is_separate_and_within_original_deadline(monkeypatch):
    owner, _ = followup_owner(monkeypatch)
    calls = []
    async def available(*args, **kwargs):
        calls.append(kwargs)
        return "available", None
    monkeypatch.setattr(followup, "latest_user_activity", available)
    req = replace(request(), expires_at=time.monotonic() + .6)
    owner._pending_answer = req
    before = req.expires_at
    assert asyncio.run(followup.checked_activity(owner, req, owner.bus)) == ("available", None)
    assert .25 < calls[0]["transport_timeout"] <= .6
    assert calls[0]["wait_timeout"] <= .6
    assert req.expires_at == before


def test_sdk_reader_accepts_bounded_followup_budget_without_changing_default():
    calls = []
    async def get(**kwargs):
        calls.append(kwargs)
        return []
    bus = SimpleNamespace(memory=SimpleNamespace(get=get))
    async def run():
        assert await latest_user_activity(bus, target="cat", transport_timeout=1., wait_timeout=1.1) == ("available", None)
        assert await latest_user_activity(bus, target="cat") == ("available", None)
    asyncio.run(run())
    assert [c["timeout"] for c in calls] == [1., .25]


@pytest.mark.parametrize("new_message", [False, True])
def test_slow_sdk_reply_is_used_but_new_user_still_cancels(monkeypatch, new_message):
    from plugin.sdk.plugin import NekoPluginBase
    owner, sent = followup_owner(monkeypatch)
    req = replace(request(), expires_at=time.monotonic() + 6)
    calls = []
    async def get(**kwargs):
        calls.append(kwargs)
        await asyncio.sleep(min(.45, kwargs["timeout"]))
        if kwargs["timeout"] < .45:
            raise TimeoutError("Controlled healthy response is slower than old .25s budget")
        return [{"type": "user_message", "lanlan": req.target,
                 "_ts": req.asked_at + (.001 if new_message and len(calls) > 1 else 0)}]
    sdk = NekoPluginBase(SimpleNamespace(plugin_id="isolated-activity", metadata={},
        bus=SimpleNamespace(memory=SimpleNamespace(get=get))))
    owner.bus = sdk.bus
    monkeypatch.setattr(followup, "latest_user_activity", latest_user_activity)
    owner._pending_answer = req
    asyncio.run(followup.deliver_followup(owner, req))
    assert len(calls) >= 2
    assert bool(sent) is (not new_message)
    if new_message:
        # A timestamp alone no longer means cancellation: without a semantic
        # relation the old submission stays held until its ORIGINAL deadline.
        assert owner._followup_status["reason"] == "message_relation_unresolved"


def test_report_separates_result_readiness_from_activity_failure():
    from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
    values = inputs()
    values["followup_status"] = {"state": "pending", "reason": "activity_retry", "result_ready": True}
    values["page_control_trace"] = [{"stage": "staged", "origin": "native_layout", "control_count": 0,
                                     "condition": "chat_control", "raw": "PRIVATE"}]
    result = json.loads(build_diagnostic_report(**values)["copy_text"])
    assert result["explicit_followup"]["result_ready"] is True
    assert result["page_control_trace"][-1]["condition"] == "chat_control"
    assert "PRIVATE" not in json.dumps(result)


def test_full_reader_retains_probe_control_origin(tmp_path):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.tests.test_dynamic_handoff_20261017 import pixels
    from plugin.plugins.hsr_companion.tests.test_experience_fixes import catalog
    calls = []
    def read(image):
        calls.append(True)
        return ("队伍编成", [box("队伍编成", 20, 20)]) if len(calls) == 1 else ("", [])
    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=read)
    result = adapter.analyze_live(pixels(), cancelled=lambda: False, on_scene=lambda value: None)
    trace = result["recognition"]["control_trace"]
    assert trace["origin"] == "same_capture_preview" and trace["control_count"] > 0
    assert copy.deepcopy(result["recognition"]["scene_regions"])


@pytest.mark.parametrize("missing", [False, True])
def test_social_page_does_not_require_a_readable_location_to_prove_page(missing):
    from plugin.plugins.hsr_companion.tests.test_exploration_fields import extract
    boxes = hud(place="观景车厢")
    boxes.pop(1)
    boxes.append(box("Enter", 45, 720))
    if missing:
        boxes.pop(0)
    else:
        boxes[0]["score"] = .89
    assert classify(boxes)["page_type"] == "exploration_hud"
    facts = extract(boxes)["facts"]
    assert not any(f["kind"] == "current_location" for f in facts)
    assert any(f["kind"] == "quest_objective" for f in facts)
