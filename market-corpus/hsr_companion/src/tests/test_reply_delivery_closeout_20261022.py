"""Selected-answer contracts. Synthetic facts, no game/model/helper traffic."""
import asyncio
import copy
import time

import pytest
from plugin.plugins.hsr_companion.runtime import answer_message
from plugin.plugins.hsr_companion.runtime.consultation import for_followup, model_guidance
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests.test_d1_closeout import panel
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

book, catalog = cases.book, cases.catalog


def select(query, book, catalog, *, visual=None, intent="equipment_guide", declaration=None):
    base = build_query_plan(query, intent)
    plan = plan_guidance(query, base, book=book, catalog=catalog, consultation=declaration)
    return plan.observation_plan(base), build_guidance(plan, query, book=book, catalog=catalog,
                                                      profile={}, visual=visual)


@pytest.mark.parametrize("intent", ["conversation", "equipment_guide"])
@pytest.mark.parametrize("query,topics", [
    ("真珠遗器怎么配？", {"relic"}), ("真珠光锥怎么配？", {"cone"}),
    ("真珠遗器和光锥怎么配？", {"relic", "cone"}),
    ("真珠不用讲光锥，只看遗器怎么配？", {"relic"}),
    ("真珠不用讲遗器，只看光锥怎么配？", {"cone"}),
])
def test_one_planned_topic_scope(book, catalog, intent, query, topics):
    _, g = select(query, book, catalog, intent=intent)
    options = g["reply_plan"]["discussion_options"]
    assert options
    assert bool(options[0]["main_stats"]) == ("relic" in topics)
    # The fixture has no external cones; inspect planned scope as well.
    assert set(g["reply_plan"]["guide_topics"]) == topics


def test_declared_topics_are_not_reinterpreted_by_answer_keywords(book, catalog):
    _, g = select("真珠这两部分都帮我看看", book, catalog, declaration={
        "scope": "reference_build", "subject_state": "specified", "character": "真珠",
        "topics": ["relic", "cone"], "answer_parts": ["options", "next_step"]})
    assert set(g["reply_plan"]["guide_topics"]) == {"relic", "cone"}
    assert g["reply_plan"]["discussion_options"][0]["main_stats"]


def test_unspoken_optional_number_does_not_interrupt(book, catalog):
    q = "这件遗器适合绯英吗？"
    _, first = select(q, book, catalog, intent="assess_relic", visual=relic(
        subs=[("暴击率", "8.7%"), ("防御力", "15.1%"), ("生命值", "80")]))
    _, later = select(q, book, catalog, intent="assess_relic", visual=relic(
        subs=[("暴击率", "8.7%"), ("防御力", "15.1%"), ("生命值", "81")]))
    assert first["reply_plan"]["conclusion"] == later["reply_plan"]["conclusion"]
    assert not for_followup(later["reply_plan"], previous=first["reply_plan"])["answer_increment"]


def test_previously_selected_exact_number_correction_is_delivered(book, catalog):
    q = "完整报出这件遗器主副词条，适合绯英吗？"
    _, g = select(q, book, catalog, intent="assess_relic", visual=relic())
    old, new = g["reply_plan"], copy.deepcopy(g["reply_plan"])
    new["answer_basis"]["rows"][-1][-1] = "81"
    assert for_followup(new, previous=old)["answer_increment"]


def test_full_rows_fit_and_next_step_are_all_in_body(book, catalog, monkeypatch):
    q = "完整报出这件遗器主副词条，并说明适合绯英吗、下一步怎么改？"
    p, g = select(q, book, catalog, intent="assess_relic", visual=relic())
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)
    card = g["assessment"]["equipment_answer"]
    text, receipt = answer_message.render_followup({"guidance_answer": g, "equipment_answer": card}, plan=p, ready=True)
    for row in [card["main_stat"], *card["sub_stats"]]:
        assert row["label"] + row["value"] in text
    assert g["reply_plan"]["next_step"] in text
    assert receipt["answer_complete"]
    assert set(receipt["carried_sections"]) >= {"rows", "assessment", "next_step"}


