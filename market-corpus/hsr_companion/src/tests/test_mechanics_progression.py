"""Deterministic source/table contracts, not real-game or LLM acceptance."""

from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.hsr_companion.data.catalog import StructuredCatalog
from plugin.plugins.hsr_companion.runtime.grounding import build_grounding
from plugin.plugins.hsr_companion.runtime.mechanics import skill_view
from plugin.plugins.hsr_companion.runtime.progression import plan_progression
from plugin.plugins.hsr_companion.runtime.team_overview import team_overview
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture

SOURCE = {"source_id": "fixture", "source_url": "https://example.com/fixture", "license_id": "test-only"}


def catalogue():
    records = [
        {"id": "character.a", "name": "测试角色甲", "entity_type": "character", "form_ids": ["character_form.a", "character_form.b"]},
        {"id": "character_form.a", "name": "测试角色甲", "entity_type": "character_form", "skills": [skill()],
         "promotion": {"materials": [[{"id": "2", "num": 4000}, {"id": "3", "num": 5}],
                                    [{"id": "2", "num": 8000}, {"id": "3", "num": 10}], []]}},
        {"id": "character_form.b", "name": "测试角色甲·形态乙", "entity_type": "character_form", "skills": [skill()]},
        {"id": "character_trace.t", "name": "测试行迹", "entity_type": "character_trace", "max_level": 3,
         "levels": [{"promotion": 0, "materials": []}, {"promotion": 2, "materials": [{"id": "2", "num": 5000}]},
                    {"promotion": 3, "materials": [{"id": "3", "num": 3}]}]},
        {**skill(), "id": "character_skill.s", "entity_type": "character_skill"},
        {"id": "item.2", "name": "信用点", "entity_type": "item"},
        {"id": "item.3", "name": "测试材料", "entity_type": "item"},
    ]
    for record in records:
        record["source_refs"] = ["fixture"]
        record["sources"] = [SOURCE]
    return StructuredCatalog({"meta": {"schema_version": 2, "data_revision": "fixture-v1"}, "records": records}, {"sources": [SOURCE]})


def skill():
    return {"id": "s", "name": "测试技能", "simple_desc": "测试用简述", "desc": "造成#1[i]%伤害，持续#2[i]回合，数值#3[f1]。",
            "params": [[.5, 2, 1.25], [1.0, 3, 2.5]], "type_text": "战技", "effect_text": "扩散"}


def test_selected_level_not_maximum_or_inventory_guess():
    first = skill_view(skill(), level=1, sources=[SOURCE])
    second = skill_view(skill(), level=2, sources=[SOURCE])
    assert first["description"] == "造成50%伤害，持续2回合，数值1.2。"
    assert "100%" in second["description"]
    assert first["sources"] == [SOURCE] and first["kind"] == "versioned_knowledge_not_observation"


@pytest.mark.parametrize("level", [None, True, 0, -1, 3, 1.5, "1"])
def test_unknown_bad_or_uncovered_level_keeps_only_summary(level):
    value = skill_view(skill(), level=level)
    assert not value["description_verified"] and value["description"] == ""
    assert value["summary"] and value["missing_information"]


@pytest.mark.parametrize("bad", ["#9[i]", "#0[i]", "#1[unsupported]", "#1[i]#unknown"])
def test_incomplete_templates_are_not_partially_explained(bad):
    assert not skill_view({**skill(), "desc": bad}, level=1)["description_verified"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "not a number"])
def test_non_numeric_parameters_do_not_turn_into_mechanism_facts(bad):
    assert not skill_view({**skill(), "params": [[bad, 2, 3]]}, level=1)["description_verified"]


def test_member_mechanisms_are_sourced_and_multiple_forms_not_selected():
    value = team_overview(["测试角色甲", "未收录角色"], catalog=catalogue())
    assert value["analysis_available"] and not value["team_optimization_available"]
    assert value["member_mechanics"][0]["needs_form_selection"]
    assert len(value["member_mechanics"][0]["forms"]) == 2
    assert value["unknown_characters"] == ["未收录角色"]
    assert value["issues"] == value["strengths"] == value["recommended_next_steps"] == []


def plan(**overrides):
    return {"kind": "ascension", "entity_id": "character_form.a", "current": 0, "target": 2, **overrides}


def test_ascension_counts_each_edge_once_and_never_claims_shortfall():
    value = plan_progression(plan(), catalog=catalogue())
    assert value["available"] and value["sources"] == [SOURCE]
    assert {m["name"]: m["required"] for m in value["materials"]} == {"信用点": 12000, "测试材料": 15}
    assert all(m["owned"] is None and m["shortfall"] is None for m in value["materials"])
    assert plan_progression(plan(current=1), catalog=catalogue())["materials"][0]["required"] == 8000


def test_trace_level_rows_not_skill_level_or_ascension_edges():
    value = plan_progression(plan(kind="trace", entity_id="character_trace.t", current=1, target=3), catalog=catalogue())
    assert value["available"]
    assert {m["required"] for m in value["materials"]} == {5000, 3}
    assert plan_progression(plan(kind="trace", entity_id="character_trace.t", current=0, target=1), catalog=catalogue())["materials"] == []


@pytest.mark.parametrize("override", [
    {"current": True}, {"target": "2"}, {"current": 3, "target": 1}, {"current": -1},
    {"target": 80}, {"target": 3}, {"entity_id": "character.a"}, {"entity_id": "missing"},
    {"kind": "trace"}, {"kind": "skill"},
])
def test_invalid_missing_or_ambiguous_plan_never_returns_partial_sum(override):
    value = plan_progression(plan(**override), catalog=catalogue())
    assert not value["available"] and value["materials"] == [] and value["missing_information"]


def test_source_parameter_view_needs_explicit_skill_level_not_random_page_level():
    # Displayed identity and account level are deliberately not proof of this skill's level.
    db = catalogue()
    record = db.get("character_skill.s")
    assert record
    visual = {"fresh": True, "characters": [], "screen_facts": {"facts": [
        {"kind": "entity_label", "entity_type": "character_skill", "entity_id": "character_skill.s", "scope": "skill"},
        {"kind": "level", "label": "等级", "value": "1", "scope": "progression"},
    ]}}
    value = build_grounding("这个技能是什么意思", visual, db)
    assert not value["skill_explanations"][0]["description_verified"]
    visual["screen_facts"]["facts"][1]["label"] = "技能等级"
    assert build_grounding("这个技能是什么意思", visual, db)["skill_explanations"][0]["description_verified"]


def test_unified_tool_accepts_plan_and_ignores_host_internal_parameters(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._catalog = catalogue()
    owner._catalog.status = lambda: {"installed": True}
    value = asyncio.run(owner.hsr_companion_prepare(query="算突破材料", cultivation_plan=plan(), _ctx="ignored"))
    assert value["cultivation_plan"]["available"]
    assert "ignored" not in str(value)
