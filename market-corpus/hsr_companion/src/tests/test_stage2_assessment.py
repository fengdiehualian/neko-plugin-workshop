"""Stage two: snapshot fidelity, sourced fit rules and real service handoffs.

Synthetic OCR is deliberately not a live recognition or host speech receipt.
"""
from __future__ import annotations

import copy
import json

import pytest
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests.test_stage1_repairs import fact, visual

book = cases.book
catalog = cases.catalog


def relic(*, main="物理属性伤害提高", value="38.8%", serial=96, subs=None):
    rows = subs if subs is not None else [("暴击率", "8.7%"), ("暴击伤害", "11.6%"), ("防御力", "15.1%"), ("生命值", "80")]
    result = visual(fact("equipment_name", "遗器名称（画面文字）", "虚构构造的驱动器"),
                    fact("equipment_slot", "遗器部位", "位面球"),
                    fact("equipment_level", "遗器强化等级", "+15"),
                    fact("main_stat", "主词条·" + main, value),
                    *[fact("sub_stat", "副词条·" + key, val) for key, val in rows])
    result.update(observed_at="2026-10-17T04:06:54+00:00", controlled_character=None,
                  status="ready", instruction="合成字段，不证明角色归属")
    result["scene"].update(label="遗器详情", primary_state="menu")
    result["screen_facts"]["observation_serial"] = serial
    return result


def assess(query, current, book, catalog, previous=None):
    demand = cases.plan(query, book, catalog, previous=previous)
    return build_guidance(demand, query, book=book, catalog=catalog, profile={}, visual=current)


def test_role_clarification_preserves_equipment_demand(book, catalog):
    prior = assess("这个遗器词条怎么样", relic(), book, catalog)["context"]
    assert prior["awaiting_subject"]
    demand = cases.plan("绯英", book, catalog, previous=prior)
    assert demand.request_focus == "equipment"
    assert demand.requires_pixels
    assert demand.equipment_kinds == {"main_stat", "sub_stat"}
    answered = build_guidance(demand, "绯英", book=book, catalog=catalog, profile={}, visual=relic())
    follow = cases.plan("这个呢", book, catalog, previous=answered["context"])
    assert follow.character_ids == ("character.evanescia",)
    assert follow.observation_plan(build_query_plan("这个呢")).required_equipment_kinds == {"main_stat", "sub_stat"}


def test_exact_item_card_is_same_for_panel_grounding_and_followup(catalog):
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
    from plugin.plugins.hsr_companion.runtime.followup import AnswerRequest, reply_data
    from plugin.plugins.hsr_companion.runtime.grounding import build_grounding
    from plugin.plugins.hsr_companion.runtime.screen_facts import public_screen_facts

    current = relic()
    card = equipment_card(current)
    panel = public_screen_facts(current["screen_facts"], fresh=True, observed_at=current["observed_at"], serial=96)
    plan = build_query_plan("这个遗器词条怎么样")
    grounded = build_grounding(plan.query, current, catalog, plan=plan)
    request = AnswerRequest("test", plan.query, "cat", 0, 0, 100, "relic_detail", False, False, True, False, plan)
    follow = reply_data(current, request)
    assert card == panel["equipment_card"] == grounded["equipment_answer"] == follow["equipment_answer"]
    assert card["main_stat"]["value"] == "38.8%"
    assert [(row["label"], row["value"]) for row in card["sub_stats"]] == [
        ("暴击率", "8.7%"), ("暴击伤害", "11.6%"), ("防御力", "15.1%"), ("生命值", "80")]
    assert "防御力15.1%" in card["exact_text"]
    assert "速度" not in card["exact_text"]


def test_cards_are_frame_local_and_revision_bound():
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
    first = equipment_card(relic())
    second = equipment_card(relic(main="防御力", value="54.0%", serial=97, subs=[("速度", "2")]))
    assert second["snapshot_id"] != first["snapshot_id"]
    assert second["main_stat"]["property"] == "防御力百分比"
    assert len(second["sub_stats"]) == 1 and second["state"] == "partial"
    assert "暴击率" not in second["exact_text"]
    stale = relic()
    stale["fresh"] = False
    assert not equipment_card(stale)["sub_stats"]


def test_sourced_fit_is_not_roll_score_or_account_optimum(book, catalog):
    result = assess("这件遗器词条适合绯英吗", relic(), book, catalog)
    score = result["assessment"]
    assert score["evaluation_available"]
    assert score["evaluation"]["main_stat"]["verdict"] == "matches_reviewed_candidate"
    assert [s["verdict"] for s in score["evaluation"]["sub_stats"]] == [
        "priority", "priority", "not_listed", "not_listed"]
    assert score["evaluation"]["sources"]
    assert not score["evaluation"]["quality_score_available"]
    assert not score["evaluation"]["replacement_decided"]
    assert score["equipment_answer"]["main_stat"]["value"] == "38.8%"


