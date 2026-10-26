"""Production semantic vocabulary composes with caller-owned work abilities."""

import copy
import json
from dataclasses import replace

import pytest
from plugin.plugins.neko_rimworld.agent.model.model_client import ModelClientError
from plugin.plugins.neko_rimworld.agent.runtime import loop
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditLedger, AgentAuditRegistry
from plugin.plugins.neko_rimworld.agent.runtime.execution_environment import compose_tools
from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
from plugin.plugins.neko_rimworld.agent.runtime.session import AgentRunSession
from plugin.plugins.neko_rimworld.game.observation import Observation
from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools
from plugin.plugins.neko_rimworld.message_contracts import ToolCall
from plugin.plugins.neko_rimworld.tool_contracts import ToolSpec

from .test_agent import FakeLogger, settings, turn
from .test_semantic_tools import public_source


class Script:
    def __init__(self, turns, mutate=None):
        self.turns = list(turns)
        self.requests = []
        self.mutate = mutate

    async def complete(self, messages, tools, *, timeout):
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        if self.mutate and len(self.requests) == 1:
            self.mutate()
        return self.turns.pop(0)


def call(name, args):
    return turn("", ToolCall(name, name, json.dumps(args)))


def native_observation(request):
    return Observation(
        {
            "observation_id": "native-current",
            "received_at": "2026-10-03T00:00:00Z",
            "request": {"operation": "query", "arguments": request},
            "response": {
                "colony_id": "colony",
                "load_epoch": "current",
                "game_tick": 10,
                "snapshot_sequence": 1,
                "payload": {
                    "status": "ok",
                    "issues": [],
                    "data": {
                        "sample": {"load_epoch": "current", "game_tick": 10, "sequence": 1},
                        "results": [
                            {
                                "from": "things",
                                "type": "Thing",
                                "rows": [],
                                "aggregates": {"records": 3, "units": 28},
                                "complete": True,
                                "page": {"has_more": False},
                            }
                        ],
                    },
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_default_game_surface_reads_and_workspace_ability_coexist():
    reads = []
    history_reads = []

    async def native(args):
        reads.append(args)
        return native_observation(args)

    async def history(args):
        history_reads.append(args)
        return {"historical": True, "current_observation": False, "note": "Earlier investigation"}

    setup = make_game_tools(public_source(), native)
    capabilities = (ToolSpec("workspace_history", "Read historical work", {"type": "object"}, history),)

    async def provide():
        return setup

    model = Script(
        [
            call("count", {"object": {"kind": "things"}, "metric": "units"}),
            call("workspace_history", {}),
            call("submit_response", {"answer": "Current and historical evidence remain separate."}),
        ]
    )
    runtime = AgentRuntime(

        tools_provider=provide,
        audit_registry=AgentAuditRegistry(),
        logger=FakeLogger(),
        model_factory=lambda _: model,

    )
    result = await runtime.run("Read current quantities and reopen previous work.", settings(), capabilities=capabilities)
    assert result["status"] == "ok" and len(reads) == len(history_reads) == 1
    assert len(result["observations"]) == 1
    for request in model.requests:
        names = {t["function"]["name"] for t in request["tools"]}
        assert {"look", "count", "workspace_history", "submit_response"} <= names
        assert "query" not in names and "canonical fields" not in request["messages"][0]["content"]
    assert result["observations"][0]["response"]["load_epoch"] == "current"


@pytest.mark.asyncio
async def test_admitted_session_keeps_its_surface_but_new_session_rejects_retired_environment(monkeypatch):
    monkeypatch.delenv("NEKO_CONTEXT_PRUNING", raising=False)
    session = AgentRunSession.create(
        ledger=AgentAuditLedger("Keep the policy"), question="Keep the policy")

    async def native(args):
        return native_observation(args)

    async def provide():
        return make_game_tools(public_source(), native)

    def change():
        monkeypatch.setenv("NEKO_CONTEXT_PRUNING", "1")

    model = Script(
        [
            call("count", {"object": {"kind": "things"}, "metric": "units"}),
            call("submit_response", {"answer": "Execution policy stayed fixed."}),
        ],
        mutate=change,
    )
    runtime = AgentRuntime(

        tools_provider=provide,
        audit_registry=AgentAuditRegistry(),
        logger=FakeLogger(),
        model_factory=lambda _: model,

    )
    result = await runtime.execute(session, settings())
    assert result["status"] == "ok" and session.context_pruner is not None
    assert model.requests[0]["tools"] == model.requests[1]["tools"]
    assert model.requests[1]["messages"][0]["content"] == model.requests[0]["messages"][0]["content"] + "\n" + session.context_pruner.guide
    with pytest.raises(ValueError, match="NEKO_CONTEXT_PRUNING is retired"):
        AgentRunSession.create(ledger=AgentAuditLedger("new"), question="new")


def test_colliding_capabilities_are_not_silently_replaced():
    async def handler(args):
        return {}

    spec = ToolSpec("look", "game read", {}, handler)
    with pytest.raises(ValueError, match="distinct"):
        compose_tools((spec,), (spec,))


@pytest.mark.asyncio
async def test_current_semantic_dispatch_respects_the_model_attempt_limit(monkeypatch):
    class Failure(Script):
        async def complete(self, messages, tools, *, timeout):
            self.requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
            raise ModelClientError("model_transport", "Offline transport failure")

    monkeypatch.setattr(loop, "MAX_MODEL_ATTEMPTS", 1)
    model = Failure([])

    async def provide():
        async def native(args):
            raise AssertionError("A failed model cannot dispatch a native read")

        return make_game_tools(public_source(), native)

    runtime = AgentRuntime(

        tools_provider=provide,
        audit_registry=AgentAuditRegistry(),
        logger=FakeLogger(),
        model_factory=lambda _: model,

    )
    result = await runtime.run("Offline study fixture", replace(settings(), finalization_reserve_seconds=0))
    assert result["status"] == "degraded" and len(model.requests) == 1
    names = {tool["function"]["name"] for tool in model.requests[0]["tools"]}
    assert {"look", "count", "submit_response"} <= names and "query" not in names


def test_duplicate_caller_tool_is_rejected_by_the_actual_session_registry():
    from plugin.plugins.neko_rimworld.agent.runtime.execution_environment import ExecutionEnvironment

    async def handler(args):
        raise AssertionError("Duplicate admission must not dispatch any tool")

    setup = make_game_tools(public_source(), handler)
    session = AgentRunSession.create(
        ledger=AgentAuditLedger("duplicate"), question="duplicate", capabilities=(ToolSpec("look", "Caller collision", {}, handler),),
    )
    environment = ExecutionEnvironment(setup, session, lambda _: None)
    with pytest.raises(ValueError, match="distinct"):
        environment.registry()
