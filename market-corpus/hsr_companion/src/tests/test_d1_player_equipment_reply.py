"""D1: a player's missing-row reply is discussion data, never fresh OCR.

Structured snapshots and an isolated store reproduce routing, not live recognition.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import time

import pytest
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
from plugin.plugins.hsr_companion.runtime.fact_lifecycle import candidate_evidence
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
from plugin.plugins.hsr_companion.tests import test_guidance as guidance_fixtures
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_stage1_repairs import fact
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

book = guidance_fixtures.book
catalog = guidance_fixtures.catalog

QUERY = "这件适合真珠吗？请完整报出主副词条。"


def partial(*, labelled=True):
    value = relic(main="暴击伤害", value="64.8%", subs=[
        ("攻击力", "7.7%"), ("防御力", "4.8%"), ("击破特攻", "11.6%"),
    ])
    for row in value["screen_facts"]["facts"]:
        if row["kind"] == "equipment_name":
            row["value"] = "魔法少女的永舞战裙"
        elif row["kind"] == "equipment_slot":
            row["value"] = "躯干"
    if labelled:
        value["screen_facts"]["facts"].append(fact("unreadable_stat", "副词条·防御力", "数值未可靠识别"))
    return value


def prepared(tmp_path, monkeypatch, book, catalog, *, current=None):
    owner, values, _ = plugin_fixture(tmp_path)
    owner._catalog, owner._guidance_book = catalog, book
    owner._target_character = lambda: ""
    owner._observation_epoch = 1
    owner._analysis_game_pid = 7
    sample = current or partial()
    # Production commits carry a raw page descriptor. This fixture used a
    # public projection as raw storage; the delivery boundary correctly saw
    # its missing raw page as a contradiction (also fails on installed code).
    sample = {**copy.deepcopy(sample), "ok": True,
        "page": {"page_type": sample["page_type"], "label": sample["scene"].get("label", "")},
        "runtime": {"stable_scene": sample["scene"]["primary_state"]},
        "observation_serial": sample["screen_facts"]["observation_serial"]}
    owner._observation_serial = sample["screen_facts"]["observation_serial"]
    owner._committed_observation = copy.deepcopy(sample)
    sample = candidate_evidence(sample)
    calls = []

    async def resolve(plugin, demand, **kwargs):
        calls.append(demand)
        assert len(calls) == 1, "Player-supplied missing value started another screen read"
        return QueryObservation(ObservationSnapshot.select(plugin, sample, fields=demand.fields),
                                {"outcome": "partial", "stages": []}, 0, kwargs["generation"])

    module = importlib.import_module(type(owner).__module__)
    monkeypatch.setattr(module, "resolve_query", resolve)
    first = asyncio.run(owner.hsr_companion_prepare(query=QUERY))
    return owner, values, calls, first


def test_formal_partial_registers_one_bound_missing_value(tmp_path, monkeypatch, book, catalog):
    _, _, _, first = prepared(tmp_path, monkeypatch, book, catalog)
    binding = first["player_reply_contract"]
    assert binding["state"] == "awaiting_player_value"
    assert binding["property"] == "防御力" and binding["kind"] == "sub_stat"
    assert binding["snapshot_id"] == first["equipment_answer"]["snapshot_id"]
    assert not first["equipment_answer"]["complete"]


@pytest.mark.parametrize("query,extra", [
    ("76", {}), ("防御力是76", {}),
    ("这件遗器的第四项副词条是防御力+76吗？", {"player_reply": "76"}),
])
def test_player_reply_completes_discussion_without_ocr_or_promoting_visual(tmp_path, monkeypatch, book, catalog, query, extra):
    owner, values, calls, first = prepared(tmp_path, monkeypatch, book, catalog)
    original = copy.deepcopy(owner._committed_observation)
    original_profile = copy.deepcopy(values["player_context"])
    result = asyncio.run(owner.hsr_companion_prepare(query=query, **extra))
    stated = result["player_equipment_statement"]
    assert stated["state"] == "accepted" and stated["origin"] == "player_statement"
    assert stated["row"]["label"] == "防御力" and stated["row"]["value"] == "76"
    assert not stated["visually_confirmed"] and not stated["describes_current_screen"]
    assert len(stated["prior_observed_rows"]) == 4  # main + three substats, unchanged
    assert result["equipment_answer"]["state"] == "not_requested"
    assert not result["current_visual_evidence"]["fresh"]
    assert not result["followup"]["pending"] and not result["query_plan"]["screen"]
    assert result["guidance_answer"]["reply_plan"]["after_answer"] == "yield_to_player"
    assert owner._committed_observation == original
    assert equipment_card(original) == first["equipment_answer"]
    assert values["player_context"] == original_profile and len(calls) == 1
    assert result["delivery_receipt"]["equipment_consistency"] == "not_requested"
    assert result["guidance_answer"]["assessment"]["evaluation_available"]
    evaluation = result["guidance_answer"]["assessment"]["evaluation"]
    assert evaluation["origin"] == "player_supplemented_discussion"
    assert evaluation["discussion_rows_complete"] and not evaluation["complete"]
    assert not any(step["kind"] == "missing_rows" for step in evaluation["next_steps"])


@pytest.mark.parametrize("change", ["epoch", "paused", "expired", "main", "sub", "pid"])
def test_changed_item_or_lifecycle_cannot_consume_old_missing_slot(tmp_path, monkeypatch, book, catalog, change):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog)
    if change == "epoch":
        owner._observation_epoch += 1
    elif change == "paused":
        owner._observer_stop_requested = True
    elif change == "expired":
        owner._equipment_reply_pending["at"] = time.monotonic() - 121
    elif change == "pid":
        owner._analysis_game_pid += 1
    else:
        kind = "main_stat" if change == "main" else "sub_stat"
        next(f for f in owner._committed_observation["screen_facts"]["facts"] if f["kind"] == kind)["value"] = "32.4%"
    result = asyncio.run(owner.hsr_companion_prepare(query="76"))
    statement = result["player_equipment_statement"]
    assert statement["state"] == "needs_context" and statement["row"] is None
    assert not result["followup"]["pending"]


@pytest.mark.parametrize("reply", ["防御力76%", "生命值76", "-76", "nan", "76或者77"])
def test_ambiguous_wrong_property_or_duplicate_unit_is_not_silently_filled(tmp_path, monkeypatch, book, catalog, reply):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog)
    result = asyncio.run(owner.hsr_companion_prepare(query=reply, player_reply=reply))
    assert result["player_equipment_statement"]["state"] == "needs_clarification"
    assert result["player_equipment_statement"]["row"] is None
    assert not result["current_visual_evidence"]["fresh"]


def test_unknown_missing_label_asks_for_property_instead_of_restarting_ocr(tmp_path, monkeypatch, book, catalog):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog, current=partial(labelled=False))
    result = asyncio.run(owner.hsr_companion_prepare(query="76"))
    assert result["player_equipment_statement"]["state"] == "needs_clarification"
    assert result["player_equipment_statement"]["row"] is None
    assert not result["followup"]["pending"]


def test_real_current_screen_question_is_not_reinterpreted_as_a_statement(tmp_path, monkeypatch, book, catalog):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog)
    module = importlib.import_module(type(owner).__module__)
    calls = []

    async def resolve(plugin, demand, **kwargs):
        calls.append(demand)
        value = partial()
        return QueryObservation(ObservationSnapshot.select(plugin, value, fields=demand.fields),
                                {"outcome": "partial", "stages": []}, 0, kwargs["generation"])

    monkeypatch.setattr(module, "resolve_query", resolve)
    result = asyncio.run(owner.hsr_companion_prepare(query="请重新识别屏幕，这件遗器的防御力是76吗？"))
    assert calls and calls[0].screen
    assert not result.get("player_equipment_statement", {}).get("row")


def test_complete_requested_rows_are_unchanged_and_do_not_arm_missing_reply(tmp_path, monkeypatch, book, catalog):
    current = relic()
    _, _, _, result = prepared(tmp_path, monkeypatch, book, catalog, current=current)
    assert result["equipment_answer"] == equipment_card(current)
    assert result["equipment_answer"]["complete"] and len(result["equipment_answer"]["sub_stats"]) == 4
    assert result["player_reply_contract"]["state"] == "not_needed"


def test_concurrent_new_question_keeps_its_own_binding(tmp_path, monkeypatch, book, catalog):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog)
    newer = copy.deepcopy(owner._equipment_reply_pending)
    newer["property"] = "生命值"

    async def audit(*args, **kwargs):
        owner._answer_query_generation += 1
        owner._equipment_reply_pending = newer

    monkeypatch.setattr(owner, "_record_tool_use", audit)
    result = asyncio.run(owner.hsr_companion_prepare(query="76"))
    assert result["player_equipment_statement"]["row"] is None
    assert result["guidance_answer"]["state"] == "superseded"
    assert owner._equipment_reply_pending is newer


@pytest.mark.parametrize("reply", ["攻击力19", "攻击力3.8%"])
def test_unknown_missing_label_accepts_explicit_nonduplicate_player_row(tmp_path, monkeypatch, book, catalog, reply):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog, current=partial(labelled=False))
    # Flat ATK is missing; ATK% is already present and must not be duplicated.
    result = asyncio.run(owner.hsr_companion_prepare(query=reply))
    stated = result["player_equipment_statement"]
    if reply.endswith("%"):
        assert stated["state"] == "needs_clarification" and stated["row"] is None
    else:
        assert stated["state"] == "accepted" and stated["row"]["value"] == "19"


def test_explicit_value_without_original_object_cannot_write_an_equipment_row(tmp_path, monkeypatch, book, catalog):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog)
    owner._equipment_reply_pending = None
    result = asyncio.run(owner.hsr_companion_prepare(query="防御力76", player_reply="76"))
    assert result["player_equipment_statement"]["state"] == "needs_context"
    assert result["player_equipment_statement"]["row"] is None


@pytest.mark.parametrize("reply", ["7" * 81, {"value": 76}])
def test_invalid_original_reply_type_or_length_is_not_coerced(tmp_path, monkeypatch, book, catalog, reply):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog)
    result = asyncio.run(owner.hsr_companion_prepare(query="补充缺项", player_reply=reply))
    assert result["player_equipment_statement"]["state"] == "needs_clarification"
    assert result["player_equipment_statement"]["row"] is None


def test_player_reply_cancels_original_visual_followup_without_rearming(tmp_path, monkeypatch, book, catalog):
    from types import SimpleNamespace

    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog)

    async def run():
        owner._pending_answer = SimpleNamespace(request_id="original-missing-value")
        owner._followup_task = asyncio.create_task(asyncio.sleep(10))
        result = await owner.hsr_companion_prepare(query="76")
        with pytest.raises(asyncio.CancelledError):
            await owner._followup_task
        assert owner._pending_answer is None
        assert owner._followup_status["reason"] == "new_query"
        assert result["player_equipment_statement"]["state"] == "accepted"
        assert result["query_timing"]["refresh_wait_seconds"] == 0
        assert not result["followup"]["pending"]

    asyncio.run(run())


def test_old_formal_result_does_not_overwrite_a_newer_missing_value_binding(tmp_path, monkeypatch, book, catalog):
    owner, _, _, _ = prepared(tmp_path, monkeypatch, book, catalog)
    owner._equipment_reply_pending = None
    newer = {"newer_binding": True}

    async def audit(*args, **kwargs):
        owner._answer_query_generation += 1
        owner._equipment_reply_pending = newer

    async def resolve(plugin, demand, **kwargs):
        return QueryObservation(ObservationSnapshot.select(plugin, partial(), fields=demand.fields),
                                {"outcome": "partial", "stages": []}, 0, kwargs["generation"])

    module = importlib.import_module(type(owner).__module__)
    monkeypatch.setattr(module, "resolve_query", resolve)
    monkeypatch.setattr(owner, "_record_tool_use", audit)
    result = asyncio.run(owner.hsr_companion_prepare(query=QUERY))
    assert result["player_reply_contract"]["state"] == "not_needed"
    assert owner._equipment_reply_pending is newer
