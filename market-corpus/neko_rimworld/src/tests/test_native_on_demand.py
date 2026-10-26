"""world_observation.v2.3: route display selection becomes native generation.

Pins the forwarding boundary end to end on the plugin side: which requests
gain the native `objects` flag, which connections must stay byte-identical,
while old contracts are rejected before consultation dispatch.
"""

import copy
from types import SimpleNamespace

import pytest
from plugin.plugins.neko_rimworld.agent.world.native_state import state_contract
from plugin.plugins.neko_rimworld.agent.world.semantic_tools import SemanticInteraction
from plugin.plugins.neko_rimworld.tool_contracts import WORLD_OBSERVATION_VERSION


def route_recorder(version):
    """Unbound _world_read harness mirroring the feasibility audit dispatch control."""
    calls = []

    async def execute_native(args, *, tool_name="query"):
        calls.append((tool_name, copy.deepcopy(args)))
        return {
            "status": "ok",
            "observation_id": "obs-1",
            "issues": [],
            "data": {"route": {"status": "found", "steps": 7}},
        }

    stub = SimpleNamespace(
        original={"inspect_route": object(), "survey_space": object()},
        setup=SimpleNamespace(directory={"world_observation": {"version": version}}),
        execute_native=execute_native,
    )
    return stub, calls


ROUTE = {"actor": {"kind": "pawn", "id": "Pawn_1"}, "to": {"kind": "thing", "id": "Thing_Bed1"}}


@pytest.mark.asyncio
async def test_v23_route_without_include_requests_no_native_objects():
    stub, calls = route_recorder("world_observation.v2.3")
    await SemanticInteraction._world_read(stub, {**ROUTE, "include": ["environment"]}, "inspect_route")
    tool_name, args = calls[0]
    assert tool_name == "inspect_route"
    assert args["objects"] is False
    assert "include" not in args and "include_objects" not in args


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested",
    [
        {"include_objects": True},
        {"include": ["objects"]},
        {"include": ["environment", "objects"], "include_objects": False},
        {"cursor": "page:2"},
    ],
)
async def test_v23_route_keeps_native_objects_when_the_view_keeps_them(requested):
    stub, calls = route_recorder("world_observation.v2.3")
    await SemanticInteraction._world_read(stub, {**ROUTE, **requested}, "inspect_route")
    assert calls[0][1]["objects"] is True


@pytest.mark.asyncio
async def test_v23_survey_explicit_environment_selection_avoids_incidental_objects():
    stub, calls = route_recorder("world_observation.v2.3")
    await SemanticInteraction._world_read(
        stub, {"region": {"map": "current"}, "include": ["environment"]}, "survey_space"
    )
    assert calls[0][1]["objects"] is False and "include" not in calls[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["world_observation.v2.3"])
async def test_current_survey_default_remains_full_discovery(version):
    stub, calls = route_recorder(version)
    await SemanticInteraction._world_read(stub, {"region": {"map": "current"}}, "survey_space")
    assert calls[0][1] == {"region": {"map": "current"}}



@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"objects": True}, {"include": ["objects"]}, {"cursor": "next-page"}])
async def test_survey_object_opt_in_and_paging_remain_available(extra):
    stub, calls = route_recorder("world_observation.v2.3")
    await SemanticInteraction._world_read(stub, {"region": {"map": "current"}, "include": ["environment"], **extra}, "survey_space")
    assert calls[0][1]["objects"] is True



def state_directory(version):
    from .consultation_setup import consultation_setup
    directory = copy.deepcopy(consultation_setup().directory)
    directory['world_observation']['version'] = version
    return directory


@pytest.mark.parametrize("version", [WORLD_OBSERVATION_VERSION])
def test_state_contract_accepts_every_supported_version(version):
    assert state_contract(state_directory(version))["name"] == "inspect_state"


def test_unknown_contract_versions_are_refused():
    assert state_contract(state_directory("world_observation.v9.9")) is None
    assert state_contract({}) is None


@pytest.mark.asyncio
async def test_activity_objects_selection_reaches_the_route_request():
    from plugin.plugins.neko_rimworld.agent.world.activity import SemanticActivityDataSource, evaluate_activity
    from plugin.plugins.neko_rimworld.tests.test_activity import NativeInteraction

    arguments = {
        "kind": "transfer",
        "actors": [{"kind": "pawn", "id": "a", "map": "Map_0"}],
        "subject": {"kind": "pawn", "id": "patient", "map": "Map_0"},
        "destinations": [{"kind": "thing", "id": "bed1", "map": "Map_0"}],
    }
    native = NativeInteraction()
    await evaluate_activity(arguments, SemanticActivityDataSource(native))
    routes = [args for op, args in native.calls if op == "inspect_route"]
    assert routes and all("include_objects" not in args for args in routes)

    native = NativeInteraction()
    await evaluate_activity({**arguments, "include": ["objects"]}, SemanticActivityDataSource(native))
    routes = [args for op, args in native.calls if op == "inspect_route"]
    assert routes and all(args.get("include_objects") is True for args in routes)
