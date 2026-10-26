"""0.9.6 stage-one repairs: service contracts, not live OCR or advice quality."""
from __future__ import annotations

import asyncio
import copy
import time

import pytest
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_coordinator import answer_status, satisfied_fields
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture

book = cases.book
catalog = cases.catalog


@pytest.mark.parametrize("query", [
    "现在你看看我有专了，还遗器什么的还需要换吗",
    "我有专武了，遗器需要换吗？",
    "抽到专锥了，脚部还要换吗？",
    "现在有专了",
])
def test_asserted_signature_and_separate_question_update_condition(book, catalog, query):
    prior = cases.answer("真珠没有专武怎么配装", book, catalog)["context"]
    result = cases.answer(query, book, catalog, previous=prior)
    assert result["guides"][0]["name"] == "真珠"
    assert not result["player_conditions"]["signature_excluded"]


@pytest.mark.parametrize("query", ["有专武吗？", "如果有专武，遗器需要换吗？", "没有专武了", "我没专了"])
def test_questions_hypotheses_and_negative_do_not_claim_signature(book, catalog, query):
    prior = cases.answer("真珠没专武怎么配装", book, catalog)["context"]
    result = cases.answer(query, book, catalog, previous=prior)
    assert result["requested"] and result["player_conditions"]["signature_excluded"]


@pytest.mark.parametrize("query", ["看看～", "这个呢", "看词条笨蛋（敲）", "词条合适吗", "对"])
def test_short_equipment_followup_retains_subject_and_stat_demand(book, catalog, query):
    prior = cases.answer("你看看这个遗器适合真珠嘛？", book, catalog)["context"]
    base = build_query_plan(query)
    demand = plan_guidance(query, base, book=book, catalog=catalog, previous=prior)
    observed = demand.observation_plan(base)
    assert demand.requested and demand.character_ids == ("character.pearl",)
    assert observed.screen and "equipment" in observed.fields
    assert observed.required_equipment_kinds == frozenset({"main_stat", "sub_stat"})


def visual(*facts):
    return {"fresh": True, "read_scope": "full", "scene": {"verified": True},
            "page_type": "relic_detail", "characters": [],
            "screen_facts": {"facts": list(facts), "visible_text": []}}


def fact(kind, label, value):
    return {"kind": kind, "scope": "equipment", "label": label, "value": value}


def test_equipment_name_or_slot_does_not_satisfy_stat_question():
    plan = build_query_plan("这件遗器的主副词条是什么")
    value = visual(fact("equipment_slot", "遗器部位", "脚部"))
    assert "equipment" not in satisfied_fields(plan, value)
    status = answer_status(plan, value, {"enabled": True, "state": "observing"})
    assert status["state"] == "partial"
    assert status["equipment_fields"]["missing"] == ["main_stat", "sub_stat"]
    value["screen_facts"]["facts"] += [fact("main_stat", "主词条·速度", "25"),
                                       fact("sub_stat", "副词条·防御力", "12.9%")]
    assert "equipment" not in satisfied_fields(plan, value)
    assert answer_status(plan, value, {"enabled": True, "state": "observing"})["state"] == "partial"
    value["screen_facts"]["facts"] += [fact("sub_stat", "副词条·暴击率", "3%"),
                                       fact("sub_stat", "副词条·生命值", "80"),
                                       fact("sub_stat", "副词条·效果抵抗", "5%")]
    assert "equipment" in satisfied_fields(plan, value)


def test_set_fact_question_still_accepts_set_without_stats():
    plan = build_query_plan("这个遗器是什么套装")
    assert not plan.required_equipment_kinds
    assert "equipment" in satisfied_fields(plan, visual(fact("equipment_name", "名称", "测试套装")))


def test_panel_assessment_keeps_player_subject_not_visual_identity(book, catalog):
    query = "看看我真珠面板怎么样？"
    demand = cases.plan(query, book, catalog)
    observed = demand.observation_plan(build_query_plan(query))
    assert demand.requested and demand.character_ids == ("character.pearl",)
    assert observed.screen and "attributes" in observed.fields
    result = build_guidance(demand, query, book=book, catalog=catalog, profile={}, visual=visual())
    assert result["assessment"]["kind"] == "attributes"
    assert result["assessment"]["subject_origin"] == "player_text"
    assert not result["assessment"]["visual_subject_confirmed"]
    assert not result["assessment"]["evaluation_available"]


