"""12:59 real argument shape; synthetic facts, no OCR/model/installation.

Reading every row to assess an item is not a demand to say every row. Preserve
the over-wide model declaration in the regression instead of correcting it.
"""
import asyncio
import copy

import pytest
from plugin.plugins.hsr_companion.runtime import answer_message
from plugin.plugins.hsr_companion.runtime.consultation import for_followup, model_guidance
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as fixtures
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

book, catalog = fixtures.book, fixtures.catalog
QUERY = "这件遗器给真珠用怎么样？"
ACTUAL = {"scope": "current_relic", "subject_state": "specified", "character": "真珠",
          "topics": ["relic"], "answer_parts": ["rows", "assessment"]}


def current_item():
    return relic(main="防御力", value="54.0%",
                 subs=[("攻击力", "55"), ("防御力", "61"), ("速度", "4"), ("效果抵抗", "3.4%")])


def select(book, catalog, *, query=QUERY, detail=None, declaration=None, visual=None):
    base = build_query_plan(query, "assess_relic", screen_fields=["equipment"], response_detail=detail)
    plan = plan_guidance(query, base, book=book, catalog=catalog,
                         consultation=copy.deepcopy(ACTUAL if declaration is None else declaration))
    return plan.observation_plan(base), build_guidance(plan, query, book=book, catalog=catalog,
        profile={}, visual=current_item() if visual is None else visual)


def test_actual_overwide_args_do_not_upgrade_ordinary_fit_to_recital(book, catalog):
    _, guidance = select(book, catalog)
    reply = guidance["reply_plan"]
    assert reply["mode"] == "brief"
    assert reply["required_sections"] == ["assessment"]
    assert not any(row["speak_value"] for row in reply["evidence_rows"])
    assert len(guidance["assessment"]["equipment_answer"]["sub_stats"]) == 4
    assert not guidance["assessment"]["evaluation"]["replacement_decided"]


@pytest.mark.parametrize("query,detail", [
    ("完整报出这件遗器的主词条和四项副词条，适合真珠吗，下一步怎么改？", None),
    ("完整报出这件遗器的主词条和四项副词条，适合真珠吗，下一步怎么改？", "brief"),
    (QUERY, "full"),
])
def test_genuine_full_mode_still_preserves_every_exact_row(book, catalog, monkeypatch, query, detail):
    declaration = {**ACTUAL, "answer_parts": ["assessment", "next_step"]}
    plan, g = select(book, catalog, query=query, detail=detail, declaration=declaration)
    assert g["reply_plan"]["mode"] == "full"
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)
    card = g["assessment"]["equipment_answer"]
    text, receipt = answer_message.render_followup({"guidance_answer": g, "equipment_answer": card}, plan=plan, ready=True)
    for row in [card["main_stat"], *card["sub_stats"]]:
        assert row["label"] + row["value"] in text
    assert receipt["answer_complete"] and "next_step" in receipt["carried_sections"]


def test_useful_fit_gives_bounded_keep_decision_without_forcing_comparison(book, catalog):
    _, g = select(book, catalog, declaration={**ACTUAL, "answer_parts": ["assessment"]})
    reply = g["reply_plan"]
    assert "留" in reply["conclusion"] and "候选" in reply["conclusion"]
    assert "防御" in reply["conclusion"]
    assert not reply["next_step"], "Do not append the same comparison homework to every fit answer"
    assert not reply["question"]
    assert not any(word in reply["conclusion"] for word in ("毕业", "极品", "最优", "必须换"))


def test_followup_solves_same_fit_without_relisting_values(book, catalog, monkeypatch):
    plan, g = select(book, catalog)
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)
    g["reply_plan"] = for_followup(g["reply_plan"])
    text, receipt = answer_message.render_followup({"guidance_answer": g,
        "equipment_answer": g["assessment"]["equipment_answer"]}, plan=plan, ready=True)
    assert g["reply_plan"]["conclusion"] in text
    assert not any(value in text for value in ("54.0%", "55", "61", "3.4%"))
    assert receipt["answer_complete"]
    assert not g["reply_plan"]["question"]


def test_requested_next_step_survives_brief_mode(book, catalog):
    _, g = select(book, catalog, query="这件遗器给真珠用怎么样，下一步怎么改？",
                  declaration={**ACTUAL, "answer_parts": ["assessment", "next_step"]})
    assert g["reply_plan"]["next_step"]
    assert "next_step" in g["reply_plan"]["required_sections"]


def test_no_evidence_still_short_and_no_generic_build_filler(book, catalog):
    _, g = select(book, catalog, visual={})
    reply = g["reply_plan"]
    assert not reply["next_step"] and not reply["discussion_options"] and not reply["question"]
    assert len(reply["conclusion"]) < 25
    assert not reply["evaluation_available"]


