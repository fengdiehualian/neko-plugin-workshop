"""Stage 3: real failed intent kept; synthetic archive, no model/network/game writes.

This tests source selection, not autonomous model selection. The 18-minute
gap is intentional: a warm ten-minute discussion is NOT the failed input.
"""
import asyncio
import copy
import importlib
import time

import pytest

from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture

book = cases.book
catalog = cases.catalog
QUERY = "那昔涟这把光锥不合适，还能先用这队吗？"


def context(age=1080):
    return {"at": time.monotonic() - age, "kind": "challenge", "mode": "moc",
            "challenge_floor": 12, "challenge_difficulty": "normal",
            "execution_intent": "team_guide", "character_ids": []}


def declared(scope="saved_cone", state="specified", name="昔涟"):
    return {"scope": scope, "subject_state": state,
            **({"character": name} if state == "specified" else {})}


def choose(book, catalog, *, intent="assess_cone", previous=None, declaration=None, query=QUERY):
    base = build_query_plan(query, intent)
    plan = plan_guidance(query, base, book=book, catalog=catalog,
                         previous=previous, consultation=declaration)
    return plan, plan.observation_plan(base)


@pytest.fixture
def archive(catalog, monkeypatch):
    lookup = catalog.get
    records = {
        "character_form.1415": {"id": "character_form.1415", "entity_type": "character_form",
            "identity_id": "character.cyrene", "name": "昔涟", "path_id": "Remembrance",
            "sources": [{"source_id": "synthetic"}]},
        "light_cone.fixture_hunt": {"id": "light_cone.fixture_hunt", "entity_type": "light_cone",
            "name": "测试巡猎光锥", "path": "Hunt", "sources": [{"source_id": "synthetic"}]},
    }
    monkeypatch.setattr(catalog, "get", lambda key: copy.deepcopy(records[key]) if key in records else lookup(key))
    return {"imported_builds": {"scope": "player_confirmed_builds", "characters": [{
        "identity_id": "character.cyrene", "form_id": "character_form.1415", "name": "昔涟",
        "edit_revision": "archive-v1", "source": "player_manual", "light_cone": {
            "id": "light_cone.fixture_hunt", "name": "测试巡猎光锥", "superimposition": 1}}]}}


@pytest.mark.parametrize("age", [0, 1080])
def test_actual_wrong_intent_without_source_does_not_start_pixels(book, catalog, age):
    # Keep the actual failed intent, testing its undeclared-source variant.
    # The safe audit ALSO reports current_cone; that distinct wrong-scope
    # variant is retained below, not quietly counted as a saved-source pass.
    plan, demand = choose(book, catalog, previous=context(age))
    assert demand.intent == "assess_cone"
    assert not demand.screen
    assert plan.consultation_action == "clarify"
    assert plan.source_state == "needs_source"


@pytest.mark.parametrize("intent", ["assess_cone", "continue_consultation"])
def test_expired_named_saved_cone_reloads_archive_not_old_team(book, catalog, archive, intent):
    plan, demand = choose(book, catalog, intent=intent, previous=context(), declaration=declared())
    assert not demand.screen and plan.character_ids == ("character.cyrene",)
    result = build_guidance(plan, QUERY, book=book, catalog=catalog, profile=archive)
    advice = result["saved_cone_answer"]
    assert advice["state"] == "ready" and advice["evidence_source"] == "saved_profile"
    assert advice["light_cone"]["passive_active"] is False
    assert advice["team_assessment"] == "not_revalidated"
    assert not result["assessment"]["requested"]
    assert not result["completion"]["can_wait_for_evidence"]
    assert not result.get("team_candidates")
    assert "最优" not in result["reply_plan"]["conclusion"]


def test_expired_pronoun_source_needs_subject_without_pixels(book, catalog, archive):
    plan, demand = choose(book, catalog, intent="continue_consultation", query="那这队呢？",
                          previous=context(), declaration=declared(state="inherit"))
    assert not demand.screen and not plan.character_ids
    result = build_guidance(plan, "那这队呢？", book=book, catalog=catalog, profile=archive)
    assert result["saved_cone_answer"]["state"] == "needs_subject"


def test_genuine_current_cone_after_team_still_requires_pixels(book, catalog):
    plan, demand = choose(book, catalog, previous=context(), declaration=declared("current_cone"))
    assert demand.screen and demand.fields == {"equipment"}
    assert plan.evidence_source == "current_screen"


