"""Goal-directed D1 handoffs, not scripted dialogue or host speech tests."""
import asyncio
import copy
import importlib
import time

import pytest

from plugin.plugins.hsr_companion.runtime.guidance import build_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests.test_guidance import book, catalog, plan
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import assess, relic
from plugin.plugins.hsr_companion.tests.test_d1_closeout import panel

FIRST = "真珠这个面板养得怎么样？接下来优先关注什么？"


@pytest.mark.parametrize("hints", [None, ["attributes", "equipment", "quest"]])
def test_exact_live_panel_question_does_not_owe_equipment_or_quest(book, catalog, hints):
    from plugin.plugins.hsr_companion.runtime.guidance import plan_guidance
    from plugin.plugins.hsr_companion.runtime.query_coordinator import needs_followup
    base = build_query_plan(FIRST, screen_fields=hints)
    selected = plan_guidance(FIRST, base, catalog=catalog, book=book, explicit_fields=bool(hints))
    observed = selected.observation_plan(base)
    assert observed.fields == {"attributes"}
    assert not needs_followup(observed, panel(), {"enabled": True, "state": "observing"})


@pytest.mark.parametrize("query,fields", [
    ("真珠面板怎么样，这件遗器主副词条是什么", {"attributes", "equipment"}),
    ("真珠面板怎么样，当前任务要做什么", {"attributes", "quest"}),
    ("真珠面板怎么样，现在在哪", {"attributes", "location"}),
])
def test_real_mixed_request_keeps_its_evidence_requirements(book, catalog, query, fields):
    assert plan(query, book, catalog).observation_plan(build_query_plan(query)).fields >= fields


def test_explicitly_declined_optional_reads_do_not_create_a_debt(book, catalog):
    query = "真珠这个面板怎么样，先不要看遗器，不用查任务"
    assert plan(query, book, catalog).observation_plan(build_query_plan(query)).fields == {"attributes"}
    mixed = "真珠这个面板怎么样，当前任务看看但不用看遗器"
    assert plan(mixed, book, catalog).observation_plan(build_query_plan(mixed)).fields == {"attributes", "quest"}


def test_explicit_screen_request_is_not_downgraded_to_general_advice(book, catalog):
    first = assess(FIRST, panel(), book, catalog)
    query = "看屏幕帮我推荐光锥"
    chosen = plan(query, book, catalog, previous=first["context"])
    assert chosen.requires_pixels
    assert "equipment" in chosen.observation_plan(build_query_plan(query)).fields


@pytest.mark.parametrize("query,action", [
    ("我应该先关注什么好？", "priority"), ("重点是什么？", "priority"),
    ("为什么要看这个？", "explain"), ("那怎么养呢？", "howto"),
    ("遗器怎么配？", "equipment_guide"), ("光锥推荐哪个？", "equipment_guide"),
    ("队伍怎么配？", "team_guide"),
])
def test_natural_followup_keeps_role_and_goal_without_requiring_a_screen(book, catalog, query, action):
    prior = assess(FIRST, panel(), book, catalog)["context"]
    selected = plan(query, book, catalog, previous=prior)
    assert selected.requested and not selected.requires_pixels
    assert selected.character_ids == ("character.pearl",)
    assert not selected.observation_plan(build_query_plan(query)).screen
    result = build_guidance(selected, query, book=book, catalog=catalog, profile={})
    reply = result["reply_plan"]
    assert reply["enabled"] and reply["goal"] == action
    assert reply["turn_contract"]["after_answer"] == "yield_to_player"
    assert not reply["evidence_rows"]  # no panel/old relic numbers recalled
    if action in {"priority", "explain", "howto"}:
        assert reply["next_step"] and not reply["question"]


def test_concrete_item_after_advice_still_needs_current_rows(book, catalog):
    first = assess(FIRST, panel(), book, catalog)
    guide = assess("遗器怎么配", {}, book, catalog, previous=first["context"])
    selected = plan("这件遗器的主副词条是什么？适合她吗", book, catalog, previous=guide["context"])
    assert selected.requires_pixels and selected.request_focus == "equipment"
    current = relic()
    answered = build_guidance(selected, selected.observation_plan(build_query_plan("这件遗器的主副词条是什么？适合她吗")).query,
                              book=book, catalog=catalog, profile={}, visual=current)
    assert len(answered["reply_plan"]["evidence_rows"]) == 5


