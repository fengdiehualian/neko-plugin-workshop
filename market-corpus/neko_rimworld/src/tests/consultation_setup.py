"""Synthetic current query directory for isolated consultation runtime checks."""

import json
from pathlib import Path

from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools


def consultation_setup():
    async def unused(arguments):
        raise AssertionError("This isolated check must not perform a native query")

    directory = {
        "schema_id": "isolated-runtime-current",
        "public_query": {
            "version": "public_query.v2",
            "parameters": {
                "type": "object", "properties": {"requests": {
                    "type": "array", "items": {"allOf": [{"oneOf": [
                        {"properties": {"from": {"const": "things"}, "args": {"type": "object"}}},
                        {"properties": {"from": {"const": "pawn"}, "args": {"type": "object"}}},
                    ]}]},
                }},
            },
            "roots": [{"from": "things", "type": "Thing"}, {"from": "pawn", "type": "Pawn"}],
            "field_index": {"Thing": {"id": "String", "spawned": "Bool", "stack_count": "Int",
                                      "definition": "ThingDef", "parent_holder": "Thing"},
                            "Pawn": {"id": "String", "name": "String"}},
            "common_fields": [], "field_notes": {},
        },
    }
    actual=json.loads((Path(__file__).parent/"fixtures/current_query_catalog.json").read_text(encoding="utf-8"))
    directory["world_observation"]=actual["world_observation"]
    return make_game_tools(directory, unused)


def semantic_tool_provider():
    setup = consultation_setup()

    async def provide():
        return setup

    return provide
