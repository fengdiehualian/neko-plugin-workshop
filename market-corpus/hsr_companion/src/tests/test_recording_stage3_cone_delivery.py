"""Current-cone FIT through the real notification formatter, not name-only.

Historical OCR field shape, synthetic admission. No old frame becomes current
evidence, and no model, helper, game input or host message is invoked.
"""
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.runtime.guidance import plan_guidance
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance
from plugin.plugins.hsr_companion.runtime.cone_message import summarize_cone_fit
from plugin.plugins.hsr_companion.runtime.answer_message import bridge_preview
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests.test_d1_handoff_closeout import delivered, received_text

book = cases.book
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cone_catalog():
    registry = json.loads((ROOT / "resources/character_registry.json").read_text(encoding="utf-8"))
    catalog = ExternalKnowledgeCatalog(registry=registry)
    if not catalog.get("light_cone.23055"):
        pytest.skip("Frozen read-only game pack unavailable; do not download it for a unit test")
    return catalog


def cone_visual():
    return {"fresh": True, "page_type": "character_detail", "read_scope": "full",
            "scene": {"verified": True, "label": "角色光锥"}, "characters": [],
            "screen_facts": {"facts": [{"kind": "entity_label", "scope": "displayed_label",
                "entity_id": "light_cone.23055", "entity_type": "light_cone", "value": "献给明日的色彩",
                "label": "光锥", "evidence": [{"text": "献给明日的色彩", "validation_scope": "literal",
                    "region": {"left": .773, "top": .109, "right": .898, "bottom": .143}}]}]}}


def test_actual_cone_fit_not_just_name_survives_formatter(book, cone_catalog, monkeypatch):
    query = "这把光锥给真珠用怎么样，有什么要注意的？"
    base = build_query_plan(query, "assess_cone")
    plan = plan_guidance(query, base, book=book, catalog=cone_catalog,
        consultation={"scope": "current_cone", "subject_state": "specified", "character": "真珠"})
    _, message, packet = delivered(monkeypatch, cone_visual(), query,
        selected=plan, book=book, catalog=cone_catalog)
    receipt = message["metadata"]["message_projection"]
    text = received_text(message)
    assert packet["guidance_answer"]["completion"]["evaluation_complete"]
    assert "献给明日的色彩" in text and "真珠" in text
    assert receipt["complete"] and receipt["answer_complete"], receipt
    assert "依据：" in text and "建议：" in text
    assert "容量" not in text
    assert receipt["mode"] == "cone_fit_summary" and not receipt["all_input_rows_delivered"]
    assert packet["grounding"]["knowledge_results"]  # Full sources remain intact.


def packet(book, catalog):
    query = "这把光锥给真珠用怎么样，有什么要注意的？"
    base = build_query_plan(query, "assess_cone")
    plan = plan_guidance(query, base, book=book, catalog=catalog,
        consultation={"scope": "current_cone", "subject_state": "specified", "character": "真珠"})
    visual = cone_visual()
    return base, {"screen_facts": visual["screen_facts"],
                  "guidance_answer": build_guidance(plan, query, book=book, catalog=catalog, profile={}, visual=visual)}


@pytest.mark.parametrize("case", ["full", "mixed", "different_cone", "different_label", "ambiguous",
                                  "unconfirmed_fit", "different_subject", "oversized_condition", "formatter_cut"])
def test_summary_never_bypasses_completeness_object_or_body_checks(book, cone_catalog, case):
    plan, data = packet(book, cone_catalog)
    preview = bridge_preview
    if case == "full":
        plan = replace(plan, response_detail="full")
    elif case == "mixed":
        plan = plan.with_fields({"page", "equipment"})
    elif case == "different_cone":
        data["screen_facts"]["facts"][0]["entity_id"] = "light_cone.other"
    elif case == "different_label":
        data["screen_facts"]["facts"][0]["value"] = "另一把光锥"
    elif case == "ambiguous":
        data["screen_facts"]["facts"].append({"kind": "entity_label", "entity_type": "light_cone", "entity_id": "light_cone.other"})
    elif case == "unconfirmed_fit":
        data["guidance_answer"]["completion"]["evaluation_complete"] = False
    elif case == "different_subject":
        data["guidance_answer"]["completion"]["subject_ids"] = ["character.evanescia"]
    elif case == "oversized_condition":
        data["guidance_answer"]["assessment"]["evaluation"]["conditions"] = ["长条件不可截断。" * 200]
    else:
        preview = lambda text: text[:10]
    before = copy.deepcopy(data)
    assert summarize_cone_fit(data, plan=plan, preview=preview) is None
    assert data == before


def test_safe_diagnostic_retains_source_and_summary_sections_not_raw_content(book, cone_catalog):
    from plugin.plugins.hsr_companion.runtime.diagnostics import _delivery_summary
    plan, data = packet(book, cone_catalog)
    _, receipt = summarize_cone_fit(data, plan=plan, preview=bridge_preview)
    summary = _delivery_summary({"message_projection": receipt,
        "consultation_source": {"source": "saved_profile", "state": "restored_named_archive", "raw": "private-message"}})
    assert summary["consultation_source"] == {"source": "saved_profile", "state": "restored_named_archive"}
    assert summary["message_projection"]["mode"] == "cone_fit_summary"
    assert set(summary["message_projection"]["carried_sections"]) == set(receipt["carried_sections"])
    assert "private-message" not in json.dumps(summary)
