"""Actual current catalog gates old/incomplete connections before model dispatch."""
import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from plugin.plugins.neko_rimworld.agent.runtime.audit import AgentAuditRegistry
from plugin.plugins.neko_rimworld.agent.runtime.loop import AgentRuntime
from plugin.plugins.neko_rimworld.agent.world.semantic_tools import semantic_interaction_factory
from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools
from plugin.plugins.neko_rimworld.tool_contracts import ToolExecutionError

from .test_agent import FakeLogger, settings

SOURCE=Path(__file__).parent/"fixtures/current_query_catalog.json"


def current():return json.loads(SOURCE.read_text(encoding="utf-8"))


def state(source):return next(tool for tool in source["world_observation"]["tools"] if tool["name"]=="inspect_state")


async def forbidden(request):raise AssertionError("Gate inspection never dispatches native reads")


def mutations():
    return ["public_v1","public_unknown","world_v22","world_unknown","world_missing","state_missing",
            "targets","stats","actor","user","sections","include_fogged","detail_limit","exact_lookup",
            "section_identity","section_health","section_skills","section_gear","section_work","section_state",
            "bad_properties","bad_sections","bad_section_items"]


def altered(change):
    source=current()
    if change.startswith("public_"):
        source["public_query"]["version"]="public_query.v1" if change=="public_v1" else "public_query.v99"
    elif change.startswith("world_"):
        if change=="world_missing":
            source.pop("world_observation")
        else:
            source["world_observation"]["version"]="world_observation.v2.2" if change=="world_v22" else "world_observation.v99"
    elif change=="state_missing":
        source["world_observation"]["tools"]=[tool for tool in source["world_observation"]["tools"] if tool["name"]!="inspect_state"]
    elif change.startswith("section_"):
        state(source)["parameters"]["properties"]["sections"]["items"]["enum"].remove(change.removeprefix("section_"))
    elif change=="bad_properties":
        state(source)["parameters"]["properties"]=None
    elif change=="bad_sections":
        state(source)["parameters"]["properties"]["sections"]="wrong"
    elif change=="bad_section_items":
        state(source)["parameters"]["properties"]["sections"]["items"]=None
    else:
        state(source)["parameters"]["properties"].pop(change)
    return source


@pytest.mark.parametrize("change",mutations())
@pytest.mark.asyncio
async def test_old_or_incomplete_connection_has_zero_model_or_native_dispatch(change):
    calls=[]
    class Model:
        async def complete(self,*args,**kwargs):
            calls.append("model")
            raise AssertionError("Rejected connection cannot dispatch a model")
    async def provider():return make_game_tools(altered(change),forbidden)
    runtime=AgentRuntime(tools_provider=provider,audit_registry=AgentAuditRegistry(),logger=FakeLogger(),model_factory=lambda settings:Model())
    result=await runtime.run("检查当前读接口",settings())
    assert result["status"]!="ok" and calls==[]
    assert result["audit"]["model_calls"]==0
    expected = "invalid_catalog" if change == "state_missing" else "semantic_surface_unavailable"
    assert result["audit"]["error_code"] == expected


@pytest.mark.parametrize("change", ["public_v1", "world_v22", "world_missing", "state_missing", "bad_properties"])
def test_factory_rejects_stale_or_incomplete_prepared_setup_without_native_dispatch(change):
    setup = make_game_tools(current(), forbidden)
    directory = copy.deepcopy(setup.directory)
    if change == "public_v1":
        directory["public_query_version"] = "public_query.v1"
    elif change == "world_v22":
        directory["world_observation"]["version"] = "world_observation.v2.2"
    elif change == "world_missing":
        directory.pop("world_observation")
    else:
        tools = directory["world_observation"]["tools"]
        if change == "state_missing":
            directory["world_observation"]["tools"] = [tool for tool in tools if tool["name"] != "inspect_state"]
        else:
            next(tool for tool in tools if tool["name"] == "inspect_state")["parameters"]["properties"] = None
    with pytest.raises(ToolExecutionError) as rejected:
        semantic_interaction_factory(replace(setup, directory=directory), object(), None)
    assert rejected.value.code == "semantic_surface_unavailable" and not rejected.value.recoverable


def test_actual_current_contract_exposes_all_current_reading_families():
    setup=make_game_tools(current(),forbidden)
    # Test the actual admission path; no separate boolean approximation.
    interaction=semantic_interaction_factory(setup,type("Session",(),{})(),lambda *args:None)
    names={tool.name for tool in interaction.read_tools()}
    assert {"look","count","survey_space","evaluate_activity"}<=names
    assert not {"query","inspect_state","inspect_route","inspect_space"}&names
    look=next(tool for tool in interaction.read_tools() if tool.name=="look")
    aspects=set(look.parameters["properties"]["aspects"]["items"]["enum"])
    assert {"health","skills","gear","work","state","mood","profile","resources","distribution"}<=aspects
