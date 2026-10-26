"""R0/R1: retained numeric input, isolated lifecycle; not OCR/cat acceptance.

Use the original assess_panel request even when its cache already has all six
numbers. Pixel validation and IPC are explicit fixtures; final revocation is
still exercised. R2 probe costs and R3 long-text output remain separate gates.
"""
import asyncio
import copy
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.runtime import answer_message, followup, query_reuse
from plugin.plugins.hsr_companion.runtime.guidance import build_guidance, plan_guidance
from plugin.plugins.hsr_companion.runtime.query_coordinator import needs_followup
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.request_completion import evaluate_request
from plugin.plugins.hsr_companion.scripts.replay_roundtrip_breakpoints import historical_visual
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests.test_d1_subject_contract_20261020 import formal_fixture
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner

ROOT = Path(__file__).resolve().parents[1]
QUERY = "真珠这面板怎么样，接下来先养哪儿？"
DECLARATION = {"scope": "current_panel", "subject_state": "specified", "character": "真珠"}
HOST_PREVIEW = answer_message.bridge_preview
book, catalog = cases.book, cases.catalog


@pytest.fixture(scope="module")
def retained():
    freeze = json.loads((ROOT / "tests/fixtures/request_chain_r0_20261021.json").read_text(encoding="utf-8"))
    raw = (ROOT / freeze["record"]).read_bytes()
    expected = next(r["sha256"] for r in freeze["source_hashes"] if r["path"] == freeze["record"])
    assert hashlib.sha256(raw).hexdigest() == expected, "Never overwrite the original failed D run"
    return json.loads(raw)


def panel(retained, *, subject=None, serial=7, defense="3335"):
    event = next(e for e in retained["pipeline_trace"] if e["kind"] == "store_commit"
                 and e["observation"].get("serial") == 7)
    value = historical_visual(event["observation"])
    value["instruction"] = "保留记录的离线形状，不是新画面证明。"
    value["scene"]["primary_state"] = "menu"
    value["screen_facts"]["observation_serial"] = serial
    for fact in value["screen_facts"]["facts"]:
        if fact.get("label") == "防御力":
            fact["value"] = defense
    if subject:
        value["characters"] = [{"entity_id": subject, "name": "真珠" if subject == "character.pearl" else "绯英",
                                "identity_scope": "detail_subject", "evidence": "validated_fixture"}]
    assert len([f for f in value["screen_facts"]["facts"] if f.get("scope") == "attributes"]) == 6
    return value


def demand(book, catalog):
    base = build_query_plan(QUERY, "assess_panel")
    guidance = plan_guidance(QUERY, base, book=book, catalog=catalog, consultation=DECLARATION)
    return guidance.observation_plan(base), guidance


def request(book, catalog, *, duration=2):
    plan, selected = demand(book, catalog)
    return followup.AnswerRequest("r1-local-only", QUERY, "猫娘", time.time(), time.monotonic(),
        time.monotonic() + duration, "unknown", False, False, True, False,
        plan=plan, guidance_plan=selected, guidance_profile={})


def owner_for(monkeypatch, current, book, catalog):
    owner, sent = followup_owner(monkeypatch, value=current)
    owner._guidance_book, owner._catalog = book, catalog
    # Test service state transitions, not host capacity (separate R3 gate).
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: text)
    return owner, sent


def test_retained_failure_has_numbers_but_no_completed_assessment(retained):
    receipt = next(r for r in retained["roundtrip"][0]["answer_deliveries"] if r["channel"] == "followup")
    assert receipt["assessment"]["reading_complete"]
    assert not receipt["assessment"]["evaluation_complete"]
    assert retained["roundtrip"][1]["submissions"][0]["metadata"]["message_projection"]["message_rows"] == 0


def test_registration_gate_uses_service_completion_not_just_numeric_fields(retained, book, catalog):
    value = panel(retained)
    plan, selected = demand(book, catalog)
    guidance = build_guidance(selected, QUERY, book=book, catalog=catalog, profile={}, visual=value)
    assert guidance["completion"]["reading_complete"]
    assert needs_followup(plan, value, {"enabled": True, "state": "observing"},
                          completion=guidance["completion"])


