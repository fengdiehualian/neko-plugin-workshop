"""Frozen D1 service repair. Synthetic facts are not live OCR/model acceptance.

Keep the actual wrong build_guide call, separate omitted/unresolved subjects,
and exercise the formal entry plus the actual notification receiving boundary.
No real observer, player archive, host message or installation is touched.
"""
import asyncio
import copy
import importlib
import time
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
from plugin.plugins.hsr_companion.runtime.query_coordinator import QueryObservation
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as guidance_cases
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_d1_handoff_closeout import delivered, received_text
from plugin.plugins.hsr_companion.tests.test_stage2_assessment import relic

book = guidance_cases.book
catalog = guidance_cases.catalog

HISTORIC_QUERY = "这个遗器适合绯樱吗"


def declaration(state="specified", character="绯英", scope="current_relic"):
    return {"scope": scope, "subject_state": state,
            **({"character": character} if state == "specified" else {})}


def prior(character="character.pearl", age=0):
    return {"at": time.monotonic() - age, "kind": "build", "character_ids": [character],
            "request_focus": "equipment", "equipment_kinds": ["main_stat", "sub_stat"],
            "consultation_action": "assess", "execution_intent": "assess_relic"}


def choose(query, book, catalog, *, intent="assess_relic", **kwargs):
    return plan_guidance(query, build_query_plan(query, intent), book=book, catalog=catalog, **kwargs)


def item(*, hand=False, serial=22):
    current = relic(main="攻击力" if hand else "生命值", value="352" if hand else "705", serial=serial,
                    subs=[("效果命中", "3.4%"), ("效果抵抗", "3.8%"), ("击破特攻", "18.1%"), ("暴击伤害", "18.1%")]
                    if hand else [("攻击力", "3.4%"), ("暴击伤害", "17.4%"), ("效果命中", "11.6%"), ("效果抵抗", "7.7%")])
    for row in current["screen_facts"]["facts"]:
        if row["kind"] == "equipment_name":
            row["value"] = "魔法少女的守护手套" if hand else "魔法少女的闪耀勋章"
        elif row["kind"] == "equipment_slot":
            row["value"] = "手部" if hand else "头部"
    return current


def test_exact_historical_wrong_call_is_not_rewritten_into_a_success(book, catalog):
    # This is the actual failed intent, NOT a quietly repaired test input.
    base = build_query_plan(HISTORIC_QUERY, "build_guide")
    chosen = plan_guidance(HISTORIC_QUERY, base, book=book, catalog=catalog)
    assert chosen.consultation_action == "build_guide"
    assert not chosen.character_ids and not chosen.observation_plan(base).screen


def test_declared_current_item_conflicts_with_historical_wrong_intent(book, catalog):
    with pytest.raises(ValueError, match="scope_intent_conflict"):
        choose(HISTORIC_QUERY, book, catalog, intent="build_guide", consultation=declaration())


def test_new_assessment_without_subject_must_not_silently_use_previous_role(book, catalog):
    chosen = choose(HISTORIC_QUERY, book, catalog, previous=prior())
    assert not chosen.character_ids


@pytest.mark.parametrize("state,character,expected", [
    ("inherit", "", ("character.pearl",)),
    ("specified", "绯英", ("character.evanescia",)),
    ("specified", "绯樱", ()),
    ("unresolved", "", ()),
])
def test_subject_states_are_not_one_empty_value(book, catalog, state, character, expected):
    chosen = choose(HISTORIC_QUERY if state != "inherit" else "这个呢", book, catalog,
                    previous=prior(), consultation=declaration(state, character))
    assert chosen.character_ids == expected


def test_explicit_unresolved_does_not_rebind_to_visual_or_keep_old_character(book, catalog):
    chosen = choose(HISTORIC_QUERY, book, catalog, previous=prior(), consultation=declaration("unresolved"))
    current = item()
    current["characters"] = [{"entity_id": "character.pearl", "name": "真珠", "identity_scope": "detail_subject"}]
    result = build_guidance(chosen, HISTORIC_QUERY, book=book, catalog=catalog, profile={}, visual=current)
    assert result["assessment"]["subject_ids"] == []
    assert result["assessment"]["equipment_answer"]["complete"]
    assert result["completion"]["state"] == "needs_subject"
    assert not result["completion"]["can_wait_for_evidence"]


@pytest.mark.parametrize("age", [0, 601])
def test_inheritance_requires_valid_context_and_cannot_override_named_new_role(book, catalog, age):
    chosen = choose("这个呢", book, catalog, previous=prior(age=age), consultation=declaration("inherit"))
    assert bool(chosen.character_ids) is (age == 0)
    with pytest.raises(ValueError, match="subject_conflict"):
        choose("这件给绯英用呢", book, catalog, previous=prior(), consultation=declaration("inherit"))


