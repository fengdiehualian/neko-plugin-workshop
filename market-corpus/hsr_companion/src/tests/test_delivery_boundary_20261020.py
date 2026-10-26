"""Frozen delivery-boundary cases: synthetic OCR, actual resident/formal paths.

Success here is not live-window OCR or host speech acceptance. No host writes.
"""
from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import replace

import pytest
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot, ObservationStore
from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import (
    audit_owner,
    commit_raw,
    navigation_raw,
    raw_page,
)
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL


async def formal_case(tmp_path, mutation, *, equipment=False, fields=None):
    owner, values, sent = audit_owner(tmp_path)
    raw = raw_page() if equipment else navigation_raw()
    if mutation == "equipment_added":
        raw["screen_facts"]["facts"].pop(-2)
    if mutation == "quest_added":
        raw["screen_facts"]["facts"] = [f for f in raw["screen_facts"]["facts"] if f["scope"] != "quest"]
    await commit_raw(owner, raw)
    original_audit = owner._record_tool_use

    async def audited(*args, **kwargs):
        await original_audit(*args, **kwargs)
        newer = copy.deepcopy(raw)
        if mutation == "box_jitter":
            for row in newer["screen_facts"]["visible_text"]:
                row["region"]["left"] += 1 / 640
            for fact in newer["screen_facts"]["facts"]:
                for proof in fact["evidence"]:
                    proof["region"]["left"] += 1 / 640
            await commit_raw(owner, newer)
        elif mutation == "quest_added":
            await commit_raw(owner, navigation_raw())
        elif mutation == "equipment_added":
            await commit_raw(owner, raw_page())
        elif mutation in {"quest_changed", "number_changed", "equipment_incomplete"}:
            if mutation == "quest_changed":
                newer["screen_facts"]["facts"][-1]["value"] = "目标乙"
            elif mutation == "number_changed":
                newer["screen_facts"]["facts"][-2]["value"] = "18"
            else:
                newer["screen_facts"]["facts"].pop(-2)
                newer["screen_facts"]["facts"][1]["value"] = "32.4%"
            await commit_raw(owner, newer)
        elif mutation in {"quest_challenged", "control_challenged"}:
            from plugin.plugins.hsr_companion.runtime.delivery_scope import coordinates
            proof = CONTROL if mutation == "control_challenged" else raw["screen_facts"]["facts"][-1]["evidence"][0]
            ObservationStore(owner).defer_regions({coordinates(proof)})
        elif mutation == "capture_failed":
            ObservationStore(owner).suspend("capture_unavailable_test")
        elif mutation == "new_question":
            owner._answer_query_generation += 1
        elif mutation == "paused":
            owner._observer_status.update(enabled=False, state="paused")
            owner._observer_stop_requested = True
        elif mutation == "new_page":
            await commit_raw(owner, raw_page("team_setup"))
        elif mutation == "identical":
            await commit_raw(owner, newer)

    owner._record_tool_use = audited
    packet = await owner.hsr_companion_prepare(query="完整报出遗器主词条和四项副词条" if equipment else "我打开的是啥页面？",
        intent="read_screen", screen_fields=fields or (["equipment"] if equipment else ["page", "quest", "visible_text"]))
    assert not any(s.get("target_lanlan") for s in sent)
    return packet, values, owner


@pytest.mark.parametrize("equipment", [False, True])
def test_same_values_geometry_jitter_keeps_formal_answer(tmp_path, equipment):
    packet, values, _ = asyncio.run(formal_case(tmp_path, "box_jitter", equipment=equipment))
    assert values["tool_audit"][-1]["answer_status_at_audit"]["state"] == "ready"
    assert packet["query_answer"]["state"] == "ready", packet["delivery_receipt"]
    if equipment:
        card = packet["equipment_answer"]
        assert card["complete"] and len(card["sub_stats"]) == 4
        assert [(r["label"], r["value"]) for r in card["sub_stats"]] == [
            ("攻击力", "5.2%"), ("防御力", "12.3%"), ("速度", "4"), ("生命值", "88")]


def test_added_quest_does_not_revoke_page_or_invent_new_values(tmp_path):
    packet, _, _ = asyncio.run(formal_case(tmp_path, "quest_added"))
    assert packet["screen_scene_answer"]["allowed"]
    assert packet["query_answer"]["confirmed_fields"] == ["page", "visible_text"]
    assert "目标甲" not in str(packet["current_visual_evidence"]["screen_facts"]["facts"])
    assert packet["delivery_receipt"]["payload_serial"] > 0