def test_arm_accepts_cached_numbers_pending_subject(retained, book, catalog, monkeypatch):
    value = panel(retained)
    owner, _ = owner_for(monkeypatch, value, book, catalog)
    plan, selected = demand(book, catalog)

    async def run():
        result = followup.arm_followup(owner, query=QUERY, visual=value, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False, plan=plan,
            guidance_plan=selected, guidance_profile={})
        assert result["pending"], result
        pending = owner._pending_answer
        # Subtraction across float exponent boundaries can differ by one ULP;
        # this is a 1 ns assertion tolerance, NOT a production deadline change.
        assert pending.expires_at - pending.armed_at == pytest.approx(followup.FOLLOWUP_SECONDS, abs=1e-9, rel=0)
        followup.cancel_followup(owner, "test_cleanup")
        await asyncio.gather(owner._followup_task, return_exceptions=True)

    asyncio.run(run())


def test_formal_direct_cache_hit_still_registers_missing_service(retained, book, catalog, tmp_path, monkeypatch):
    owner, _, _, _ = formal_fixture(tmp_path, monkeypatch, catalog, panel(retained))
    owner._guidance_book = book
    owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: [])))
    gate = None

    async def run():
        nonlocal gate
        gate = asyncio.Event()

        async def blocked():
            await gate.wait()

        owner._current_visual_evidence = blocked
        result = await owner.hsr_companion_prepare(query=QUERY, intent="assess_panel", consultation=DECLARATION)
        assert result["query_answer"]["state"] == "ready"  # Keep independent numeric readiness.
        assert "服务完成另看 request_completion" in result["query_answer"]["instruction"]
        assert not result["guidance_answer"]["completion"]["evaluation_complete"]
        assert result["followup"]["pending"], result["followup"]
        followup.cancel_followup(owner, "test_cleanup")
        await asyncio.gather(owner._followup_task, return_exceptions=True)

    asyncio.run(run())


def test_numbers_then_identity_finish_same_request_without_requiring_new_numbers(retained, book, catalog, monkeypatch):
    first, final = panel(retained), panel(retained, subject="character.pearl", serial=8)
    owner, sent = owner_for(monkeypatch, first, book, catalog)
    history, at_submission = [], []

    async def run():
        pending = request(book, catalog)
        owner._pending_answer = pending
        frames = [first, {**copy.deepcopy(first), "observed_at": "different timestamp"}, final]

        async def current():
            value = frames[min(len(history), len(frames) - 1)]
            history.append(value)
            owner._observation_serial = value["screen_facts"]["observation_serial"]
            return copy.deepcopy(value)

        owner._current_visual_evidence = current
        push = owner.push_message

        def capture(**kw):
            at_submission.append(owner._pending_answer is pending)
            return push(**kw)

        owner.push_message = capture
        await followup.deliver_followup(owner, pending)
        assert len(sent) == 2, owner._followup_status
        assert at_submission[0], "A partial read must not claim/clear the request"
        assert sent[0]["metadata"]["outcome"] == "partial"
        assert sent[1]["metadata"]["outcome"] == "ready"
        assert owner._pending_answer is None
        assert owner._followup_status["answer_complete"]
        assert all(m["metadata"]["request_id"] == pending.request_id for m in sent)
        assert "真珠" in sent[-1]["parts"][0]["text"] and "防御力" in sent[-1]["parts"][0]["text"]
        # Ordinary evaluation no longer has to recite numbers. Ownership and
        # exact values remain mandatory in the SAME final selected packet.
        assert any(f["value"] == "3335" for f in owner._test_answer_payloads[-1]["screen_facts"]["facts"])

    asyncio.run(run())


def test_cache_reselection_is_allowed_when_only_subject_is_missing(retained, book, catalog, monkeypatch):
    first, final = panel(retained), panel(retained, subject="character.pearl")
    owner, sent = owner_for(monkeypatch, first, book, catalog)
    owner._reusable_observations = {"full": {"evidence": final}}
    selected = []

    async def reuse(*a, **kw):
        selected.append(kw["plan"].fields)
        return copy.deepcopy(final)  # Explicit fixture: real reuse guards have their own tests.

    monkeypatch.setattr(query_reuse, "try_reuse", reuse)

    async def run():
        pending = request(book, catalog)
        owner._pending_answer = pending
        await followup.deliver_followup(owner, pending)
        assert selected == [frozenset({"attributes"})]
        assert len(sent) == 1 and owner._followup_status["answer_complete"]

    asyncio.run(run())


