"""Reproduce 17:59 acceptance failure using declared structured evidence.

The boots readout is user-confirmed. The hands snapshot is synthetic; this
suite tests coordination/transport, not OCR accuracy or host generation.
"""
import asyncio
import copy
import hashlib
import importlib
import sys
import time
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card, equipment_object
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance
from plugin.plugins.hsr_companion.runtime.query_coordinator import answer_status, needs_followup
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as guidance_cases
from plugin.plugins.hsr_companion.tests.test_d1_handoff_closeout import received_text
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

QUERY = "这件给真珠用怎么样？"
OBSERVER = {"enabled": True, "state": "observing"}
book = guidance_cases.book
catalog = guidance_cases.catalog
plan = guidance_cases.plan


def label_only():
    # Reproduce receipt 95's empty equipment card plus the loose-topic label
    # path. Geometry/OCR and the exact historic label are NOT reconstructed.
    return {"fresh": True, "read_scope": "full", "page_type": "relic_detail",
            "observed_at": "2026-10-18T09:58:38.812880+00:00",
            "scene": {"verified": True, "label": "遗器详情", "primary_state": "menu"},
            "characters": [], "instruction": "结构化复现，不是实机采集",
            "screen_facts": {"observation_serial": 95, "visible_text": [], "facts": [
                {"scope": "entity", "kind": "entity_label", "entity_type": "relic_set",
                 "entity_id": "relic_set.133", "value": "闪耀功勋的魔法少女"}]}}


def hands():
    value = relic(main="攻击力", value="352", serial=96,
                  subs=[("防御力", "35"), ("速度", "2"), ("生命值", "3.4%"), ("效果命中", "7.7%")])
    for f in value["screen_facts"]["facts"]:
        if f["kind"] == "equipment_name":
            f["value"] = "测试手部装备"
        elif f["kind"] == "equipment_slot":
            f["value"] = "手部"
    return value


@pytest.mark.parametrize("query", [QUERY, "这件适合真珠吗", "这个遗器给真珠用怎么样？"])
def test_fit_demand_is_not_satisfied_by_a_set_label(book, catalog, query):
    demand = plan(query, book, catalog)
    observed = demand.observation_plan(build_query_plan(query))
    assert observed.required_equipment_kinds == {"main_stat", "sub_stat"}
    status = answer_status(observed, label_only(), OBSERVER)
    assert status["state"] != "ready" and status["missing_fields"] == ["equipment"]
    assert needs_followup(observed, label_only(), OBSERVER)


def test_unbound_label_is_not_current_equipment_identity(book, catalog):
    chosen = plan(QUERY, book, catalog)
    result = build_guidance(chosen, QUERY, book=book, catalog=catalog, profile={}, visual=label_only())
    assert not result["current_equipment_label_confirmed"]
    assert not result["current_equipment_ids"]
    assert result["assessment"]["state"] != "evidence_available"


def test_formal_entry_registers_followup_for_empty_card(tmp_path, monkeypatch):
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    owner, _, _ = plugin_fixture(tmp_path)
    current = label_only()
    owner._observation_serial = 95
    armed = []

    async def resolve(plugin, query_plan, **kwargs):
        return QueryObservation(ObservationSnapshot.select(plugin, current, fields=query_plan.fields),
                                {"outcome": "partial", "stages": []}, 0, kwargs["generation"])

    def arm(plugin, **kwargs):
        armed.append(kwargs)
        return {"pending": True, "reason": "test_registration_boundary"}

    module = importlib.import_module(type(owner).__module__)
    monkeypatch.setattr(module, "resolve_query", resolve)
    monkeypatch.setattr(module, "arm_followup", arm)
    # The actual audit had only equipment, supplied by the caller's hint.
    result = asyncio.run(owner.hsr_companion_prepare(query=QUERY, screen_fields=["equipment"]))
    assert len(armed) == 1 and result["followup"]["pending"]
    assert result["query_answer"]["state"] != "ready"
    assert result["equipment_answer"]["name"] is None
    assert result["equipment_answer"]["sub_stats"] == []
    assert not result["guidance_answer"]["assessment"]["evaluation_available"]


