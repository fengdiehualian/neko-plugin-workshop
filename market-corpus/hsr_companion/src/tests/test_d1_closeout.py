"""D1 expression scope: full facts survive brief replies; no host speech claim."""
import asyncio
import copy
import importlib
import json
import time

import pytest

from plugin.plugins.hsr_companion.runtime.guidance import build_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests.test_guidance import book, catalog, plan
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import assess, relic
from plugin.plugins.hsr_companion.tests.test_stage1_repairs import fact, visual


def panel():
    current = visual(*[fact("stat", label, value) for label, value in [
        ("生命值", "3311"), ("攻击力", "1754"), ("防御力", "3132"),
        ("速度", "143"), ("暴击率", "8.2%"), ("暴击伤害", "80.4%")]])
    for row in current["screen_facts"]["facts"]:
        row["scope"] = "attributes"
    current["page_type"] = "character_attributes"
    return current


def test_normal_panel_selects_focus_without_deleting_facts(book, catalog):
    current = panel()
    # This test assumes an attributable panel. Unknown ownership is covered
    # separately and must not acquire advice/question rights from numbers alone.
    current["characters"] = [{"entity_id": "character.pearl", "name": "真珠", "identity_scope": "detail_subject"}]
    result = assess("真珠这个面板养得怎么样？接下来优先关注什么？", current, book, catalog)
    reply = result["reply_plan"]
    assert reply["mode"] == "brief"
    assert 1 <= len(reply["evidence_rows"]) <= 2
    assert reply["evidence_rows"][0]["label"] == "防御力"
    assert reply["max_questions"] == 0 and not reply["question"]  # no automatic generic usage interview
    assert len(result["assessment"]["evaluation"]["rows"]) == 6
    assert not reply["numerical_grade_available"]
    assert reply["missing_is_not_absent"]
    assert result["guides"] and result["sources"]


@pytest.mark.parametrize("query", [
    "这件遗器的主副词条是什么？适合绯英吗？",
    "完整列出这件遗器的所有词条，适合绯英吗？",
    "逐项分析这件遗器适合绯英吗，别漏词条",
])
def test_explicit_full_request_preserves_all_five_rows(book, catalog, query):
    result = assess(query, relic(), book, catalog)
    reply = result["reply_plan"]
    assert reply["mode"] == "full"
    assert reply["must_include_all_confirmed_requested_rows"]
    assert reply["max_sentences"] is None and reply["max_questions"] == 0
    assert [(r["kind"], r["label"], r["value"]) for r in reply["evidence_rows"]] == [
        ("main_stat", "物理属性伤害提高", "38.8%"),
        ("sub_stat", "暴击率", "8.7%"), ("sub_stat", "暴击伤害", "11.6%"),
        ("sub_stat", "防御力", "15.1%"), ("sub_stat", "生命值", "80")]
    assert reply["rows_complete"]


def test_full_partial_reports_every_known_row_and_missing_without_filling(book, catalog):
    result = assess("这件遗器的主副词条是什么？适合绯英吗？",
                    relic(subs=[("攻击力", "19"), ("暴击率", "5.8%")]), book, catalog)
    reply = result["reply_plan"]
    assert len(reply["evidence_rows"]) == 3
    assert not reply["rows_complete"] and reply["missing_rows_must_be_disclosed"]
    assert [r["value"] for r in reply["evidence_rows"]] == ["38.8%", "19", "5.8%"]
    assert "23" not in json.dumps(reply, ensure_ascii=False)


def test_only_requested_main_is_not_expanded_to_full_substats(book, catalog):
    result = assess("这件遗器的主词条是什么？适合绯英吗？", relic(), book, catalog)
    assert [(r["kind"], r["value"]) for r in result["reply_plan"]["evidence_rows"]] == [("main_stat", "38.8%")]


def test_brief_relic_keeps_full_card_but_does_not_recite_it(book, catalog):
    result = assess("这件遗器适合绯英吗？", relic(), book, catalog)
    assert result["reply_plan"]["mode"] == "brief"
    assert len(result["reply_plan"]["evidence_rows"]) <= 2
    assert len(result["assessment"]["equipment_answer"]["sub_stats"]) == 4


def test_full_detail_can_be_followed_up_and_does_not_leak_old_item(book, catalog):
    first = assess("这件遗器的主副词条是什么？适合绯英吗？", relic(), book, catalog)
    second = assess("这个呢", relic(main="攻击力", value="43.2%", serial=97,
                    subs=[("速度", "2")]), book, catalog, previous=first["context"])
    assert second["reply_plan"]["mode"] == "full"
    assert [r["value"] for r in second["reply_plan"]["evidence_rows"]] == ["43.2%", "2"]
    ordinary = assess("这件遗器适合绯英吗", relic(), book, catalog, previous=second["context"])
    assert ordinary["reply_plan"]["mode"] == "brief"