@pytest.mark.parametrize("mutation", ["quest_challenged", "quest_changed"])
def test_bad_quest_withholds_quest_not_the_independent_page(tmp_path, mutation):
    packet, _, _ = asyncio.run(formal_case(tmp_path, mutation))
    assert packet["screen_scene_answer"]["allowed"]
    assert packet["query_answer"]["state"] == "partial"
    assert "目标甲" not in str(packet["current_visual_evidence"]["screen_facts"]["facts"])
    assert packet["current_visual_evidence"]["fresh"]


@pytest.mark.parametrize("mutation", ["control_challenged", "capture_failed", "new_question", "paused", "new_page"])
def test_real_lifecycle_or_page_uncertainty_still_blocks_old_answer(tmp_path, mutation):
    packet, _, _ = asyncio.run(formal_case(tmp_path, mutation))
    assert not packet["current_visual_evidence"]["fresh"]
    assert not packet["screen_scene_answer"]["allowed"]
    assert not packet["query_answer"]["confirmed_fields"]


@pytest.mark.parametrize("mutation", ["number_changed", "equipment_incomplete"])
def test_new_item_or_incomplete_replacement_never_merges_old_rows(tmp_path, mutation):
    packet, _, _ = asyncio.run(formal_case(tmp_path, mutation, equipment=True, fields=["page", "equipment"]))
    assert packet["screen_scene_answer"]["allowed"]
    assert not packet["equipment_answer"]["main_stat"]
    assert not packet["equipment_answer"]["sub_stats"]
    assert packet["query_answer"]["state"] == "partial"


def test_final_receipt_reports_boundary_reason_not_just_empty_payload(tmp_path):
    packet, _, _ = asyncio.run(formal_case(tmp_path, "quest_changed"))
    decision = packet["delivery_receipt"]["boundary"]
    assert decision["state"] == "partial"
    assert "quest" in decision["withheld_fields"]
    assert decision["source_serial"] < decision["current_serial"]
    assert "目标" not in str(decision) and "region" not in str(decision)


def test_current_geometry_does_not_authorize_missing_or_changed_number(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw_page())
        snapshot = ObservationSnapshot.select(owner, await owner._current_visual_evidence(), fields={"equipment"})
        newer = raw_page()
        newer["screen_facts"]["facts"][-2]["value"] = "18"
        await commit_raw(owner, newer)
        assert not snapshot.matches(owner)
        selected = snapshot.delivery(owner)
        assert not selected["screen_facts"]["equipment_card"]["main_stat"]
        assert not selected["screen_facts"]["equipment_card"]["sub_stats"]
    asyncio.run(run())


def test_main_only_query_still_carries_whole_item_swap_protection(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw_page())
        selected = await owner._current_visual_evidence()
        selected["screen_facts"]["facts"] = [f for f in selected["screen_facts"]["facts"] if f["kind"] != "sub_stat"]
        snapshot = ObservationSnapshot.select(owner, selected, fields={"equipment"})
        newer = raw_page()
        newer["screen_facts"]["facts"][-2]["value"] = "18"
        await commit_raw(owner, newer)
        assert not snapshot.matches(owner)
        assert not snapshot.delivery(owner)["screen_facts"]["equipment_card"]["main_stat"]
    asyncio.run(run())


