"""Saved text changes retain matching caller-provided detail."""
from plugin.plugins.neko_rimworld.agent.delivery.handoff import HANDOFF_TOKEN_BUDGET, SubmissionDraft


def test_text_revision_keeps_saved_detail_without_reconfirmation():
    draft = SubmissionDraft()
    draft.apply({"answer": "结论。", "detail": "完整报告与条件。"})
    updated = draft.apply({"answer": "补充条件后的结论。"})
    assert updated["agent_text"] == "补充条件后的结论。"
    assert draft.detail == "完整报告与条件。" and draft.snapshot()["complete"]
    assert HANDOFF_TOKEN_BUDGET == 180
