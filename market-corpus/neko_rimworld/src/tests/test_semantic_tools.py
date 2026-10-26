"""The semantic surface is the sole shipped interface.

What matters here is the wiring: which connections get the closed-enum vocabulary,
that the Mod's directory really leaves the prompt, and that unsupported
directories stop before dispatch. The compile and render semantics are
covered by the surface's own tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from plugin.plugins.neko_rimworld.agent.delivery.handoff import SubmissionDraft
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditRegistry
from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
from plugin.plugins.neko_rimworld.agent.runtime.prompt import build_system_prompt
from plugin.plugins.neko_rimworld.agent.world.semantic_tools import (
    SemanticInteraction,
    semantic_interaction_factory,
)
from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools
from plugin.plugins.neko_rimworld.message_contracts import ToolCall
from plugin.plugins.neko_rimworld.tool_contracts import ToolExecutionError, ToolSetup

from .test_agent import FakeLogger, settings

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGUE_MARKER = "canonical fields"


def catalog():
    return json.loads((FIXTURES / "current_query_catalog.json").read_text(encoding="utf-8-sig"))


def public_source(*, directory=True):
    """A public_query catalog, with or without the directory the Surface needs."""
    source = catalog()
    parameters = {
        "type": "object",
        "properties": {
            "requests": {
                "type": "array",
                "items": {
                    "allOf": [
                        {
                            "oneOf": [
                                {"properties": {"from": {"const": "things"}, "args": {"type": "object"}}},
                                {"properties": {"from": {"const": "pawn"}, "args": {"type": "object"}}},
                            ]
                        }
                    ]
                },
            }
        },
    }
    public = {"version": "public_query.v2", "parameters": parameters, "rules": CATALOGUE_MARKER, "roots": []}
    if directory:
        public.update(
            roots=[
                {"from": "things", "type": "Thing", "description": "Map things."},
                {"from": "pawn", "type": "Pawn", "description": "One pawn."},
            ],
            field_index={
                "Thing": {
                    "id": "String",
                    "spawned": "Bool",
                    "stack_count": "Int",
                    "definition": "ThingDef",
                    "parent_holder": "Thing",
                },
                "Pawn": {"id": "String", "name": "String"},
            },
            common_fields=[],
            field_notes={},
        )
    source["public_query"] = public
    return source


async def _noop_query(arguments):
    raise AssertionError("no native read expected")


def setup_for(**kwargs) -> ToolSetup:
    return make_game_tools(public_source(**kwargs), _noop_query)


class Session:
    """Only the attributes the adapter reads while building tools or a prompt."""

    def __init__(self):
        self.submission = SubmissionDraft()
        self.observations = []


def _update(*_args, **_kwargs):
    return None


def test_semantic_surface_is_the_shipped_default():
    assert isinstance(semantic_interaction_factory(setup_for(), Session(), _update), SemanticInteraction)




def test_the_mod_directory_leaves_the_prompt_but_the_enums_stay():
    """The reason this interface exists: the whole field catalogue stops being sent."""
    setup = setup_for()
    assert setup.directory["rules"] == CATALOGUE_MARKER

    adapter = semantic_interaction_factory(setup, Session(), _update)
    assert isinstance(adapter, SemanticInteraction)
    prompt = build_system_prompt()

    assert CATALOGUE_MARKER not in prompt
    assert '"field_index"' not in prompt
    assert "Specify look.aspects" in prompt


def test_semantic_surface_replaces_the_query_vocabulary():
    adapter = semantic_interaction_factory(setup_for(), Session(), _update)
    assert [tool.name for tool in adapter.tools()] == ["look", "count", "survey_space", "evaluate_activity", "submit_response"]

    adapter.finalizing = True
    assert [tool.name for tool in adapter.tools()] == ["submit_response"]


def test_native_activity_is_default_and_replaces_overlapping_path_tools():
    source = public_source()
    adapter = semantic_interaction_factory(make_game_tools(source, _noop_query), Session(), _update)
    names = [tool.name for tool in adapter.tools()]
    assert "evaluate_activity" in names
    assert names.count("evaluate_activity") == 1
    assert "look" in names and "count" in names
    assert "inspect_route" not in names and "inspect_space" not in names






def test_a_mod_without_the_directory_is_rejected():
    setup = setup_for(directory=False)
    with pytest.raises(ToolExecutionError, match="[Cc]urrent|cannot be compiled") as rejected:
        semantic_interaction_factory(setup, None, None)
    assert rejected.value.code == "semantic_surface_unavailable" and not rejected.value.recoverable


def test_a_setup_without_a_query_tool_is_rejected():
    """The adapter may only read through the Mod's query handler."""
    setup = ToolSetup(tools=(), directory=public_source()["public_query"])
    with pytest.raises(ToolExecutionError, match="[Cc]urrent|cannot be compiled") as rejected:
        semantic_interaction_factory(setup, None, None)
    assert rejected.value.code == "semantic_surface_unavailable" and not rejected.value.recoverable


