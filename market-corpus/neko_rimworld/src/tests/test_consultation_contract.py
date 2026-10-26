"""The single consultation protocol rejects removed APIs and saved answer shapes."""

import inspect

import pytest
from plugin.plugins.neko_rimworld.agent.delivery.handoff import (
    SubmissionDraft,
    make_submission_tool,
)
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditRegistry
from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
from plugin.plugins.neko_rimworld.tool_contracts import ToolExecutionError

from .test_agent import FakeLogger, settings


@pytest.mark.parametrize("answer", [[{"text": "旧形状"}], {"text": "旧形状"},
                                    [{"observation_id": "old", "paths": ["/count"]}], 12, None])
async def test_removed_answer_shapes_cannot_replace_a_saved_text(answer):
    draft = SubmissionDraft()
    draft.apply({"answer": "有效结论。", "detail": "完整事实和条件。"})
    with pytest.raises(ToolExecutionError) as caught:
        await make_submission_tool(draft, []).handler({"answer": answer})
    assert caught.value.code == "invalid_submission"
    assert draft.answer == "有效结论。" and draft.detail == "完整事实和条件。"
    assert not draft.snapshot()["complete"]


async def test_length_only_repair_preserves_the_full_saved_detail():
    draft = SubmissionDraft()
    tool = make_submission_tool(draft, [])
    detail = "保留完整事实、角色、阶段与范围。" * 100
    with pytest.raises(ToolExecutionError) as caught:
        await tool.handler({"answer": " x" * 200, "detail": detail})
    assert caught.value.code == "handoff_over_budget"
    assert caught.value.details["required_fields"] == ["answer"]
    result = await tool.handler({"answer": "简短结论，仍需核对条件。"})
    assert result["full_text"] == result["agent_text"] + "\n\n" + detail
    assert draft.detail == detail and draft.snapshot()["complete"]


def test_execution_and_submission_have_one_current_contract():
    assert set(inspect.signature(AgentRuntime).parameters) == {
        "tools_provider", "audit_registry", "logger", "model_factory", "report_sink",
    }
    assert set(inspect.signature(AgentRuntime.run).parameters) == {"self", "question", "settings", "capabilities"}
    assert set(inspect.signature(AgentRuntime.execute).parameters) == {"self", "session", "settings"}
    assert set(inspect.signature(make_submission_tool).parameters) == {"draft", "observations", "on_update", "answer_only"}
    assert set(inspect.signature(SubmissionDraft.apply).parameters) == {"self", "patch", "observations"}
    schema = make_submission_tool(SubmissionDraft(), []).parameters
    assert schema["properties"]["answer"]["type"] == "string"


async def test_an_uncompilable_connection_stops_before_model_dispatch():
    from plugin.plugins.neko_rimworld.tool_contracts import ToolSetup

    class Model:
        calls = 0

        async def complete(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("Unsupported connection must not dispatch")

    model = Model()
    async def unsupported():
        return ToolSetup(())
    runtime = AgentRuntime(tools_provider=unsupported, audit_registry=AgentAuditRegistry(),
                           logger=FakeLogger(), model_factory=lambda _: model)
    result = await runtime.run("读取当前状态。", settings())
    assert model.calls == 0 and result["status"] == "degraded"
    assert result["audit"]["error_code"] == "semantic_surface_unavailable"


def test_claims_are_mandatory_diagnostics_and_cannot_be_disabled():
    draft = SubmissionDraft()
    result = draft.apply({"answer": "Bob是我们的殖民者。"})
    assert result["agent_text"] == "Bob是我们的殖民者。"
    assert draft.claim_diagnostics[0]["code"] == "identity_claim"
    assert draft.claims_blocked is False and draft.snapshot()["complete"]
    with pytest.raises(TypeError):
        draft.apply({"answer": "Bob是我们的殖民者。"}, claims_supported=False)