def test_user_equipment_statement_is_not_visual_or_account_ownership(book, catalog, monkeypatch):
    original = catalog.get
    monkeypatch.setattr(catalog, "get", lambda key: {
        "id": key, "name": "未来，有我们一起", "entity_type": "light_cone", "path": "Elation",
        "sources": [{"source_id": "fixture"}]} if key == "light_cone.22007" else original(key))
    first = assess(FIRST, panel(), book, catalog)
    selected = plan("我带的是「未来，有我们一起」", book, catalog, previous=first["context"])
    assert selected.requested and not selected.requires_pixels
    answered = build_guidance(selected, "我带的是「未来，有我们一起」", book=book, catalog=catalog, profile={})
    stated = answered["player_conditions"]["stated_equipment"]
    assert stated[0]["id"] == "light_cone.22007"
    assert stated[0]["source"] == "player_statement" and not stated[0]["visually_confirmed"]
    assert not answered["assessment"]["visual_subject_confirmed"]
    assert not answered["current_equipment_label_confirmed"]
    assert not answered["reply_plan"]["evidence_rows"]
    assert answered["reply_plan"]["discussion_options"]
    continued = plan("光锥推荐哪个", book, catalog, previous=first["context"])
    guide = build_guidance(continued, "光锥推荐哪个", book=book, catalog=catalog, profile={})
    bare = plan("未来有我们一起", book, catalog, previous=guide["context"])
    named = build_guidance(bare, "未来有我们一起", book=book, catalog=catalog, profile={})
    assert named["player_conditions"]["stated_equipment"][0]["id"] == "light_cone.22007"
    assert not bare.requires_pixels and not named["current_equipment_label_confirmed"]


def test_followup_is_not_a_general_chat_catchall_or_stale_role(book, catalog):
    prior = assess(FIRST, panel(), book, catalog)["context"]
    for query in ("今天天气怎么样", "现在在哪", "现在是什么页面", "谢谢你", "我们玩别的吧"):
        assert not plan(query, book, catalog, previous=prior).requested
    expired = {**prior, "at": time.monotonic() - 601}
    assert not plan("重点是什么", book, catalog, previous=expired).requested
    changed = assess("绯英面板怎么样", panel(), book, catalog, previous=prior)
    assert changed["context"]["character_ids"] == ["character.evanescia"]
    assert not changed["player_conditions"]["stated_equipment"]


def test_player_can_supply_condition_after_priority_answer_without_another_question(book, catalog):
    first = assess(FIRST, panel(), book, catalog)
    second = assess("我应该先关注什么好", {}, book, catalog, previous=first["context"])
    assert not second["context"]["question_offered"]
    third = assess("大招接不上", {}, book, catalog, previous=second["context"])
    assert third["reply_plan"]["topic"] == "rotation"
    assert third["reply_plan"]["next_step"] and not third["reply_plan"]["question"]


@pytest.mark.parametrize("query", ["真珠面板怎么样", "真珠养得怎么样"])
def test_assessment_needs_actual_values_even_with_an_explicit_role(book, catalog, query):
    chosen = plan(query, book, catalog)
    assert chosen.requires_pixels and chosen.observation_plan(build_query_plan(query)).fields == {"attributes"}


def test_plain_advice_works_paused_and_does_not_get_new_visual_facts(tmp_path):
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    owner, values, sent = plugin_fixture(tmp_path)
    owner._observer_status.update(enabled=False, state="paused")
    before = copy.deepcopy(values["player_context"])

    async def forbidden(*args, **kwargs):
        raise AssertionError("A scoped knowledge answer must not read pixels")
    owner._current_visual_evidence = forbidden
    owner._read_game_state = forbidden
    owner._wait_for_data_pack = forbidden

    async def run():
        await owner.hsr_companion_prepare(query="真珠遗器光锥怎么配")
        for query in ("重点是什么", "那怎么养呢", "光锥推荐哪个"):
            result = await owner.hsr_companion_prepare(query=query)
            assert result["guidance_answer"]["reply_plan"]["enabled"]
            assert not result["query_plan"]["screen"] and not result["followup"]["pending"]
        assert values["player_context"] == before and not sent
    asyncio.run(run())


