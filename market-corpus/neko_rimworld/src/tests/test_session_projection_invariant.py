"""Current projection is mandatory; registry output is never compiled twice."""

import copy

import pytest
from plugin.plugins.neko_rimworld.agent.context.context_pruning import ContextPruner
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditLedger
from plugin.plugins.neko_rimworld.agent.runtime.session import AgentRunSession
from plugin.plugins.neko_rimworld.agent.runtime.tools import ToolRegistry
from plugin.plugins.neko_rimworld.message_contracts import ToolCall, ToolResult
from plugin.plugins.neko_rimworld.tool_contracts import ToolSpec


class CountingPruner(ContextPruner):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def compile(self, *args, **kwargs):
        self.calls += 1
        return super().compile(*args, **kwargs)


def session(**kwargs):
    return AgentRunSession(
        ledger=AgentAuditLedger("question"), question="question",
        messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "question"}],
        **kwargs,
    )


@pytest.mark.parametrize("invalid", [None, False, {}, object()])
def test_invalid_supplied_pruner_is_explicitly_rejected(invalid):
    with pytest.raises(TypeError, match="current ContextPruner"):
        session(context_pruner=invalid)


def test_default_pruner_is_per_session_and_supplied_current_pruner_is_preserved():
    first, second = session(), session()
    assert isinstance(first.context_pruner, ContextPruner)
    assert first.context_pruner is not second.context_pruner
    supplied = CountingPruner()
    assert session(context_pruner=supplied).context_pruner is supplied


@pytest.mark.asyncio
async def test_registry_projection_is_used_once_with_raw_results_metrics_and_guide_intact():
    pruner = CountingPruner()
    run = session(context_pruner=pruner)
    registry = ToolRegistry(context_pruner=pruner)
    data = {"scope": {"actor": "Pawn_1", "user": "Pawn_2", "map": "Map_1"},
            "sample": {"start_tick": 10, "end_tick": 11}, "status": "partial",
            "issues": [{"code": "unavailable", "path": "health"}], "rows": [{"id": "Pawn_1"}]}
    original = copy.deepcopy(data)

    async def read(arguments):
        return data

    registry.register(ToolSpec("look", "read", {"type": "object"}, read))
    result = await registry.execute(registry.prepare(ToolCall("call_1", "look", "{}")))
    raw_result = copy.deepcopy(result.value)
    message = run.tool_message(result)
    assert pruner.calls == 1
    assert message == result.as_message()
    assert data == original and result.value == raw_result
    assert run.evidence_views == [{"call_id": "call_1", "tool": "look", **result.model_view_metrics}]
    before = copy.deepcopy(run.messages)
    request = run.request_messages("advisory")
    assert request[0]["content"] == "system\n" + pruner.guide
    assert request[1:] == [*before[1:], {"role": "system", "content": "advisory"}]
    assert run.request_messages("advisory") == request and run.messages == before


def test_uncompiled_context_result_projects_and_invalid_mutation_does_not_bypass_it():
    run = session(context_pruner=CountingPruner())
    result = ToolResult("call_1", "look", False, {"ok": False, "error": {"code": "unavailable"}})
    value = copy.deepcopy(result.value)
    assert run.tool_message(result)["role"] == "tool"
    assert run.context_pruner.calls == 1 and len(run.evidence_views) == 1
    assert result.value == value
    run.context_pruner = None  # Unsupported mutation cannot activate a raw mode.
    with pytest.raises(AttributeError):
        run.tool_message(result)


@pytest.mark.parametrize("name,terminal", [("wiki", False), ("submit_response", True), ("look", True)])
def test_noncontext_and_terminal_results_keep_their_contract(name, terminal):
    run = session(context_pruner=CountingPruner())
    result = ToolResult("call_1", name, True, {"ok": True, "data": {"text": "value"}}, terminal=terminal)
    assert run.tool_message(result) == result.as_message()
    assert run.context_pruner.calls == 0 and run.evidence_views == []
