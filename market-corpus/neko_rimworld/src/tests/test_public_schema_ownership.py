"""Final public contracts are business-owned before neutral serialization."""

import copy
import json
from pathlib import Path

import pytest
from plugin.plugins.neko_rimworld.agent.context.context_tools import model_tools
from plugin.plugins.neko_rimworld.agent.delivery.handoff import SubmissionDraft, make_submission_tool
from plugin.plugins.neko_rimworld.agent.world.native_state import state_contract
from plugin.plugins.neko_rimworld.agent.world.semantic_tools import SemanticInteraction
from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools
from plugin.plugins.neko_rimworld.tool_contracts import ToolExecutionError

from .test_semantic_tools import Session, _update


def current_interaction():
    calls = []

    async def forbidden(args):
        calls.append(args)
        raise AssertionError("No native transport is available in this contract test")

    catalog = json.loads((Path(__file__).parent / "fixtures/current_query_catalog.json").read_text(encoding="utf-8"))
    setup = make_game_tools(catalog, forbidden)
    return SemanticInteraction(setup, Session(), _update), calls


def test_context_serialization_preserves_owner_prose_and_is_independent_of_tool_name():
    original = {
        "type": "function",
        "function": {
            "name": "look",
            "description": "owner prose",
            "parameters": {
                "type": "object",
                "properties": {"field": {"type": "string", "description": "field prose"}},
                "required": ["field"],
                "additionalProperties": False,
            },
        },
    }
    renamed = copy.deepcopy(original)
    renamed["function"]["name"] = "arbitrary_tool"
    normal = model_tools([original])[0]
    other = model_tools([renamed])[0]
    assert normal["function"]["description"] == "owner prose"
    assert normal["function"]["parameters"]["properties"]["field"]["description"] == "field prose"
    other["function"]["name"] = "look"
    assert normal == other
    assert original["function"]["parameters"]["properties"]["field"]["description"] == "field prose"


def test_final_look_count_are_constructed_without_the_retired_middle_contract():
    interaction, _ = current_interaction()
    specs = {tool.name: tool for tool in interaction.read_tools()}
    for name in ("look", "count"):
        assert "allOf" not in specs[name].parameters
    assert "Pawn health/skills/gear/work" in specs["look"].parameters["properties"]["aspects"]["description"]
    assert (
        "production"
        not in specs["look"].parameters["properties"]["object"]["oneOf"][0]["properties"]["capabilities"]["items"][
            "enum"
        ]
    )
    assert "production" in specs["look"].parameters["properties"]["focus"]["properties"]["state"]["items"]["enum"]


@pytest.mark.asyncio
async def test_retired_production_selection_fails_explicitly_before_a_native_request():
    interaction, calls = current_interaction()
    tool = next(t for t in interaction.read_tools() if t.name == "count")
    with pytest.raises(ToolExecutionError, match="Unsupported capability selection"):
        await tool.handler({"object": {"kind": "things", "capabilities": ["production"]}, "metric": "records"})
    assert calls == []


def test_current_state_requires_native_facet_selection_without_optional_fallback():
    interaction, _ = current_interaction()
    directory = copy.deepcopy(interaction.setup.directory)
    native = state_contract(directory)
    del native["parameters"]["properties"]["facets"]
    assert state_contract(directory) is None


@pytest.mark.asyncio
async def test_answer_repair_uses_the_delivery_owner_and_retains_complete_saved_detail():
    draft = SubmissionDraft()
    normal = make_submission_tool(draft, [])
    repair = make_submission_tool(draft, [], answer_only=True)
    assert normal.description == repair.description
    assert normal.parameters["properties"]["answer"] == repair.parameters["properties"]["answer"]
    assert set(repair.parameters["properties"]) == {"answer"}
    await normal.handler({"answer": "建议先安排休息。", "detail": "完整请求的表格与条件保存在这里。"})
    saved = draft.detail
    await repair.handler({"answer": "先休息。"})
    assert draft.detail == saved


@pytest.mark.parametrize("advertisement", ["meaning", "enum"])
def test_same_version_retired_capability_advertisement_is_rejected(advertisement):
    interaction, _ = current_interaction()
    directory = copy.deepcopy(interaction.setup.directory)
    world = directory["world_observation"]
    if advertisement == "meaning":
        world["capability_meanings"]["production"] = "retired alias"
    else:
        survey = next(tool for tool in world["tools"] if tool["name"] == "survey_space")
        survey["parameters"]["properties"]["select"]["properties"]["capabilities"]["items"]["enum"].append("production")
    assert state_contract(directory) is None
    catalog = json.loads((Path(__file__).parent / "fixtures/current_query_catalog.json").read_text(encoding="utf-8"))
    catalog["world_observation"] = world
    with pytest.raises(ToolExecutionError, match="retired production selection capability"):
        make_game_tools(catalog, lambda arguments: None)
