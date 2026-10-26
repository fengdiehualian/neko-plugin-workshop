"""Stage-one contracts: topic, player conditions and pixels are not one state."""
from __future__ import annotations

import asyncio
import copy
import time

import pytest
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.service_contract import (
    PREPARE_TOOL_DESCRIPTION,
    SERVICE_ROUTING_RULE,
    mark_service_request,
    public_service_request,
)
from plugin.plugins.hsr_companion.tests import test_guidance as guidance_cases
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_guidance import answer, plan

book = guidance_cases.book
catalog = guidance_cases.catalog


def test_unresolved_pronoun_requests_character_evidence(book, catalog):
    query = "她的遗器和光锥怎么配？"
    demand = plan(query, book, catalog)
    assert demand.requires_pixels
    assert "characters" in demand.observation_plan(build_query_plan(query)).fields


@pytest.mark.parametrize("name", ["绯英", "真珠", "黄泉", "卡芙卡"])
def test_bare_name_completes_pending_build_question(book, catalog, name):
    unresolved = answer("她的遗器和光锥怎么配", book, catalog)
    demand = plan(name, book, catalog, previous=unresolved["context"])
    assert demand.requested and demand.character_ids
    assert not demand.requires_pixels


def test_expired_constraint_question_still_requests_clarification(book, catalog):
    first = answer("绯英怎么配队", book, catalog)
    old = {**first["context"], "at": time.monotonic() - 601}
    demand = plan("没有爻光呢", book, catalog, previous=old)
    assert demand.requested and not demand.character_ids
    result = build_guidance(demand, "没有爻光呢", book=book, catalog=catalog, profile={})
    assert result["state"] == "needs_input" and not result["guides"]


def test_now_owned_role_removes_previous_exclusion(book, catalog):
    first = answer("绯英没有爻光怎么配队", book, catalog)
    result = answer("我有爻光了", book, catalog, previous=first["context"])
    assert result["guides"][0]["name"] == "绯英"
    assert "爻光" not in result["player_conditions"]["excluded_names"]
    assert "爻光" in result["player_conditions"]["owned_characters"]


def test_signature_condition_can_be_retracted(book, catalog):
    first = answer("绯英没专武怎么配装", book, catalog)
    result = answer("现在有专武了", book, catalog, previous=first["context"])
    assert result["guides"][0]["name"] == "绯英"
    assert not result["player_conditions"]["signature_excluded"]


def test_proposed_teammate_is_not_a_new_build_subject(book, catalog):
    first = answer("绯英没有爻光怎么配队", book, catalog)
    demand = plan("换成停云行吗", book, catalog, previous=first["context"])
    assert demand.character_ids == ("character.evanescia",)
    result = build_guidance(demand, "换成停云行吗", book=book, catalog=catalog, profile={})
    assert result["player_conditions"]["proposed_teammates"] == ["停云"]
    assert any("未核实" in value for value in result["missing_information"])


def test_current_cone_assessment_keeps_pixels_and_discussion_separate(book, catalog):
    first = answer("绯英怎么配装", book, catalog)
    query = "你看这个光锥行吗"
    demand = plan(query, book, catalog, previous=first["context"])
    assert demand.requested and demand.character_ids == ("character.evanescia",)
    observed = demand.observation_plan(build_query_plan(query))
    assert observed.screen and "equipment" in observed.fields
    result = build_guidance(demand, query, book=book, catalog=catalog, profile={})
    assert not result["current_equipment_label_confirmed"]
    assert any("当前装备" in value for value in result["missing_information"])