def test_resident_updates_many_times_then_formal_query_keeps_complete_item(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        for index in range(10):
            raw = raw_page()
            raw["screen_facts"]["facts"][1]["evidence"][0]["region"]["left"] += index / 640
            await commit_raw(owner, raw)
        packet = await owner.hsr_companion_prepare(query="完整报出主词条和四项副词条", intent="read_screen", screen_fields=["equipment"])
        assert packet["equipment_answer"]["complete"]
        assert len(packet["equipment_answer"]["sub_stats"]) == 4
        assert packet["query_answer"]["state"] == "ready"
    asyncio.run(run())


def test_weak_same_item_reread_does_not_erase_complete_candidate(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw_page())
        weaker = raw_page()
        weaker["screen_facts"]["facts"].pop(-2)
        await commit_raw(owner, weaker)
        packet = await owner.hsr_companion_prepare(query="完整报出主词条和四项副词条", intent="read_screen", screen_fields=["equipment"])
        assert packet["equipment_answer"]["complete"]
        assert len(packet["equipment_answer"]["sub_stats"]) == 4
    asyncio.run(run())


@pytest.mark.parametrize("changed_number", [False, True])
def test_followup_formatter_update_keeps_identical_item_but_reselects_changed_item(tmp_path, monkeypatch, changed_number):
    from plugin.plugins.hsr_companion.runtime import answer_message, followup
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    from plugin.plugins.hsr_companion.tests.test_page_followup import request

    owner, _, sent = audit_owner(tmp_path)
    owner._target_character = lambda: "fake-cat"
    original = answer_message.render_followup
    render_count = 0

    async def activity(*args, **kwargs):
        return "available", None

    async def validated(owner, evidence, **kwargs):
        return evidence

    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    monkeypatch.setattr(followup, "latest_user_activity", activity)
    monkeypatch.setattr(followup, "validated_delivery", validated)

    async def run():
        nonlocal render_count
        await commit_raw(owner, raw_page())
        loop = asyncio.get_running_loop()

        def rendering(data, **kwargs):
            nonlocal render_count
            render_count += 1
            if render_count == 1:
                newer = raw_page()
                for fact in newer["screen_facts"]["facts"]:
                    for proof in fact["evidence"]:
                        proof["region"]["left"] += 1 / 640
                if changed_number:
                    newer["screen_facts"]["facts"][-2]["value"] = "18"
                asyncio.run_coroutine_threadsafe(commit_raw(owner, newer), loop).result(timeout=3)
            return original(data, **kwargs)

        monkeypatch.setattr(answer_message, "render_followup", rendering)
        req = replace(request(scene_only=False, content=True), target="fake-cat",
            expires_at=time.monotonic() + 8,
            plan=build_query_plan("完整报出遗器主词条和四项副词条", intent="read_screen", screen_fields=["equipment"]))
        owner._pending_answer = req
        await asyncio.wait_for(followup.deliver_followup(owner, req), 10)

    asyncio.run(run())
    replies = [s for s in sent if s.get("ai_behavior") == "respond"]
    assert len(replies) == 1
    assert render_count == (2 if changed_number else 1)
    # The final record is built from the SAME source as the submitted text.
    receipt = owner._answer_deliveries[-1]
    assert receipt["answer_state"] == "ready"


def test_boundary_diagnostic_never_exports_arbitrary_payload():
    from plugin.plugins.hsr_companion.runtime.diagnostics import _delivery_summary
    result = _delivery_summary({"boundary": {"state": "partial", "reason": "fields_revoked",
        "source_serial": 1, "current_serial": 2, "withheld_fields": ["quest", "private text"],
        "raw_text": "secret", "region": {"left": .1}}})
    assert result["boundary"]["withheld_fields"] == ["quest"]
    assert "secret" not in str(result) and "region" not in str(result)


def test_newly_completed_item_keeps_previously_confirmed_subset_without_borrowing_fourth_row(tmp_path):
    packet, values, _ = asyncio.run(formal_case(tmp_path, "equipment_added", equipment=True))
    assert values["tool_audit"][-1]["answer_status_at_audit"]["state"] == "partial"
    assert packet["equipment_answer"]["main_stat"]["value"] == "64.8%"
    assert len(packet["equipment_answer"]["sub_stats"]) == 3
    assert not packet["equipment_answer"]["complete"]


def test_skill_subject_is_semantic_identity_not_diagnostic_metadata():
    from plugin.plugins.hsr_companion.runtime.delivery_boundary import semantic_key
    first = {"kind": "effect_description", "scope": "skill", "value": "提高伤害", "subject_entity_id": "character.a"}
    assert semantic_key(first) != semantic_key({**first, "subject_entity_id": "character.b"})


def test_controlled_identity_box_jitter_does_not_revoke_same_actor(tmp_path):
    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
    from plugin.plugins.hsr_companion.session import utc_now_iso
    owner, _, _ = audit_owner(tmp_path)
    raw = navigation_raw()
    raw.update(observed_at=utc_now_iso(), ok=True, source="assistant_local_ocr",
        observation_serial=1, runtime={"stable_scene": "exploration", "stable_character_ids": ["character.a"]},
        characters=[{"name": "甲", "entity_id": "character.a", "slot": 1, "identity_scope": "hud_label", "evidence": "frame-a"}],
        selection={"slot": 1, "verified": True})
    raw["recognition"]["team_rows"] = [{}, {}, {}, {}]
    evidence = public_visual_evidence(raw, owner._observer_status)
    owner._observation_serial = 1
    owner._committed_observation = copy.deepcopy(raw)
    snapshot = ObservationSnapshot.select(owner, evidence, fields={"controlled_character"})
    # Raw recognition timestamp is genuinely current; this fixture isolates
    # only the identity comparison, not freshness policy.
    owner._committed_observation.update(observed_at=utc_now_iso(), observation_serial=2)
    owner._committed_observation["characters"][0]["evidence"] = "frame-b"
    owner._observation_serial = 2
    assert snapshot.matches(owner)
