"""Fixed scope: one intent/field contract, no OCR, install or real chat writes.

Reproduce the reported default-wording failures first. Structured intents
must not be reclassified from query words. Exercise actual formal entry,
continuous guidance and changed equipment snapshots; never count these as
live model intent-selection or OCR accuracy tests. Partial reads remain legal.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import time

import pytest
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests import test_moc_planning as moc_cases
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

book = cases.book
catalog = cases.catalog
builds = moc_cases.builds


@pytest.mark.parametrize("query,fields,path", [
    ("我打开的是啥页面", {"page"}, "scene"),
    ("这是哪一页", {"page"}, "scene"),
    ("这个页面是干嘛的", {"page", "visible_text"}, "full"),
    ("这是什么页面，写了什么", {"page", "visible_text"}, "full"),
])
def test_original_default_wording_failures(query, fields, path):
    chosen = build_query_plan(query)
    assert chosen.fields == fields and chosen.public()["read_path"] == path
    assert chosen.refresh_args()["require_content"] is ("visible_text" in fields)


def test_explicit_page_fields_cannot_gain_a_quest_from_why_word():
    chosen = build_query_plan("这个页面是干嘛的", screen_fields=["page"])
    assert chosen.fields == {"page"} and chosen.scene_only and not chosen.navigation


@pytest.mark.parametrize("query", ["这是什么页面，写了什么", "我打开的是啥页面", "帮我看看这个入口", "瞅瞅眼前这页"])
def test_structured_read_has_same_execution_for_different_words(book, catalog, query):
    base = build_query_plan(query, "read_screen", screen_fields=["page", "visible_text"])
    chosen = plan_guidance(query, base, book=book, catalog=catalog)
    demand = chosen.observation_plan(base)
    assert not chosen.requested
    assert demand.fields == {"page", "visible_text"}
    assert demand.public()["planning_source"] == "semantic_intent"
    assert demand.public()["read_path"] == "full"


def test_page_explanation_is_not_navigation_even_with_advice_words(book, catalog):
    query = "这里要干嘛，怎么配，这件该如何处理？"
    base = build_query_plan(query, "explain_page")
    chosen = plan_guidance(query, base, book=book, catalog=catalog)
    assert not chosen.requested and chosen.observation_plan(base).fields == {"page", "visible_text"}
    assert not base.navigation and not base.required_equipment_kinds


@pytest.mark.parametrize("intent,fields", [
    ("assess_relic", {"equipment"}), ("assess_cone", {"equipment"}),
    ("assess_panel", {"attributes"}), ("equipment_guide", set()), ("build_guide", set()),
])
def test_semantic_action_not_overwritten_by_unrelated_words(book, catalog, intent, fields):
    query = "接下来真珠这个页面是干嘛的，给我推荐一下"
    base = build_query_plan(query, intent)
    chosen = plan_guidance(query, base, book=book, catalog=catalog)
    demand = chosen.observation_plan(base)
    assert chosen.character_ids == ("character.pearl",)
    assert demand.fields == fields and demand.screen is bool(fields)
    assert bool(demand.required_equipment_kinds) is (intent == "assess_relic")


def test_full_rows_survive_a_brief_assessment_hint(book, catalog):
    query = "完整报出这件遗器主词条和四项副词条，适合真珠吗？"
    base = build_query_plan(query, "assess_relic", response_detail="brief")
    chosen = plan_guidance(query, base, book=book, catalog=catalog)
    result = build_guidance(chosen, query, book=book, catalog=catalog, profile={}, visual=relic())
    assert result["reply_plan"]["mode"] == "full"
    assert len(result["reply_plan"]["evidence_rows"]) == 5


@pytest.mark.parametrize("intent,fields", [
    ("read_screen", []), ("equipment_guide", ["equipment"]),
    ("assess_cone", ["quest"]), ("explain_page", ["resource"]),
])
def test_conflicting_semantic_parameters_are_not_silently_reinterpreted(intent, fields):
    with pytest.raises(ValueError):
        build_query_plan("anything", intent, screen_fields=fields)


def test_expired_continuation_needs_subject_not_old_item(book, catalog):
    prior = {"at": time.monotonic() - 601, "kind": "build", "character_ids": ["character.pearl"],
             "request_focus": "equipment", "consultation_action": "assess", "equipment_kinds": ["main_stat", "sub_stat"]}
    base = build_query_plan("接着看看它", "continue_consultation")
    chosen = plan_guidance(base.query, base, book=book, catalog=catalog, previous=prior)
    assert not chosen.character_ids and not chosen.observation_plan(base).screen


def test_formal_continuation_swap_and_advice_share_canonical_path(tmp_path, monkeypatch, catalog):
    owner, values, sent = plugin_fixture(tmp_path)
    owner._catalog = catalog
    current = relic(main="生命值", value="705", serial=1, subs=[("攻击力", "57"), ("生命值", "8.2%"),
                             ("攻击力", "3.8%"), ("效果命中", "7.7%")])
    owner._observation_serial = 1
    before = copy.deepcopy(values["player_context"])
    demands = []

    async def resolve(plugin, demand, **kwargs):
        demands.append(demand)
        return QueryObservation(ObservationSnapshot.select(plugin, current, fields=demand.fields),
                                {"outcome": "reused", "stages": []}, 0, kwargs["generation"])

    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)

    async def run():
        nonlocal current
        first = await owner.hsr_companion_prepare(query="这件给真珠看看", intent="assess_relic")
        assert first["guidance_answer"]["requested"] and first["equipment_answer"]["complete"]
        current = relic(main="速度", value="25", serial=2, subs=[("防御力", "33"), ("攻击力", "16"),
                             ("攻击力", "4.3%"), ("效果命中", "15.9%")])
        owner._observation_serial = 2
        second = await owner.hsr_companion_prepare(query="接着瞅瞅它", intent="continue_consultation")
        assert second["equipment_answer"]["main_stat"]["value"] == "25"
        # The model receives the selected answer, not the redundant full
        # article. The service's actual bound role is the routing contract.
        assert second["guidance_answer"]["completion"]["subject_names"] == ["真珠"]
        assert second["guidance_answer"]["completion"]["subject_ids"] == ["character.pearl"]
        assert "57" not in second["equipment_answer"]["exact_text"]
        advice = await owner.hsr_companion_prepare(query="我有专武，接下来选法呢？", intent="equipment_guide")
        assert not advice["query_plan"]["screen"] and not advice["followup"]["pending"]
        assert not demands[-1].fields and values["player_context"] == before and not sent

    asyncio.run(run())


def test_real_mixed_question_keeps_all_fields_and_requires_full_reader(book, catalog):
    base = build_query_plan("面板和任务都帮我看看", "assess_panel", screen_fields=["attributes", "quest"])
    chosen = plan_guidance(base.query, base, book=book, catalog=catalog, discussion_character="真珠")
    demand = chosen.observation_plan(base)
    assert demand.fields == {"attributes", "quest"} and not demand.navigation and not demand.scene_only
    fallback = build_query_plan("这个页面是干嘛的，当前任务要做什么？")
    assert fallback.fields == {"page", "visible_text", "quest"} and not fallback.navigation


@pytest.mark.parametrize("fields,path", [
    (["page"], "scene"), (["location"], "scene"), (["quest"], "navigation"),
    (["page", "visible_text"], "full"), (["equipment"], "full"), (["attributes", "quest"], "full"),
    (["characters"], "full"), (["resource"], "full"), (["controlled_character"], "full"),
])
def test_execution_invariants_have_one_field_authority(fields, path):
    chosen = build_query_plan("此句不参与二次分流", "read_screen", screen_fields=fields)
    assert chosen.public()["read_path"] == path
    if "visible_text" in fields:
        assert chosen.refresh_args()["require_content"] and not chosen.scene_only
    assert chosen.screen and chosen.fields == set(fields)


def test_cone_continuation_cannot_turn_into_five_relic_rows(book, catalog):
    base = build_query_plan("给绯英看看这把", "assess_cone")
    chosen = plan_guidance(base.query, base, book=book, catalog=catalog)
    previous = build_guidance(chosen, base.query, book=book, catalog=catalog, profile={})["context"]
    next_base = build_query_plan("再瞅瞅这把的词条", "continue_consultation")
    next_plan = plan_guidance(next_base.query, next_base, book=book, catalog=catalog, previous=previous)
    assert next_plan.character_ids == chosen.character_ids
    assert not next_plan.observation_plan(next_base).required_equipment_kinds


def test_semantic_stat_question_and_player_value_keep_old_statement_boundary(book, catalog):
    base = build_query_plan("真珠防御力要多少？", "stat_threshold")
    chosen = plan_guidance(base.query, base, book=book, catalog=catalog)
    assert chosen.stat_property == "防御力" and not chosen.requires_pixels
    first = build_guidance(chosen, base.query, book=book, catalog=catalog, profile={})
    next_base = build_query_plan("3132", "stat_value")
    second = plan_guidance(next_base.query, next_base, book=book, catalog=catalog, previous=first["context"])
    assert second.player_stat_value == "3132" and not second.observation_plan(next_base).screen
    result = build_guidance(second, next_base.query, book=book, catalog=catalog, profile={})
    assert result["reply_plan"]["stated_stat"]["origin"] == "player_statement"
    assert not result["reply_plan"]["stated_stat"]["visually_confirmed"]


def test_tool_schema_actually_exposes_the_semantic_contract(tmp_path):
    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods
    owner, _, _ = plugin_fixture(tmp_path)
    found = [meta for meta, _ in collect_llm_tool_methods(owner) if meta.name == "hsr_companion_prepare"]
    assert len(found) == 1
    props = found[0].parameters["properties"]
    assert {"read_screen", "continue_consultation", "equipment_guide", "stat_threshold"} <= set(props["intent"]["enum"])
    assert props["response_detail"]["enum"] == ["brief", "full"]


def test_new_entry_preserves_roster_replacement_and_unverified_proposals(book, catalog):
    def choose(query, intent, previous=None):
        return plan_guidance(query, build_query_plan(query, intent), book=book, catalog=catalog, previous=previous)

    initial = choose("绯英配队，我有爻光、真珠", "team_guide")
    prior = build_guidance(initial, "", book=book, catalog=catalog, profile={})["context"]
    narrowed = choose("我只有真珠", "continue_consultation", prior)
    assert narrowed.character_ids == initial.character_ids
    assert narrowed.supplied_owned == ("真珠",)  # Explicit restriction cannot merge old roster back in.
    proposed = choose("换成布洛妮娅呢", "continue_consultation", prior)
    assert proposed.character_ids == initial.character_ids
    assert proposed.proposed_teammates == ("布洛妮娅",)
    result = build_guidance(proposed, "", book=book, catalog=catalog, profile={})
    assert any("尚未核实" in note for note in result["missing_information"])


def test_semantic_stage_followup_keeps_scope_not_old_screen_or_roster_expansion(book, catalog):
    query = "本期普通混沌12层，我只有真珠、绯英，怎么配队？"
    base = build_query_plan(query, "team_guide")
    first = plan_guidance(query, base, book=book, catalog=catalog)
    prior = build_guidance(first, query, book=book, catalog=catalog, profile={})["context"]
    query = "我只有真珠，下半怎么安排？"
    follow = plan_guidance(query, build_query_plan(query, "continue_consultation"),
                           book=book, catalog=catalog, previous=prior)
    assert (follow.mode, follow.challenge_floor, follow.challenge_difficulty) == ("moc", 12, "normal")
    assert follow.challenge_side == "lower" and follow.supplied_owned == ("真珠",)
    assert not follow.requires_pixels
    # An explicit new stage must not inherit the old floor/ordinary-mode scope.
    changed = plan_guidance("本期虚构叙事怎么配队", build_query_plan("本期虚构叙事怎么配队", "team_guide"),
                            book=book, catalog=catalog, previous=prior)
    assert changed.mode == "pf" and not changed.challenge_floor and not changed.challenge_difficulty


@pytest.mark.parametrize("query,name", [("本期新角色怎么配装", "真珠"), ("本期复刻角色光锥怎么选", "绯英")])
def test_new_entry_keeps_existing_banner_calendar(book, catalog, query, name):
    base = build_query_plan(query, "equipment_guide")
    chosen = plan_guidance(query, base, book=book, catalog=catalog, now=cases.NOW)
    result = build_guidance(chosen, query, book=book, catalog=catalog, profile={}, now=cases.NOW)
    assert [r["name"] for r in result["guides"]] == [name]
    assert result["target_origin"] == "banner_calendar" and not chosen.requires_pixels


def test_pronoun_guide_can_read_only_its_missing_subject(book, catalog):
    query = "她的遗器光锥怎么配？"
    base = build_query_plan(query, "equipment_guide", screen_fields=["characters"])
    chosen = plan_guidance(query, base, book=book, catalog=catalog)
    demand = chosen.observation_plan(base)
    assert demand.fields == {"characters"} and not demand.content
    visual = {"fresh": True, "scene": {"verified": True}, "characters": [
        {"entity_id": "character.pearl", "identity_scope": "detail_subject"}]}
    result = build_guidance(chosen, query, book=book, catalog=catalog, profile={}, visual=visual)
    assert [r["name"] for r in result["guides"]] == ["真珠"]
    assert result["target_origin"] == "current_visual"


@pytest.mark.parametrize("intent,fields", [("challenge_guide", []), ("team_guide", ["visible_text"])])
def test_challenge_after_character_advice_does_not_resume_last_character(book, catalog, intent, fields):
    first = plan_guidance("绯英的光锥怎么配", build_query_plan("绯英的光锥怎么配", "equipment_guide"),
                           book=book, catalog=catalog)
    prior = build_guidance(first, "", book=book, catalog=catalog, profile={})["context"]
    query = "接着看看眼前这个挑战"
    base = build_query_plan(query, intent, screen_fields=fields)
    chosen = plan_guidance(query, base, book=book, catalog=catalog, previous=prior)
    assert chosen.kind == "challenge" and not chosen.character_ids
    assert chosen.observation_plan(base).fields == {"visible_text"}
    assert chosen.observation_plan(base).planning_source == "semantic_intent"


def test_semantic_team_entry_keeps_two_disjoint_teams_and_off_path_warning(tmp_path, monkeypatch, book, catalog, builds):
    owner, values, sent = plugin_fixture(tmp_path)
    owner._guidance_book, owner._catalog = book, catalog
    builds["imported_builds"]["characters"][0]["light_cone"] = {"id": "light_cone.wrong", "superimposition": 1}
    before = copy.deepcopy(values["player_context"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("Explicit known stage must not start capture, network or wait for a visual field")

    async def roster():
        return copy.deepcopy(builds["imported_builds"])

    # Use the historical period clock of the frozen D2 fixture, not today's
    # unrelated calendar. This tests constraints, not current攻略 correctness.
    real = importlib.import_module(type(owner).__module__).build_guidance
    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "build_guidance",
                        lambda *a, **kw: real(*a, **{**kw, "now": moc_cases.NOW}))
    owner._imported_roster = roster
    owner._current_visual_evidence = owner._read_game_state = owner._wait_for_data_pack = forbidden

    async def run():
        first = await owner.hsr_companion_prepare(query="本期混沌普通12层怎么配队", intent="team_guide")
        result = first["guidance_answer"]["challenge_plan"]
        assert result["state"] == "ready" and len(result["assignments"]) == 2
        assert len({m["identity_id"] for t in result["assignments"] for m in t["members"]}) == 8
        assert result["warnings"][0]["reason"] == "cone_path_mismatch"
        next_result = await owner.hsr_companion_prepare(query="没有爻光呢", intent="continue_consultation")
        result = next_result["guidance_answer"]["challenge_plan"]
        assert not result["assignments"]
        assert all(m["name"] != "爻光" for half in result["candidates"] for team in half for m in team["members"])
        assert values["player_context"] == before and not sent

    asyncio.run(run())