def test_missing_equipment_keeps_general_guide_but_never_claims_fit(book, catalog):
    prior = cases.answer("真珠没专武怎么配装", book, catalog)["context"]
    query = "我有专了，看看这个遗器还要换吗？"
    demand = cases.plan(query, book, catalog, previous=prior)
    result = build_guidance(demand, query, book=book, catalog=catalog, profile={}, visual={"fresh": False})
    assert result["guides"] and not result["player_conditions"]["signature_excluded"]
    assert result["assessment"]["state"] == "unconfirmed"
    assert not result["assessment"]["facts"]
    assert not result["assessment"]["evaluation_available"]


def test_incomplete_reference_team_not_presented_as_four_person_team(book, catalog):
    result = cases.answer("真珠没有爻光怎么配队", book, catalog)
    assert result["team_candidates"]
    for team in result["team_candidates"]:
        assert team["filled_slot_count"] == sum(bool(member) for member in team["members"])
        if team["filled_slot_count"] < 4:
            assert not team["complete"]
            assert any(slot["reason"] == "excluded" for slot in team["unconfirmed_slots"])


def test_expired_equipment_followup_does_not_reuse_role(book, catalog):
    prior = cases.answer("这个遗器适合真珠吗", book, catalog)["context"]
    prior["at"] = time.monotonic() - 601
    result = cases.plan("这个呢", book, catalog, previous=prior)
    assert not result.character_ids


