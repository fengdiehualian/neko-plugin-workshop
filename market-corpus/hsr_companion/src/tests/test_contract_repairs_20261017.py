"""Negative/compatibility contracts for stage2a-20261017.2; no live host."""
import asyncio
import copy
import hashlib
import json

import pytest
from plugin.plugins.hsr_companion.runtime.delivery_scope import scoped_guard
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
from plugin.plugins.hsr_companion.runtime.query_coordinator import (
    answer_status,
    has_answerable_content,
    satisfied_fields,
)
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.service_contract import record_answer_delivery
from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import SUBS, audit_owner, commit_raw, raw_page
from plugin.plugins.hsr_companion.tests.test_current_observation import ROLE
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL, frame, picture
from plugin.plugins.hsr_companion.tests.test_diagnostics import inputs


@pytest.mark.parametrize("pending_kind,query,ready", [
    ("sub_stat", "这件遗器的主词条是什么", True),
    ("main_stat", "这件遗器的主词条是什么", False),
    ("main_stat", "这件遗器的副词条是什么", True),
    ("sub_stat", "这件遗器的副词条是什么", False),
    ("equipment_binding_missing", "这件遗器的主词条是什么", False),
])
def test_pending_is_per_requested_kind_not_a_global_boolean(tmp_path, pending_kind, query, ready):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        raw = raw_page()
        raw["screen_facts"]["field_checks"] = [{"scope": "equipment", "kind": pending_kind, "state": "pending"}]
        await commit_raw(owner, raw)
        visual = await owner._current_visual_evidence()
        plan = build_query_plan(query)
        assert bool(satisfied_fields(plan, visual)) is ready
        status = answer_status(plan, visual, owner._observer_status)
        assert (status["state"] == "ready") is ready

    asyncio.run(run())


@pytest.mark.parametrize("level", [None, "+0", "+3", "+12", "+15"])
def test_fewer_rows_are_useful_partials_not_a_fabricated_total(tmp_path, level):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw_page(subs=SUBS[:3], level=level))
        result = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么")
        card = result["equipment_answer"]
        assert len(card["sub_stats"]) == 3
        assert not card["complete"]
        assert card["expected_sub_stat_count"] == (4 if level == "+15" else None)
        assert result["query_answer"]["state"] == "partial"
        assert result["query_timing"]["refresh"]["outcome"] == "partial"
        assert has_answerable_content(build_query_plan("这件遗器的主副词条是什么"), result["current_visual_evidence"])

    asyncio.run(run())


@pytest.mark.parametrize("damage", ["epoch", "serial", "missing", "ambiguous", "patch_missing"])
def test_identity_proofs_cannot_be_borrowed_or_invented(tmp_path, damage):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        raw = raw_page("team_setup")
        raw["characters"] = [{**ROLE, **CONTROL, "identity_scope": "team_selection"}]
        raw["screen_facts"]["facts"] = []
        await commit_raw(owner, raw)
        await commit_raw(owner, raw)
        cached = copy.deepcopy(owner._reusable_observations["full"])
        evidence = cached["evidence"]
        guards = [cached["guard"]]
        if damage == "epoch":
            cached["epoch"] += 1
        elif damage == "serial":
            evidence["screen_facts"]["observation_serial"] += 1
            evidence = copy.deepcopy(evidence)
            evidence["screen_facts"]["observation_serial"] -= 1
        elif damage == "missing":
            cached["identity_proofs"] = []
        elif damage == "ambiguous":
            actor = copy.deepcopy(cached["identity_proofs"][0])
            actor["region"] = {"left": .6, "right": .9, "top": .7, "bottom": .8}
            cached["identity_proofs"].append(actor)
        else:
            guards = []
        assert scoped_guard(owner, evidence, build_query_plan("画面上是谁"), guards, proof_source=cached) is None

    asyncio.run(run())