def test_bound_fit_uses_existing_fixed_main_and_own_rows(book, catalog):
    chosen = choose(HISTORIC_QUERY, book, catalog, consultation=declaration())
    result = build_guidance(chosen, HISTORIC_QUERY, book=book, catalog=catalog, profile={}, visual=item())
    assert result["completion"]["state"] == "complete"
    assert result["completion"]["subject_names"] == ["绯英"]
    assert result["reply_plan"]["fixed_main_stat"]
    assert "固定" in result["reply_plan"]["conclusion"]
    assert result["reply_plan"]["fit_summary"]["priority_substat_count"] == 2


@pytest.mark.parametrize("value", [
    {}, {"scope": "current_relic", "subject_state": ""},
    {"scope": "current_relic", "subject_state": "inherit", "character": "真珠"},
    {"scope": "current_relic", "subject_state": "specified", "character": ""},
    {"scope": "current_relic", "subject_state": "unresolved", "extra": "do not execute"},
])
def test_invalid_declarations_fail_explicitly(book, catalog, value):
    with pytest.raises(ValueError, match="invalid_consultation"):
        choose("这个呢", book, catalog, consultation=value)


@pytest.mark.parametrize("intent,scope,field", [
    ("assess_cone", "current_cone", "equipment"),
    ("assess_panel", "current_panel", "attributes"),
    ("build_guide", "reference_build", None),
])
def test_other_existing_d1_paths_keep_their_own_execution(book, catalog, intent, scope, field):
    base = build_query_plan("给绯英看看", intent)
    chosen = plan_guidance(base.query, base, book=book, catalog=catalog, consultation=declaration(scope=scope))
    assert chosen.observation_plan(base).fields == ({field} if field else set())


def test_fresh_assessment_can_explicitly_use_old_compatible_hint(book, catalog):
    chosen = choose("这件呢", book, catalog, previous=prior(), discussion_character="绯英")
    assert chosen.character_ids == ("character.evanescia",)


def formal_fixture(tmp_path, monkeypatch, catalog, current):
    owner, values, sent = plugin_fixture(tmp_path)
    owner._catalog = catalog
    holder = [current]

    async def resolve(plugin, demand, **kwargs):
        return QueryObservation(ObservationSnapshot.select(plugin, holder[0], fields=demand.fields),
                                {"outcome": "reused", "stages": []}, 0, kwargs["generation"])

    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", resolve)
    owner._observation_serial = current["screen_facts"]["observation_serial"]
    return owner, values, sent, holder


def test_formal_entry_honors_declaration_and_swap_without_changing_archive(tmp_path, monkeypatch, catalog):
    owner, values, sent, holder = formal_fixture(tmp_path, monkeypatch, catalog, item())
    before = copy.deepcopy(values["player_context"])

    async def run():
        first = await owner.hsr_companion_prepare(query=HISTORIC_QUERY, intent="assess_relic", consultation=declaration())
        assert first["guidance_answer"]["completion"]["subject_names"] == ["绯英"]
        assert first["guidance_answer"]["completion"]["state"] == "complete"
        assert not first["followup"]["pending"]
        holder[0] = item(hand=True, serial=23)
        owner._observation_serial = 23
        second = await owner.hsr_companion_prepare(query="这个呢", intent="continue_consultation",
                                                 consultation=declaration("inherit"))
        assert second["guidance_answer"]["completion"]["subject_names"] == ["绯英"]
        assert second["equipment_answer"]["name"] == "魔法少女的守护手套"
        assert "17.4%" not in second["equipment_answer"]["exact_text"]
        assert second["guidance_answer"]["assessment"]["evaluation_available"]
        assert second["delivery_receipt"]["assessment"]["state"] == "complete"
        assert values["player_context"] == before and not sent

    asyncio.run(run())


def test_formal_conflict_is_rejected_before_observation(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)

    async def forbidden(*args, **kwargs):
        raise AssertionError("conflicting declaration must not start reading")

    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "resolve_query", forbidden)
    result = asyncio.run(owner.hsr_companion_prepare(query=HISTORIC_QUERY, intent="build_guide", consultation=declaration()))
    assert result["is_error"] and "scope_intent_conflict" in result["output"]["reason"]


