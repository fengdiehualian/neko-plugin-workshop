"""Recorded natural-question failures, not live OCR or cat-speech receipts.

Keep the user's sequence and failed cases. Hints simulate the overly broad
screen_fields seen in the effective audit, not a claim to know old call args.
"""
import asyncio
import copy
import importlib
import json
from dataclasses import replace

import pytest
from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as guidance_cases
from plugin.plugins.hsr_companion.tests import test_moc_planning as moc_cases
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_d1_closeout import panel
from plugin.plugins.hsr_companion.tests.test_diagnostics import inputs
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, request, visual
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

book = guidance_cases.book
catalog = guidance_cases.catalog
builds = moc_cases.builds


def choose(query, book, catalog, *, hints=None, previous=None, hint=""):
    base = build_query_plan(query, screen_fields=hints)
    chosen = plan_guidance(query, base, book=book, catalog=catalog, previous=previous,
                           explicit_fields=bool(hints), discussion_character=hint)
    return chosen, chosen.observation_plan(base)


@pytest.mark.parametrize("query", [
    "真珠这个面板，接下来优先提升什么呢？",
    "真珠面板现在最需要改善什么？",
    "看下真珠的属性，先优化哪一项？",
])
@pytest.mark.parametrize("hints", [None, ["attributes", "quest", "equipment"]])
def test_panel_priority_routes_to_assessment_without_unasked_reads(book, catalog, query, hints):
    chosen, demand = choose(query, book, catalog, hints=hints)
    assert chosen.kind == "build" and chosen.character_ids == ("character.pearl",)
    assert chosen.requires_pixels and chosen.request_focus == "attributes"
    assert demand.fields == {"attributes"}
    result = build_guidance(chosen, query, book=book, catalog=catalog, profile={}, visual=panel())
    assert result["assessment"]["evaluation_available"]
    assert result["reply_plan"]["enabled"]


@pytest.mark.parametrize("hint,query", [
    ("", "我有专武，真珠的遗器主词条怎么配？"),
    ("真珠", "我有专武，遗器主词条怎么配？"),
    ("", "真珠遗器主词条推荐怎么选？"),
])
@pytest.mark.parametrize("hints", [None, ["attributes", "equipment"]])
def test_general_build_advice_does_not_acquire_pixels_from_hints(book, catalog, hint, query, hints):
    chosen, demand = choose(query, book, catalog, hint=hint, hints=hints)
    assert chosen.kind == "build" and chosen.character_ids == ("character.pearl",)
    assert chosen.consultation_action == "equipment_guide"
    assert not chosen.requires_pixels and not demand.screen and not demand.fields
    result = build_guidance(chosen, query, book=book, catalog=catalog, profile={})
    assert result["guides"][0]["name"] == "真珠"
    assert result["reply_plan"]["enabled"]


def test_real_screen_request_and_full_rows_are_not_downgraded(book, catalog):
    for query in ("看屏幕帮真珠推荐光锥", "完整报出这件遗器主词条和四项副词条，适合真珠吗"):
        chosen, demand = choose(query, book, catalog, hints=["equipment"])
        assert chosen.requires_pixels and demand.screen and "equipment" in demand.fields
    result = build_guidance(chosen, query, book=book, catalog=catalog, profile={}, visual=relic())
    assert result["reply_plan"]["mode"] == "full"
    assert len(result["reply_plan"]["evidence_rows"]) == 5


@pytest.mark.parametrize("query", ["这个遗器属性怎么样，适合真珠吗？", "这把光锥属性给绯英用怎么样？"])
def test_item_attributes_do_not_become_character_panel(book, catalog, query):
    chosen, demand = choose(query, book, catalog)
    assert chosen.request_focus == "equipment" and demand.screen