def test_a_directory_that_cannot_compile_is_refused_before_the_prompt_promises_it():
    source = public_source()
    del source["public_query"]["field_index"]
    setup = make_game_tools(source, _noop_query)
    assert setup.directory is not None and "field_index" not in setup.directory
    with pytest.raises(ToolExecutionError, match="[Cc]urrent|cannot be compiled") as rejected:
        semantic_interaction_factory(setup, None, None)
    assert rejected.value.code == "semantic_surface_unavailable" and not rejected.value.recoverable




@pytest.mark.asyncio
async def test_unsupported_semantic_connection_never_calls_the_model():
    class NeverCalled:
        async def complete(self, *args, **kwargs):
            raise AssertionError("Unsupported connection must stop before model dispatch")

    async def provider():
        return setup_for(directory=False)

    runtime = AgentRuntime( tools_provider=provider, audit_registry=AgentAuditRegistry(),
                           logger=FakeLogger(), model_factory=lambda _: NeverCalled(),
                           )
    result = await runtime.run("question", settings())
    assert result["status"] == "degraded"
    assert result["audit"]["error_code"] == "semantic_surface_unavailable"


class _Model:
    """Returns the scripted turns in order.

    `test_agent.ScriptedModel` asserts the first tool is the legacy `query_game`,
    which is not true on the public_query path this surface serves.
    """

    def __init__(self, turns):
        self._turns = list(turns)
        self.requests = []

    async def complete(self, messages, tools, *, timeout):  # noqa: ARG002
        self.requests.append({"messages": list(messages), "tools": list(tools)})
        return self._turns.pop(0)


@pytest.mark.asyncio
async def test_wiki_is_the_only_extra_passthrough_and_never_an_observation(tmp_path):
    from dataclasses import replace

    from plugin.plugins.neko_rimworld.agent.reference.wiki_tools import make_wiki_tool
    from plugin.plugins.neko_rimworld.agent.runtime.tools import ToolRegistry
    from plugin.plugins.neko_rimworld.tool_contracts import ToolSpec

    from .test_wiki_tools import store_from_directory, write_snapshot

    write_snapshot(tmp_path)
    wiki = make_wiki_tool(store_from_directory(tmp_path))
    forbidden = ToolSpec("unapproved", "not exposed", {}, _noop_query)
    setup = setup_for()
    setup = replace(setup, tools=(*setup.tools, wiki, forbidden))
    session = Session()
    adapter = semantic_interaction_factory(setup, session, _update)
    assert [tool.name for tool in adapter.tools()] == ["look", "count", "survey_space", "evaluate_activity", "wiki", "submit_response"]
    assert "wiki" in {tool.name for tool in adapter.tools()}
    registry = ToolRegistry()
    for tool in adapter.tools():
        registry.register(tool)
    result = await registry.execute(registry.prepare(ToolCall("wiki1", "wiki", '{"query":"Steel"}')))
    assert result.observation is None
    assert session.observations == []
    adapter.finalizing = True
    assert [tool.name for tool in adapter.tools()] == ["submit_response"]


def test_missing_wiki_does_not_advertise_knowledge():
    semantic_interaction_factory(setup_for(), Session(), _update)
    assert "wiki(query)" not in build_system_prompt()


def report():
    return {
        "report_id": "report-1",
        "state": "final",
        "created_at": "2026-09-01T00:00:00Z",
        "question": "早先那一问。",
        "full_text": "早先的完整报告。",
        "observations": [],
    }