def test_full_panel_and_negative_detail_preference(book, catalog):
    full = assess("真珠面板怎么样？全部属性逐项说说", panel(), book, catalog)
    assert len(full["reply_plan"]["evidence_rows"]) == 6
    brief = assess("真珠面板怎么样？不用详细展开，重点就好", panel(), book, catalog)
    assert brief["reply_plan"]["mode"] == "brief"


@pytest.mark.parametrize("query", [
    "这件遗器词条都报一下，适合绯英吗", "把这件遗器的主副词条念出来，适合绯英吗",
    "这件遗器适合绯英吗？完整词条都要，说得简短点",
    "这件遗器适合绯英吗？不用详细分析，但全部主副词条都列出",
])
def test_full_stat_demand_wins_over_soft_brevity(book, catalog, query):
    reply = assess(query, relic(), book, catalog)["reply_plan"]
    assert reply["mode"] == "full" and len(reply["evidence_rows"]) == 5


def test_full_attribute_extraction_independent_of_reviewed_role(book, catalog):
    result = assess("卡芙卡的面板怎么样？全部属性都列出来", panel(), book, catalog)
    reply = result["reply_plan"]
    assert len(reply["evidence_rows"]) == 6
    assert not reply["evaluation_available"] and not reply["next_step"]
    current = panel()
    conflicting = fact("stat", "防御力", "3456")
    conflicting["scope"] = "attributes"
    current["screen_facts"]["facts"].append(conflicting)
    assert not assess("真珠面板怎么样，全部属性列出", current, book, catalog)["reply_plan"]["evidence_rows"]


def test_selector_never_rewrites_or_mutates_complete_card(book, catalog):
    from plugin.plugins.hsr_companion.runtime.consultation import build_reply_plan
    result = assess("这件遗器的主副词条是什么？适合绯英吗", relic(), book, catalog)
    assessment = result["assessment"]
    assessment["evaluation"]["main_stat"]["value"] = "99%"  # inconsistent annotation is not the fact source
    before = copy.deepcopy(assessment)
    demand = plan("这件遗器的主副词条是什么？适合绯英吗", book, catalog)
    role = next(r for r in book.characters if r["name"] == "绯英")
    reply = build_reply_plan(demand, assessment, role)
    assert reply["evidence_rows"][0]["value"] == "38.8%"
    reply["evidence_rows"][0]["value"] = "1%"
    assert assessment == before


def test_context_question_reply_keeps_subject_not_a_second_guide(book, catalog):
    first = assess("真珠面板怎么样？", panel(), book, catalog)
    demand = plan("大招接不上", book, catalog, previous=first["context"])
    assert demand.requested and not demand.requires_pixels
    assert demand.character_ids == ("character.pearl",)
    second = build_guidance(demand, "大招接不上", book=book, catalog=catalog, profile={})
    assert second["reply_plan"]["topic"] == "rotation"
    assert second["reply_plan"]["max_questions"] == 0
    assert second["reply_plan"]["evidence_rows"] == []  # no cached panel numbers
    assert second["reply_plan"]["next_step"]


def test_condition_response_is_bounded_and_resets_on_role_or_expiry(book, catalog):
    first = assess("真珠面板怎么样", panel(), book, catalog)
    changed = assess("绯英的面板怎么样", panel(), book, catalog, previous=first["context"])
    assert changed["reply_plan"]["topic"] == "general"
    expired = {**first["context"], "at": time.monotonic() - 601}
    assert not plan("大招接不上", book, catalog, previous=expired).requested
    assert not plan("大招接不上", book, catalog).requested
    for query in ("现在在哪，任务要干什么", "现在是什么页面", "今天好开心"):
        assert not plan(query, book, catalog, previous=first["context"]).requested
    challenge = plan("混沌回忆怎么配队", book, catalog, previous=first["context"])
    assert challenge.kind == "challenge" and not challenge.request_focus


def test_known_usage_condition_is_not_asked_again_on_next_item(book, catalog):
    first = assess("真珠面板怎么样", panel(), book, catalog)
    second = assess("大招接不上", {}, book, catalog, previous=first["context"])
    third = assess("这件遗器适合真珠吗", relic(), book, catalog, previous=second["context"])
    assert third["reply_plan"]["topic"] == "rotation"
    assert not third["reply_plan"]["question"]


