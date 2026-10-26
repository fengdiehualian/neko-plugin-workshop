from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditRegistry
from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
from plugin.plugins.neko_rimworld.game.observation import Observation
from plugin.plugins.neko_rimworld.game.protocol import decode_envelope
from plugin.plugins.neko_rimworld.game.query_tools import CatalogTools, make_game_tools
from plugin.plugins.neko_rimworld.message_contracts import AssistantTurn, ToolCall
from plugin.plugins.neko_rimworld.tool_contracts import ToolExecutionError

from .test_agent import FakeLogger, settings, submission_call

FIXTURES = Path(__file__).parent / "fixtures"


def test_nonreference_prompt_separates_short_conclusion_from_complete_facts():
    from plugin.plugins.neko_rimworld.agent.runtime.prompt import build_system_prompt

    prompt = build_system_prompt()
    assert "Finish through submit_response" in prompt
    assert "detail contains only the remaining findings" in prompt
    assert "maximum 180" in prompt and "12000 characters" in prompt
    assert "Pure factual answers can contain only reference parts" not in prompt


def catalog():
    return json.loads((FIXTURES / "current_query_catalog.json").read_text(encoding="utf-8-sig"))


def response():
    return decode_envelope((FIXTURES / "template_result.json").read_bytes())


@pytest.mark.asyncio
async def test_public_query_replaces_legacy_tools_without_changing_observations():
    source = catalog()
    source["public_query"] = {
        "version": "public_query.v2",
        "parameters": {"type": "object", "properties": {"requests": {"type": "array"}}},
        "rules": "canonical fields",
        "roots": [],
    }
    source["public_query"]["field_notes"] = {
        "HealthSummary": {"health_percent": "A native aggregate, not a causal explanation."}
    }
    calls = []

    async def query(arguments):
        calls.append(copy.deepcopy(arguments))
        return Observation.from_response(arguments["operation"], arguments["arguments"], response())

    setup = make_game_tools(source, query)
    assert [t.name for t in setup.tools] == ["query", "survey_space", "inspect_route", "inspect_state"]
    arguments = {"requests": [{"from": "things", "aggregate": {"units": {"op": "sum", "field": "stack_count"}}}]}
    observed = await setup.tools[0].handler(arguments)
    assert calls == [{"operation": "query", "arguments": arguments}]
    assert observed.value["response"] == response().to_dict()
    assert setup.directory["field_notes"] == source["public_query"]["field_notes"]
    assert "Use coverage before interpreting missing values" in setup.tools[0].description


def _public_source(**public):
    source = catalog()
    source["public_query"] = {
        "version": "public_query.v2",
        "parameters": {"type": "object", "properties": {"requests": {"type": "array"}}},
        "rules": "canonical fields",
        "roots": [],
    }
    source["public_query"].update(public)
    return source


async def _noop_query(arguments):
    raise AssertionError("no query expected")












@pytest.mark.asyncio
async def test_catalog_cache_singleflight_reconnect_and_bound_tools():
    class Bridge:
        connection_key = "1" * 32

    bridge = Bridge()
    requests = []

    async def query(arguments):
        requests.append(arguments)
        await asyncio.sleep(0)
        raw = response().to_dict()
        raw["connection_epoch"] = bridge.connection_key
        raw["payload"] = {"status": "ok", "issues": [], "data": _public_source()}
        return Observation({"response": raw})

    provider = CatalogTools(lambda: bridge, query)
    a, b = await asyncio.gather(provider.load(), provider.load())
    assert len(requests) == 1
    assert a.directory == b.directory
    bridge.connection_key = "8" * 32
    with pytest.raises(ToolExecutionError, match="connection changed"):
        await a.tools[0].handler({"requests": []})
    await provider.load()
    assert len(requests) == 2
    bridge.connection_key = None
    offline = await provider.load()
    assert not offline.tools and offline.directory is None


@pytest.mark.asyncio
async def test_catalog_failure_does_not_publish_stale_descriptions():
    class Bridge:
        connection_key = "1" * 32

    async def query(_):
        raise ToolExecutionError("catalog_unavailable", "No game loaded.")

    provider = CatalogTools(lambda: Bridge(), query)
    setup = await provider.load()
    assert not setup.tools and setup.directory is None




@pytest.mark.asyncio
async def test_terminal_batch_cannot_submit_before_observation_is_seen():
    from .test_capability_composition import native_observation

    async def query(args):
        return native_observation(args["arguments"])

    setup = make_game_tools(catalog(), query)

    async def provide():
        return setup

    class Model:
        count = 0

        async def complete(self, messages, tools, *, timeout):
            self.count += 1
            if self.count == 1:
                return AssistantTurn(
                    "r",
                    "m",
                    "tool_calls",
                    "",
                    (
                        ToolCall("q", "count", '{"object":{"kind":"things"},"metric":"records"}'),
                        submission_call("Premature", "Premature"),
                    ),
                )
            results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
            assert results[-1]["error"]["code"] == "terminal_tool_must_be_alone"
            observed = next(json.loads(m["content"]) for m in messages if m.get("name") == "count")
            assert observed["ok"] and "data" in observed
            return AssistantTurn("r", "m", "tool_calls", "", (submission_call("Observed", "Observed"),))

    runtime = AgentRuntime(
        tools_provider=provide,
        audit_registry=AgentAuditRegistry(),
        logger=FakeLogger(),
        model_factory=lambda _: Model(),
    )
    result = await runtime.run("Inspect", settings())
    assert result["answer"] == "Observed" and result["audit"]["model_calls"] == 2
    assert len(result["observations"]) == 1








def test_retired_catalogs_fail_without_querying_or_publishing_tools():
    for source in (_public_source(version="public_query.v1"), _public_source(version="unsupported")):
        with pytest.raises(ToolExecutionError) as caught:
            make_game_tools(source, _noop_query)
        assert caught.value.code == "semantic_surface_unavailable"
        assert caught.value.recoverable is False


def test_public_catalog_does_not_require_retired_template_contracts():
    source = _public_source()
    for key in ("schema", "query_rules", "template_parameters", "query_parameters", "type_index", "query_roots"):
        source.pop(key, None)
    CatalogTools._validate(source)
    assert [tool.name for tool in make_game_tools(source, _noop_query).tools] == ["query", "survey_space", "inspect_route", "inspect_state"]