def test_screen_question_does_not_erase_or_refresh_discussion(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await owner.hsr_companion_prepare(query="绯英怎么配装")
        before = copy.deepcopy(owner._guidance_context)
        await owner.hsr_companion_prepare(query="现在是什么界面")
        assert owner._guidance_context == before
        result = await owner.hsr_companion_prepare(query="没有爻光怎么配队")
        assert result["guidance_answer"]["guides"][0]["name"] == "绯英"
        assert not result["query_plan"]["screen"]

    asyncio.run(run())


def test_explicit_discussion_hint_is_not_visual_confirmation(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._observer_status.update(enabled=False, state="paused")

    async def run():
        result = await owner.hsr_companion_prepare(query="没有爻光怎么配队", discussion_character="绯英")
        guide = result["guidance_answer"]
        assert guide["guides"][0]["name"] == "绯英"
        assert guide["target_origin"] == "conversation_hint"
        assert result["current_visual_evidence"]["characters"] == []
        assert not result["query_plan"]["screen"]

    asyncio.run(run())


@pytest.mark.parametrize("query", ["这个光锥效果是什么", "这件遗器的主词条是什么", "这个技能说了什么", "我有多少星琼"])
def test_fact_questions_with_prior_discussion_keep_original_observation(book, catalog, query):
    first = answer("绯英怎么配装", book, catalog)
    base = build_query_plan(query)
    demand = plan_guidance(query, base, book=book, catalog=catalog, previous=first["context"])
    assert not demand.requested
    assert demand.observation_plan(base) == base and base.screen


def test_current_explicit_subject_wins_over_conversation_hint(book, catalog):
    demand = plan("真珠怎么养", book, catalog, discussion_character="绯英")
    assert demand.character_ids == ("character.pearl",) and demand.target_origin == "player_text"


def test_invalid_hint_does_not_invent_or_select_subject(book, catalog):
    demand = plan("没有爻光怎么配队", book, catalog, discussion_character="不存在的角色")
    assert not demand.character_ids


def test_displayed_teammates_do_not_become_selected_subject(book, catalog):
    demand = plan("她的遗器怎么配", book, catalog)
    visual = {"fresh": True, "scene": {"verified": True}, "characters": [
        {"entity_id": "character.evanescia", "identity_scope": "hud_label"}]}
    result = build_guidance(demand, "她的遗器怎么配", book=book, catalog=catalog, profile={}, visual=visual)
    assert not result["guides"] and result["state"] == "needs_input"


def test_verified_detail_subject_binds_build_but_not_account(book, catalog):
    demand = plan("她的遗器怎么配", book, catalog)
    visual = {"fresh": True, "scene": {"verified": True}, "characters": [
        {"entity_id": "character.evanescia", "identity_scope": "detail_subject"}]}
    result = build_guidance(demand, "她的遗器怎么配", book=book, catalog=catalog, profile={}, visual=visual)
    assert result["guides"][0]["name"] == "绯英"
    assert result["target_origin"] == "current_visual"
    assert not result["player_conditions"]["owned_characters"]


@pytest.mark.parametrize("bad_data", ["[]", "{bad-json", '{"schema_version":99}',
                                      '{"schema_version":1,"server":"cn","revision":"bad","sources":[]}'])
def test_bad_strategy_resource_does_not_break_plain_queries(tmp_path, monkeypatch, bad_data):
    from pathlib import Path

    owner, _, _ = plugin_fixture(tmp_path)
    original = Path.read_text

    def read(path, *args, **kwargs):
        return bad_data if path.name == "guidance.cn-4.6.json" else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)

    async def run():
        plain = await owner.hsr_companion_prepare(query="现在是什么界面")
        assert not plain.get("is_error") and plain["query_plan"]["screen"]
        guide = await owner.hsr_companion_prepare(query="绯英怎么配装")
        assert not guide.get("is_error")
        assert guide["guidance_answer"]["state"] == "unavailable"
        assert not guide["guidance_answer"]["guides"]

    asyncio.run(run())


def test_entry_failure_visible_without_completed_audit_and_no_private_payload(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        result = await owner.hsr_companion_prepare(query="PRIVATE", screen_fields=["unsupported"])
        assert result["is_error"]
        assert public_service_request(owner._service_request)["state"] == "failed"
        assert "PRIVATE" not in str(public_service_request(owner._service_request))
        assert not values.get("tool_audit")

    asyncio.run(run())


def test_old_request_completion_cannot_replace_latest_entry_status():
    from types import SimpleNamespace
    owner = SimpleNamespace()
    mark_service_request(owner, 1, "started")
    mark_service_request(owner, 2, "started")
    mark_service_request(owner, 1, "completed")
    assert public_service_request(owner._service_request)["generation"] == 2
    assert owner._service_request["state"] == "started"


def test_tool_discovery_and_passive_context_share_followup_routing():
    from plugin.plugins.hsr_companion.runtime.observation import visual_context_text
    for text in (PREPARE_TOOL_DESCRIPTION, SERVICE_ROUTING_RULE):
        assert "discussion_character" in text and "光锥" in text
    assert len(PREPARE_TOOL_DESCRIPTION) < 500
    assert SERVICE_ROUTING_RULE in visual_context_text({})


def test_signature_question_is_not_an_ownership_confirmation(book, catalog):
    first = answer("绯英没专武怎么配装", book, catalog)
    result = answer("她有专武吗", book, catalog, previous=first["context"])
    assert result["player_conditions"]["signature_excluded"]


def test_pause_during_query_does_not_restore_cleared_discussion(tmp_path):
    from plugin.plugins.hsr_companion.runtime.service_contract import clear_discussion
    owner, _, _ = plugin_fixture(tmp_path)
    real_read = owner._read_profile

    async def read():
        value = await real_read()
        clear_discussion(owner)
        return value

    owner._read_profile = read

    async def run():
        result = await owner.hsr_companion_prepare(query="绯英怎么配队")
        assert owner._guidance_context is None
        assert result["guidance_answer"]["state"] == "superseded"

    asyncio.run(run())


def test_diagnostics_distinguish_entry_status_from_old_audit_without_payload():
    import json

    from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
    from plugin.plugins.hsr_companion.tests.test_diagnostics import inputs

    values = inputs()
    values["service_request"] = {"state": "started", "generation": 7, "started_at": "2026-10-16T12:00:00+00:00",
                                 "query": "PRIVATE", "exception": "PRIVATE"}
    report = json.loads(build_diagnostic_report(**values)["copy_text"])
    assert report["service_request"]["state"] == "started"
    assert report["service_request"]["started_at"] == "2026-10-16T12:00:00+00:00"
    assert not report["service_request"]["tool_selection_enforced"]
    assert "PRIVATE" not in str(report)


def test_current_equipment_label_is_not_wearer_or_selected_slot(book, catalog):
    first = answer("绯英怎么配装", book, catalog)
    demand = plan("这个光锥行吗", book, catalog, previous=first["context"])
    result = build_guidance(demand, "这个光锥行吗", book=book, catalog=catalog, profile={}, visual={
        "fresh": True, "screen_facts": {"facts": [{"kind": "entity_label", "entity_type": "light_cone",
                                                     "entity_id": "light_cone.22007"}]}})
    assert result["current_equipment_label_confirmed"]
    assert result["equipment_identity_scope"] == "visible_label_not_selection_or_wearer"
    assert not result["player_conditions"]["equipment_inventory_known"]


def test_discovered_tool_schema_exposes_discussion_hint_without_new_tool(tmp_path):
    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods
    owner, _, _ = plugin_fixture(tmp_path)
    found = [meta for meta, _ in collect_llm_tool_methods(owner) if meta.name == "hsr_companion_prepare"]
    assert len(found) == 1
    assert found[0].description == PREPARE_TOOL_DESCRIPTION
    assert found[0].parameters["properties"]["discussion_character"]["type"] == "string"