def test_partial_full_request_does_not_yield_before_registered_missing_evidence(book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    current = relic()
    owner, sent = followup_owner(monkeypatch, value=current)
    owner._guidance_book, owner._catalog = book, catalog
    query = "这件遗器完整主副词条是什么，适合绯英吗"
    selected = plan(query, book, catalog)
    observed = selected.observation_plan(build_query_plan(query))

    async def run():
        armed = followup.arm_followup(owner, query=query, visual=relic(subs=[("速度", "2")]),
            asked_at=time.time(), scene_only=False, navigation=False, content=True, controlled=False,
            plan=observed, guidance_plan=selected, guidance_profile={})
        assert armed["pending"]
        await owner._followup_task
    asyncio.run(run())
    text = sent[0]["parts"][0]["text"]
    result = owner._test_answer_payloads[-1]
    reply = result["guidance_answer"]["reply_plan"]
    assert len(reply["evidence_rows"]) == 5 and reply["must_include_all_confirmed_requested_rows"]
    assert reply["turn_contract"]["finish_when"] == "original_missing_requested_evidence_delivered"
    assert not reply["question"]
    from utils.result_parser import parse_push_message_content
    received = parse_push_message_content(text, lang="zh")
    for row in reply["evidence_rows"]:
        assert row["label"] + row["value"] in received


def test_question_is_a_handoff_not_a_license_to_continue(book, catalog):
    current = panel()
    # A usage question requires an attributed panel. Unbound-number handling
    # has its own R1 tests and must not be bypassed for a style assertion.
    current["characters"] = [{"entity_id": "character.pearl", "name": "真珠", "identity_scope": "detail_subject"}]
    reply = assess(FIRST, current, book, catalog)["reply_plan"]
    turn = reply["turn_contract"]
    assert not turn["question_is_last"] and turn["after_answer"] == "yield_to_player"
    assert turn["background_observation"] == "silent_unchanged"
    assert turn["optional_information_creates_no_followup"]
    assert not reply["length_is_hard_limit"]
    assert not reply["question"]  # a useful answer need not always end in a question


def test_hypothetical_or_negative_mentions_do_not_become_stated_equipment(book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime.consultation import stated_equipment
    original = catalog.get
    monkeypatch.setattr(catalog, "get", lambda key: {"id": key, "name": "未来，有我们一起", "sources": [{"source_id": "fixture"}]}
                        if key == "light_cone.22007" else original(key))
    prior = {"character_ids": ["character.pearl"]}
    for query in ("如果我带的是未来，有我们一起", "我没有未来，有我们一起", "我用的不是未来，有我们一起"):
        assert stated_equipment(query, prior=prior, book=book, catalog=catalog) == ()


def test_formal_entry_answers_panel_then_waits_for_player_no_stray_followup(tmp_path, monkeypatch):
    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    owner, values, sent = plugin_fixture(tmp_path)
    before = copy.deepcopy(values["player_context"])
    current = {**relic(), **panel()}
    current["scene"].update(label="角色属性", primary_state="menu")
    current["screen_facts"]["observation_serial"] = 96
    owner._observation_serial = 96
    demands = []

    async def resolve(plugin, demand, **kwargs):
        demands.append((demand, kwargs))
        chosen = current if demand.screen else public_visual_evidence({"invalidated": True}, owner._observer_status)
        return QueryObservation(ObservationSnapshot.select(plugin, chosen, fields=demand.fields),
                                {"outcome": "reused", "stages": []}, 0, kwargs["generation"])
    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)

    async def run():
        first = await owner.hsr_companion_prepare(query=FIRST, screen_fields=["attributes", "equipment", "quest"])
        assert first["query_plan"]["fields"] == ["attributes"]
        assert first["query_answer"]["state"] == "ready"
        assert not first["followup"]["pending"]
        second = await owner.hsr_companion_prepare(query="我应该先关注什么好？")
        assert second["guidance_answer"]["reply_plan"]["goal"] == "priority"
        assert not second["followup"]["pending"]
        assert not demands[-1][0].screen and demands[-1][1]["read_unrequested"] is False
        assert not second["guidance_answer"]["reply_plan"]["question"]
        assert values["player_context"] == before and not sent
    asyncio.run(run())