def test_serial_and_timestamp_changes_alone_do_not_generate_more_progress(retained, book, catalog, monkeypatch):
    value = panel(retained)
    owner, sent = owner_for(monkeypatch, value, book, catalog)
    reads = []

    async def run():
        pending = request(book, catalog, duration=.65)
        owner._pending_answer = pending

        async def current():
            result = copy.deepcopy(value)
            result["observed_at"] = str(len(reads))
            reads.append(1)
            result["screen_facts"]["observation_serial"] = 7 + len(reads)
            owner._observation_serial = 7 + len(reads)
            return result

        owner._current_visual_evidence = current
        await followup.deliver_followup(owner, pending)
        assert len(reads) >= 2
        assert len(sent) == 1 and sent[0]["metadata"]["outcome"] == "partial"
        assert owner._followup_status["state"] == "expired"

    asyncio.run(run())


@pytest.mark.parametrize("subject,defense,complete", [
    ("character.pearl", "3336", True), ("character.evanescia", "1062", False),
])
def test_final_uses_reselected_whole_snapshot_not_later_name_on_old_numbers(
        retained, book, catalog, monkeypatch, subject, defense, complete):
    first, final = panel(retained), panel(retained, subject=subject, serial=8, defense=defense)
    owner, sent = owner_for(monkeypatch, first, book, catalog)

    async def run():
        pending = request(book, catalog)
        owner._pending_answer = pending
        reads = []

        async def current():
            value = final if reads else first
            reads.append(1)
            owner._observation_serial = value["screen_facts"]["observation_serial"]
            return copy.deepcopy(value)

        owner._current_visual_evidence = current
        await followup.deliver_followup(owner, pending)
        assert len(sent) == 2
        receipt = sent[-1]["metadata"]["message_projection"]
        assert receipt["answer_complete"] is complete
        if complete:
            # Ordinary advice need not recite a changed unspoken value; the
            # SAME final packet must still carry the reselected exact reading.
            facts = owner._test_answer_payloads[-1]["screen_facts"]["facts"]
            assert any(f["value"] == defense for f in facts)
            assert not any(f["value"] == "3335" for f in facts)
            assert "3335" not in sent[-1]["parts"][0]["text"]
        else:
            assert not owner._followup_status["answer_complete"]
            assert owner._pending_answer is None

    asyncio.run(run())


@pytest.mark.parametrize("change", ["new_generation", "pause", "cancel", "expire"])
def test_async_formatter_cannot_publish_after_revocation(retained, book, catalog, monkeypatch, change):
    value = panel(retained, subject="character.pearl")
    owner, sent = owner_for(monkeypatch, value, book, catalog)
    owner._answer_query_generation = 1
    original = answer_message.render_followup

    def format_and_change(*a, **kw):
        result = original(*a, **kw)
        if change == "new_generation":
            owner._answer_query_generation = 2
        elif change == "pause":
            owner._observer_stop_requested = True
        elif change == "cancel":
            owner._pending_answer = None
        else:
            time.sleep(.06)
        return result

    monkeypatch.setattr(answer_message, "render_followup", format_and_change)

    async def run():
        pending = replace(request(book, catalog, duration=.04 if change == "expire" else .15), generation=1)
        owner._pending_answer = pending
        await followup.deliver_followup(owner, pending)
        assert not sent

    asyncio.run(run())


def test_progress_key_ignores_proof_bookkeeping_but_tracks_subject_and_values(retained, book, catalog):
    value = panel(retained)
    plan, selected = demand(book, catalog)

    def progress(v):
        g = build_guidance(selected, QUERY, book=book, catalog=catalog, profile={}, visual=v)
        return evaluate_request(plan, v, completion=g["completion"])

    original = progress(value)
    newer = copy.deepcopy(value)
    newer["observed_at"] = "a new acquisition"
    newer["screen_facts"]["observation_serial"] = 999
    for fact in newer["screen_facts"]["facts"]:
        fact["score"] = .999
        fact["evidence"] = []  # Key comparison only, NOT pixel admission.
    assert progress(newer).progress_key == original.progress_key
    assert progress(panel(retained, subject="character.pearl")).progress_key != original.progress_key
    assert progress(panel(retained, defense="3336")).progress_key != original.progress_key


