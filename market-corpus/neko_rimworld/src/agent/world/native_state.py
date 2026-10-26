"""Native physical identity, device state and selected Pawn facts."""

from __future__ import annotations

import copy
import re

from ...game.catalog_contract import has_retired_capability_alias
from ...serialization import canonical
from ...tool_contracts import MAX_TOOL_RESULT_CHARS, WORLD_OBSERVATION_VERSION
from ..context.model_context import MAX_SOURCE_CHARS, model_size
from .object_identity import normalize_object_identities
from .query_surface import SurfaceError
from .semantic_surface import (
    PAWN_KINDS,
    THING_KINDS,
)
from .work_assignments import WorkReadings
from .world_order import order_world_fields

PHYSICAL = (*PAWN_KINDS, *THING_KINDS)


STATE_ASPECTS = ("state", "health", "skills", "gear", "work")
STATE_FACETS = ("condition", "reservations", "containment", "bed", "door", "production", "plant", "turret", "trap", "storage", "construction", "power", "fuel", "battery", "temperature_control", "ammunition", "breakdown", "switch", "rot", "manning", "explosive", "environment")
GEAR_FIELDS = ("carried", "equipment", "apparel", "inventory")


def observed_id_reference(reference, observations):
    """Recover a known physical ID without guessing from its spelling or reading again."""
    if not isinstance(reference, dict) or "name" not in reference or set(reference) & {"id", "ids", "names"}:
        return reference
    term, kind = reference["name"], reference.get("kind")
    if kind not in ("pawn", "thing") or not isinstance(term, str):
        return reference
    matches, conflicting_names = set(), set()

    def visit(value, population=None):
        if isinstance(value, dict):
            root = value.get("from")
            if root in ("pawn", "pawns", "spawned_free_colonists"):
                population = "pawn"
            elif root in ("thing", "things"):
                population = "thing"
            observed_kind = value.get("kind")
            if observed_kind not in ("pawn", "thing"):
                observed_kind = "pawn" if value.get("runtime_type") == "Verse.Pawn" else population
            held_map = value.get("map_held")
            map_id = (
                value.get("map_id") or value.get("map") or (held_map.get("id") if isinstance(held_map, dict) else None)
            )
            requested_map = reference.get("map")
            in_scope = isinstance(map_id, str) and (requested_map in (None, "current", map_id))
            ident = value.get("id")
            if observed_kind == kind and isinstance(ident, str) and in_scope:
                if ident == term:
                    matches.add((ident, map_id))
                if term in (value.get("name"), value.get("label")) and ident != term:
                    conflicting_names.add(ident)
            for key, child in value.items():
                if key not in ("request", "requested_target"):
                    visit(child, population)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, population)

    for observation in observations:
        response = observation.get("response", {}) if isinstance(observation, dict) else {}
        payload = response.get("payload", {})
        if payload.get("status") in ("ok", "partial"):
            visit(payload.get("data"))
    if len(matches) != 1 or conflicting_names:
        return reference
    result = copy.deepcopy(reference)
    result["id"] = result.pop("name")
    return result


def _belongs(key, fields):
    return any(key == field or key.startswith(field + "_") for field in fields)


def state_contract(directory):
    if not isinstance(directory, dict):
        return None
    world = directory.get("world_observation")
    if not isinstance(world, dict) or world.get("version") != WORLD_OBSERVATION_VERSION or has_retired_capability_alias(world):
        return None
    tools = world.get("tools")
    if not isinstance(tools, list):
        return None
    required = {"targets", "stats", "actor", "user", "sections", "include_fogged", "detail_limit", "exact_lookup", "facets"}
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("name") != "inspect_state" or tool.get("operation") != "inspect_state":
            continue
        parameters = tool.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            continue
        properties = parameters.get("properties")
        if not isinstance(properties, dict) or not required <= properties.keys() or any(not isinstance(properties[key], dict) for key in required):
            continue
        section_spec = properties["sections"].get("items")
        sections = section_spec.get("enum") if isinstance(section_spec, dict) else None
        if isinstance(sections, list) and all(isinstance(section, str) for section in sections) and {"identity", *STATE_ASPECTS} <= set(sections):
            return tool
    return None


