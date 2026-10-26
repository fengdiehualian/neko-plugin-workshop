"""Formal-entry regression promoted from the frozen pre-fix audit.

Pre-fix failures remain in docs/CHAIN_AUDIT_20261017_RED.xml. No production
module is patched except a transparent refresh spy or bounded test timeout.
No live game, host memory, installed plugin, images on disk or model calls.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import threading
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter, serial_ocr
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime import followup, live_refresh, manual_refresh
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot, ObservationStore
from plugin.plugins.hsr_companion.runtime.query_coordinator import needs_followup, satisfied_fields
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp
from plugin.plugins.hsr_companion.tests.test_current_observation import ROLE, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL, frame, picture, text_result
from plugin.plugins.hsr_companion.tests.test_resident_observer import configure_loop, health, sample

ENTRY = importlib.import_module("plugin.plugins.hsr_companion")
SUBS = (("攻击力", "5.2%"), ("防御力", "12.3%"), ("速度", "4"), ("生命值", "88"))


def fact(kind, scope, label, value, index):
    region = {"left": .45, "right": .85, "top": .15 + index * .055, "bottom": .19 + index * .055}
    return {"kind": kind, "scope": scope, "label": label, "value": value,
            "evidence": [{"region": region, "text": label + value}]}


def raw_page(page="relic_detail", *, subs=SUBS, level="+15", pending=False):
    raw = text_result()
    raw["page"] = {"page_type": page, "label": page}
    rows = [fact("equipment_slot", "equipment", "遗器部位", "躯干", 0),
            fact("main_stat", "equipment", "主词条·暴击伤害", "64.8%", 1)]
    rows += [fact("sub_stat", "equipment", "副词条·" + key, value, i + 2)
             for i, (key, value) in enumerate(subs)]
    if level:
        rows.append(fact("equipment_level", "equipment", "遗器强化等级", level, 6))
    raw["screen_facts"] = {"facts": rows, "visible_text": [{**CONTROL, "text": page}],
                           "field_checks": [{"scope": "equipment", "kind": "sub_stat", "state": "pending"}] if pending else []}
    return raw


def navigation_raw(*, objective=True):
    raw = raw_page("exploration_hud")
    raw["scene"].update(primary_state="exploration", verified=True, confidence="high")
    raw["screen_facts"]["facts"] = [
        fact("current_location", "location", "当前位置", "地点甲", 0),
        fact("tracked_quest", "quest", "追踪任务", "任务甲", 1),
        *([fact("quest_objective", "quest", "任务目标", "目标甲", 2)] if objective else [])]
    return raw


def simple_raw(scope, kind, label, value):
    raw = raw_page("character_attributes" if scope == "attributes" else "activity_menu")
    raw["screen_facts"]["facts"] = [fact(kind, scope, label, value, 0)]
    return raw


async def commit_raw(owner, raw, payload=None):
    payload = payload or picture()
    regions = [CONTROL, *[p for row in raw["screen_facts"]["facts"] for p in row["evidence"]],
               *[c for c in raw.get("characters", []) if c.get("region")]]
    owner._next_frame_stamp = live_refresh.frame_stamp(payload)
    owner._next_frame_page_regions = [CONTROL]
    owner._next_frame_identity_guarded = bool(raw.get("characters") and all(c.get("region") for c in raw["characters"]))
    owner._next_frame_scene_regions = regions
    owner._next_frame_query_guard = (regions, region_stamp(payload, regions))
    owner._next_frame_full_query_guard = owner._next_frame_query_guard
    owner._next_frame_delivery_guard = owner._next_frame_query_guard
    owner._analysis_game_pid = 1
    await owner._apply_local_analysis(copy.deepcopy(raw), image_fingerprint="audit-synthetic",
                                      source="assistant_local_ocr", deliver_event=False)


def audit_owner(tmp_path):
    owner, values, sent = plugin_fixture(tmp_path)
    values["observer_settings"] = {"enabled": True, "proactive_enabled": False}
    owner._observer_status["proactive_enabled"] = False
    owner._target_character = lambda: ""  # Never look up or send to a real cat.
    owner._observer_task = SimpleNamespace(done=lambda: False)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
    owner._vision = SimpleNamespace(verify_text_regions=lambda payload, proofs, **kw: {proof_key(p) for p in proofs})
    return owner, values, sent


@pytest.mark.parametrize("query,raw", [
    ("现在是什么页面", raw_page("team_setup")),
    ("现在是什么页面", raw_page("warp")),
    ("现在是什么页面", raw_page("battle_pass")),
    ("现在在哪，接下来任务做什么", navigation_raw()),
    ("这件遗器的主词条是什么", raw_page(subs=SUBS[:1], level=None)),
    ("这件遗器的主副词条是什么", raw_page()),
    ("我有多少星琼", simple_raw("resource", "resource_count", "星琼", "160")),
    ("现在属性面板写了什么", simple_raw("attributes", "stat", "速度", "134")),
    ("这个技能说了什么", simple_raw("skill", "effect_description", "技能说明", "提高造成的伤害")),
])
def test_formal_entry_currently_working_paths(tmp_path, query, raw):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw)
        result = await owner.hsr_companion_prepare(query=query)
        assert not result.get("is_error"), result
        assert result["query_answer"]["state"] == "ready", result["query_timing"]
        assert not result["followup"]["pending"]
        if "equipment" in result["query_plan"]["fields"]:
            assert result["equipment_answer"] == result["screen_context_answer"]["equipment_answer"]
            assert result["equipment_answer"]["main_stat"]["value"] == "64.8%"
        else:
            assert result["current_visual_evidence"]["page_type"] == raw["page"]["page_type"]

    asyncio.run(run())


def test_formal_identity_ignores_model_supplied_names(tmp_path):
    owner, _, _ = audit_owner(tmp_path)
    raw = raw_page("team_setup")
    raw["characters"] = [{**ROLE, **CONTROL, "identity_scope": "team_selection"}]
    raw["screen_facts"]["facts"] = []

    async def run():
        await commit_raw(owner, raw)
        await commit_raw(owner, raw)
        before = await owner._current_visual_evidence()
        assert [c["name"] for c in before["characters"]] == ["银狼"]
        result = await owner.hsr_companion_prepare(query="画面上是谁", observed_character_names=["花火"])
        assert result["screen_identity_answer"]["names"] == ["银狼"]
        assert not result["player_context"]

    asyncio.run(run())


def test_identity_geometry_is_present_in_commit_but_lost_before_scoped_guard(tmp_path):
    from plugin.plugins.hsr_companion.runtime.delivery_scope import scoped_guard
    owner, _, _ = audit_owner(tmp_path)
    raw = raw_page("team_setup")
    raw["characters"] = [{**ROLE, **CONTROL, "identity_scope": "team_selection"}]
    raw["screen_facts"]["facts"] = []

    async def run():
        await commit_raw(owner, raw)
        await commit_raw(owner, raw)
        current = await owner._current_visual_evidence()
        assert current["fresh"] and current["characters"]
        assert owner._committed_observation["characters"][0]["region"] == CONTROL["region"]
        assert "region" not in current["characters"][0]
        plan = build_query_plan("画面上是谁")
        selected = scoped_guard(owner, current, plan, [owner._accepted_frame_full_query_guard])
        assert selected is not None
        assert "region" not in selected[0]["characters"][0]  # Geometry stays internal.

    asyncio.run(run())


@pytest.mark.parametrize("query", ["现在是什么页面", "现在在哪", "现在在哪，接下来任务做什么"])
def test_formal_entry_forwards_the_actual_plan_for_every_screen_path(tmp_path, monkeypatch, query):
    owner, _, _ = audit_owner(tmp_path)
    original = ENTRY.refresh_for_query
    captured = []

    async def spy(*args, **kwargs):
        captured.append(kwargs.get("query_plan"))
        return await original(*args, **kwargs)

    monkeypatch.setattr(ENTRY, "refresh_for_query", spy)

    async def run():
        await commit_raw(owner, navigation_raw())
        result = await owner.hsr_companion_prepare(query=query)
        assert not result.get("is_error"), result
        assert captured and captured[0] is not None
        assert captured[0].public() == result["query_plan"]

    asyncio.run(run())


@pytest.mark.parametrize("case", ["unknown_level_three_rows", "pending_four_rows", "unparsed_main"])
def test_formal_entry_does_not_label_incomplete_equipment_ready(tmp_path, case):
    owner, _, _ = audit_owner(tmp_path)
    raw = raw_page(subs=SUBS[:3], level=None) if case == "unknown_level_three_rows" else raw_page(pending=True)
    if case == "unparsed_main":
        raw = raw_page(level=None)
        raw["screen_facts"]["facts"][1]["value"] = "未读到数值"
        raw["screen_facts"]["facts"][1]["evidence"][0]["text"] = "未读到数值"

    async def run():
        await commit_raw(owner, raw)
        result = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么")
        assert not result.get("is_error"), result
        assert not result["equipment_answer"]["complete"]
        assert result["query_answer"]["state"] != "ready", result["query_answer"]

    asyncio.run(run())


def test_partial_readiness_is_consistent_for_manual_and_followup(tmp_path):
    """The same partial packet cannot be upgraded by a different consumer."""
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw_page(subs=SUBS[:3], level=None))
        visual = await owner._current_visual_evidence()
        plan = build_query_plan("这件遗器的主副词条是什么")
        request = followup.AnswerRequest("audit", plan.query, "fake-cat", 0, 0, 100,
                                         "relic_detail", False, False, True, False, plan)
        assert not equipment_card(visual)["complete"]
        assert satisfied_fields(plan, visual) == set()
        assert not manual_refresh.content_ready(visual)
        assert not followup.answer_ready(visual, request)
        assert needs_followup(plan, visual, owner._observer_status)

    asyncio.run(run())


def test_historical_panel_data_is_not_current_answer_permission(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw_page())
        coordinates = {tuple(p["region"][k] for k in ("left", "top", "right", "bottom"))
                       for f in owner._committed_observation["screen_facts"]["facts"]
                       if f["kind"] in {"main_stat", "sub_stat"} for p in f["evidence"]}
        ObservationStore(owner).defer_regions(coordinates)
        display = await ObservationStore(owner).display()
        assert display["last_successful"]["screen_facts"]["equipment_card"]["complete"]
        assert not equipment_card(display["current"])["main_stat"]
        assert display["last_successful"]["fresh"] is False
        assert not satisfied_fields(build_query_plan("这件遗器的主副词条是什么"), display["current"])

    asyncio.run(run())


def test_audit_precedes_final_revocation_and_is_not_a_delivery_receipt(tmp_path):
    owner, values, _ = audit_owner(tmp_path)
    original = owner._record_tool_use

    async def pause_after_audit(*args, **kwargs):
        await original(*args, **kwargs)
        owner._observer_stop_requested = True
        owner._observer_status.update(enabled=False, state="paused")
        ObservationStore(owner).revoke()

    owner._record_tool_use = pause_after_audit

    async def run():
        await commit_raw(owner, raw_page())
        result = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么")
        assert values["tool_audit"][-1]["answer_status_at_audit"]["state"] == "ready"
        assert result["query_answer"]["state"] == "paused"
        assert not result["equipment_answer"]["main_stat"]
        assert not result["followup"]["pending"]

    asyncio.run(run())


def test_new_commit_with_same_fields_does_not_revoke_detached_answer(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw_page())
        snap = ObservationSnapshot.select(owner, await owner._current_visual_evidence(), fields={"equipment"})
        await commit_raw(owner, raw_page())
        assert snap.matches(owner)
        changed = raw_page()
        changed["screen_facts"]["facts"][1]["value"] = "32.4%"
        await commit_raw(owner, changed)
        assert not snap.matches(owner)

    asyncio.run(run())


@pytest.mark.parametrize("raw,query,expected_state", [
    (raw_page(), "这件遗器的主副词条是什么", "ready"),
    (navigation_raw(), "现在在哪，接下来任务做什么", "ready"),
    (navigation_raw(objective=False), "现在在哪，接下来任务做什么", "partial"),
])
def test_formal_resident_entry_joins_already_running_native_worker(tmp_path, raw, query, expected_state):
    owner, values, _ = audit_owner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Vision(LocalVisionAdapter):
        @serial_ocr
        def analyze(self, payload, **kwargs):
            calls.append(getattr(owner, "_visual_query_waiters", 0))
            entered.set()
            assert release.wait(3)
            return copy.deepcopy(raw)

    async def run():
        configure_loop(owner, SimpleNamespace(health=health, fetch_frame=lambda seq: sample(picture())))
        owner._vision = Vision(None)
        owner._observer_task = asyncio.create_task(owner._observer_loop())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            assert calls == [0]
            # Real public tool -> coordinator -> refresh -> resident commit.
            task = asyncio.create_task(owner.hsr_companion_prepare(query=query))
            await asyncio.sleep(.05)
            release.set()
            result = await task
            assert not result.get("is_error"), result
            assert result["query_answer"]["state"] == expected_state, result["query_timing"]
            if "equipment" in result["query_plan"]["fields"]:
                assert result["equipment_answer"]["complete"]
            if expected_state == "partial":
                assert result["query_timing"]["refresh"]["outcome"] == "partial"
            assert calls == [0]
            assert values["tool_audit"][-1]["tool"] == "hsr_companion_prepare"
        finally:
            release.set()
            owner._observer_stop_requested = True
            owner._observer_task.cancel()
            await asyncio.gather(owner._observer_task, return_exceptions=True)
            await live_refresh.drain_analysis_worker(owner)

    asyncio.run(run())


def test_submission_receipt_does_not_prove_final_cat_wording(tmp_path, monkeypatch):
    owner, _, sent = audit_owner(tmp_path)
    payloads = []
    record = followup.record_answer_delivery

    def capture(*args, **kwargs):
        payloads.append(kwargs["payload"])
        return record(*args, **kwargs)

    monkeypatch.setattr(followup, "record_answer_delivery", capture)

    async def available(*args, **kwargs):
        return "available", None

    async def validated(owner, evidence, **kwargs):
        return evidence

    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    monkeypatch.setattr(followup, "checked_activity", available)  # External host activity read.
    monkeypatch.setattr(followup, "validated_delivery", validated)  # Isolate emission boundary only.
    owner._target_character = lambda: "fake-cat"

    async def run():
        await commit_raw(owner, raw_page())
        plan = build_query_plan("这件遗器的主副词条是什么")
        request = followup.AnswerRequest("audit", plan.query, "fake-cat", 0, 0, float("inf"),
                                         "relic_detail", False, False, True, False, plan)
        owner._pending_answer = request
        await followup.deliver_followup(owner, request)
        pushes = [m for m in sent if m.get("metadata", {}).get("kind") == "query_followup"]
        assert len(pushes) == 1
        text = pushes[0]["parts"][0]["text"]
        data = payloads[-1]
        assert data["equipment_answer"]["complete"]
        assert len(data["equipment_answer"]["sub_stats"]) == 4
        from utils.result_parser import parse_push_message_content
        received = parse_push_message_content(text, lang="zh")
        for row in [data["equipment_answer"]["main_stat"], *data["equipment_answer"]["sub_stats"]]:
            assert row["label"] + row["value"] in received
        assert owner._equipment_handoffs[-1]["ack_scope"] == "plugin_payload_only_not_host_speech"
        assert owner._followup_status["state"] == "submitted"

    asyncio.run(run())


def test_formal_followup_cancels_without_respond_when_activity_channel_fails(tmp_path, monkeypatch):
    """Actual activity reader/retries; injected host IPC failure, not a live outage."""
    owner, _, sent = audit_owner(tmp_path)
    owner._target_character = lambda: "fake-cat"

    def failed_get(**kwargs):
        raise RuntimeError("injected activity IPC failure")

    monkeypatch.setattr(type(owner), "bus", property(lambda self: SimpleNamespace(memory=SimpleNamespace(get=failed_get))))
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    # A persistent failure still cancels without sending, but only at the
    # ORIGINAL obligation deadline. Use a controlled short unit-test deadline
    # rather than expecting three transient errors to cancel a 35s request.
    monkeypatch.setattr(followup, "FOLLOWUP_SECONDS", .8)

    async def run():
        await commit_raw(owner, raw_page(subs=SUBS[:3]))
        result = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么")
        assert result["query_answer"]["state"] == "partial"
        assert result["followup"]["pending"]
        await asyncio.wait_for(owner._followup_task, 3)
        # On a loaded suite the .8s test-only obligation can expire before
        # entering the IPC retry stage. Both terminal paths must stay silent;
        # test_commit_activity_repair separately exercises the retry branch.
        assert (owner._followup_status["state"], owner._followup_status["reason"]) in {
            ("cancelled", "activity_unavailable_after_retries"),
            ("expired", "no_new_verified_facts"),
        }
        assert not [m for m in sent if m.get("ai_behavior") == "respond"]

    asyncio.run(run())


def test_pure_guidance_does_not_require_live_pixels_or_claim_account_ownership(tmp_path):
    owner, values, _ = audit_owner(tmp_path)
    owner._observer_status.update(enabled=False, state="paused")

    async def run():
        before = copy.deepcopy(values["player_context"])
        result = await owner.hsr_companion_prepare(query="绯英没有专武怎么配装")
        assert result["guidance_answer"]["guides"]
        assert not result["query_plan"]["screen"]
        assert result["query_answer"]["state"] == "not_requested"
        assert not result["current_visual_evidence"]["characters"]
        assert values["player_context"] == before

    asyncio.run(run())