@pytest.mark.parametrize("kind", ["missing_subject", "missing_rules", "different_subject"])
def test_terminal_missing_information_does_not_arm_pixel_wait(retained, book, catalog, monkeypatch, kind):
    value = panel(retained, subject="character.evanescia" if kind == "different_subject" else None)
    plan, selected = demand(book, catalog)
    if kind == "missing_subject":
        selected = replace(selected, character_ids=(), allow_visual_subject=False, subject_state="unresolved")
    elif kind == "missing_rules":
        selected = replace(selected, character_ids=("character.bronya",))
    owner, sent = owner_for(monkeypatch, value, book, catalog)

    async def run():
        armed = followup.arm_followup(owner, query=QUERY, visual=value, asked_at=time.time(),
            scene_only=False, navigation=False, content=True, controlled=False,
            plan=plan, guidance_plan=selected, guidance_profile={})
        assert not armed["pending"]
        assert owner._pending_answer is None and not sent

    asyncio.run(run())


def test_confirmation_of_cached_panel_preserves_original_deadline_and_request(retained, book, catalog, monkeypatch):
    from plugin.plugins.hsr_companion.runtime.pending_reply import handle_pending_reply

    value, final = panel(retained), panel(retained, subject="character.pearl")
    owner, sent = owner_for(monkeypatch, value, book, catalog)
    messages = []
    owner.bus = SimpleNamespace(memory=SimpleNamespace(get=lambda **kw: messages))
    plan, selected = demand(book, catalog)

    async def run():
        gate = asyncio.Event()

        async def current():
            await gate.wait()
            return copy.deepcopy(final)

        owner._current_visual_evidence = current
        asked = time.time()
        result = followup.arm_followup(owner, query=QUERY, visual=value, asked_at=asked,
            scene_only=False, navigation=False, content=True, controlled=False,
            plan=plan, guidance_plan=selected, guidance_profile={})
        assert result["pending"]
        pending = owner._pending_answer
        deadline = pending.expires_at
        await asyncio.sleep(.02)
        messages.append({"type": "user_message", "content": "好", "lanlan": "猫娘", "_ts": time.time()})
        for _ in range(2):
            reply = await handle_pending_reply(owner, {"request_id": pending.request_id,
                                                      "relation": "confirm", "player_message": "好"})
            assert reply["accepted"] and owner._pending_answer is pending
            assert pending.expires_at == deadline
        gate.set()
        await asyncio.wait_for(owner._followup_task, 3)
        assert len(sent) == 1 and owner._followup_status["answer_complete"]

    asyncio.run(run())


@pytest.mark.parametrize("change", ["value", "subject"])
def test_formatter_race_reselects_instead_of_attaching_new_subject_to_old_values(
        retained, book, catalog, monkeypatch, change):
    initial = panel(retained, subject="character.pearl")
    final = panel(retained, subject="character.pearl" if change == "value" else "character.evanescia",
                  serial=8, defense="3336" if change == "value" else "1062")
    owner, sent = owner_for(monkeypatch, initial, book, catalog)
    holder, formatted = [initial], []
    # Replay the semantic replacement boundary on a separately selected packet;
    # these inputs do not pretend to prove current pixels or confirm identities.
    monkeypatch.setattr("plugin.plugins.hsr_companion.runtime.fact_lifecycle.candidate_evidence",
                        lambda raw: copy.deepcopy(holder[0]))
    original = answer_message.render_followup

    def format_and_switch(*a, **kw):
        result = original(*a, **kw)
        if not formatted:
            holder[0] = final
            owner._observation_serial = 8
            owner._committed_observation = {"observation_serial": 8}
        formatted.append(result)
        return result

    monkeypatch.setattr(answer_message, "render_followup", format_and_switch)

    async def current():
        return copy.deepcopy(holder[0])

    owner._current_visual_evidence = current

    async def run():
        pending = request(book, catalog)
        owner._pending_answer = pending
        await followup.deliver_followup(owner, pending)
        assert len(formatted) >= 2 and len(sent) == 1
        text = sent[0]["parts"][0]["text"]
        assert "3335" not in text
        assert owner._followup_status["answer_complete"] is (change == "value")
        if change == "value":
            facts = owner._test_answer_payloads[-1]["screen_facts"]["facts"]
            assert any(f["value"] == "3336" for f in facts)
            assert not any(f["value"] == "3335" for f in facts)
            assert "防御力" in text and "依据：" in text
        else:
            assert "另一角色" in text

    asyncio.run(run())