@pytest.mark.parametrize("query", ["真珠培养优先提升什么属性？", "真珠应该先关注什么属性？"])
def test_general_attribute_priority_is_advice_not_current_panel(book, catalog, query):
    chosen, demand = choose(query, book, catalog, hints=["attributes"])
    assert chosen.kind == "build" and chosen.consultation_action == "priority"
    assert not demand.screen and not chosen.requires_pixels


@pytest.mark.parametrize("query", [
    "这个光锥给绯英用要注意什么？",
    "这把光锥给绯英用有什么影响？",
])
def test_current_cone_usage_question_gets_guidance_not_fact_only(book, catalog, query):
    chosen, demand = choose(query, book, catalog)
    assert chosen.kind == "build" and chosen.request_focus == "equipment"
    assert chosen.character_ids == ("character.evanescia",)
    assert chosen.requires_pixels and demand.fields == {"equipment"}
    assert not demand.required_equipment_kinds  # A cone is not five relic rows.


@pytest.mark.parametrize("hints", [None, ["quest"], ["quest", "characters"]])
@pytest.mark.parametrize("query", ["这期怎么配队？", "接下来这期怎么配队？"])
def test_challenge_shorthand_uses_stage_text_not_caller_quest_hint(book, catalog, builds, hints, query):
    chosen, demand = choose(query, book, catalog, hints=hints)
    assert chosen.kind == "challenge" and demand.screen
    assert demand.fields == {"visible_text"} and not demand.navigation
    current = {"fresh": True, "read_scope": "full", "scene": {"verified": True},
               "screen_facts": {"facts": [], "visible_text": [
                   {"text": "忘却之庭 来生泅渡其十二"},
                   {"text": "取得胜利时，剩余10轮以上"},
                   {"text": "取得胜利时，剩余20轮以上"}]}}
    result = build_guidance(chosen, query, book=book, catalog=catalog, profile=builds,
                            visual=current, now=moc_cases.NOW)
    teams = result["challenge_plan"]["assignments"]
    assert len(teams) == 2 and len({m["identity_id"] for t in teams for m in t["members"]}) == 8


def test_mixed_real_task_question_is_preserved(book, catalog):
    query = "真珠这个面板优先提升什么，当前任务要做什么？"
    _, demand = choose(query, book, catalog)
    assert demand.fields == {"attributes", "quest"}
    query = "这关怎么配队，当前任务是什么？"
    _, demand = choose(query, book, catalog)
    assert demand.fields == {"visible_text", "quest"}


def test_expired_followup_closes_silently_and_cannot_pollute_next_answer(monkeypatch):
    owner, sent = followup_owner(monkeypatch, value=visual(ready=False))
    old = replace(request(), expires_at=0)

    async def run():
        owner._pending_answer = old
        await followup.deliver_followup(owner, old)
        assert owner._pending_answer is None and not sent
        assert owner._followup_status["state"] == "expired"
        assert owner._followup_status["outcome"] == "timeout"
        new = request()
        owner._pending_answer = new
        await followup.deliver_followup(owner, old)
        assert owner._pending_answer is new and not sent

    asyncio.run(run())


def test_expired_followup_is_visible_in_safe_diagnostic():
    from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
    report = build_diagnostic_report(**inputs(), followup_status={
        "state": "expired", "outcome": "timeout", "reason": "no_new_verified_facts"})
    status = json.loads(report["copy_text"])["explicit_followup"]
    assert status["state"] == "expired" and status["reason"] == "no_new_verified_facts"


