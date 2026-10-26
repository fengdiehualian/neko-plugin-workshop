"""T1 negotiated native lookup narrows candidates without changing ambiguity."""

import copy

from plugin.plugins.neko_rimworld.agent.world.semantic_surface import apply_lookup, lookup_spec


class RecordingSurface:
    def __init__(self, supported):
        self.argument_schemas = {"thing_defs": {"properties": {"exact_names": {}} if supported else {}}}

    def compile(self, plan):
        return copy.deepcopy(plan)


def test_exact_definition_lookup_uses_negotiated_index_and_retains_label_union():
    obj = {"kind": "things", "definition": "Bed"}
    indexed = lookup_spec(RecordingSurface(True), obj)
    legacy = lookup_spec(RecordingSurface(False), obj)
    block = indexed["request"]["blocks"][0]
    assert block["args"] == {"exact_names": ["Bed"]}
    assert "args" not in legacy["request"]["blocks"][0]
    assert (
        block["where"]
        == legacy["request"]["blocks"][0]["where"]
        == {
            "any": [
                {"def_name": {"eq": "Bed", "case_sensitive": False}},
                {"label": {"eq": "Bed", "case_sensitive": False}},
            ]
        }
    )
    assert apply_lookup(obj, indexed, {"status": "ambiguous", "ids": ["Bed", "OtherLabelBed"]}) is None
    assert apply_lookup(obj, indexed, {"status": "incomplete", "ids": ["Bed"]}) is None
    assert apply_lookup(obj, indexed, {"status": "resolved", "ids": ["Bed"]})["definition"] == "Bed"


def test_capability_selection_schema_includes_native_meanings():
    import json
    from pathlib import Path

    from plugin.plugins.neko_rimworld.agent.world.semantic_surface import _object_schema
    from plugin.plugins.neko_rimworld.game.query_tools import make_game_tools

    async def forbidden(arguments):
        raise AssertionError("Catalog schema construction must not read the game")
    catalog = json.loads((Path(__file__).parent / "fixtures/current_query_catalog.json").read_text(encoding="utf-8"))
    directory = make_game_tools(catalog, forbidden).directory
    schema = _object_schema(directory)
    capabilities = schema["properties"]["capabilities"]
    assert "bill_giver" in capabilities["items"]["enum"]
    assert "production" not in capabilities["items"]["enum"]
    assert "Pawn surgery" in capabilities["description"]