def test_unsuitable_main_and_incomplete_substats_are_not_strong_item(book, catalog):
    result = assess("这个遗器适合真珠吗", relic(subs=[("暴击率", "8.7%")]), book, catalog)["assessment"]
    assert result["evaluation"]["main_stat"]["verdict"] == "outside_reviewed_candidates"
    assert not result["evaluation"]["complete"]
    assert not result["evaluation"]["replacement_decided"]


def test_conflicting_rows_are_not_evaluated_or_filled(book, catalog):
    current = relic()
    current["screen_facts"]["facts"].append(fact("main_stat", "主词条·防御力", "54.0%"))
    result = assess("这个遗器适合真珠吗", current, book, catalog)["assessment"]
    assert result["equipment_answer"]["state"] == "conflict"
    assert not result["evaluation_available"]


def test_panel_analysis_has_actual_values_without_graduation_threshold(book, catalog):
    current = visual(fact("stat", "防御力", "3456"), fact("stat", "速度", "134"), fact("stat", "暴击率", "5.0%"))
    for row in current["screen_facts"]["facts"]:
        row["scope"] = "attributes"
    current["page_type"] = "character_attributes"
    # This positive assessment fixture must bind its panel owner explicitly.
    # Unknown/mismatched owners now have separate negative contract cases.
    current["characters"] = [{"entity_id": "character.pearl", "name": "真珠", "identity_scope": "detail_subject"}]
    result = assess("真珠的面板怎么样", current, book, catalog)["assessment"]
    assert result["evaluation_available"]
    review = result["evaluation"]
    assert review["kind"] == "attributes"
    assert any(row["label"] == "防御力" and row["value"] == "3456" for row in review["rows"])
    assert "能量恢复效率" in review["missing_priorities"]
    assert not review["graduation_threshold_available"]
    assert not review["complete"]


def test_unreviewed_role_and_missing_pixels_do_not_get_generic_evaluation(book, catalog):
    assert not assess("这个遗器适合卡芙卡吗", relic(), book, catalog)["assessment"]["evaluation_available"]
    current = relic()
    current["fresh"] = False
    assert not assess("这个遗器适合真珠吗", current, book, catalog)["assessment"]["evaluation_available"]


def test_audit_digest_contains_no_item_names_values_or_chat():
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card, handoff_receipt
    card = equipment_card(relic())
    value = handoff_receipt(card, channel="direct")
    text = json.dumps(value, ensure_ascii=False)
    assert card["content_digest"] in text
    assert "虚构" not in text and "38.8" not in text and "暴击" not in text
    assert value["sub_stat_count"] == 4
    changed = copy.deepcopy(card)
    changed["state"] = "unconfirmed"
    assert handoff_receipt(changed, channel="followup")["channel"] == "followup"


@pytest.mark.parametrize("part,main,value,verdict", [
    ("头部", "生命值", "705", "matches_reviewed_candidate"),
    ("手部", "攻击力", "352", "matches_reviewed_candidate"),
    ("躯干", "治疗量加成", "34.5%", "matches_reviewed_candidate"),
    ("脚部", "速度", "25", "matches_reviewed_candidate"),
    ("位面球", "物理属性伤害提高", "38.8%", "outside_reviewed_candidates"),
    ("连结绳", "能量恢复效率", "19.4%", "matches_reviewed_candidate"),
])
def test_slot_rules_are_not_special_cased_to_live_item(book, catalog, part, main, value, verdict):
    current = relic(main=main, value=value, subs=[("效果抵抗", "8.6%")])
    next(f for f in current["screen_facts"]["facts"] if f["kind"] == "equipment_slot")["value"] = part
    result = assess("这个遗器适合真珠吗", current, book, catalog)["assessment"]
    assert result["evaluation"]["main_stat"]["verdict"] == verdict
    assert result["evaluation"]["main_stat"]["value"] == value


def test_pending_row_quarantine_rebuilds_card_and_never_leaks_old_main():
    from plugin.plugins.hsr_companion.runtime.delivery_scope import withhold_regions
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
    current = relic()
    box = {"left": .1, "top": .2, "right": .3, "bottom": .4}
    current["screen_facts"]["facts"][3]["evidence"] = [{"region": box}]
    current["screen_facts"]["equipment_card"] = equipment_card(current)
    result = withhold_regions(current, {(.1, .2, .3, .4)})
    assert result["screen_facts"]["equipment_card"]["main_stat"] is None
    assert "38.8%" not in result["screen_facts"]["equipment_card"]["exact_text"]


