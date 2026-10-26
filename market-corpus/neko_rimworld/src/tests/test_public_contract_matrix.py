"""Validate the actual public schema against supported handler vocabulary."""

import copy
import itertools

import jsonschema
import pytest
from plugin.plugins.neko_rimworld.agent.world.count_tools import count_spec
from plugin.plugins.neko_rimworld.agent.world.look_tools import look_spec, read_look
from plugin.plugins.neko_rimworld.agent.world.query_surface import SurfaceError
from plugin.plugins.neko_rimworld.agent.world.semantic_surface import (
    ASPECTS,
    KINDS,
    validate_by,
    validate_metric,
)

from .test_object_tools import Reader as BaseReader


class Reader(BaseReader):
    async def execute_native(self, args, tool_name):
        result = await super().execute_native(args, tool_name)
        for row in result["data"]["objects"]:
            row["target"].update(definition="Human", map="Map_0")
        return result


def look_schema():
    from .consultation_setup import consultation_setup
    return look_spec(consultation_setup().directory)["parameters"]


@pytest.mark.asyncio
async def test_explicit_state_and_query_aspects_agree_with_reader():
    validator = jsonschema.Draft202012Validator(look_schema())
    for kind, aspect in itertools.product(KINDS, (*ASPECTS, "state")):
        obj = {"kind": kind}
        if kind in ("pawn", "thing", "map"):
            obj["id"] = "a"
        if kind == "thing_def":
            obj["definition"] = "Bed"
        args = {"object": obj, "aspects": [aspect]}
        schema_ok = validator.is_valid(args)
        # The adapter is a fake native data source; vocabulary validation runs in product code.
        reader = Reader([{"id": "a"}])
        try:
            await read_look(reader, copy.deepcopy(args))
            runtime_ok = True
        except SurfaceError:
            runtime_ok = False
        # The query facts owner validates nonphysical facets before reading; this fake supplies only rows.
        if kind not in ("pawn", "pawns", "colonists", "thing", "things"):
            from plugin.plugins.neko_rimworld.agent.world.semantic_surface import validate_aspects

            try:
                validate_aspects(kind, [aspect])
                runtime_ok = True
            except SurfaceError:
                runtime_ok = False
        from plugin.plugins.neko_rimworld.agent.world.semantic_surface import supported_aspects
        expected = aspect in (*supported_aspects(kind), *(("state",) if kind in ("pawn", "pawns", "colonists", "thing", "things") else ()))
        assert runtime_ok == expected, (kind, aspect)
        assert schema_ok, (kind, aspect)
    assert not validator.is_valid({"object": {"kind": "thing", "id": "a"}})
    # Applicability is enforced by the reader; the concise model schema carries it once in aspects prose.


@pytest.mark.asyncio
async def test_count_combinations_reject_before_native_dispatch():
    from .test_public_schema_ownership import current_interaction
    interaction, _ = current_interaction()
    native_calls = []
    class NativeReached(Exception):
        pass
    async def resolved(obj):
        return obj, None, None
    async def native(request):
        native_calls.append(request)
        raise NativeReached
    interaction.query_readings._resolve = resolved
    interaction.query_readings.execute_native = native
    validator = jsonschema.Draft202012Validator(count_spec()["parameters"])
    for kind, metric, by in itertools.product(
        KINDS, ("records", "units"), (None, "map", "definition", "category", "holder")
    ):
        args = {"object": {"kind": kind}, "metric": metric}
        if by is not None:
            args["by"] = by
        try:
            validate_metric(kind, metric)
            validate_by(kind, by)
            valid = True
        except SurfaceError:
            valid = False
        assert validator.is_valid(args) == (kind in ("maps", "pawns", "colonists", "things", "thing_defs")), args
        before = len(native_calls)
        try:
            await interaction.query_readings.count(args)
        except NativeReached:
            dispatched = True
        except SurfaceError:
            dispatched = False
        assert dispatched == valid, args
        assert len(native_calls) - before == int(valid), args


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {"object": {"kind": "thing", "id": "a"}, "aspects": ["identity"], "actor": {"name": "p"}},
        {"object": {"kind": "map", "id": "a"}, "stats": ["MoveSpeed"]},
        {"object": {"kind": "thing_defs"}, "stats": ["MoveSpeed"]},
        {"object": {"kind": "cell", "x": 1, "z": 1}, "offset": 1},
    ],
)
async def test_cross_parameter_rejections_are_public(args):
    with pytest.raises(SurfaceError):
        await read_look(Reader([]), args)