def test_registered_fit_completes_with_new_hands_not_old_label(book, catalog, monkeypatch):
    current = hands()
    owner, sent = followup_owner(monkeypatch, value=current)
    owner._guidance_book, owner._catalog = book, catalog
    demand = plan(QUERY, book, catalog)
    observed = demand.observation_plan(build_query_plan(QUERY))

    async def run():
        armed = followup.arm_followup(owner, query=QUERY, visual=label_only(), asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False,
            plan=observed, guidance_plan=demand, guidance_profile={})
        assert armed["pending"]
        await owner._followup_task

    asyncio.run(run())
    assert len(sent) == 1
    card = owner._test_answer_payloads[0]["equipment_answer"]
    assert card == equipment_card(current) and card["slot"] == "手部"
    assert "缔约长靴" not in received_text(sent[0])
    assert "测试手部装备" in received_text(sent[0])
    assert "同部位" in received_text(sent[0])


def test_frozen_installed_policy_reproduces_exact_ready_empty_card_contradiction(book, catalog):
    archive = Path(__file__).resolve().parents[1] / "dist/hsr_companion-0.9.6-demo-d1-handoff-20261018.1.neko-plugin"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == "48d45823de53d094e7decb1efbf67d0d65da619a8fd132d5dccc840b26048a08"
    prefix = "payload/plugins/hsr_companion/runtime/"
    loaded = []
    try:
        with zipfile.ZipFile(archive) as package:
            for name in ("consultation", "guidance"):
                module_name = f"plugin.plugins.hsr_companion.runtime._historical_fit_{name}"
                module = types.ModuleType(module_name)
                module.__package__ = "plugin.plugins.hsr_companion.runtime"
                sys.modules[module_name] = module
                loaded.append(module)
                exec(compile(package.read(prefix + name + ".py"), "frozen/" + name + ".py", "exec"), module.__dict__)
        old = loaded[1]
        old.consultation_fields = loaded[0].consultation_fields
        base = build_query_plan(QUERY, screen_fields=["equipment"])
        prior = old.plan_guidance(QUERY, base, book=book, catalog=catalog, explicit_fields=True)
        old_plan = prior.observation_plan(base)
        assert not old_plan.required_equipment_kinds
        assert equipment_card(label_only())["state"] == "unconfirmed"
        assert answer_status(old_plan, label_only(), OBSERVER)["state"] == "ready"
        assert not needs_followup(old_plan, label_only(), OBSERVER)
        new = plan(QUERY, book, catalog).observation_plan(base)
        assert answer_status(new, label_only(), OBSERVER)["state"] != "ready"
        assert needs_followup(new, label_only(), OBSERVER)
    finally:
        for module in loaded:
            sys.modules.pop(module.__name__, None)


@pytest.fixture
def reviewed_trace(catalog, monkeypatch):
    original = catalog.get
    trace = {"id": "character_trace.1503101", "name": "洞察万物", "entity_type": "character_trace",
        "owner_ids": ["character.pearl"], "params": [[2400, .32, 100, .03, 3600, .2]],
        "sources": [{"source_id": "starrailres_structured_cn", "revision": "dbe8cdfcfb0bf657b9fe1cf92d5537f118ccb487"}]}
    monkeypatch.setattr(catalog, "get", lambda entity_id: copy.deepcopy(trace) if entity_id == trace["id"] else original(entity_id))
    return trace


def test_defense_advice_uses_reviewed_mechanic_not_generic_disclaimer(book, catalog, reviewed_trace):
    query = "真珠防御力要多少合适？"
    result = build_guidance(plan(query, book, catalog), query, book=book, catalog=catalog, profile={})
    reply = result["reply_plan"]
    rule = reply["stat_advice"]
    assert rule["available"] and rule["activation_value"] == 2400
    assert rule["cap_value"] == 6000 and rule["max_self_elation_percent"] == 140
    assert rule["recommended_target"] is None and not rule["graduation_threshold_available"]
    assert "上限" in reply["conclusion"] and "毕业" in reply["conclusion"]
    assert "位面球" in reply["next_step"] and "防御力" in reply["next_step"]
    assert rule["sources"] and not plan(query, book, catalog).requires_pixels


