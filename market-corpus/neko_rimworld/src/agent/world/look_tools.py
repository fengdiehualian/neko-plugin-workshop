"""The public look contract and request-scoped selection/read composition."""

from __future__ import annotations

import copy

from ...schema_serialization import schema_contract
from .colony_tools import DOMAINS, colony_kind, extend_look_spec, read_colony
from .native_state import (
    PHYSICAL,
    STATE_ASPECTS,
    STATE_FACETS,
    NativeStateReader,
    observed_id_reference,
    state_contract,
)
from .object_identity import normalize_object_identities
from .query_surface import SurfaceError
from .semantic_surface import (
    ASPECTS,
    KINDS,
    _object_schema,
    normalize_object,
    supported_aspects,
    validate_aspects,
    validate_detail,
)


def look_spec(directory):
    contract = state_contract(directory)
    if contract is None:
        raise SurfaceError("look requires the complete current native state contract")
    spec = {
        "name": "look",
        "description": 'Read selected object facts, e.g. object={"kind":"pawn","id":"Thing_Human1"},aspects=["identity","gear"],detail=10. aspects/focus/detail are top-level, not inside object. Use only aspects allowed for this kind. actor acts; user receives.',
        "parameters": {
            "type": "object",
            "properties": {
                "object": _object_schema(directory),
                "aspects": {"type": "array", "minItems": 1, "items": {"enum": [*ASPECTS, "state"]}},
                "detail": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
            },
            "additionalProperties": False,
        },
    }
    props = spec["parameters"]["properties"]
    cell = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "x", "z"],
        "properties": {
            "kind": {"const": "cell"},
            "map": {"type": "string", "default": "current"},
            "x": {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
            "z": {"type": "integer", "minimum": -2147483648, "maximum": 2147483647},
        },
    }
    props["offset"] = {
        "type": "integer",
        "minimum": 0,
        "maximum": 8192,
        "default": 0,
        "description": "Continue object rows using returned page.next_offset. Each page is a fresh observation, not a frozen snapshot.",
    }
    props["object"] = {"oneOf": [props["object"], cell]}
    groups = {}
    for kind in KINDS:
        aspects = tuple(supported_aspects(kind)) + (("state",) if kind in PHYSICAL else ())
        groups.setdefault(aspects, []).append(kind)
    rules = "; ".join("/".join(kinds) + ": " + ",".join(aspects) for aspects, kinds in groups.items())
    props["aspects"]["description"] = (
        "Allowed by kind: "
        + rules
        + "; cell: identity,state. Omitted: state for physical objects/cells, identity otherwise. "
        "state is physical/device state, excluding Pawn health/skills/gear/work. Request those aspects explicitly. Do not combine state aspects with distribution. "
        "actor/user requires a state aspect. stats/context require physical objects; cells accept no stats."
    )
    for key in ("stats", "actor", "user"):
        props[key] = copy.deepcopy(contract["parameters"]["properties"][key])
        if key in ("actor", "user"):
            props[key] = {
                "type": "object",
                "additionalProperties": False,
                "description": "Pawn context: provide exactly one of id or name, not both. Optional map defaults to current.",
                "properties": {
                    **{name: copy.deepcopy(props[key]["properties"][name]) for name in ("id", "name", "map")},
                    "kind": {"type": "string", "const": "pawn"},
                },
                "oneOf": [
                    {"required": ["id"], "not": {"required": ["name"]}},
                    {"required": ["name"], "not": {"required": ["id"]}},
                ],
            }
    spec["parameters"]["required"] = ["object", "aspects"]
    props["focus"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            key: {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "string"}}
            for key in ("skills", "work", "capacities")
        },
        "description": "Select exact skills/work/capacities or physical state facets. Their aspects are included in this call; omitted keys keep full lists. Unmatched/unsupported is not zero. focus.state=[bed,reservations] avoids unrelated state.",
    }
    props["focus"]["properties"]["state"] = {
        "type": "array",
        "minItems": 1,
        "maxItems": len(STATE_FACETS),
        "uniqueItems": True,
        "items": {"enum": list(STATE_FACETS)},
    }
    props["include"] = {
        "type": "array",
        "uniqueItems": True,
        "items": {"enum": ["body_parts"]},
        "description": "Full body_parts in this same health read.",
    }

    extend_look_spec(spec, directory)
    descriptions = {key: props[key]["description"] for key in ("aspects", "focus")}
    spec["parameters"] = schema_contract(spec["parameters"])
    for key, description in descriptions.items():
        spec["parameters"]["properties"][key]["description"] = description
    return spec


