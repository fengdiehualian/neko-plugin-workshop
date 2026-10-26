"""Demo admission contracts, not a real OCR or host speech receipt."""
import copy
import json
from types import SimpleNamespace

import pytest

from plugin.plugins.hsr_companion.runtime import service_contract as contract
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic, assess
from plugin.plugins.hsr_companion.tests.test_stage1_repairs import fact, visual
from plugin.plugins.hsr_companion.tests.test_guidance import book, catalog, answer, plan


@pytest.mark.parametrize("channel", ["direct", "followup"])
def test_receipt_audits_sent_rows_not_recomputed_visual(channel):
    current = relic(subs=[("攻击力", "19")])
    selected = equipment_card(current)
    sent = equipment_card(relic(subs=[("攻击力", "23")]))
    owner = SimpleNamespace()
    receipt = contract.record_answer_delivery(owner, generation=2, channel=channel,
        payload={"equipment_answer": sent}, visual=current,
        answer={"state": "partial", "confirmed_fields": [], "missing_fields": ["equipment"]})
    assert receipt["equipment"]["content_digest"] == sent["content_digest"]
    assert receipt["equipment"]["content_digest"] != selected["content_digest"]
    assert receipt["equipment_consistency"] == "mismatch"
    assert not hasattr(owner, "_delivery_value_trace")


def test_trace_opt_in_expiry_copies_exact_packet_and_never_retains_question(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(contract.time, "monotonic", lambda: clock[0])
    owner = SimpleNamespace()
    contract.configure_delivery_trace(owner, seconds=10)
    card = equipment_card(relic(subs=[("攻击力", "19")]))
    for generation in range(10):
        contract.record_answer_delivery(owner, generation=generation, channel="direct",
            payload={"equipment_answer": card, "query": "PRIVATE_QUESTION"}, visual=relic(subs=[("攻击力", "19")]),
            answer={"state": "partial", "confirmed_fields": [], "missing_fields": ["equipment"]})
    card["sub_stats"][0]["value"] = "23"
    trace = contract.delivery_value_trace(owner)
    assert len(trace["entries"]) == 8
    assert trace["entries"][-1]["sent_equipment"]["sub_stats"][0]["value"] == "19"
    assert "PRIVATE_QUESTION" not in json.dumps(trace)
    clock[0] = 111
    assert contract.delivery_value_trace(owner)["entries"] == []
    assert not owner._delivery_value_trace


def test_stale_declared_digest_cannot_hide_changed_sent_value():
    owner = SimpleNamespace()
    current = relic(subs=[("攻击力", "19")])
    card = equipment_card(current)
    card["sub_stats"][0]["value"] = "23"  # stale digest is deliberately unchanged
    receipt = contract.record_answer_delivery(owner, generation=1, channel="direct",
        payload={"equipment_answer": card}, visual=current,
        answer={"state": "partial", "confirmed_fields": [], "missing_fields": ["equipment"]})
    assert receipt["equipment_consistency"] == "mismatch"
    assert receipt["equipment_digest_valid"] is False


def test_wrong_rendered_text_is_not_hidden_by_correct_structured_rows():
    owner = SimpleNamespace()
    current = relic(subs=[("攻击力", "19")])
    card = equipment_card(current)
    card["exact_text"] = card["exact_text"].replace("攻击力19", "攻击力23")
    receipt = contract.record_answer_delivery(owner, generation=1, channel="followup",
        payload={"equipment_answer": card}, visual=current,
        answer={"state": "partial", "confirmed_fields": [], "missing_fields": ["equipment"]})
    assert receipt["equipment_digest_valid"] is True
    assert receipt["equipment_text_valid"] is False
    assert receipt["equipment_consistency"] == "mismatch"


def test_detailed_trace_is_never_in_default_diagnosis_or_export(tmp_path):
    import asyncio
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.sdk.plugin import unwrap
    owner, _, sent = plugin_fixture(tmp_path)
    owner._avatars = SimpleNamespace(status=lambda: {"installed": True})
    owner._message_path_ready = lambda: {"available": True}

    async def health():
        return {"connected": False, "installed": True}

    owner._assistant_status = health
    asyncio.run(owner.diagnose_companion(delivery_trace_seconds=120))
    current = relic(subs=[("攻击力", "19")])
    contract.record_answer_delivery(owner, generation=1, channel="direct",
        payload={"equipment_answer": equipment_card(current)}, visual=current,
        answer={"state": "partial", "confirmed_fields": [], "missing_fields": ["equipment"]})
    report = unwrap(asyncio.run(owner.diagnose_companion()))["diagnostic"]
    assert "delivery_value_trace" not in report
    assert "攻击力" not in json.dumps(report, ensure_ascii=False)
    detail = unwrap(asyncio.run(owner.diagnose_companion(include_delivery_trace=True)))["diagnostic"]
    assert detail["delivery_value_trace"]["entries"][-1]["sent_equipment"]["sub_stats"][0]["value"] == "19"
    assert "攻击力" not in detail["copy_text"]
    assert not sent


def test_three_roles_have_sourced_actionable_panel_explanations(book, catalog):
    for name in ("真珠", "绯英", "黄泉"):
        current = visual(fact("stat", "攻击力", "3123"), fact("stat", "防御力", "3567"),
                         fact("stat", "暴击率", "54.2%"), fact("stat", "暴击伤害", "181.4%"))
        for row in current["screen_facts"]["facts"]:
            row["scope"] = "attributes"
        result = assess(f"{name}的面板怎么样", current, book, catalog)
        evaluation = result["assessment"]["evaluation"]
        assert evaluation["available"]
        assert evaluation["focus_checks"]
        assert evaluation["next_steps"]
        assert all(c["meaning"] and c["check"] and c["value"] for c in evaluation["focus_checks"])
        assert not evaluation["graduation_threshold_available"]
        assert not evaluation["quality_score_available"]
        assert evaluation["sources"]


def test_relic_suggestion_is_item_local_partial_allowed_and_no_invented_score(book, catalog):
    current = relic(main="防御力", value="54.0%", subs=[("攻击力", "19"), ("暴击率", "5.8%")])
    result = assess("这件遗器适合绯英吗", current, book, catalog)["assessment"]["evaluation"]
    assert result["main_stat"]["verdict"] == "outside_reviewed_candidates"
    assert result["next_steps"][0]["kind"] == "main_stat_review"
    assert result["sub_stats"][0]["value"] == "19"
    assert result["sub_stats"][0]["meaning"]
    assert not result["complete"]
    assert not result["replacement_decided"]


def test_old_role_build_does_not_join_current_banner_list(book, catalog):
    result = answer("黄泉遗器光锥怎么配", book, catalog)
    assert result["guides"][0]["identity_id"] == "character.acheron"
    assert result["guides"][0]["banner_status"] == "not_applicable"
    assert "黄泉" not in [g["name"] for g in answer("本期角色怎么配装", book, catalog)["guides"]]
    assert not plan("黄泉怎么配装", book, catalog).requires_pixels


def test_natural_cultivation_question_uses_panel_demand(book, catalog):
    selected = plan("这个角色养得怎么样", book, catalog, discussion_character="绯英")
    assert selected.request_focus == "attributes"
    assert selected.requires_pixels
    assert selected.character_ids == ("character.evanescia",)


def test_stat_notes_schema_is_validated(book):
    from plugin.plugins.hsr_companion.runtime.guidance import GuidanceCatalog
    data = copy.deepcopy(book.data)
    data["characters"][0]["stat_notes"] = {"防御力": {"meaning": "", "check": "check"}}
    with pytest.raises(ValueError, match="stat notes"):
        GuidanceCatalog(data)