def test_retained_role_uses_its_own_geometry_not_the_latest_commit(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        raw = raw_page("team_setup")
        raw["characters"] = [{**ROLE, **CONTROL, "identity_scope": "team_selection"}]
        raw["screen_facts"]["facts"] = []
        await commit_raw(owner, raw)
        await commit_raw(owner, raw)
        cached = copy.deepcopy(owner._reusable_observations["full"])
        raw["characters"][0]["region"] = {"left": .6, "right": .9, "top": .7, "bottom": .8}
        await commit_raw(owner, raw)
        plan = build_query_plan("画面上是谁")
        assert scoped_guard(owner, cached["evidence"], plan, [cached["guard"]]) is None
        selected = scoped_guard(owner, cached["evidence"], plan, [cached["guard"]], proof_source=cached)
        assert selected is not None
        assert selected[1][0][0]["region"] == CONTROL["region"]
        assert "region" not in selected[0]["characters"][0]

    asyncio.run(run())


def test_formal_menu_navigation_returns_page_without_waiting_for_nonexistent_hud(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        raw = raw_page("warp")
        raw["screen_facts"]["facts"] = []
        await commit_raw(owner, raw)
        result = await owner.hsr_companion_prepare(query="现在在哪，接下来任务做什么")
        assert result["query_timing"]["refresh"]["outcome"] == "partial"
        assert not result["query_answer"]["applicable"]
        assert not result["followup"]["pending"]
        assert result["current_visual_evidence"]["page_type"] == "warp"

    asyncio.run(run())


def test_final_receipt_hashes_the_return_body_not_an_earlier_projection(tmp_path):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit_raw(owner, raw_page())
        result = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么")
        body = copy.deepcopy(result)
        receipt = body.pop("delivery_receipt")
        expected = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        assert receipt["body_digest"] == expected
        assert receipt["equipment"]["content_digest"] == result["equipment_answer"]["content_digest"]
        assert receipt["equipment"]["sub_stat_count"] == 4
        assert receipt["answer_state"] == result["query_answer"]["state"] == "ready"
        assert receipt["generation"] == owner._answer_query_generation
        assert receipt["ack_scope"] == "plugin_return_payload_only_not_host_speech"
        result["delivery_receipt"]["confirmed_fields"].clear()
        assert owner._answer_deliveries[-1]["confirmed_fields"] == ["equipment"]

    asyncio.run(run())


@pytest.mark.parametrize("word", ["页面", "界面", "场景"])
def test_role_page_mixed_query_preserves_identity_scope_and_private_proofs(tmp_path, word):
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        raw = raw_page("team_setup")
        raw["characters"] = [{**ROLE, **CONTROL, "identity_scope": "team_selection"}]
        raw["screen_facts"]["facts"] = []
        await commit_raw(owner, raw)
        await commit_raw(owner, raw)
        result = await owner.hsr_companion_prepare(query=f"现在是什么{word}？都有谁？")
        assert result["query_answer"]["state"] == "ready"
        assert result["current_visual_evidence"]["page_type"] == "team_setup"
        assert result["screen_identity_answer"]["names"] == ["银狼"]
        assert result["current_visual_evidence"]["characters"][0]["identity_scope"] == "team_selection"
        assert "region" not in result["current_visual_evidence"]["characters"][0]

    asyncio.run(run())


def test_final_receipts_are_bounded_and_export_no_raw_game_or_chat_text(tmp_path):
    from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
    owner, _, _ = audit_owner(tmp_path)
    visual = {"fresh": False}
    answer = {"state": "unconfirmed", "confirmed_fields": [], "missing_fields": ["equipment"]}
    for generation in range(20):
        record_answer_delivery(owner, generation=generation, channel="direct", payload={"secret": "PRIVATE_TEST_TEXT"},
                               visual=visual, answer=answer)
    assert len(owner._answer_deliveries) == 8
    assert "PRIVATE_TEST_TEXT" not in json.dumps(owner._answer_deliveries)
    latest = owner._answer_deliveries[-1]
    latest.update(query="PRIVATE_TEST_TEXT", confirmed_fields="PRIVATE_TEST_TEXT", equipment="PRIVATE_TEST_TEXT")
    report = json.loads(build_diagnostic_report(**inputs(), answer_deliveries=owner._answer_deliveries)["copy_text"])
    assert "PRIVATE_TEST_TEXT" not in json.dumps(report)
    assert len(report["answer_deliveries"]) == 8


def test_stage_audit_ready_does_not_override_final_paused_receipt(tmp_path):
    owner, values, _ = audit_owner(tmp_path)
    original = owner._record_tool_use

    async def paused(*args, **kwargs):
        await original(*args, **kwargs)
        owner._observer_stop_requested = True
        owner._observer_status.update(enabled=False, state="paused")

    owner._record_tool_use = paused

    async def run():
        await commit_raw(owner, raw_page())
        result = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么")
        assert values["tool_audit"][-1]["answer_status_at_audit"]["state"] == "ready"
        assert result["delivery_receipt"]["answer_state"] == "paused"
        assert not result["delivery_receipt"]["equipment"]["main_stat_present"]
        assert not equipment_card(result["current_visual_evidence"])["main_stat"]

    asyncio.run(run())


@pytest.mark.parametrize("query", ["现在是什么页面", "现在在哪，接下来任务做什么"])
def test_final_page_navigation_validation_does_not_depend_on_unasked_equipment(tmp_path, query):
    from types import SimpleNamespace

    from plugin.plugins.hsr_companion.runtime.live_refresh import validated_delivery
    from plugin.plugins.hsr_companion.tests.test_delivery_races import commit
    owner, _, _ = audit_owner(tmp_path)

    async def run():
        await commit(owner, picture())
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        plan = build_query_plan(query)
        selected = await validated_delivery(owner, await owner._current_visual_evidence(), plan=plan)
        assert selected is not None
        assert not selected["screen_facts"]["facts"]
        assert selected["page_type"] == "relic_detail"

    asyncio.run(run())