def test_player_number_is_conditional_not_a_new_visual_or_graduation(book, catalog, reviewed_trace):
    query = "真珠防御力要多少合适？"
    first = build_guidance(plan(query, book, catalog), query, book=book, catalog=catalog, profile={})
    selected = plan("3132", book, catalog, previous=first["context"])
    result = build_guidance(selected, "3132", book=book, catalog=catalog, profile={})
    reply = result["reply_plan"]
    assert reply["stat_advice"]["comparison"]["assumes_trace_active_and_same_basis"]
    assert reply["stat_advice"]["comparison"]["relation"] == "above_activation_below_cap"
    assert not reply["evidence_rows"] and reply["stated_stat"]["origin"] == "player_statement"
    assert not reply["numerical_grade_available"] and not selected.requires_pixels


@pytest.mark.parametrize("broken", ["owner", "revision", "changed_params", "missing_params", "bad_shape", "nan", "wrong_id"])
def test_unreviewed_trace_never_supplies_numeric_target(book, catalog, monkeypatch, reviewed_trace, broken):
    from plugin.plugins.hsr_companion.runtime.mechanics import reviewed_stat_advice
    trace = copy.deepcopy(reviewed_trace)
    if broken == "owner":
        trace["owner_ids"] = ["character.evanescia"]
    elif broken == "revision":
        trace["sources"][0]["revision"] = "future_unreviewed"
    elif broken == "changed_params":
        trace["params"][0][0] = 2200
    elif broken == "missing_params":
        trace["params"] = []
    elif broken == "bad_shape":
        trace["params"] = {"0": []}
    elif broken == "nan":
        trace["params"][0][0] = "NaN"
    elif broken == "wrong_id":
        trace["id"] = "character_trace.other"
    original = catalog.get
    monkeypatch.setattr(catalog, "get", lambda key: trace if key == "character_trace.1503101" else original(key))
    role = next(r for r in book.characters if r["name"] == "真珠")
    result = reviewed_stat_advice(role, "防御力", catalog=catalog, stated_value="3132")
    assert not result["available"] and result["recommended_target"] is None
    assert result["comparison"] is None and "cap_value" not in result


@pytest.mark.parametrize("value,relation", [("2000", "below_activation"), ("2400", "above_activation_below_cap"),
    ("3132", "above_activation_below_cap"), ("6000", "at_or_above_cap"), ("7000", "at_or_above_cap")])
def test_valid_mechanic_comparison_is_conditional(book, catalog, reviewed_trace, value, relation):
    from plugin.plugins.hsr_companion.runtime.mechanics import reviewed_stat_advice
    role = next(r for r in book.characters if r["name"] == "真珠")
    result = reviewed_stat_advice(role, "防御力", catalog=catalog, stated_value=value)
    assert result["comparison"]["relation"] == relation
    assert result["comparison"]["assumes_trace_active_and_same_basis"]
    assert not result["comparison"]["proves_graduation"] and result["recommended_target"] is None


def test_units_other_roles_and_missing_pack_do_not_inherit_pearl_threshold(book, catalog, reviewed_trace):
    from plugin.plugins.hsr_companion.runtime.mechanics import reviewed_stat_advice
    for name in ("黄泉", "绯英"):
        role = next(r for r in book.characters if r["name"] == name)
        assert not reviewed_stat_advice(role, "防御力", catalog=catalog)["available"]
    role = next(r for r in book.characters if r["name"] == "真珠")
    assert reviewed_stat_advice(role, "防御力", catalog=catalog, stated_value="3132%")["comparison"] is None
    assert not reviewed_stat_advice(role, "防御力", catalog=SimpleNamespace(get=lambda key: None))["available"]


def test_missing_numeric_row_preserves_new_identity_but_shared_pending_does_not():
    current = hands()
    current["screen_facts"]["facts"] = [f for f in current["screen_facts"]["facts"] if f.get("kind") != "sub_stat"]
    current["screen_facts"]["field_checks"] = [{"scope": "equipment", "kind": "sub_stat", "state": "pending"}]
    assert equipment_object(current)["confirmed"]
    current["screen_facts"]["field_checks"].append({"scope": "equipment", "kind": "equipment_name", "state": "pending"})
    assert not equipment_object(current)["confirmed"] and equipment_object(current)["name"] is None