def test_required_next_step_missing_never_reports_success(book, catalog, monkeypatch):
    q = "完整报出这件遗器主副词条，并说明适合绯英吗、下一步怎么改？"
    p, g = select(q, book, catalog, intent="assess_relic", visual=relic())
    g["reply_plan"]["next_step"] = ""
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)
    _, receipt = answer_message.render_followup({"guidance_answer": g,
        "equipment_answer": g["assessment"]["equipment_answer"]}, plan=p, ready=True)
    assert not receipt["answer_complete"]
    assert "next_step" in receipt["missing_sections"]


def test_panel_selector_uses_actual_attributes_kind(book, catalog):
    v = panel()
    v["characters"] = [{"entity_id": "character.pearl", "name": "真珠", "identity_scope": "detail_subject"}]
    _, g = select("真珠这面板怎么样，接下来先养哪儿？", book, catalog, intent="assess_panel", visual=v)
    assert g["assessment"]["evaluation"]["kind"] == "attributes"
    assert g["reply_plan"]["next_step"]
    assert g["reply_plan"]["primary_direction"] in g["reply_plan"]["conclusion"]


def test_late_identity_still_finishes_assessment(book, catalog):
    _, g = select("这件适合绯英吗？", book, catalog, intent="assess_relic", visual=relic())
    first = copy.deepcopy(g["reply_plan"])
    first["evaluation_available"] = False
    first["answer_basis"]["subjects"] = []
    assert for_followup(g["reply_plan"], previous=first)["answer_increment"]


def test_no_reading_gives_no_generic_advice(book, catalog):
    _, g = select("这件遗器适合绯英吗？", book, catalog, intent="assess_relic", visual={})
    reply = g["reply_plan"]
    assert reply["response_state"] == "unconfirmed"
    assert not reply["next_step"] and not reply["evidence_rows"]
    assert not reply["discussion_options"]
    assert reply["max_questions"] == 0


def test_explicit_rows_request_keeps_all_rows(book, catalog):
    # The former test used a plain fit question and treated a model-added rows
    # flag as a player request. Preserve actual list coverage, not that defect.
    _, g = select("完整报出这件词条，适合绯英吗", book, catalog, intent="assess_relic", visual=relic(),
        declaration={"scope": "current_relic", "subject_state": "specified", "character": "绯英",
                     "answer_parts": ["rows", "assessment"]})
    assert g["reply_plan"]["mode"] == "full"
    assert len(g["reply_plan"]["evidence_rows"]) == 5


@pytest.mark.parametrize("intent", ["conversation", "continue_consultation"])
def test_item_continuation_inherits_answer_purpose_not_old_rows(book, catalog, intent):
    q = "完整报出这件遗器主副词条，并说明适合绯英吗、下一步怎么改？"
    _, first = select(q, book, catalog, intent="assess_relic", visual=relic())
    base = build_query_plan("这个呢？", intent)
    p = plan_guidance("这个呢？", base, book=book, catalog=catalog, previous=first["context"])
    new = relic(main="治疗量加成", value="34.5%", serial=97, subs=[("攻击力", "19")])
    g = build_guidance(p, "这个呢？", book=book, catalog=catalog, profile={}, visual=new)
    assert set(g["reply_plan"]["required_sections"]) == {"rows", "assessment", "next_step"}
    assert [r["value"] for r in g["reply_plan"]["evidence_rows"]] == ["34.5%", "19"]


def test_explicit_new_answer_parts_replace_continuation_parts(book, catalog):
    _, first = select("完整报出这件遗器主副词条，并说明适合绯英吗、下一步怎么改？", book, catalog,
                      intent="assess_relic", visual=relic())
    base = build_query_plan("别列数了，只说合不合适", "continue_consultation", response_detail="brief")
    p = plan_guidance(base.query, base, book=book, catalog=catalog, previous=first["context"], consultation={
        "scope": "current_relic", "subject_state": "inherit", "answer_parts": ["assessment"]})
    g = build_guidance(p, base.query, book=book, catalog=catalog, profile={}, visual=relic())
    assert g["reply_plan"]["required_sections"] == ["assessment"]
    assert g["reply_plan"]["mode"] == "brief"