def test_formal_entry_continuation_keeps_purpose_and_source(tmp_path, monkeypatch, catalog, builds):
    """Same owner, accepted fixture fields, real prepare; not new OCR evidence."""
    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation

    owner, values, sent = plugin_fixture(tmp_path)
    before = copy.deepcopy(values)
    current = {**relic(), **panel()}
    current["scene"].update(label="角色属性", primary_state="menu")
    current["screen_facts"]["observation_serial"] = 1
    owner._observation_serial = 1
    owner._catalog = catalog
    demands = []

    async def resolve(plugin, demand, **kwargs):
        demands.append(demand)
        selected = current if demand.screen else public_visual_evidence({"invalidated": True}, owner._observer_status)
        return QueryObservation(ObservationSnapshot.select(plugin, selected, fields=demand.fields),
                                {"outcome": "reused", "stages": []}, 0, kwargs["generation"])

    async def imported(**kwargs):
        return copy.deepcopy(builds["imported_builds"])

    # The formal entry is exercised below after binding the provided fixture
    # catalog explicitly; no real account or game-window read is performed.
    owner._imported_roster = imported
    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)

    async def run():
        nonlocal current
        current = {**relic(serial=1), "page_type": "exploration_hud", "characters": [],
                   "scene": {"verified": True, "primary_state": "exploration", "label": "探索"},
                   "screen_facts": {"observation_serial": 1, "visible_text": [], "facts": [
                       {"kind": "current_location", "scope": "location", "label": "所在地", "value": "观景车厢"},
                       {"kind": "quest_objective", "scope": "quest", "label": "目标", "value": "询问哈托努斯关于宁芙的下落"}]}}
        navigation = await owner.hsr_companion_prepare(query="现在在哪？接下来的任务是什么？")
        assert navigation["query_answer"]["state"] == "ready"
        assert not navigation["followup"]["pending"]
        current = {**relic(), **panel()}
        current["scene"].update(label="角色属性", primary_state="menu")
        current["screen_facts"]["observation_serial"] = owner._observation_serial = 2
        first = await owner.hsr_companion_prepare(query="真珠这个面板，接下来优先提升什么呢？",
                                                 screen_fields=["attributes", "equipment", "quest"])
        assert first["query_plan"]["fields"] == ["attributes"]
        assert first["guidance_answer"]["reply_plan"]["enabled"]
        assert first["query_answer"]["state"] == "ready" and not first["followup"]["pending"]
        second = await owner.hsr_companion_prepare(query="我有专武，遗器主词条怎么配？",
                                                  screen_fields=["attributes", "equipment"])
        assert not demands[-1].screen and not second["followup"]["pending"]
        assert second["guidance_answer"]["reply_plan"]["goal"] == "equipment_guide"
        current = relic(serial=3)
        owner._observation_serial = 3
        item = await owner.hsr_companion_prepare(query="这个遗器给真珠怎么样？")
        assert item["query_answer"]["state"] == "ready" and not item["followup"]["pending"]
        assert len(item["guidance_answer"]["assessment"]["equipment_answer"]["sub_stats"]) == 4
        current = {**relic(serial=4), "page_type": "light_cone_detail", "characters": [],
                   "screen_facts": {"observation_serial": 4, "visible_text": [], "facts": [
                       {"kind": "entity_label", "scope": "equipment", "entity_type": "light_cone",
                        "entity_id": "light_cone.fixture", "value": "测试光锥", "label": "光锥"}]}}
        owner._observation_serial = 4
        cone = await owner.hsr_companion_prepare(query="这个光锥给绯英用要注意什么？")
        assert cone["query_answer"]["state"] == "ready" and not cone["followup"]["pending"]
        assert cone["guidance_answer"]["requested"]
        current = {**relic(serial=5), "page_type": "character_detail", "characters": [],
                   "screen_facts": {"observation_serial": 5, "facts": [], "visible_text": [
                       {"text": "忘却之庭 来生泅渡其十二"}, {"text": "剩余10轮以上"}, {"text": "剩余20轮以上"}]}}
        owner._observation_serial = 5
        challenge = await owner.hsr_companion_prepare(query="这期怎么配队？", screen_fields=["quest"])
        assert demands[-1].fields == {"visible_text"}
        assigned = challenge["guidance_answer"]["challenge_plan"]["assignments"]
        assert len(assigned) == 2 and len({m["identity_id"] for t in assigned for m in t["members"]}) == 8
        assert not challenge["followup"]["pending"]
        assert values["player_context"] == before["player_context"] and not sent

    asyncio.run(run())