def test_capacity_notice_closes_as_undelivered_not_a_completed_answer(retained, book, catalog, monkeypatch):
    value = panel(retained, subject="character.pearl")
    owner, sent = owner_for(monkeypatch, value, book, catalog)
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: "truncated")

    async def run():
        pending = request(book, catalog)
        owner._pending_answer = pending
        await followup.deliver_followup(owner, pending)
        assert len(sent) == 1 and owner._pending_answer is None
        status = owner._followup_status
        assert status["outcome"] == "transport_limited" and status["reason"] == "body_not_delivered"
        assert status["request_completion"]["service_ready"]
        assert not status["request_completion"]["body_complete"]
        assert not status["request_completion"]["delivered_complete"]
        assert not status["answer_complete"]  # R3 must later supply the page content.

    asyncio.run(run())


def test_subject_final_evaluation_survives_real_host_formatter(retained, book, catalog, monkeypatch):
    value = panel(retained, subject="character.pearl")
    owner, sent = owner_for(monkeypatch, value, book, catalog)
    monkeypatch.setattr(answer_message, "bridge_preview", HOST_PREVIEW)

    async def run():
        pending = request(book, catalog, duration=3)
        owner._pending_answer = pending
        await followup.deliver_followup(owner, pending)
        assert len(sent) == 1 and owner._followup_status["answer_complete"]
        from plugin.plugins.hsr_companion.tests.test_d1_handoff_closeout import received_text
        body = received_text(sent[0])
        assert "真珠" in body and "防御力" in body and "依据：" in body
        assert any(f["value"] == "3335" for f in owner._test_answer_payloads[-1]["screen_facts"]["facts"])

    asyncio.run(run())


def test_busy_validation_does_not_terminally_close_an_otherwise_ready_service(retained, book, catalog, monkeypatch):
    value = panel(retained, subject="character.pearl")
    owner, sent = owner_for(monkeypatch, value, book, catalog)
    calls = []

    async def validate(*a, **kw):
        calls.append(1)
        return value if len(calls) > 1 else None

    monkeypatch.setattr(followup, "validated_delivery", validate)

    async def run():
        pending = request(book, catalog)
        owner._pending_answer = pending
        await followup.deliver_followup(owner, pending)
        assert len(calls) == 2 and len(sent) == 1
        assert owner._followup_status["answer_complete"]

    asyncio.run(run())


def test_progress_diagnostic_excludes_raw_values_and_names():
    from plugin.plugins.hsr_companion.runtime.diagnostics import _delivery_summary
    output = _delivery_summary({"request_completion": {"facts_ready": True, "service_ready": False,
        "state": "needs_subject_binding", "subject_names": ["PRIVATE_ROLE"], "value": "PRIVATE_VALUE"}})
    assert output["request_completion"]["facts_ready"]
    assert not output["request_completion"]["service_ready"]
    assert "PRIVATE" not in str(output)


def test_projection_can_be_complete_text_without_completing_the_service(retained, book, catalog, monkeypatch):
    value = panel(retained, subject="character.pearl")
    owner, sent = owner_for(monkeypatch, value, book, catalog)
    original = answer_message.render_followup

    def incomplete_service(*a, **kw):
        body, projection = original(*a, **kw)
        return body, {**projection, "answer_complete": False}

    monkeypatch.setattr(answer_message, "render_followup", incomplete_service)

    async def run():
        pending = request(book, catalog)
        owner._pending_answer = pending
        await followup.deliver_followup(owner, pending)
        assert len(sent) == 1
        status = owner._followup_status
        assert status["request_completion"]["body_complete"]
        assert not status["request_completion"]["delivered_complete"]
        assert not status["answer_complete"]
        assert status["reason"] == "service_projection_incomplete"

    asyncio.run(run())
