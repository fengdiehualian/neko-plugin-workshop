"""R3: retained same-capture literals through the actual host formatter.

D did not save its full input payload. Reconstruct 23 rows from that ONE
reader's retained boxes and accepted complete literal. No cross-frame repair,
new OCR, host traffic or claim of complete original screenshot replay.
"""
import asyncio
import copy
import hashlib
import inspect
import json
import socket
import time
from pathlib import Path

import pytest
from plugin.plugins.hsr_companion.runtime import answer_message, followup
from plugin.plugins.hsr_companion.runtime.consultation import detail_preference
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests import test_guidance as cases
from plugin.plugins.hsr_companion.tests.test_d1_handoff_closeout import received_text
from plugin.plugins.hsr_companion.tests.test_d1_subject_contract_20261020 import formal_fixture
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner

ROOT = Path(__file__).resolve().parents[1]
GOALS = ["取得胜利时,剩余10轮以上", "取得胜利时,剩余20轮以上", "没有角色无法战斗"]
TITLE = "来生泅渡其十二"
book, catalog = cases.book, cases.catalog


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    with asyncio.Runner() as runner:
        runner.get_loop()  # Windows event-loop plumbing, not external traffic.
        monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("offline only")))
        yield runner


@pytest.fixture(scope="module")
def retained():
    raw = (ROOT / "dist/roundtrip-d-live-20261021-2.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "42b85cb008812fac790ecfb3aa862b8ebd695c64b55bd674df187a38adcef153"
    record = json.loads(raw)
    review = next(e for e in record["pipeline_trace"] if e["kind"] == "producer_fact_review"
                  and e.get("query_generation") == 2 and e["before"]["page"] == "challenge_setup")
    parsed = next(e for e in record["pipeline_trace"] if e["kind"] == "ocr_to_parser"
                  and (e.get("reader") or {}).get("payload_sha256") == review["original_sha256"]
                  and e["page"] == "challenge_setup")
    rows = [{"text": b["text"], "score": b["score"],
             "role": "heading_candidate" if b["bottom"] <= 320 else "visible_fragment",
             "region": {k: b[k] / bound for k, bound in (
                 ("left", 2560), ("right", 2560), ("top", 1600), ("bottom", 1600))}}
            for b in parsed["boxes"]]
    changes = next(e["changed"] for e in review["trace"] if e["stage"] == "proof_selection")
    # Only whole, admitted literals from the same producer/capture, never the
    # failed first mechanism line or a guessed continuation of a fragment.
    rows += [copy.deepcopy(p["proof"]) for p in changes if p["text_revalidated"]
             and p["proof"].get("role") == "visible_fragment"
             and p["proof"]["text"] not in {r["text"] for r in rows}]
    rows.sort(key=lambda r: (r["region"]["top"], r["region"]["left"]))
    assert len(rows) == 23
    assert all(t in {r["text"] for r in rows} for t in [TITLE, *GOALS])
    original = next(d for d in record["roundtrip"][1]["answer_deliveries"]
                    if d["channel"] == "followup" and d["message_projection"]["mode"] == "transport_limited_notice")
    return {"fresh": True, "page_type": "challenge_setup", "read_scope": "full",
            "scene": {"verified": True, "label": "忘却之庭挑战准备"}, "characters": [],
            "instruction": "离线形状夹具，不证明新画面或每条原始投递顺序。",
            "observed_at": record["roundtrip"][1]["final_committed"]["observed_at"],
            "screen_facts": {"observation_serial": 10, "facts": [], "visible_text": rows},
            "_original_receipt": original["message_projection"]}


def plan(query="这个页面是干嘛的？页面上写了什么？", *, detail=None):
    return build_query_plan(query, "explain_page", screen_fields=["page", "visible_text"], response_detail=detail)


def payload(value):
    return {"page_type": value["page_type"], "scene": copy.deepcopy(value["scene"]),
            "screen_facts": copy.deepcopy(value["screen_facts"]),
            "answer_progress": {"complete": True, "confirmed_fields": ["page", "visible_text"], "missing_fields": []}}


def assert_core(text):
    assert "忘却之庭" in text and TITLE in text
    assert all(t in text for t in GOALS)
    assert "容量" not in text


def test_original_failure_is_preserved_and_not_a_full_payload_snapshot(retained):
    original = retained["_original_receipt"]
    assert original["required_rows"] == 24 and original["message_rows"] == 0
    assert not original["complete"]


@pytest.mark.parametrize("detail", [None, "brief"])
def test_original_long_literals_deliver_required_core_through_real_formatter(retained, detail, record_property):
    value = payload(retained)
    frozen = copy.deepcopy(value)
    text, receipt = answer_message.render_followup(value, plan=plan(detail=detail), ready=True)
    assert receipt["mode"] == "page_summary", receipt
    assert receipt["complete"] and receipt["answer_complete"] and receipt["service_satisfied"]
    assert not receipt["all_input_rows_delivered"] and receipt["omitted_rows"] > 0
    assert {"page", "stage_title", "objectives"} <= set(receipt["carried_sections"])
    assert receipt["source_rows"] == 24
    assert receipt["message_rows"] == receipt["required_rows"] >= 5
    assert_core(text)
    assert answer_message.bridge_preview(text) == text
    assert value == frozen  # Original full tool/audit packet is not shortened.
    record_property("summary_receipt", json.dumps({"body": text, "projection": receipt}, ensure_ascii=False))


@pytest.mark.parametrize("query,detail", [
    ("完整报出页面全部文字，不要漏。", None), ("逐字念一下页面文字。", None),
    ("页面写了什么？", "full"), ("完整报出页面文字。", "brief")])
def test_explicit_full_never_silently_becomes_summary(retained, query, detail):
    text, receipt = answer_message.render_followup(payload(retained), plan=plan(query, detail=detail), ready=True)
    assert receipt["mode"] != "page_summary"
    assert not receipt["complete"] and not receipt["answer_complete"]
    assert not receipt["service_satisfied"]
    assert receipt["requested_detail"] == "full"
    assert receipt["message_rows"] == 0 and receipt["omitted_rows"] == receipt["source_rows"]
    assert "词条" not in text  # A page-text capacity failure is not a relic claim.


def test_short_full_request_retains_every_literal(retained):
    value = payload(retained)
    value["screen_facts"]["visible_text"] = [{"text": "短目标原文A"}, {"text": "短机制原文B"}]
    text, receipt = answer_message.render_followup(value, plan=plan("逐字读出页面文字。", detail="full"), ready=True)
    assert receipt["complete"] and receipt["all_input_rows_delivered"]
    assert receipt["omitted_rows"] == 0
    assert all(r["text"] in text for r in value["screen_facts"]["visible_text"])
    assert answer_message.bridge_preview(text) == text


@pytest.mark.parametrize("mutation", ["missing_title", "missing_goals", "conflicting_title", "unknown_page", "missing_geometry"])
def test_minimum_summary_contract_cannot_be_replaced_by_just_page_name(retained, mutation):
    value = payload(retained)
    rows = value["screen_facts"]["visible_text"]
    if mutation == "missing_title":
        rows[:] = [r for r in rows if r["text"] != TITLE]
    elif mutation == "missing_goals":
        rows[:] = [r for r in rows if r["text"] not in GOALS]
    elif mutation == "conflicting_title":
        title = copy.deepcopy(next(r for r in rows if r["text"] == TITLE))
        title["text"] = "另一个冲突关卡标题"
        rows.append(title)
    elif mutation == "unknown_page":
        value.update(page_type="unknown", scene={"verified": False})
    else:
        for r in rows:
            r.pop("region", None)
    _, receipt = answer_message.render_followup(value, plan=plan(), ready=True)
    assert receipt["mode"] != "page_summary"
    # If all remaining original rows fit, the unchanged full-input route is
    # valid. The minimum-section contract applies to an overflow SUMMARY.
    if receipt["complete"]:
        assert receipt["all_input_rows_delivered"]
    else:
        assert not receipt["answer_complete"]


def test_formatter_rejection_cannot_be_counted_as_delivered(retained, monkeypatch):
    monkeypatch.setattr(answer_message, "bridge_preview", lambda text: "已被截断")
    _, receipt = answer_message.render_followup(payload(retained), plan=plan(), ready=True)
    assert not receipt["complete"] and not receipt["service_satisfied"]
    assert receipt["message_rows"] == 0


def test_optional_mechanism_formatter_failure_keeps_proven_core(retained, monkeypatch):
    preview = answer_message.bridge_preview

    def optional_fails(text):
        if "\n机制原文：" in text:
            raise OSError("simulated optional formatter failure")
        return preview(text)

    monkeypatch.setattr(answer_message, "bridge_preview", optional_fails)
    text, receipt = answer_message.render_followup(payload(retained), plan=plan(), ready=True)
    assert_core(text)
    assert receipt["complete"] and "mechanics" not in receipt["carried_sections"]


def test_full_dump_formatter_error_does_not_prevent_valid_minimum_projection(retained, monkeypatch):
    preview = answer_message.bridge_preview

    def full_dump_fails(text):
        if "文字数据：" in text:
            raise ValueError("simulated formatter error on the all-row dump")
        return preview(text)

    monkeypatch.setattr(answer_message, "bridge_preview", full_dump_fails)
    text, receipt = answer_message.render_followup(payload(retained), plan=plan(), ready=True)
    assert_core(text)
    assert receipt["mode"] == "page_summary" and receipt["service_satisfied"]


def test_mixed_equipment_request_and_team_advice_do_not_take_page_summary(retained):
    data = payload(retained)
    mixed = build_query_plan("读取当前页面和词条", "read_screen", screen_fields=["page", "visible_text", "equipment"])
    _, receipt = answer_message.render_followup(data, plan=mixed, ready=True)
    assert receipt["mode"] != "page_summary"
    data["guidance_answer"] = {"requested": True, "kind": "challenge", "team_candidates": []}
    _, receipt = answer_message.render_followup(data, plan=plan(), ready=True)
    assert receipt["mode"] != "page_summary"


def test_existing_fullness_preference_includes_verbatim_and_negation():
    assert detail_preference("逐字报出页面文字。") == "full"
    assert detail_preference("不用逐字报，简单说说重点。") == "brief"


def test_followup_lifecycle_submits_core_and_records_actual_omissions(retained, monkeypatch, offline):
    owner, sent = followup_owner(monkeypatch, value=copy.deepcopy(retained))
    selected = plan()
    pending = followup.AnswerRequest("r3-offline", selected.query, "猫娘", time.time(), time.monotonic(),
        time.monotonic() + 5, "unknown", False, False, True, False, plan=selected)
    owner._pending_answer = pending
    offline.run(followup.deliver_followup(owner, pending))
    assert len(sent) == 1 and owner._pending_answer is None
    assert_core(received_text(sent[0]))
    status = owner._followup_status
    assert status["answer_complete"] and status["outcome"] == "ready"
    assert status["request_completion"]["delivered_complete"]
    receipt = owner._answer_deliveries[-1]
    assert receipt["message_projection"]["mode"] == "page_summary"
    assert receipt["message_projection"]["omitted_rows"] > 0
    assert len(owner._test_answer_payloads[-1]["screen_facts"]["visible_text"]) == 23


def test_full_page_followup_ends_incomplete_without_claiming_summary_satisfied_full(retained, monkeypatch, offline):
    owner, sent = followup_owner(monkeypatch, value=copy.deepcopy(retained))
    selected = plan("逐字报出全部页面文字。", detail="full")
    pending = followup.AnswerRequest("r3-full", selected.query, "猫娘", time.time(), time.monotonic(),
        time.monotonic() + 5, "unknown", False, False, True, False, plan=selected)
    owner._pending_answer = pending
    offline.run(followup.deliver_followup(owner, pending))
    assert len(sent) == 1 and owner._pending_answer is None
    assert "未完整交付" in received_text(sent[0])
    status = owner._followup_status
    assert status["outcome"] == "transport_limited" and not status["answer_complete"]
    assert not status["request_completion"]["delivered_complete"]
    projection = owner._answer_deliveries[-1]["message_projection"]
    assert projection["requested_detail"] == "full" and not projection["service_satisfied"]
    assert projection["source_rows"] == projection["omitted_rows"] == 24
    assert len(owner._test_answer_payloads[-1]["screen_facts"]["visible_text"]) == 23


def test_formal_tool_keeps_full_packet_while_notification_is_a_projection(retained, catalog, tmp_path, monkeypatch, offline):
    owner, _, sent, _ = formal_fixture(tmp_path, monkeypatch, catalog, copy.deepcopy(retained))
    result = offline.run(owner.hsr_companion_prepare(query=plan().query, intent="explain_page", screen_fields=["page", "visible_text"]))
    assert result["query_answer"]["state"] == "ready", result
    assert len(result["current_visual_evidence"]["screen_facts"]["visible_text"]) == 23
    assert not sent  # Prepare is a tool return, not a host speech receipt.
    before = copy.deepcopy(result)
    text, projection = answer_message.render_followup(payload(result["current_visual_evidence"]), plan=plan(), ready=True)
    assert_core(text)
    assert projection["omitted_rows"] > 0 and result == before


@pytest.mark.parametrize("change", ["generation", "epoch", "pause"])
def test_summary_formatter_still_obeys_final_request_and_snapshot_guards(retained, monkeypatch, offline, change):
    owner, sent = followup_owner(monkeypatch, value=copy.deepcopy(retained))
    owner._answer_query_generation = 1
    selected = plan()
    pending = followup.AnswerRequest("r3-revoke", selected.query, "猫娘", time.time(), time.monotonic(),
        time.monotonic() + .4, "unknown", False, False, True, False, plan=selected, generation=1)
    render = answer_message.render_followup

    def revoke(*a, **kw):
        result = render(*a, **kw)
        if change == "generation":
            owner._answer_query_generation += 1
        elif change == "epoch":
            owner._observation_epoch += 1
        else:
            owner._observer_stop_requested = True
        return result

    monkeypatch.setattr(answer_message, "render_followup", revoke)
    owner._pending_answer = pending
    offline.run(followup.deliver_followup(owner, pending))
    assert not sent


def test_summary_diagnostics_are_counts_and_categories_not_ocr_prose():
    from plugin.plugins.hsr_companion.runtime.diagnostics import _delivery_summary
    value = _delivery_summary({"message_projection": {"mode": "page_summary", "complete": True,
        "required_rows": 6, "message_rows": 6, "source_rows": 24, "omitted_rows": 18,
        "service_satisfied": True, "all_input_rows_delivered": False,
        "carried_sections": ["page", "stage_title", "objectives", "DO_NOT_EXPORT"], "raw_text": "DO_NOT_EXPORT"}})
    projection = value["message_projection"]
    assert projection["mode"] == "page_summary" and projection["source_rows"] == 24
    assert projection["omitted_rows"] == 18 and projection["service_satisfied"]
    assert "DO_NOT_EXPORT" not in json.dumps(value)


def test_legacy_panel_fixture_identity_gap_is_unchanged_without_verbatim_extension(book, catalog, monkeypatch, record_property):
    from plugin.plugins.hsr_companion.runtime import consultation, guidance, semantic_guidance
    from plugin.plugins.hsr_companion.tests.test_d1_closeout import panel
    from plugin.plugins.hsr_companion.tests.test_stage2_assessment import assess
    query = "真珠这个面板养得怎么样？接下来优先关注什么？"
    current = assess(query, panel(), book, catalog)
    original = consultation.detail_preference
    namespace = dict(consultation.__dict__)
    exec(inspect.getsource(original).replace("|逐字", ""), namespace)
    with monkeypatch.context() as patcher:
        for module in (consultation, guidance, semantic_guidance):
            if getattr(module, "detail_preference", None) is original:
                patcher.setattr(module, "detail_preference", namespace["detail_preference"])
        baseline = assess(query, panel(), book, catalog)
    assert current["reply_plan"] == baseline["reply_plan"]
    assert baseline["completion"]["panel_subject_state"] == "unconfirmed"
    assert baseline["reply_plan"]["max_questions"] == 0
    record_property("legacy_fixture_baseline", json.dumps({"current_matches_before_verbatim": True,
        "panel_subject_state": "unconfirmed", "max_questions": 0}))


def test_formatter_production_budget_then_original_short_budget(book, catalog, monkeypatch, record_property, offline):
    # The retained three-second cold failure remains a failure. Compare the
    # actual production budget and then the unchanged old test in a warm process;
    # neither outcome can erase that earlier deadline/temperature observation.
    from plugin.plugins.hsr_companion.tests import test_request_completion_r1_20261021 as original
    frames = json.loads((ROOT / "dist/roundtrip-d-live-20261021-2.json").read_bytes())
    owners, calls, phase = [], [], ["production_budget"]
    owner_for, render = original.owner_for, answer_message.render_followup

    def capture_owner(*args):
        result = owner_for(*args)
        owners.append(result[0])
        return result

    def measured(*args, **kwargs):
        started = time.monotonic()
        result = None
        try:
            result = render(*args, **kwargs)
            return result
        finally:
            pending = getattr(owners[-1], "_pending_answer", None)
            calls.append({"phase": phase[0], "seconds": round(time.monotonic() - started, 3),
                          "remaining": round(pending.expires_at - time.monotonic(), 3) if pending else None,
                          "mode": result[1]["mode"] if result else None})

    monkeypatch.setattr(original, "owner_for", capture_owner)
    monkeypatch.setattr(answer_message, "render_followup", measured)
    # The offline fixture already made Windows' loopback self-pipe before
    # forbidding external connections. Reuse that loop, not a second self-pipe.
    monkeypatch.setattr(original.asyncio, "run", offline.run)
    try:
        owner, sent = capture_owner(monkeypatch, original.panel(frames, subject="character.pearl"), book, catalog)
        monkeypatch.setattr(answer_message, "bridge_preview", original.HOST_PREVIEW)
        pending = original.request(book, catalog, duration=followup.FOLLOWUP_SECONDS)
        owner._pending_answer = pending
        offline.run(followup.deliver_followup(owner, pending))
        assert len(sent) == 1 and owner._followup_status["answer_complete"]
        assert "真珠" in received_text(sent[0]) and "防御力" in received_text(sent[0])
        assert any(f["value"] == "3335" for f in owner._test_answer_payloads[-1]["screen_facts"]["facts"])
        production_status = copy.deepcopy(owner._followup_status)
        phase[0] = "original_short_budget_same_process"
        original.test_subject_final_evaluation_survives_real_host_formatter(frames, book, catalog, monkeypatch)
    finally:
        record_property("r1_formatter_probe", json.dumps({"formatter": calls,
            "production_followup_seconds": followup.FOLLOWUP_SECONDS,
            "production_status": locals().get("production_status", {}),
            "status": getattr(owners[-1], "_followup_status", {}) if owners else {}}))