def test_known_max_level_missing_rows_does_not_finish_stat_followup():
    from plugin.plugins.hsr_companion.runtime.query_coordinator import satisfied_fields
    query = build_query_plan("这件遗器的主副词条是什么")
    assert "equipment" not in satisfied_fields(query, relic(subs=[("速度", "2")]))
    assert "equipment" in satisfied_fields(query, relic())


def test_same_frame_control_proof_survives_public_text_truncation(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
    from plugin.plugins.hsr_companion.detectors.layout import scene_control_regions
    from plugin.plugins.hsr_companion.runtime.live_refresh import revalidate_guard_text
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame, picture

    controls = scene_control_regions({"page_type": "relic_detail"}, [
        {"text": "强化", "score": .94, "left": 125, "top": 61, "right": 189, "bottom": 89},
    ], width=1920, height=1080)
    owner, _, _ = plugin_fixture(tmp_path)
    owner._committed_observation = {"screen_facts": {"facts": [], "visible_text": []}}
    owner._vision = SimpleNamespace(verify_text_regions=lambda payload, rows, **kw: {proof_key(p) for p in rows})
    keys = {proof_key(p)[0] for p in controls}
    result = asyncio.run(revalidate_guard_text(owner, frame(picture()), keys, controls=controls))
    assert result.state == "confirmed"


def test_unproven_control_stays_unknown_and_cannot_bypass_literal_guard(tmp_path):
    import asyncio
    from types import SimpleNamespace

    from plugin.plugins.hsr_companion.runtime.live_refresh import revalidate_guard_text
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame, picture

    owner, _, _ = plugin_fixture(tmp_path)
    owner._committed_observation = {"screen_facts": {"facts": [], "visible_text": []}}
    def forbidden(*args, **kwargs):
        raise AssertionError("Missing literal must never become a success")
    owner._vision = SimpleNamespace(verify_text_regions=forbidden)
    result = asyncio.run(revalidate_guard_text(owner, frame(picture()), {(.1, .2, .3, .4)}))
    assert result.state == "missing_proof"


def test_direct_entry_returns_same_card_as_evaluation_and_no_profile_write(tmp_path, monkeypatch):
    import asyncio
    import importlib

    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture

    owner, values, _ = plugin_fixture(tmp_path)
    profile = copy.deepcopy(values["player_context"])
    current = relic()
    owner._observation_serial = 96
    async def resolve(plugin, plan, **kwargs):
        return QueryObservation(ObservationSnapshot.select(plugin, current, fields=plan.fields),
                                {"outcome": "reused", "stages": []}, 0, kwargs["generation"])
    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)

    async def run():
        first = await owner.hsr_companion_prepare(query="这个遗器词条怎么样")
        clarified = await owner.hsr_companion_prepare(query="绯英")
        assert clarified["equipment_answer"] == first["equipment_answer"]
        assert clarified["equipment_answer"] == clarified["guidance_answer"]["assessment"]["equipment_answer"]
        assert clarified["equipment_answer"] == clarified["screen_context_answer"]["equipment_answer"]
        assert clarified["guidance_answer"]["assessment"]["evaluation_available"]
        assert owner._equipment_handoffs[-1]["content_digest"] == clarified["equipment_answer"]["content_digest"]
        assert values["player_context"] == profile
    asyncio.run(run())


def test_diagnostic_separates_direct_timeout_and_submitted_equipment_followup():
    from plugin.plugins.hsr_companion.runtime.diagnostics import build_diagnostic_report
    from plugin.plugins.hsr_companion.runtime.equipment import equipment_card, handoff_receipt
    from plugin.plugins.hsr_companion.tests.test_diagnostics import inputs
    params = inputs()
    params["last_tool"] = {"refresh_diagnostics": {"outcome": "timeout"}}
    receipts = [handoff_receipt(equipment_card(relic()), channel="followup")]
    result = json.loads(build_diagnostic_report(**params, equipment_handoffs=receipts,
                        followup_status={"state": "submitted", "outcome": "ready"})["copy_text"])
    assert result["last_query"]["outcome"] == "timeout"
    assert result["explicit_followup"]["outcome"] == "ready"
    assert result["equipment_handoffs"][0]["sub_stat_count"] == 4
    assert "38.8" not in json.dumps(result)