async def read_look(interaction, args):
    if not isinstance(args, dict) or not isinstance(args.get("object"), dict):
        raise SurfaceError("look requires object")
    kind = colony_kind(args["object"].get("kind"))
    if kind in DOMAINS:
        return await read_colony(interaction, args, kind)
    unknown = set(args) - {
        "object",
        "aspects",
        "detail",
        "stats",
        "actor",
        "user",
        "offset",
        *({"include", "focus"}),
    }
    if unknown:
        raise SurfaceError("look does not accept: " + ", ".join(sorted(unknown)))
    include = args.get("include", [])
    if not isinstance(include, list) or any(v not in ("body_parts",) for v in include):
        raise SurfaceError("look include accepts body_parts")
    # Model-only inclusion controls never enter the native request.
    focus = args.get("focus", {})
    if (
        not isinstance(focus, dict)
        or set(focus) - {"skills", "work", "capacities", "state"}
        or any(
            not isinstance(v, list)
            or not 1 <= len(v) <= 32
            or any(not isinstance(n, str) or not n or len(n) > 256 for n in v)
            for v in focus.values()
        )
    ):
        raise SurfaceError("focus selects skills/work/capacities or physical state facets")
    if "state" in focus and (
        len(set(focus["state"])) != len(focus["state"]) or set(focus["state"]) - set(STATE_FACETS)
    ):
        raise SurfaceError("focus.state requires distinct registered physical facet names")
        # Native requests below are built from explicit native keys. Retain
        # these controls here so page fitting uses the requested model view.
    raw_object = args["object"]
    kind = raw_object.get("kind")
    if kind in ("pawn", "thing"):
        raw_object = observed_id_reference(
            raw_object, getattr(getattr(interaction, "session", None), "observations", ())
        )
    cell = kind == "cell"
    obj = copy.deepcopy(raw_object) if cell else normalize_object(raw_object)
    if kind in ("pawn", "thing") and len(obj.get("names", [])) == 1:
        reference = {"kind": kind, "name": obj["names"][0]}
        if "map" in obj:
            reference["map"] = obj["map"]
        recovered = observed_id_reference(reference, getattr(getattr(interaction, "session", None), "observations", ()))
        if "id" in recovered:
            obj["ids"] = [recovered["id"]]
            obj.pop("names")
    aspects = args.get("aspects", ["state"] if kind in PHYSICAL or cell else ["identity"])
    if not isinstance(aspects, list) or not aspects or any(not isinstance(a, str) for a in aspects):
        raise SurfaceError("aspects must be a nonempty string array")
    aspects = list(dict.fromkeys(aspects))
    aspects = list(
        dict.fromkeys(
            [
                *aspects,
                *(
                    {"skills": "skills", "work": "work", "capacities": "health", "state": "state"}[k]
                    for k in args.get("focus", {})
                ),
            ]
        )
    )
    detail = validate_detail(args.get("detail", 0))
    offset = args.get("offset", 0)
    if type(offset) is not int or not 0 <= offset <= 8192 or cell and offset:
        raise SurfaceError("offset must be 0..8192 for object populations; cells use zero")
    state = [a for a in aspects if a in STATE_ASPECTS]
    if state and "distribution" in aspects:
        raise SurfaceError("Use count for quantities/grouping; look for object state")
    query_aspects = [a for a in aspects if a not in STATE_ASPECTS]
    if kind not in PHYSICAL and not cell:
        if state or any(k in args for k in ("stats", "actor", "user")):
            raise SurfaceError("state/stats/context require physical objects or cells")
        return await interaction.query_readings.look({**args, "aspects": aspects})
    if cell:
        if set(aspects) - {"identity", "state"} or "stats" in args:
            raise SurfaceError("cells support identity/state, not Pawn/Thing stats")
        if set(obj) - {"kind", "map", "x", "z"} or any(type(obj.get(k)) is not int for k in ("x", "z")):
            raise SurfaceError("cell requires integer x/z and optional map")
    else:
        validate_aspects(kind, [a for a in aspects if a != "state"]) if any(a != "state" for a in aspects) else None
    stats = args.get("stats")
    if "stats" in args and (
        not isinstance(stats, list) or not 1 <= len(stats) <= 16 or any(not isinstance(x, str) or not x for x in stats)
    ):
        raise SurfaceError("stats requires 1..16 exact StatDef names")
    for key in ("actor", "user"):
        if key in args:
            ref = args[key]
            if (
                not isinstance(ref, dict)
                or set(ref) - {"id", "name", "kind", "map"}
                or ("id" in ref) == ("name" in ref)
                or ref.get("kind", "pawn") != "pawn"
                or not isinstance(ref.get("id", ref.get("name")), str)
                or not ref.get("id", ref.get("name"))
            ):
                raise SurfaceError(key + " requires one Pawn id or name and optional map")
    if any(k in args for k in ("actor", "user")) and not state:
        raise SurfaceError("actor/user context requires a state aspect")
    if not cell and not state and stats is None and not any(k in args for k in ("actor", "user")):
        return NativeStateReader._fit_page(
            normalize_object_identities(
                await interaction.query_readings.look({**args, "aspects": aspects})
            ),
            args,
        )
    direct = (
        kind in ("pawn", "thing")
        and offset == 0
        and not set(obj) - {"kind", "ids", "names", "map", "include_fogged"}
        and len(obj.get("ids", obj.get("names", []))) == 1
        and not set(query_aspects) - {"identity"}
    )
    if cell or direct:
        base = {
            "status": "ok",
            "tool": "look",
            "object": obj,
            "scope": {"object": obj, "include_fogged": False if cell else obj.get("include_fogged", True)},
            "coverage": {},
            "facts": [{"object": obj, "facts": {}}],
            "not_read": [],
        }
    else:
        # Identity selection uses the established population/scope/ambiguity contract.
        # Mood/biography still use their existing reader, but no duplicate health/work read.
        base = await interaction.query_readings.look(
            {
                "object": raw_object,
                "aspects": list(dict.fromkeys(["identity", *query_aspects])),
                "detail": detail,
                "offset": offset,
            }
        )
        if not base.get("facts"):
            return base
    return await interaction.native_state.read(base, obj, args, aspects, detail, direct=direct)