def test_entry_mixed_question_then_short_followup_preserves_state_not_profile(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    owner._observer_status.update(enabled=False, state="paused")
    profile_before = copy.deepcopy(values["player_context"])

    async def run():
        await owner.hsr_companion_prepare(query="真珠没专武怎么配装")
        changed = await owner.hsr_companion_prepare(query="我有专了，这个遗器要换吗？")
        assert not changed["guidance_answer"]["player_conditions"]["signature_excluded"]
        follow = await owner.hsr_companion_prepare(query="这个呢")
        assert follow["guidance_answer"]["guides"][0]["name"] == "真珠"
        assert follow["query_plan"]["screen"]
        assert follow["guidance_answer"]["assessment"]["state"] == "unconfirmed"
        assert values["player_context"] == profile_before
        await owner.hsr_companion_prepare(query="今天好开心")
        cleared = await owner.hsr_companion_prepare(query="这个呢")
        assert not cleared["guidance_answer"].get("guides")

    asyncio.run(run())


@pytest.mark.parametrize("query,kinds", [("这件遗器的主词条是什么", {"main_stat"}),
                                       ("这件遗器的副词条是什么", {"sub_stat"})])
def test_main_and_substat_requests_have_independent_requirements(query, kinds):
    plan = build_query_plan(query)
    assert plan.required_equipment_kinds == kinds
    assert "equipment" not in satisfied_fields(plan, visual(fact("unreadable_stat", "主词条", "未读取")))
    assert "equipment" not in satisfied_fields(plan, {**visual(fact(next(iter(kinds)), "属性", "25")), "fresh": False})


def test_page_or_resource_question_does_not_resume_equipment_assessment(book, catalog):
    prior = cases.answer("这个遗器适合真珠吗", book, catalog)["context"]
    for query in ("现在是什么页面", "现在呢", "我有多少星琼", "这个光锥效果是什么", "这件遗器的主词条是什么"):
        base = build_query_plan(query)
        demand = plan_guidance(query, base, book=book, catalog=catalog, previous=prior)
        assert not demand.requested
        assert demand.observation_plan(base) == base


def test_followup_waits_for_stats_and_sends_same_demand_once(book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner

    query = "这个遗器的词条适合真珠吗"
    demand = cases.plan(query, book, catalog)
    observation = demand.observation_plan(build_query_plan(query))
    partial = visual(fact("equipment_slot", "遗器部位", "脚部"))
    ready = visual(fact("main_stat", "主词条·速度", "25"), fact("sub_stat", "副词条·防御力", "12.9%"),
                   fact("sub_stat", "副词条·暴击率", "3%"), fact("sub_stat", "副词条·生命值", "80"),
                   fact("sub_stat", "副词条·效果抵抗", "5%"))
    owner, sent = followup_owner(monkeypatch, value=ready)
    owner._guidance_book, owner._catalog = book, catalog

    async def run():
        armed = followup.arm_followup(owner, query=query, visual=partial, asked_at=time.time(),
                                     scene_only=False, navigation=False, content=True, controlled=False,
                                     plan=observation, guidance_plan=demand, guidance_profile={})
        assert armed["pending"]  # A slot alone must not suppress the pending answer.
        await owner._followup_task

    asyncio.run(run())
    assert len(sent) == 1 and sent[0]["metadata"]["outcome"] == "ready"
    text = sent[0]["parts"][0]["text"]
    from utils.result_parser import parse_push_message_content
    received = parse_push_message_content(text, lang="zh")
    assert "速度25" in received and "防御力12.9%" in received
    assert not owner._test_answer_payloads[-1]["guidance_answer"]["assessment"]["evaluation_available"]
    assert "适配评价未核验" in received
    assert owner._guidance_context["request_focus"] == "equipment"


def test_followup_cancel_does_not_restore_assessment_context(book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner

    query = "这个遗器适合真珠吗"
    demand = cases.plan(query, book, catalog)
    observation = demand.observation_plan(build_query_plan(query))
    owner, sent = followup_owner(monkeypatch, value=visual())
    owner._guidance_book, owner._catalog = book, catalog
    owner._guidance_context = None

    async def run():
        followup.arm_followup(owner, query=query, visual=visual(), asked_at=time.time(),
                              scene_only=False, navigation=False, content=True, controlled=False,
                              plan=observation, guidance_plan=demand, guidance_profile={})
        task = owner._followup_task
        owner._observer_status["enabled"] = False
        followup.cancel_followup(owner, "pause")
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert not sent and owner._guidance_context is None


def test_grounding_accepts_discussion_subject_without_claiming_wearer(catalog):
    from plugin.plugins.hsr_companion.runtime.grounding import build_grounding

    result = build_grounding("这个遗器适合谁", visual(fact("main_stat", "主词条·速度", "25")), catalog,
                             analysis_subject_ids=("character.pearl",), analysis_subject_origin="discussion_context")
    assert result["analysis_subject"]["ids"] == ["character.pearl"]
    assert result["analysis_subject"]["scope"] == "discussion_only_not_visual_or_wearer"
    assert not any("尚未确认要分析适配的角色" in line for line in result["missing_information"])
    assert not result["target_entity_ids"]
    assert not any(f.get("kind") == "equipment_wearer" for f in result["facts"])


def test_invalid_grounding_hint_not_an_analysis_subject(catalog):
    from plugin.plugins.hsr_companion.runtime.grounding import build_grounding

    result = build_grounding("这个遗器适合谁", visual(), catalog,
                             analysis_subject_ids=("character.fake",), analysis_subject_origin="discussion_context")
    assert result["analysis_subject"]["ids"] == []


def test_entry_handoff_agrees_with_grounding_and_never_invents_visual_role(tmp_path, monkeypatch):
    import importlib

    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation

    owner, values, _ = plugin_fixture(tmp_path)
    current = visual(fact("main_stat", "主词条·速度", "25"), fact("sub_stat", "副词条·防御力", "12.9%"))
    current.update(instruction="合成字段，不证明穿戴者", observed_at="2026-10-17T02:00:00+00:00", controlled_character=None)
    current["scene"].update(primary_state="menu", label="遗器详情")
    observed_plans = []

    async def resolve(plugin, plan, **kwargs):
        observed_plans.append(plan)
        return QueryObservation(ObservationSnapshot.select(plugin, current, fields=plan.fields),
                                {"outcome": "ready", "stages": []}, 0, kwargs["generation"])

    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)
    before = copy.deepcopy(values["player_context"])

    async def run():
        first = await owner.hsr_companion_prepare(query="这个遗器适合真珠吗")
        assert first["screen_context_answer"]["analysis_subject"]["ids"] == ["character.pearl"]
        follow = await owner.hsr_companion_prepare(query="这个呢")
        assert follow["reply_demand"]["kind"] == "mixed"
        assert follow["screen_context_answer"]["analysis_subject"]["ids"] == ["character.pearl"]
        assert follow["guidance_answer"]["assessment"]["state"] == "partial"
        assert not follow["guidance_answer"]["assessment"]["evaluation_available"]
        assert not follow["guidance_answer"]["assessment"]["visual_subject_confirmed"]
        assert not follow["screen_identity_answer"]["names"]
        assert follow["query_answer"]["state"] == "partial"
        assert not follow["followup"]["pending"]
        assert values["player_context"] == before

    asyncio.run(run())
    assert all(plan.required_equipment_kinds == {"main_stat", "sub_stat"} for plan in observed_plans)