@pytest.mark.parametrize("cone_id,path,available,verdict", [
    ("light_cone.22007", "Elation", True, "conditional_candidate"),
    ("light_cone.23055", "Elation", True, "signature_reference"),
    ("light_cone.unreviewed", "Elation", True, "not_in_reviewed_candidates"),
    ("light_cone.22007", "Nihility", False, None),
])
def test_cone_assessment_requires_unique_sourced_path_and_keeps_conditions(book, catalog, monkeypatch, cone_id, path, available, verdict):
    original = catalog.get
    monkeypatch.setattr(catalog, "get", lambda key: {"id": cone_id, "name": "合成光锥", "path": path,
                         "sources": [{"source_id": "fixture"}]} if key == cone_id else original(key))
    current = visual({"kind": "entity_label", "scope": "equipment", "label": "光锥", "value": "合成光锥",
                      "entity_type": "light_cone", "entity_id": cone_id})
    result = assess("这个光锥适合真珠吗", current, book, catalog)["assessment"]
    assert result["evaluation_available"] is available
    if available:
        assert result["evaluation"]["verdict"] == verdict
        assert result["evaluation"]["conditions"]
        assert not result["evaluation"]["replacement_decided"]


def test_ready_followup_payload_preserves_card_and_sourced_evaluation(book, catalog, monkeypatch):
    import asyncio
    import time

    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner

    current = relic()
    owner, sent = followup_owner(monkeypatch, value=current)
    owner._guidance_book, owner._catalog = book, catalog
    query = "这个遗器词条适合绯英吗"
    demand = cases.plan(query, book, catalog)
    plan = demand.observation_plan(build_query_plan(query))
    async def run():
        followup.arm_followup(owner, query=query, visual={"fresh": False}, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False,
            plan=plan, guidance_plan=demand, guidance_profile={})
        await owner._followup_task
    asyncio.run(run())
    assert len(sent) == 1
    text = sent[0]["parts"][0]["text"]
    data = owner._test_answer_payloads[-1]
    assert data["equipment_answer"] == data["guidance_answer"]["assessment"]["equipment_answer"]
    assert data["guidance_answer"]["assessment"]["evaluation_available"]
    assert owner._last_equipment_handoff["content_digest"] == data["equipment_answer"]["content_digest"]
    assert owner._followup_status["state"] == "submitted"
    from utils.result_parser import parse_push_message_content
    assert data["equipment_answer"]["name"] in parse_push_message_content(text, lang="zh")


def test_busy_control_followup_recovers_same_commit_after_verifier_frees(tmp_path, monkeypatch):
    import asyncio
    import time
    from io import BytesIO
    from types import SimpleNamespace

    from PIL import Image, ImageDraw
    from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL, frame, picture
    from plugin.plugins.hsr_companion.tests.test_delivery_races import complete_commit as commit
    from plugin.plugins.hsr_companion.tests.test_page_followup import png

    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    monkeypatch.setattr(followup, "latest_user_activity", lambda *a, **k: asyncio.sleep(0, result=("available", None)))
    owner, _, sent = plugin_fixture(tmp_path)
    owner._target_character = lambda: "test_cat"
    monkeypatch.setattr(type(owner), "bus", property(lambda self: SimpleNamespace(memory=SimpleNamespace(get=lambda *a, **k: None))))
    with Image.open(BytesIO(picture())) as opened:
        changed = opened.copy()
    ImageDraw.Draw(changed).rectangle((10, 8, 12, 10), fill="white")
    payload = png(changed)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
    owner._vision = SimpleNamespace(verify_text_regions=lambda data, rows, **kw: {proof_key(p) for p in rows})

    async def run():
        await commit(owner, picture(), page_control={**CONTROL, "text": "遗器详情", "control": True})
        serial = owner._observation_serial
        owner._accepted_frame_page_regions = [{**CONTROL, "text": "遗器详情", "control": True}]
        coords = {tuple(CONTROL["region"][k] for k in ("left", "top", "right", "bottom"))}
        ObservationStore(owner).defer_regions(coords)
        assert not (await owner._current_visual_evidence())["fresh"]
        plan = build_query_plan("这件遗器主副词条是什么")
        armed = followup.arm_followup(owner, query=plan.query, visual={"fresh": False}, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False, plan=plan)
        assert armed["pending"]
        await asyncio.wait_for(owner._followup_task, timeout=3)
        assert owner._observation_serial == serial  # No second recognition/commit.
        assert owner._followup_status["outcome"] == "ready"
        assert len([s for s in sent if s.get("metadata", {}).get("kind") == "query_followup"]) == 1
        # Scoped recovery does not bless every unrelated field on the page.
        assert owner._observation_pending_reason == "verification_pending_control"
    asyncio.run(run())