def test_main_mismatch_is_not_a_keep_or_replace_verdict(book, catalog):
    _, g = select(book, catalog, visual=relic(main="火属性伤害提高", value="38.8%"))
    reply = g["reply_plan"]
    assert "不优先" in reply["conclusion"]
    assert not g["assessment"]["evaluation"]["replacement_decided"]
    assert "丢" not in reply["conclusion"]


def test_unspoken_number_update_still_silent(book, catalog):
    _, first = select(book, catalog)
    later_item = current_item()
    next(f for f in later_item["screen_facts"]["facts"] if f["kind"] == "sub_stat" and f["value"] == "55")["value"] = "56"
    _, later = select(book, catalog, visual=later_item)
    assert not for_followup(later["reply_plan"], previous=first["reply_plan"])["answer_increment"]


def test_fixed_head_is_not_penalized_for_fixed_hp(book, catalog):
    item = relic(main="生命值", value="705", subs=[("速度", "4"), ("防御力", "15.1%")])
    next(f for f in item["screen_facts"]["facts"] if f["kind"] == "equipment_slot")["value"] = "头部"
    _, g = select(book, catalog, visual=item)
    assert g["reply_plan"]["fixed_main_stat"]
    assert "固定" in g["reply_plan"]["conclusion"]
    assert not any(word in g["reply_plan"]["conclusion"] for word in ("生命值不适合", "生命值收益低"))


def test_explicit_no_recital_preference_beats_overwide_full_parameter(book, catalog):
    _, g = select(book, catalog, query="不用完整报数，这件遗器给真珠用怎么样？", detail="full")
    assert g["reply_plan"]["mode"] == "brief"
    assert "rows" not in g["reply_plan"]["required_sections"]


def test_formal_entry_keeps_actual_args_and_full_evidence_without_archive_writes(tmp_path, monkeypatch, catalog):
    from plugin.plugins.hsr_companion.runtime.prepare_trace import public_records
    from plugin.plugins.hsr_companion.tests.test_d1_subject_contract_20261020 import formal_fixture

    owner, values, sent, holder = formal_fixture(tmp_path, monkeypatch, catalog, current_item())
    before = copy.deepcopy(values["player_context"])

    async def run():
        first = await owner.hsr_companion_prepare(query=QUERY, intent="assess_relic",
            screen_fields=["equipment"], consultation=copy.deepcopy(ACTUAL))
        assert not first.get("is_error")
        assert first["guidance_answer"]["reply_plan"]["mode"] == "brief"
        assert first["guidance_answer"]["completion"]["evaluation_complete"]
        assert first["equipment_answer"]["complete"] and len(first["equipment_answer"]["sub_stats"]) == 4
        assert "先用 facts 回答" not in first["screen_context_answer"]["instruction"]
        record = public_records(owner._prepare_call_records)[-1]
        raw_parts = record["arguments"]["consultation"]["fields"]["answer_parts"]["items"]
        assert [row["value"] for row in raw_parts] == ["rows", "assessment"]
        assert record["arguments"]["response_detail"]["presence"] == "omitted"
        holder[0] = relic(main="火属性伤害提高", value="38.8%", serial=97)
        owner._observation_serial = 97
        swapped = await owner.hsr_companion_prepare(query="换成这件呢？", intent="continue_consultation",
            consultation={"scope": "current_relic", "subject_state": "inherit"})
        assert swapped["guidance_answer"]["completion"]["subject_names"] == ["真珠"]
        assert swapped["equipment_answer"]["main_stat"]["value"] == "38.8%"
        assert "54.0%" not in swapped["equipment_answer"]["exact_text"]
        assert "不优先" in swapped["guidance_answer"]["reply_plan"]["conclusion"]
        assert not swapped["followup"]["pending"]

    asyncio.run(run())
    assert values["player_context"] == before and not sent


def test_brief_model_view_removes_competing_advice_not_evidence(book, catalog):
    _, g = select(book, catalog)
    before = copy.deepcopy(g)
    projected = model_guidance(g)
    view = projected["assessment"]["evaluation"]
    assert not {"summary", "conditions", "next_steps"} & view.keys()
    assert projected["assessment"]["equipment_answer"] == before["assessment"]["equipment_answer"]
    assert projected["assessment"]["facts"] == before["assessment"]["facts"]
    assert [(row["label"], row["value"], row["verdict"]) for row in view["sub_stats"]] == [
        (row["label"], row["value"], row["verdict"]) for row in before["assessment"]["evaluation"]["sub_stats"]]
    assert not any("meaning" in row or "check" in row for row in view["sub_stats"])
    assert g == before, "Projection must not modify cached/full evidence or sourced assessment"


def test_full_model_view_keeps_requested_analysis_and_every_row(book, catalog):
    _, g = select(book, catalog, query="完整报出这件遗器主副词条，适合真珠吗，下一步怎么改？",
                  declaration={**ACTUAL, "answer_parts": ["rows", "assessment", "next_step"]})
    projected = model_guidance(g)
    assert projected["assessment"] == g["assessment"]
    assert len(projected["reply_plan"]["evidence_rows"]) == 5
    assert projected["reply_plan"]["next_step"]
