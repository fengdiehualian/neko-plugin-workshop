"""Recording failure replay: stable scope, no OCR or live chat side effects.

Hypotheses: natural wording must keep the consultation; a brief projection
must not erase read completeness; direct/followup share one request identity.
Desktop Reload is a reconnect, not a plugin or memory reset. A separate live
cat acceptance is required after installation; these tests do not prove it.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import time

import pytest
from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.answer_message import render_followup
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.service_contract import (
    clear_discussion,
    mark_service_request,
    record_answer_delivery,
    response_contract,
)
from plugin.plugins.hsr_companion.tests import test_d1_player_equipment_reply as reply_cases
from plugin.plugins.hsr_companion.tests import test_guidance as guidance_cases
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_d1_handoff_closeout import received_text
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

book = guidance_cases.book
catalog = guidance_cases.catalog
plan = guidance_cases.plan


def head(*, serial=43):
    value = relic(main="生命值", value="705", serial=serial, subs=[
        ("攻击力", "57"), ("生命值", "8.2%"), ("攻击力", "3.8%"), ("效果命中", "7.7%")])
    for row in value["screen_facts"]["facts"]:
        if row["kind"] == "equipment_name":
            row["value"] = "卜者的演算玉兆"
        elif row["kind"] == "equipment_slot":
            row["value"] = "头部"
    return value


@pytest.mark.parametrize("query", ["这件呢？这件怎么样？", "这件怎么样？", "换成这件呢？", "这件呢？"])
def test_natural_item_continuations_keep_role_and_full_stat_demand(book, catalog, query):
    first = plan("这件遗器给真珠合适吗？", book, catalog)
    prior = build_guidance(first, "这件遗器给真珠合适吗？", book=book, catalog=catalog,
                           profile={}, visual=head())["context"]
    chosen = plan(query, book, catalog, previous=prior)
    assert chosen.requested and chosen.character_ids == ("character.pearl",)
    assert chosen.request_focus == "equipment"
    assert chosen.observation_plan(build_query_plan(query)).required_equipment_kinds == {"main_stat", "sub_stat"}


@pytest.mark.parametrize("keep_context", [False, True])
@pytest.mark.parametrize("query", [
    "我真珠有专武了，遗器主词条怎么选？", "真珠遗器主词条如何选？",
    "真珠有专武，遗器主词条该怎么选择？",
])
def test_generic_stat_selection_never_becomes_a_screen_debt(book, catalog, keep_context, query):
    prior = {"at": time.monotonic(), "kind": "build", "character_ids": ["character.pearl"],
             "request_focus": "equipment", "equipment_kinds": ["main_stat", "sub_stat"]}
    chosen = plan(query, book, catalog, previous=prior if keep_context else None)
    assert chosen.consultation_action == "equipment_guide"
    assert not chosen.observation_plan(build_query_plan(query)).screen


def test_current_item_question_keeps_screen_and_unknown_subject_is_not_invented(book, catalog):
    chosen = plan("这件遗器主词条合适吗？", book, catalog)
    assert chosen.requires_pixels and not chosen.character_ids
    assert plan("今天吃什么？", book, catalog).requested is False


def test_brief_complete_relic_projects_explicit_read_status_through_real_bridge(book, catalog):
    query = "这件遗器给真珠合适吗？"
    chosen = plan(query, book, catalog)
    value = head()
    guidance = build_guidance(chosen, query, book=book, catalog=catalog, profile={}, visual=value)
    card = equipment_card(value)
    assert card["complete"] and len(guidance["reply_plan"]["evidence_rows"]) < 5
    data = {"equipment_answer": card, "guidance_answer": guidance}
    text, projection = render_followup(data, plan=chosen.observation_plan(build_query_plan(query)), ready=True)
    assert projection["complete"]
    assert "主副词条已读全" in text and "只列重点" in text
    message = {"parts": [{"type": "text", "text": text}], "ai_behavior": "respond", "visibility": []}
    assert received_text(message) == text
    assert card == equipment_card(value)  # Selecting evidence never changes the full card.


def test_brief_partial_relic_does_not_claim_summary_rows_are_all_read_rows(book, catalog):
    value = head()
    value["screen_facts"]["facts"] = [f for f in value["screen_facts"]["facts"]
                                       if not (f["kind"] == "sub_stat" and f["value"] == "57")]
    query = "这件遗器给真珠合适吗？"
    chosen = plan(query, book, catalog)
    card = equipment_card(value)
    guidance = build_guidance(chosen, query, book=book, catalog=catalog, profile={}, visual=value)
    text, projection = render_followup({"equipment_answer": card, "guidance_answer": guidance},
        plan=chosen.observation_plan(build_query_plan(query)), ready=True)
    assert projection["complete"] and not card["complete"]
    assert "主副词条已读全" not in text and "副词条已确认3/4项" in text
    assert "攻击力57" not in text
    assert received_text({"parts": [{"type": "text", "text": text}], "ai_behavior": "respond", "visibility": []}) == text


@pytest.mark.parametrize("complete", [False, True])
def test_full_rows_not_sacrificed_for_read_status(book, catalog, complete):
    value = head()
    if not complete:
        value["screen_facts"]["facts"] = [f for f in value["screen_facts"]["facts"]
                                                   if not (f["kind"] == "sub_stat" and f["value"] == "57")]
    query = "完整报出这件遗器主词条和四项副词条，适合真珠吗？"
    chosen = plan(query, book, catalog)
    card = equipment_card(value)
    data = {"equipment_answer": card, "guidance_answer": build_guidance(
        chosen, query, book=book, catalog=catalog, profile={}, visual=value)}
    text, projection = render_followup(data, plan=chosen.observation_plan(build_query_plan(query)), ready=True)
    assert projection["complete"]
    for row in [card["main_stat"], *card["sub_stats"]]:
        assert row["label"] + row["value"] in text
    assert ("缺项未读全" in text) is (not complete)


def test_followup_cannot_restore_a_cleared_discussion(monkeypatch, book, catalog):
    owner, sent = followup_owner(monkeypatch, value=head())
    owner._guidance_book, owner._catalog = book, catalog
    owner._discussion_generation = 3
    query = "这件遗器给真珠合适吗？"
    chosen = plan(query, book, catalog)
    original = render_followup

    def reset_before_submit(*args, **kwargs):
        clear_discussion(owner)
        return original(*args, **kwargs)

    monkeypatch.setattr(importlib.import_module(render_followup.__module__), "render_followup", reset_before_submit)

    async def run():
        followup.arm_followup(owner, query=query, visual={"fresh": False}, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False,
            plan=chosen.observation_plan(build_query_plan(query)), guidance_plan=chosen)
        await owner._followup_task
        assert not sent and owner._guidance_context is None

    asyncio.run(run())


def test_continuous_formal_calls_after_frontend_reconnect_do_not_require_cat_memory(tmp_path, monkeypatch, catalog):
    owner, values, sent = plugin_fixture(tmp_path)
    owner._catalog = catalog
    current = head(serial=1)
    owner._observation_serial = 1
    before = copy.deepcopy(values["player_context"])

    async def resolve(plugin, demand, **kwargs):
        return QueryObservation(ObservationSnapshot.select(plugin, current, fields=demand.fields),
                                {"outcome": "reused", "stages": []}, 0, kwargs["generation"])

    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)

    async def run():
        nonlocal current
        first = await owner.hsr_companion_prepare(query="这件遗器给真珠合适吗？")
        first_id = first["response_contract"]["request_id"]
        assert first["guidance_answer"]["requested"]
        assert first["delivery_receipt"]["request_id"] == first_id
        assert first["delivery_receipt"]["origin"] == "hsr_direct_tool"
        # Frontend reload destroys the caller's conversational hints, not the
        # plugin process or its verified fields. No implicit history argument.
        second = await owner.hsr_companion_prepare(query="这件呢？这件怎么样？", discussion_character="")
        assert second["guidance_answer"]["reply_plan"]["enabled"]
        assert second["response_contract"]["request_id"] != first_id
        assert second["response_contract"]["desktop_reload_is_context_reset"] is False
        third = await owner.hsr_companion_prepare(query="我真珠有专武了，遗器主词条怎么选？")
        assert not third["query_plan"]["screen"] and not third["followup"]["pending"]
        # New equipment has a different serial and must not borrow head stats.
        current = relic(main="速度", value="25", serial=2, subs=[("防御力", "33"), ("攻击力", "16"),
                            ("攻击力", "4.3%"), ("效果命中", "15.9%")])
        owner._observation_serial = 2
        fourth = await owner.hsr_companion_prepare(query="这件给真珠用怎么样？")
        assert fourth["equipment_answer"]["main_stat"]["value"] == "25"
        assert "57" not in fourth["equipment_answer"]["exact_text"]
        assert fourth["response_contract"]["channel"] == "direct"
        assert values["player_context"] == before and not sent

    asyncio.run(run())


def test_player_supplement_also_has_identity_without_claiming_fresh_pixels(tmp_path, monkeypatch, book, catalog):
    owner, values, calls, first = reply_cases.prepared(tmp_path, monkeypatch, book, catalog)
    saved = copy.deepcopy(values["player_context"])
    result = asyncio.run(owner.hsr_companion_prepare(query="76"))
    contract = result["response_contract"]
    assert contract["source"] == "hsr_companion" and contract["channel"] == "direct"
    assert contract["request_id"] != first["response_contract"]["request_id"]
    assert result["delivery_receipt"]["request_id"] == contract["request_id"]
    assert result["player_equipment_statement"]["row"]["value"] == "76"
    assert not result["player_equipment_statement"]["visually_confirmed"]
    assert not result["current_visual_evidence"]["fresh"]
    assert not result["followup"]["pending"] and len(calls) == 1
    assert values["player_context"] == saved


def test_direct_and_followup_share_one_origin_without_claiming_host_control(monkeypatch):
    owner, sent = followup_owner(monkeypatch, value=head())
    owner._answer_query_generation = 7
    mark_service_request(owner, 7, "started")
    original_id = owner._service_request["request_id"]

    async def run():
        demand = build_query_plan("完整报出这件遗器主副词条")
        armed = followup.arm_followup(owner, query=demand.query, visual={"fresh": False}, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False, plan=demand)
        assert armed["request_id"] == original_id
        await owner._followup_task

    asyncio.run(run())
    assert len(sent) == 1
    assert sent[0]["metadata"]["request_id"] == original_id
    assert sent[0]["metadata"]["generation"] == 7
    assert sent[0]["metadata"]["origin"] == "hsr_explicit_followup"


def test_late_receipt_keeps_its_packet_identity_not_the_new_active_question(monkeypatch):
    owner, _ = followup_owner(monkeypatch, value=head())
    mark_service_request(owner, 7, "started")
    contract = response_contract(owner, 7, channel="direct")
    mark_service_request(owner, 8, "started")
    receipt = record_answer_delivery(owner, generation=7, channel="direct",
        payload={"response_contract": contract}, visual={"fresh": False},
        answer={"state": "unconfirmed", "confirmed_fields": [], "missing_fields": ["equipment"]})
    assert receipt.get("request_id") == contract["request_id"]
    assert receipt["request_id"] != owner._service_request["request_id"]