@pytest.mark.parametrize("query", ["这个光锥适合真珠吗", "这套遗器套装适合真珠吗"])
def test_cone_and_set_reference_questions_do_not_require_relic_numeric_rows(book, catalog, query):
    assert not plan(query, book, catalog).equipment_kinds


def test_real_mixed_identity_request_is_not_removed(book, catalog):
    query = "这个遗器给真珠怎么样，右边角色都有谁？"
    observed = plan(query, book, catalog).observation_plan(build_query_plan(query))
    assert observed.fields >= {"equipment", "characters"}


def test_partial_fit_uses_only_new_rows_and_same_slot_comparison(book, catalog):
    current = hands()
    current["screen_facts"]["facts"] = [f for f in current["screen_facts"]["facts"]
        if f.get("kind") != "sub_stat" or f.get("value") in {"35", "2"}]
    reply = build_guidance(plan(QUERY, book, catalog), QUERY, book=book, catalog=catalog, profile={}, visual=current)["reply_plan"]
    assert reply["current_object"]["name"] == "测试手部装备" and not reply["rows_complete"]
    assert reply["fixed_main_stat"] and "固定" in reply["conclusion"]
    assert "同部位" in reply["next_step"] and "位面球" not in reply["next_step"]
    assert len(reply["evidence_rows"]) <= 2


def test_formal_prepare_then_real_registration_then_new_item_delivery(tmp_path, monkeypatch):
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
    from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
    from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
    owner, values, sent = plugin_fixture(tmp_path)
    owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: [])))
    owner._target_character = lambda: "isolated-test-cat-no-host-message"
    current = label_only()
    owner._observation_serial = 95
    switched = None
    packets = []
    original = followup.record_answer_delivery

    def record(*args, **kwargs):
        packets.append(copy.deepcopy(kwargs["payload"]))
        return original(*args, **kwargs)

    async def resolve(plugin, query_plan, **kwargs):
        return QueryObservation(ObservationSnapshot.select(plugin, current, fields=query_plan.fields),
                                {"outcome": "partial" if current["screen_facts"]["observation_serial"] == 95 else "reused"},
                                0, kwargs["generation"])

    async def read():
        await switched.wait()
        return copy.deepcopy(current)

    async def available(*args, **kwargs): return "available", None
    async def validated(plugin, value, **kwargs): return value
    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)
    monkeypatch.setattr(followup, "record_answer_delivery", record)
    monkeypatch.setattr(followup, "latest_user_activity", available)
    monkeypatch.setattr(followup, "validated_delivery", validated)
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    owner._current_visual_evidence = read

    async def run():
        nonlocal current, switched
        switched = asyncio.Event()
        before = copy.deepcopy(values["player_context"])
        result = await owner.hsr_companion_prepare(query=QUERY, screen_fields=["equipment"])
        assert result["followup"]["pending"] and result["query_answer"]["state"] == "unconfirmed"
        assert not result["guidance_answer"]["reply_plan"]["current_object"]["confirmed"]
        assert result["equipment_answer"]["name"] is None
        current = hands()
        owner._observation_serial = 96
        switched.set()
        await asyncio.wait_for(owner._followup_task, 10)
        assert owner._followup_status["state"] == "submitted" and len(sent) == 1
        assert packets[0]["equipment_answer"] == equipment_card(current)
        assert len(packets[0]["equipment_answer"]["sub_stats"]) == 4
        text = received_text(sent[0])
        assert "测试手部装备" in text and "同部位" in text and "缔约长靴" not in text
        full = await owner.hsr_companion_prepare(query="完整报出这件遗器主词条和四项副词条，分别是多少？")
        assert not full["followup"]["pending"] and full["equipment_answer"] == equipment_card(current)
        assert len(full["guidance_answer"]["reply_plan"]["evidence_rows"]) == 5
        assert full["delivery_receipt"]["equipment_consistency"] == "matches"
        assert values["player_context"] == before
    asyncio.run(run())