def test_actual_formatter_keeps_full_rows_fit_and_next_step(book, catalog):
    q = "完整报出这件遗器主副词条，并说明适合绯英吗、下一步怎么改？"
    p, g = select(q, book, catalog, intent="assess_relic", visual=relic())
    card = g["assessment"]["equipment_answer"]
    text, receipt = answer_message.render_followup({"guidance_answer": g, "equipment_answer": card}, plan=p, ready=True)
    assert answer_message.bridge_preview(text) == text
    assert receipt["answer_complete"], receipt
    for r in [card["main_stat"], *card["sub_stats"]]:
        assert r["label"] + r["value"] in text
    assert g["reply_plan"]["next_step"] in text


def test_brief_body_solves_fit_without_number_recital(book, catalog):
    p, g = select("这件遗器适合绯英吗？", book, catalog, intent="assess_relic", visual=relic())
    text, receipt = answer_message.render_followup({"guidance_answer": g,
        "equipment_answer": g["assessment"]["equipment_answer"]}, plan=p, ready=True)
    assert receipt["answer_complete"] and answer_message.bridge_preview(text) == text
    assert g["reply_plan"]["conclusion"] in text and g["reply_plan"]["next_step"] in text
    assert not any(r["value"] in text for r in g["reply_plan"]["evidence_rows"])


def test_model_view_uses_selection_without_destroying_full_facts(book, catalog):
    _, g = select("这件遗器适合绯英吗？", book, catalog, intent="assess_relic", visual=relic())
    before = copy.deepcopy(g)
    projected = model_guidance(g)
    assert not {"guides", "team_candidates", "instruction"} & projected.keys()
    assert projected["imported_builds"] == before["imported_builds"]
    assert projected["assessment"]["equipment_answer"] == before["assessment"]["equipment_answer"]
    assert projected["reply_plan"]["conclusion"] == before["reply_plan"]["conclusion"]
    assert "answer_basis" not in projected["reply_plan"]
    assert g == before
    d2 = {"kind": "challenge", "reply_plan": {"enabled": False}, "challenge_plan": {"assignments": [1, 2]}}
    assert model_guidance(d2) is d2