def test_historical_wrong_current_scope_is_not_silently_declared_repaired(book, catalog, archive):
    # Effective historical audit: assess_cone/current_cone/specified. The
    # missing raw arguments/first call cannot be reconstructed from the audit.
    # A consistently wrong semantic declaration remains a live model-selection
    # acceptance risk. No keyword classifier is allowed to rewrite it here.
    plan, demand = choose(book, catalog, previous=context(), declaration=declared("current_cone"))
    result = build_guidance(plan, QUERY, book=book, catalog=catalog, profile=archive)
    assert demand.screen and plan.assessment_scope == "current_cone"
    assert "saved_cone_answer" not in result
    assert not result["completion"]["evaluation_complete"]


def test_runtime_tool_schema_exposes_saved_source(tmp_path):
    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods
    owner, _, _ = plugin_fixture(tmp_path)
    tool = next(meta for meta, _ in collect_llm_tool_methods(owner) if meta.name == "hsr_companion_prepare")
    assert "saved_cone" in tool.parameters["properties"]["consultation"]["properties"]["scope"]["enum"]


def test_saved_followup_reselects_archive_without_becoming_current_cone(book, catalog, archive):
    first, _ = choose(book, catalog, previous=context(), declaration=declared())
    prior = build_guidance(first, QUERY, book=book, catalog=catalog, profile=archive)["context"]
    second, demand = choose(book, catalog, intent="continue_consultation", previous=prior,
                            query="那这会有什么影响？")
    assert not demand.screen and second.evidence_source == "saved_profile"
    archive["imported_builds"]["characters"][0]["light_cone"] = None
    result = build_guidance(second, "那这会有什么影响？", book=book, catalog=catalog, profile=archive)
    assert result["saved_cone_answer"]["state"] == "needs_cone_data"


def test_source_clarification_keeps_newly_named_role_not_expired_team(book, catalog, archive):
    first, demand = choose(book, catalog, previous=context())
    assert not demand.screen and first.character_ids == ("character.cyrene",)
    prior = build_guidance(first, QUERY, book=book, catalog=catalog, profile=archive)["context"]
    assert prior["kind"] == "build" and "mode" not in prior
    second, demand = choose(book, catalog, intent="continue_consultation", previous=prior,
                            declaration=declared(state="inherit"), query="存档里的那把")
    assert second.character_ids == ("character.cyrene",) and not demand.screen
    result = build_guidance(second, "存档里的那把", book=book, catalog=catalog, profile=archive)
    assert result["saved_cone_answer"]["state"] == "ready"


@pytest.mark.parametrize("intent", ["assess_relic", "assess_panel", "build_guide", "team_guide"])
def test_saved_cone_cannot_silently_rewrite_other_purposes(book, catalog, intent):
    with pytest.raises(ValueError, match="scope_intent_conflict"):
        choose(book, catalog, intent=intent, declaration=declared())


@pytest.mark.parametrize("change", ["missing", "ambiguous", "changed_cone"])
def test_archive_missing_or_changed_never_borrows_old_equipment(book, catalog, archive, change):
    plan, _ = choose(book, catalog, previous=context(), declaration=declared())
    if change == "missing":
        archive["imported_builds"]["characters"] = []
    elif change == "ambiguous":
        archive["imported_builds"]["characters"] *= 2
    else:
        archive["imported_builds"]["characters"][0]["light_cone"] = None
    result = build_guidance(plan, QUERY, book=book, catalog=catalog, profile=archive)
    assert result["saved_cone_answer"]["state"] != "ready"
    assert not result["completion"]["can_wait_for_evidence"]


def test_formal_entry_saved_source_cannot_wait_for_current_cone(tmp_path, monkeypatch, catalog, archive):
    owner, values, sent = plugin_fixture(tmp_path)
    owner._catalog = catalog
    owner._guidance_context = context()
    before = copy.deepcopy(values["player_context"])
    module = importlib.import_module(type(owner).__module__)
    original_resolve = module.resolve_query
    demands = []

    async def resolve(plugin, demand, **kwargs):
        demands.append(demand)
        assert not demand.screen and not demand.fields
        return await original_resolve(plugin, demand, **kwargs)

    async def roster(**kwargs):
        return copy.deepcopy(archive["imported_builds"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("Saved-source query must not read frames, game state or wait for downloads")

    monkeypatch.setattr(module, "resolve_query", resolve)
    monkeypatch.setattr(owner, "_imported_roster", roster)
    monkeypatch.setattr(owner, "_current_visual_evidence", forbidden)
    monkeypatch.setattr(owner, "_read_game_state", forbidden)
    monkeypatch.setattr(owner, "_wait_for_data_pack", forbidden)
    result = asyncio.run(owner.hsr_companion_prepare(query=QUERY, intent="assess_cone",
                                                    consultation=declared()))
    assert demands and not result["query_plan"]["screen"]
    assert result["guidance_answer"]["saved_cone_answer"]["state"] == "ready"
    assert not result["followup"]["pending"] and not sent
    assert values["player_context"] == before