@pytest.mark.parametrize("partial", [False, True])
def test_missing_subject_preserves_rows_but_never_waits_for_screenshot(tmp_path, monkeypatch, catalog, partial):
    current = item()
    if partial:
        current["screen_facts"]["facts"] = [f for f in current["screen_facts"]["facts"]
                                            if f["kind"] != "sub_stat" or f["value"] == "17.4%"]
    owner, _, sent, _ = formal_fixture(tmp_path, monkeypatch, catalog, current)
    owner._guidance_context = prior()
    result = asyncio.run(owner.hsr_companion_prepare(query=HISTORIC_QUERY, intent="assess_relic",
                                                   consultation=declaration("unresolved"), response_detail="full"))
    assert result["equipment_answer"] == equipment_card(current)
    assert result["guidance_answer"]["completion"]["state"] == "needs_subject"
    assert result["followup"]["reason"] == "needs_subject" and not result["followup"]["pending"]
    assert result["guidance_answer"]["reply_plan"]["question"]["purpose"] == "identify_subject"
    assert not sent


def test_unknown_new_subject_clears_old_topic_for_the_next_inheritance(tmp_path, monkeypatch, catalog):
    owner, _, sent, _ = formal_fixture(tmp_path, monkeypatch, catalog, item())
    owner._guidance_context = prior()

    async def run():
        unknown = await owner.hsr_companion_prepare(query=HISTORIC_QUERY, intent="assess_relic",
                                                  consultation=declaration("unresolved"))
        assert unknown["guidance_answer"]["completion"]["state"] == "needs_subject"
        after = await owner.hsr_companion_prepare(query="这个呢", intent="continue_consultation",
                                                consultation=declaration("inherit"))
        assert after["guidance_answer"]["completion"]["subject_ids"] == []
        assert not after["followup"]["pending"] and not sent

    asyncio.run(run())


def test_bound_role_with_missing_item_arms_original_bounded_followup(tmp_path, monkeypatch, catalog):
    from plugin.plugins.hsr_companion.tests.test_d1_fit_repair import label_only
    owner, _, _, _ = formal_fixture(tmp_path, monkeypatch, catalog, label_only())
    armed = []

    def arm(plugin, **kwargs):
        armed.append(kwargs)
        return {"pending": True, "expires_in_seconds": 35}

    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "arm_followup", arm)
    result = asyncio.run(owner.hsr_companion_prepare(query=HISTORIC_QUERY, intent="assess_relic", consultation=declaration()))
    assert result["guidance_answer"]["completion"]["state"] == "needs_evidence"
    assert result["followup"]["pending"] and len(armed) == 1
    assert armed[0]["guidance_plan"].character_ids == ("character.evanescia",)
    assert not armed[0]["guidance_plan"].allow_visual_subject


def test_role_binding_does_not_certify_new_or_old_equipment(book, catalog):
    chosen = choose(HISTORIC_QUERY, book, catalog, consultation=declaration())
    stale = item()
    stale["fresh"] = False
    answer = build_guidance(chosen, HISTORIC_QUERY, book=book, catalog=catalog, profile={}, visual=stale)
    assert answer["completion"]["subject_names"] == ["绯英"]
    assert not answer["assessment"]["evaluation_available"]
    assert not answer["assessment"]["equipment_answer"]["main_stat"]


def test_complete_read_without_rules_is_not_a_completed_fit(book, catalog):
    chosen = choose("这件给布洛妮娅用怎么样", book, catalog, consultation=declaration(character="布洛妮娅"))
    answer = build_guidance(chosen, chosen.observation_plan(build_query_plan("", "assess_relic")).query,
                            book=book, catalog=catalog, profile={}, visual=item())
    assert answer["completion"]["state"] == "rules_unavailable"
    assert answer["assessment"]["equipment_answer"]["complete"]
    assert not answer["completion"]["can_wait_for_evidence"]


def test_partial_rows_are_evaluated_without_claiming_complete_fit(book, catalog):
    current = item()
    current["screen_facts"]["facts"] = [f for f in current["screen_facts"]["facts"]
                                        if f["kind"] != "sub_stat" or f["value"] == "17.4%"]
    chosen = choose(HISTORIC_QUERY, book, catalog, consultation=declaration())
    result = build_guidance(chosen, HISTORIC_QUERY, book=book, catalog=catalog, profile={}, visual=current)
    assert result["completion"]["state"] == "partial"
    assert result["completion"]["evaluation_available"] and not result["completion"]["evaluation_complete"]
    assert result["reply_plan"]["fit_summary"]["observed_substat_count"] == 1


def test_followup_carries_subject_item_and_sourced_fit_to_receiving_bridge(book, catalog, monkeypatch):
    chosen = choose(HISTORIC_QUERY, book, catalog, consultation=declaration())
    _, message, payload = delivered(monkeypatch, item(hand=True), HISTORIC_QUERY,
                                    selected=chosen, book=book, catalog=catalog)
    text = received_text(message)
    assert "绯英" in text and "魔法少女的守护手套" in text and "依据：" in text
    assert payload["guidance_answer"]["completion"]["state"] == "complete"
    assert not payload["guidance_answer"]["reply_plan"]["question"]
    assert "17.4%" not in text