def test_real_pending_loop_ignores_unspoken_update_and_sends_final_once(book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner
    q = "这件遗器适合绯英吗？"
    first = relic(subs=[("暴击率", "8.7%"), ("防御力", "15.1%"), ("生命值", "80")])
    optional = relic(subs=[("暴击率", "8.7%"), ("防御力", "15.1%"), ("生命值", "81")], serial=97)
    final = relic(serial=98)
    owner, sent = followup_owner(monkeypatch)
    owner._guidance_book, owner._catalog = book, catalog
    base = build_query_plan(q, "assess_relic", screen_fields=["page", "equipment"])
    planned = plan_guidance(q, base, book=book, catalog=catalog)
    observed = planned.observation_plan(base)
    reads = []

    async def current():
        assert not sent, "Optional HP update must not consume the progress slot"
        value = final if reads else optional
        reads.append(1)
        owner._observation_serial = value["screen_facts"]["observation_serial"]
        return copy.deepcopy(value)

    owner._current_visual_evidence = current

    async def run():
        g = build_guidance(planned, q, book=book, catalog=catalog, profile={}, visual=first)
        armed = followup.arm_followup(owner, query=q, visual=first, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False,
            plan=observed, guidance_plan=planned, guidance_profile={}, guidance_answer=g)
        assert armed["pending"]
        request = owner._pending_answer
        deadline = request.expires_at
        await owner._followup_task
        assert request.expires_at == deadline
        assert owner._pending_answer is None and owner._followup_status["answer_complete"]

    asyncio.run(run())
    assert len(reads) == 2 and len(sent) == 1
    text = sent[0]["parts"][0]["text"]
    assert "生命值81" not in text and sent[0]["metadata"]["outcome"] == "ready"


def test_unspoken_mismatch_is_still_meaningful_progress(book, catalog):
    _, final = select("真珠这面板怎么样？", book, catalog, intent="assess_panel", visual=panel())
    wrong = panel()
    wrong["characters"] = [{"entity_id": "character.evanescia", "name": "绯英", "identity_scope": "detail_subject"}]
    _, later = select("真珠这面板怎么样？", book, catalog, intent="assess_panel", visual=wrong)
    assert for_followup(later["reply_plan"], previous=final["reply_plan"])["answer_increment"]
    assert not later["reply_plan"]["evaluation_available"]


def test_previously_spoken_brief_correction_survives_real_formatter(book, catalog):
    q = "这件遗器适合绯英吗？"
    p, first = select(q, book, catalog, intent="assess_relic", visual=relic())
    first["reply_plan"]["evidence_rows"] = [{"kind": "sub_stat", "label": "生命值", "value": "80", "speak_value": True}]
    v = relic(subs=[("暴击率", "8.7%"), ("暴击伤害", "11.6%"), ("防御力", "15.1%"), ("生命值", "81")])
    _, later = select(q, book, catalog, intent="assess_relic", visual=v)
    later["reply_plan"] = for_followup(later["reply_plan"], previous=first["reply_plan"])
    assert later["reply_plan"]["answer_increment"]
    text, receipt = answer_message.render_followup({"guidance_answer": later,
        "equipment_answer": later["assessment"]["equipment_answer"]}, plan=p, ready=True)
    assert receipt["answer_complete"] and answer_message.bridge_preview(text) == text
    assert "生命值81" in text and "生命值80" not in text


def test_missing_required_part_after_formatter_stays_incomplete(book, catalog, monkeypatch):
    p, g = select("完整报出这件遗器主副词条，并说明适合绯英吗、下一步怎么改？", book, catalog,
                  intent="assess_relic", visual=relic())
    # A bridge that silently cuts even one essential character is not a full
    # delivery; no byte/row cutter is introduced to make it look successful.
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text[:-1])
    _, receipt = answer_message.render_followup({"guidance_answer": g,
        "equipment_answer": g["assessment"]["equipment_answer"]}, plan=p, ready=True)
    assert not receipt["answer_complete"] and not receipt["complete"]
    assert set(receipt["missing_sections"]) == {"rows", "assessment", "next_step"}


@pytest.mark.parametrize("full", [False, True])
def test_temporary_reading_regression_is_not_new_answer_progress(book, catalog, full):
    q = "完整报出这件遗器主副词条，适合绯英吗？" if full else "这件遗器适合绯英吗？"
    _, first = select(q, book, catalog, intent="assess_relic", visual=relic())
    _, unread = select(q, book, catalog, intent="assess_relic", visual={})
    assert not for_followup(unread["reply_plan"], previous=first["reply_plan"])["answer_increment"]


def test_page_fallback_cannot_claim_an_omitted_consultation_part(monkeypatch):
    from plugin.plugins.hsr_companion.runtime import page_message
    p = build_query_plan("页面说明和下一步建议", "read_screen", screen_fields=["page", "visible_text"])
    # Simulate the formatter taking the existing page fallback. A page-only
    # receipt must not certify an additional requested service it didn't carry.
    monkeypatch.setattr(answer_message, "bridge_preview", lambda _: None)
    monkeypatch.setattr(page_message, "summarize_page", lambda *a, **kw: ("页面摘要", {
        "complete": True, "has_content": True, "answer_complete": True,
        "carried_sections": ["page", "stage_title", "objectives"]}))
    _, receipt = answer_message.render_followup({"guidance_answer": {"reply_plan": {
        "enabled": True, "mode": "brief", "required_sections": ["next_step"]}}}, plan=p, ready=True)
    assert not receipt["answer_complete"] and receipt["missing_sections"] == ["next_step"]