class NativeStateReader:
    def __init__(self, interaction):
        self.interaction = interaction
        self.work_readings = WorkReadings()

    async def read(self, base, obj, args, aspects, detail, *, direct=False):
        cell = obj["kind"] == "cell"
        state = [aspect for aspect in aspects if aspect in STATE_ASPECTS]
        stats = args.get("stats")
        base = copy.deepcopy(base)
        base["requested_aspects"] = aspects
        base["not_read"] = [s for s in base.get("not_read", []) if not s.startswith("aspects not requested:")]
        base["state_observations"] = []
        focused_state = args.get("focus", {}).get("state")
        if focused_state:
            base["state_facet_selection"] = {"requested": list(focused_state), "native_selected": True}
        base["consistency"] = (
            "Selection and state have separate samples below; no atomic snapshot or silent merge across loads."
        )
        if direct:
            base["consistency"] = (
                "Identity and state come from the reported native observation; this is not an atomic snapshot."
            )
        facts = base["facts"]
        readable_facts = facts
        if not direct and not cell:
            readable_facts = []
            for item in facts:
                ident = item.get("object", {}).get("id")
                if isinstance(ident, str) and ident:
                    readable_facts.append(item)
                else:
                    # A partial selection can enumerate a row before its ID is
                    # read. Keep that evidence, but never send its unavailable
                    # marker as a native target or discard the readable rows.
                    item["state"] = {
                        "status": "unavailable",
                        "reason": "identity_unavailable",
                        "message": "State was not read because selection did not return a usable object ID.",
                    }
                    base["status"] = "partial"
        for offset in range(0, len(readable_facts), 8):
            batch = readable_facts[offset : offset + 8]
            if direct:
                target = {"kind": obj["kind"]}
                target["id" if "ids" in obj else "name"] = obj.get("ids", obj.get("names"))[0]
                if "map" in obj:
                    target["map"] = obj["map"]
                elif obj["kind"] == "thing" and "names" in obj:
                    target["map"] = "current"
                targets = [target]
            else:
                targets = [self._target(f["object"], obj) for f in batch]
            request = {
                "targets": targets,
                "sections": state or ["identity"],
                "include_fogged": False if cell else obj.get("include_fogged", True),
                "detail": "details" if detail else "summary",
            }
            if focused_state:
                request["facets"] = list(focused_state)
            if direct:
                request["exact_lookup"] = True
            if detail:
                request["detail_limit"] = detail
            for key in ("stats", "actor", "user"):
                if key in args:
                    request[key] = copy.deepcopy(args[key])
            native = await self.interaction.execute_native(request, tool_name="inspect_state")
            data = order_world_fields(native["data"])
            base["state_observations"].append(
                {
                    "observation_id": native.get("observation_id") or data.get("observation_id"),
                    "sample": data.get("sample"),
                    "status": native.get("status"),
                    "context": data.get("context"),
                    "issues": native.get("issues", []),
                    "read_error": data.get("read_error"),
                    "changed_during_read": data.get("changed_during_read", False),
                }
            )
            if native["status"] != "ok":
                base["status"] = "partial"
            rows = data.get("objects", [])
            if len(rows) > len(batch) or native["status"] == "ok" and len(rows) != len(batch):
                raise SurfaceError("native state response does not match the requested target count")
            if len(rows) < len(batch):
                base["status"] = "partial"
                base["state_failure"] = {
                    key: copy.deepcopy(data[key])
                    for key in ("read_error", "resolution", "changed_during_read")
                    if key in data
                }
                rows = [
                    *rows,
                    *(
                        {"status": "unavailable", "reason": "state_read_incomplete", "requested_target": target}
                        for target in targets[len(rows) :]
                    ),
                ]
            for item, row in zip(batch, rows):
                item["state_sample"] = {
                    "tick": row.get("sample_tick"),
                    "observation_id": native.get("observation_id") or data.get("observation_id"),
                }
                if row.get("status") in ("unavailable", "unsupported") or "facets" not in row and not cell:
                    item["state"] = copy.deepcopy(row)
                    if direct and obj.get("names") and re.fullmatch(r"Thing_[A-Za-z0-9_]+\d+", obj["names"][0]):
                        base.setdefault("input_hints", []).append(
                            {
                                "code": "possible_id_in_name",
                                "field": "object.name" if "name" in args["object"] else "object.names",
                                "value": obj["names"][0],
                                "suggested_field": "object.id",
                                "message": "This unresolved exact display name resembles a stable Thing ID. If you intended an ID, retry with object.id; names are never silently coerced.",
                            }
                        )
                    base["status"] = "partial"
                    continue
                if cell:
                    item["facts"]["state" if "state" in state else "identity"] = (
                        row if "state" in state else row["target"]
                    )
                    continue
                if direct:
                    identity = row.get("target", {})
                    if not identity.get("id") or "ids" in obj and identity["id"] != obj["ids"][0]:
                        raise SurfaceError("native state target identity mismatch")
                    item["object"] = self._identity(identity)
                    item["facts"]["identity"] = {"object": copy.deepcopy(item["object"])}
                    base["coverage"].update({"matched": 1, "listing": "complete", "scan": "complete"})
                if row.get("target", {}).get("id") != item["object"].get("id"):
                    raise SurfaceError("native state target identity mismatch")
                item["state_coverage"] = row.get("coverage")
                facets = row["facets"]
                pawn_state = facets.get("pawn")
                if (
                    isinstance(pawn_state, dict)
                    and pawn_state.get("status") == "observed"
                    and "health_summary_fraction" in pawn_state
                ):
                    pawn_state["field_semantics"] = {
                        "health_summary_fraction": "Native overall health summary, not proof of no injuries or impaired capacities.",
                        "bleed_fraction_per_game_day": "Blood loss fraction per game day, not per second or a survival forecast.",
                        "pain_fraction": "Current pain fraction, separate from injury severity and blood loss.",
                    }
                if "state" in state:
                    visible_facets = (
                        {k: v for k, v in facets.items() if k not in ("pawn", "work", "stats")}
                    )
                    item["facts"]["state"] = {
                        "facets": visible_facets,
                        "unsupported_components": row.get("unsupported_components"),
                    }
                pawn = facets.get("pawn", {})
                for aspect in state:
                    if aspect == "state":
                        continue
                    if aspect == "work":
                        value = facets.get("work", {"status": "not_applicable"})
                    elif pawn.get("status") != "observed":
                        value = pawn or {"status": "not_applicable"}
                    elif aspect == "health":
                        value = {
                            k: v for k, v in pawn.items() if not _belongs(k, (*GEAR_FIELDS, "skills", "traits"))
                        }
                    elif aspect == "skills":
                        value = {k: v for k, v in pawn.items() if k == "status" or _belongs(k, ("skills",))}
                    else:
                        value = {k: v for k, v in pawn.items() if k == "status" or _belongs(k, GEAR_FIELDS)}
                    item["facts"][aspect] = value
                if stats is not None:
                    item["facts"]["stats"] = (
                        facets.pop("stats", {"status": "not_applicable"})
                        if "state" in state
                        else facets.get("stats", {"status": "not_applicable"})
                    )
        base["coverage"]["state_facets_read_successfully"] = (
            all(f.get("state_coverage", {}).get("registered_facets_read_successfully") is True for f in facts)
            if not cell
            else base["status"] == "ok"
        )
        if base["coverage"]["state_facets_read_successfully"] is False:
            base["status"] = "partial"
        base["not_read"].append(
            "Omitted aspects are not evidence of absence. stats are computed only when requested; contextual predicates require the relevant actor/user."
        )
        if obj["kind"] in PAWN_KINDS and ("work" in aspects or "state" in aspects):
            base["work_assignments"] = self.work_readings.summarize(base)
        return self._fit_page(normalize_object_identities(base), args)

    @staticmethod
    def _fit_page(base, arguments=None):
        facts = base.get("facts", [])
        original = len(facts)
        while (len(canonical(base)) > MAX_SOURCE_CHARS) or model_size(
            base, arguments=arguments
        ) > MAX_TOOL_RESULT_CHARS - 4096:
            if len(facts) <= 1:
                raise SurfaceError("Object facts exceed output bound; select fewer aspects or smaller detail")
            facts.pop()
        if len(facts) < original:
            base["coverage"]["listing"] = "partial"
            page = base.setdefault("page", {})
            page["returned"] = len(facts)
            page["has_more"] = True
            page["next_offset"] = (
                page.get("offset", 0) + len(facts) if base["coverage"].get("scan") == "complete" else None
            )
            page["reduced_for_size"] = True
            base["not_read"].append(
                "Additional object rows omitted for output size; use page.next_offset. Returned object facts are not pruned."
            )
        return base

    @staticmethod
    def _identity(native):
        identity = copy.deepcopy(native)
        definition = native.get("definition")
        identity["definition"] = {"def_name": definition, "label": native.get("definition_label")}
        identity["map_id"] = native.get("map")
        identity["map_held"] = {"id": native["map"]} if native.get("map") else None
        membership = native.get("pawn_membership") or {}
        identity.update(membership)
        if native.get("kind") == "pawn":
            identity["is_prisoner_of_colony"] = membership.get("is_prisoner")
            identity["is_slave_of_colony"] = membership.get("is_slave")
        else:
            identity["label"] = native.get("name")
        return identity

    @staticmethod
    def _target(identity, obj):
        if obj["kind"] == "cell":
            return copy.deepcopy(obj)
        target = {"id": identity["id"]}
        held = identity.get("map_held")
        candidates = (identity.get("map_id"), identity.get("map"), held.get("id") if isinstance(held, dict) else None, obj.get("map"))
        map_id = next((value for value in candidates if isinstance(value, str) and value), None)
        if map_id:
            target["map"] = map_id
        return target