def test_detail_followup_and_explicit_short_override(book, catalog):
    first = assess("绯英的面板怎么样", panel(), book, catalog)
    second = assess("详细说说", panel(), book, catalog, previous=first["context"])
    assert len(second["reply_plan"]["evidence_rows"]) == 6
    third = assess("简单说说", panel(), book, catalog, previous=second["context"])
    assert third["reply_plan"]["mode"] == "brief"


def test_full_request_does_not_need_a_role_to_list_actual_rows(book, catalog):
    first = assess("这件遗器的主副词条是什么？合适吗", relic(), book, catalog)
    assert first["context"]["awaiting_subject"]
    assert first["reply_plan"]["max_questions"] == 1
    assert not first["reply_plan"]["evaluation_available"]
    assert len(first["reply_plan"]["evidence_rows"]) == 5
    clarified = assess("绯英", relic(), book, catalog, previous=first["context"])
    assert clarified["reply_plan"]["mode"] == "full"
    assert len(clarified["reply_plan"]["evidence_rows"]) == 5


def test_pure_detail_questions_are_still_the_original_screen_route(book, catalog):
    for query in ("这件遗器的主副词条是什么", "这个光锥效果是什么", "现在在哪"):
        assert not plan(query, book, catalog).requested


@pytest.mark.parametrize("broken", ["conflict", "stale", "unsupported"])
def test_no_grade_or_invented_evidence_when_unavailable(book, catalog, broken):
    current = relic()
    name = "绯英"
    if broken == "conflict":
        current["screen_facts"]["facts"].append(fact("main_stat", "主词条·防御力", "54.0%"))
    elif broken == "stale":
        current["fresh"] = False
    else:
        name = "卡芙卡"
    result = assess(f"这件遗器适合{name}吗", current, book, catalog)
    reply = result["reply_plan"]
    assert not reply["evaluation_available"]
    assert not reply["numerical_grade_available"]
    assert not reply["next_step"]
    if broken != "unsupported":
        assert reply["evidence_rows"] == []


def test_formal_entry_keeps_card_receipt_and_focus_consistent(tmp_path, monkeypatch):
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture

    owner, values, _ = plugin_fixture(tmp_path)
    before = copy.deepcopy(values["player_context"])
    current = relic()
    owner._observation_serial = 96

    async def resolve(plugin, demand, **kwargs):
        return QueryObservation(ObservationSnapshot.select(plugin, current, fields=demand.fields),
                                {"outcome": "reused", "stages": []}, 0, kwargs["generation"])
    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)

    async def run():
        brief = await owner.hsr_companion_prepare(query="这件遗器适合绯英吗？")
        full = await owner.hsr_companion_prepare(query="这件遗器主副词条是什么？适合绯英吗？")
        for value in (brief, full):
            assert value["equipment_answer"] == value["guidance_answer"]["assessment"]["equipment_answer"]
            assert value["delivery_receipt"]["equipment_consistency"] == "matches"
            assert value["answer_policy"]["consultation_reply"]
        assert len(brief["guidance_answer"]["reply_plan"]["evidence_rows"]) <= 2
        assert len(full["guidance_answer"]["reply_plan"]["evidence_rows"]) == 5
        assert values["player_context"] == before
    asyncio.run(run())


@pytest.mark.parametrize("query,mode,count", [
    ("这件遗器适合绯英吗？", "brief", 2),
    ("这件遗器主副词条是什么？适合绯英吗？", "full", 5),
    ("这件遗器主副词条是什么？", None, None),
])
def test_real_followup_executor_uses_same_policy_no_new_question(book, catalog, monkeypatch, query, mode, count):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    current = relic()
    owner, sent = followup_owner(monkeypatch, value=current)
    owner._guidance_book, owner._catalog = book, catalog
    demand = plan(query, book, catalog)
    observed = demand.observation_plan(build_query_plan(query))

    async def run():
        followup.arm_followup(owner, query=query, visual={"fresh": False}, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False,
            plan=observed, guidance_plan=demand if demand.requested else None, guidance_profile={})
        await owner._followup_task
    asyncio.run(run())
    assert len(sent) == 1
    text = sent[0]["parts"][0]["text"]
    data = owner._test_answer_payloads[-1]
    assert len(data["equipment_answer"]["sub_stats"]) == 4
    if mode:
        reply = data["guidance_answer"]["reply_plan"]
        assert reply["mode"] == mode and len(reply["evidence_rows"]) == count
        assert reply["max_questions"] == 0 and not reply["question"]
    if mode != "brief":
        from utils.result_parser import parse_push_message_content
        received = parse_push_message_content(text, lang="zh")
        for row in [data["equipment_answer"]["main_stat"], *data["equipment_answer"]["sub_stats"]]:
            assert row["label"] + row["value"] in received
    assert owner._followup_status["state"] == "submitted"