def test_full_fit_followup_keeps_every_requested_row(book, catalog, monkeypatch):
    query = "完整报出这件主词条和四项副词条，适合绯英吗"
    chosen = choose(query, book, catalog, consultation=declaration())
    _, message, payload = delivered(monkeypatch, item(), query, selected=chosen, book=book, catalog=catalog)
    text = received_text(message)
    card = payload["equipment_answer"]
    for row in [card["main_stat"], *card["sub_stats"]]:
        assert row["label"] + row["value"] in text
    assert "绯英" in text and "固定" in text


def test_structured_schema_is_exposed_on_the_existing_tool(tmp_path):
    from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods
    owner, _, _ = plugin_fixture(tmp_path)
    meta = next(meta for meta, _ in collect_llm_tool_methods(owner) if meta.name == "hsr_companion_prepare")
    declared = meta.parameters["properties"]["consultation"]
    assert set(declared["properties"]["subject_state"]["enum"]) == {"inherit", "specified", "unresolved"}
    assert declared["additionalProperties"] is False


def test_diagnostic_records_completion_without_exporting_role_or_game_values():
    from plugin.plugins.hsr_companion.runtime.diagnostics import _delivery_summary
    result = _delivery_summary({"assessment": {"state": "needs_subject", "reading_state": "evidence_available",
        "reading_complete": True, "evaluation_complete": False, "subject_state": "unresolved", "subject_count": 0,
        "subject_names": ["PRIVATE_ROLE"], "value": "PRIVATE_VALUE", "subject_ids": ["PRIVATE_ID"]}})
    assert result["assessment"]["reading_complete"] and not result["assessment"]["evaluation_complete"]
    assert "PRIVATE" not in str(result)


def test_resident_formal_swap_confirm_and_fit_followup_share_the_same_subject(tmp_path, monkeypatch, catalog):
    """One process: head fit -> new item pending -> 好 -> new-item fit.

    Public SDK event shapes and receiving formatter are real; captures, bus
    transport and model choice are isolated fixtures, not a live cat result.
    """
    from plugin.plugins.hsr_companion.runtime import followup
    from plugin.plugins.hsr_companion.runtime.pending_reply import handle_pending_reply
    from plugin.plugins.hsr_companion.tests.test_d1_fit_repair import label_only
    owner, values, sent, holder = formal_fixture(tmp_path, monkeypatch, catalog, item())
    owner._target_character = lambda: "isolated-d1-test-no-host"
    messages = []
    owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kwargs: messages)))
    packets = []
    record = followup.record_answer_delivery

    def capture(*args, **kwargs):
        packets.append(copy.deepcopy(kwargs["payload"]))
        return record(*args, **kwargs)

    async def validated(plugin, evidence, **kwargs):
        return evidence  # The visual proof boundary has separate D0 tests.

    monkeypatch.setattr(followup, "record_answer_delivery", capture)
    monkeypatch.setattr(followup, "validated_delivery", validated)
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)

    async def run():
        gate = asyncio.Event()

        async def current():
            await gate.wait()
            return copy.deepcopy(holder[0])

        owner._current_visual_evidence = current
        first = await owner.hsr_companion_prepare(query=HISTORIC_QUERY, intent="assess_relic", consultation=declaration())
        assert first["guidance_answer"]["completion"]["state"] == "complete"
        holder[0] = label_only()
        owner._observation_serial = 95
        second = await owner.hsr_companion_prepare(query="这个呢", intent="continue_consultation",
                                                 consultation=declaration("inherit"))
        assert second["followup"]["pending"]
        request = owner._pending_answer
        deadline = request.expires_at
        await asyncio.sleep(.02)  # Ensure a valid AFTER-question, non-future SDK fixture event.
        messages.append({"type": "user_message", "content": "好", "lanlan": owner._target_character(), "_ts": time.time()})
        confirmation = await handle_pending_reply(owner, {"request_id": request.request_id,
                                                        "relation": "confirm", "player_message": "好"})
        assert confirmation["accepted"] and owner._pending_answer is request
        assert request.expires_at == deadline
        holder[0] = item(hand=True, serial=96)
        owner._observation_serial = 96
        gate.set()
        await asyncio.wait_for(owner._followup_task, 5)
        assert len(sent) == 1 and len(packets) == 1
        text = received_text(sent[0])
        assert "绯英" in text and "魔法少女的守护手套" in text and "依据：" in text
        assert "17.4%" not in text and "真珠" not in text
        assert packets[0]["guidance_answer"]["completion"]["state"] == "complete"
        assert packets[0]["response_contract"]["request_id"] == request.request_id
        assert owner._pending_answer is None and values["player_context"] == {"owned_characters": ["花火"]}

    asyncio.run(run())
