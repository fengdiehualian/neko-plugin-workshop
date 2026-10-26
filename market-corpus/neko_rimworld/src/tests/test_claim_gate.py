"""Heuristic claim diagnostics remain visible without controlling delivery."""
from __future__ import annotations

import pytest
from plugin.plugins.neko_rimworld.agent.delivery.handoff import (
    SubmissionDraft,
    SubmissionPatchError,
    make_submission_tool,
)
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditLedger
from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
from plugin.plugins.neko_rimworld.agent.runtime.session import AgentRunSession


def observation_without_identity():
    return {
        "observation_id": "obs-1",
        "response": {
            "connection_epoch": "connection", "colony_id": "colony", "load_epoch": "load",
            "payload": {"status": "ok", "data": {"rows": [{"id": "Pawn1", "label": "Stin"}]}},
        },
    }


def session():
    return AgentRunSession.create(ledger=AgentAuditLedger("test question"), question="q")


def test_first_claim_warning_retains_full_report_and_short_callback():
    draft = SubmissionDraft()
    text = "他是我们的殖民者。"
    result = draft.apply({"answer": text, "detail": "作者补充报告。"}, observations=[observation_without_identity()])
    assert result["agent_text"] == text and result["full_text"] == text + "\n\n作者补充报告。"
    snapshot = draft.snapshot()
    assert snapshot["complete"] and not draft.claims_blocked
    assert snapshot["claim_diagnostics"][0]["code"] == "identity_claim"
    assert snapshot["rendered_revision"] == snapshot["agent_revision"] == snapshot["revision"]


def test_new_submission_updates_diagnostics_instead_of_retaining_old_warning():
    draft = SubmissionDraft()
    draft.apply({"answer": "他是我们的殖民者。"})
    assert draft.claim_diagnostics
    draft.apply({"answer": "本次没有取得派系字段。"})
    assert draft.claim_diagnostics == [] and draft.snapshot()["complete"]


def test_claim_warning_does_not_expand_a_pure_length_repair():
    run = session()
    run.messages.append({"role": "tool", "content": "LARGE ORIGINAL EVIDENCE"})
    detail = "已保存完整报告。"
    with pytest.raises(SubmissionPatchError) as caught:
        run.submission.apply({"answer": "他是我们的殖民者。" * 100, "detail": detail})
    assert caught.value.code == "handoff_over_budget" and run.submission.claim_diagnostics
    assert run.submission.full_text and run.submission.agent_text is None
    assert run.submission.rendered_revision == run.submission.revision
    repair = run.length_repair_messages()
    assert repair and "LARGE ORIGINAL EVIDENCE" not in str(repair) and detail not in str(repair)
    result = run.submission.apply({"answer": "派系未读，身份未验证。"})
    assert result["full_text"].endswith(detail) and run.submission.snapshot()["complete"]


@pytest.mark.asyncio
async def test_tool_records_claim_diagnostics_without_requesting_another_round():
    draft = SubmissionDraft()
    updates = []
    tool = make_submission_tool(draft, [observation_without_identity()], lambda *args: updates.append(args))
    result = await tool.handler({"answer": "他是我们的殖民者。"})
    assert tool.terminal and result["delivery"]["claim_warning_count"] == 1
    snapshot, event, details = updates[-1]
    assert event == "submission_patch_accepted"
    assert snapshot["claim_diagnostics"] == details["claim_diagnostics"]


def test_invalid_mixed_patch_retains_earlier_matched_report_and_callback():
    run = session()
    draft = run.submission
    draft.apply({"answer": "旧结论。", "detail": "旧详情。"})
    old_revision = draft.revision
    with pytest.raises(SubmissionPatchError):
        draft.apply({"answer": "新结论。", "detail": 7})
    assert draft.answer == "新结论。" and draft.detail == "旧详情。"
    assert draft.revision > old_revision
    assert draft.full_text == "旧结论。\n\n旧详情。" and draft.agent_text == "旧结论。"
    assert draft.rendered_revision == draft.agent_revision == old_revision
    assert not draft.snapshot()["complete"]
    retained = AgentRuntime._attach_handoff({"status": "degraded", "answer": run.fallback_answer}, run)
    assert retained["summary"] == "旧结论。" and retained["status"] == "degraded"
    assert retained["delivery"]["submission_mode"] == "retained_submission"
    current = draft.apply({"detail": "新详情。"})
    assert current["full_text"] == "新结论。\n\n新详情。"
    assert draft.rendered_revision == draft.agent_revision == draft.revision


def test_later_oversized_answer_preserves_new_full_text_without_old_summary():
    run = session()
    draft = run.submission
    draft.apply({"answer": "旧结论。", "detail": "保留详情。"})
    new_answer = "最新结论和条件。" * 100
    with pytest.raises(SubmissionPatchError):
        draft.apply({"answer": new_answer})
    assert draft.full_text == new_answer + "\n\n保留详情。"
    assert draft.agent_text is None and draft.agent_revision is None
    retained = AgentRuntime._attach_handoff({"status": "degraded", "answer": run.fallback_answer}, run)
    assert retained["delivery"]["submission_mode"] == "operational_fallback"
    assert retained["answer"] == draft.full_text


def test_incomplete_or_different_report_never_borrows_a_saved_callback():
    run = session()
    run.submission.apply({"answer": "已写出的结论。"})
    result = AgentRuntime._attach_handoff({"status": "degraded", "answer": "另一份未完成结果。"}, run)
    assert result["delivery"]["submission_mode"] == "operational_fallback"
    empty = session()
    empty.last_assistant_content = "进度说明。"
    result = AgentRuntime._attach_handoff({"status": "degraded", "answer": empty.fallback_answer}, empty)
    assert result["delivery"]["submission_mode"] == "operational_fallback"
    assert not empty.submission.full_text


def test_combined_transport_overflow_keeps_prior_rendered_pair():
    run = session()
    draft = run.submission
    draft.apply({"answer": "旧结论。", "detail": "旧详情。"})
    old_revision = draft.revision
    with pytest.raises(SubmissionPatchError) as caught:
        draft.apply({"answer": "a" * 7000, "detail": "b" * 7000})
    assert caught.value.code == "submission_too_large"
    assert len(draft.answer) == len(draft.detail) == 7000
    assert draft.full_text == "旧结论。\n\n旧详情。" and draft.agent_text == "旧结论。"
    assert draft.rendered_revision == draft.agent_revision == old_revision
    assert not draft.snapshot()["complete"] and run.length_repair_messages() is None


def test_rendered_and_callback_revision_mismatch_cannot_be_reused():
    run = session()
    run.submission.apply({"answer": "作者结论。"})
    run.submission.agent_revision += 1
    result = AgentRuntime._attach_handoff({"status": "degraded", "answer": run.fallback_answer}, run)
    assert result["delivery"]["submission_mode"] == "operational_fallback"
