"""Regression contracts from the V2.1 eight-case trace, without model calls."""

import copy

import pytest
from plugin.plugins.neko_rimworld.agent.world import semantic_surface as surface
from plugin.plugins.neko_rimworld.agent.world.look_tools import look_spec

from .consultation_setup import consultation_setup


def test_blocked_definition_lookup_never_certifies_instance_count():
    native = {
        "observation_id": "definition-probe",
        "data": {
            "results": [
                {
                    "from": "thing_defs",
                    "state": "complete",
                    "fields": "complete",
                    "listing": "complete",
                    "matched": 0,
                    "known_matches": 0,
                    "rows": [],
                    "scope": {"args": {}, "notes": ["Loaded definitions, not physical quantities."]},
                }
            ]
        },
        "issues": [],
    }
    before = copy.deepcopy(native)
    resolution = {"status": "missing_in_scope", "field": "def_name"}
    output = surface.render_blocked(
        "count", {"kind": "things", "definition": "Thing_Silver2520", "map": "Map_0"}, resolution, native
    )
    assert output["lookup"]["source"] == "thing_defs"
    assert output["lookup"]["scope"]["args"] == {}
    assert output["lookup"]["coverage"]["matched"] == 0
    assert output["coverage"]["scan"] == "not_run"
    assert output["coverage"]["matched"] is None
    assert output["execution"] == "not_run" and output["measures"] == {}
    assert "not executed" in output["explanation"]
    assert native == before


def test_pawn_map_and_single_id_route_without_dropping_scope():
    obj = surface.normalize_object({"kind": "pawn", "id": "pawn1", "map": "map2"})
    root, args, where = surface.route_object(obj)
    assert root == "pawns" and args == {"map_id": "map2"}
    assert where == {"id": {"in": ["pawn1"]}}

    class Compiler:
        def compile(self, request):
            return request

    name = surface.normalize_object({"kind": "pawn", "name": "Alice", "map": "map2"})
    lookup = surface.lookup_spec(Compiler(), name)
    assert lookup["request"]["blocks"][0]["args"] == {"map_id": "map2"}
    with pytest.raises(surface.SurfaceError):
        surface.normalize_object({"kind": "pawn", "id": "a", "ids": ["b"]})




@pytest.mark.asyncio
async def test_look_reader_rejects_wrong_kind_aspect_before_guessing():
    from jsonschema import validate
    from plugin.plugins.neko_rimworld.agent.world.look_tools import read_look
    from plugin.plugins.neko_rimworld.agent.world.query_surface import SurfaceError

    from .test_object_tools import Reader

    schema = look_spec(consultation_setup().directory)["parameters"]
    validate({"object": {"kind": "pawn", "id": "p", "map": "m"}, "aspects": ["work"]}, schema)
    validate({"object": {"kind": "thing", "id": "t"}, "aspects": ["identity"]}, schema)
    reader = Reader([])
    with pytest.raises(SurfaceError):
        await read_look(reader, {"object": {"kind": "thing", "id": "t"}, "aspects": ["work"]})
    assert reader.calls == []


def test_native_pack_preserves_actual_lookup_root_and_scope():
    packed = surface.pack(
        {
            "results": [
                {
                    "from": "thing_defs",
                    "scan_complete": True,
                    "fields_complete": True,
                    "matched_count": 0,
                    "known_matches": 0,
                    "rows": [],
                    "page": {"offset": 0, "returned": 0, "has_more": False},
                    "scope": {"args": {}, "description": "Loaded definitions, not quantities."},
                }
            ]
        }
    )
    result = surface.render_blocked(
        "count",
        {"kind": "things", "definition": "Thing_Silver2520"},
        {"field": "def_name", "status": "missing_in_scope"},
        {"data": packed},
    )
    assert result["lookup"]["source"] == "thing_defs"
    assert result["lookup"]["scope"]["notes"] == ["Loaded definitions, not quantities."]
    assert result["execution"] == "not_run"






def test_map_alias_routes_to_map_identity_without_scope_conflict():
    obj = surface.normalize_object({"kind": "map", "map": "Map_0"})
    assert surface.route_object(obj) == ("map", {"id": "Map_0"}, None)
    assert surface.route_object(surface.normalize_object({"kind": "map", "id": "Map_0"})) == surface.route_object(obj)
    with pytest.raises(surface.SurfaceError):
        surface.normalize_object({"kind": "map", "map": "Map_0", "id": "Map_1"})




def test_map_alias_through_real_look_handler():
    import asyncio

    from plugin.plugins.neko_rimworld.agent.world.semantic_tools import semantic_interaction_factory
    from plugin.plugins.neko_rimworld.game.observation import Observation
    from plugin.plugins.neko_rimworld.game.protocol import QUERY_RESULT, Payload, SessionDescriptor, make_envelope
    from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools

    from .test_semantic_tools import Session, _update, public_source

    source = public_source()
    p = source["public_query"]
    p["roots"].append({"from": "map", "type": "Map", "description": "One map"})
    p["field_index"]["Map"] = {"id": "String", "name": "String"}
    p["parameters"]["properties"]["requests"]["items"]["allOf"][0]["oneOf"].append(
        {"properties": {"from": {"const": "map"}, "args": {"type": "object", "properties": {"id": {"type": "string"}}}}}
    )
    calls = []

    async def backend(request):
        calls.append(request)
        data = {
            "results": [
                {
                    "from": "map",
                    "scan_complete": True,
                    "fields_complete": True,
                    "matched_count": 1,
                    "known_matches": 1,
                    "rows": [{"id": "Map_0", "name": "Home"}],
                    "page": {"offset": 0, "returned": 1, "has_more": False},
                    "scope": {"args": {"id": "Map_0"}},
                }
            ]
        }
        env = make_envelope(
            QUERY_RESULT,
            "1" * 32,
            1,
            payload=Payload(status="ok", data=data, issues=[]),
            session=SessionDescriptor("2" * 32, "3" * 32, 1, 1),
            request_id="4" * 32,
        )
        return Observation.from_response(request["operation"], request["arguments"], env)

    adapter = semantic_interaction_factory(make_game_tools(source, backend), Session(), _update)
    result = asyncio.run(adapter.look({"object": {"kind": "map", "map": "Map_0"}, "aspects": ["identity"]}))
    assert len(calls) == 1 and calls[0]["arguments"]["from"] == "map"
    assert calls[0]["arguments"]["args"] == {"id": "Map_0"}
    assert result["facts"][0]["object"]["id"] == "Map_0"
