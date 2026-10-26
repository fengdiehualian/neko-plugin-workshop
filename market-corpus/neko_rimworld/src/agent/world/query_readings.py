"""Current public-query selection, mood/profile, resources and population measures."""

from __future__ import annotations

import copy
from typing import Any

from .native_state import observed_id_reference
from .query_surface import SurfaceError
from .semantic_surface import (
    DEF_KINDS,
    THING_KINDS,
    apply_lookup,
    capability_values,
    interpret_lookup,
    lookup_spec,
    normalize_object,
    plan_count,
    plan_look,
    render,
    render_blocked,
    validate_aspects,
    validate_by,
    validate_detail,
    validate_metric,
)


def profile_trait_semantics(result):
    """Annotate observed measures in place within the projection, never raw evidence."""
    result = copy.deepcopy(result)
    for item in result.get("facts", []):
        facts = item.get("facts", {})
        traits = facts.get("profile", {}).get("traits")
        if isinstance(traits, dict) and traits.get("state") != "unavailable":
            traits["field_semantics"] = {
                "degree": "Trait variant code, not a numeric speed or stat bonus.",
                "description": "Game-generated trait tooltip, including effects when available. Respect suppressed; a trait effect is not the pawn's final stat value.",
            }
    return result


def blocked_reading(tool, obj, resolution, native):
    """Explain the failed phase without changing the retained native receipt."""
    result = render_blocked(tool, obj, resolution, native)
    if resolution.get("field") != "def_name" or resolution.get("status") != "incomplete":
        return result
    action = ("instance " if obj["kind"] in THING_KINDS else "definition ") + ("counting" if tool == "count" else "reading")
    result["failure_stage"] = "definition_resolution"
    result["explanation"] = (
        "Definition resolution is incomplete; requested " + action + " was not run. "
        "Quantity remains unknown. Changing map or display limit does not change this definition scan. "
        "Conclusions needing the missing quantity remain conditional."
    )
    for issue in result["issues"]:
        if issue.get("reason") == "deadline_exceeded":
            issue["message"] = "Read deadline reached during definition resolution; requested " + action + " was not run."
    return result


class QueryReadings:
    def __init__(self, interaction):
        self.setup = interaction.setup
        self.session = interaction.session
        self.surface = interaction.surface
        self.execute_native = interaction.execute_native
        self._def_name_resolver = None

    def _def_resolver(self):
        """Optional deterministic Def-name recovery; None keeps lookups unchanged.

        A broken or unavailable index degrades to no resolver: recovery is an
        affordance, never a new failure path for ordinary lookups.
        """
        view = getattr(self.setup, "knowledge_view", None)
        if view is None:
            return None
        if self._def_name_resolver is None:
            from .def_names import DefNameResolver

            self._def_name_resolver = DefNameResolver.try_from_knowledge_view(view)
        return self._def_name_resolver or None

    async def _resolve(self, obj: dict[str, Any]):
        """Resolve a name or definition, or return candidates instead of a pick.

        An ambiguous or incomplete lookup must never silently choose one object:
        the caller gets the candidates and the scope that produced them.
        """
        if obj.get("kind") in ("pawn", "thing") and len(obj.get("names", [])) == 1:
            reference = {"kind": obj["kind"], "name": obj["names"][0]}
            if "map" in obj:
                reference["map"] = obj["map"]
            recovered = observed_id_reference(reference, self.session.observations)
            if "id" in recovered:
                obj = {**obj, "ids": [recovered["id"]]}
                obj.pop("names")
        resolver = self._def_resolver()
        name_resolution = None
        requested_term = None
        if resolver is not None and isinstance(obj.get("definition"), str) and obj["kind"] in DEF_KINDS + THING_KINDS:
            requested_term = obj["definition"]
            advice = resolver.normalize(requested_term)
            if advice["state"] == "unique" and advice["auto_selection"] != requested_term:
                # Deterministic word-order recovery only rewrites the requested
                # name; the native exact lookup below stays the arbiter of what
                # exists, so a stale index degrades to an ordinary miss.
                name_resolution = {
                    "requested": requested_term,
                    "resolved_as": advice["auto_selection"],
                    "method": "token_normalization",
                    "catalog": resolver.source,
                }
                obj = {**obj, "definition": advice["auto_selection"]}
        spec = lookup_spec(self.surface, obj)
        if spec is None:
            return obj, None, None
        native = await self.execute_native(spec["request"])
        resolution = interpret_lookup(spec, native["data"])
        if name_resolution is not None:
            resolution["name_resolution"] = name_resolution
        if resolver is not None and spec.get("kind") == "definition" and resolution["status"] == "missing_in_scope":
            discovery = resolver.discovery(requested_term or spec["names"][0], *spec["names"])
            if discovery is not None:
                resolution["discovery"] = discovery
        return apply_lookup(obj, spec, resolution), resolution, native

    def _validate_capabilities(self, obj):
        if "capabilities" in obj:
            allowed = capability_values(self.setup.directory)
            if not allowed or any(value not in allowed for value in obj["capabilities"]):
                raise SurfaceError("Unsupported capability selection on this connection")

    async def look(self, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict) or "object" not in args or "aspects" not in args:
            raise SurfaceError("look requires object and aspects")
        original = normalize_object(args["object"])
        self._validate_capabilities(original)
        aspects = validate_aspects(original["kind"], args["aspects"])
        if set(aspects) & {"health", "skills", "gear", "work", "state"}:
            raise SurfaceError("Native Pawn/state facets are read through inspect_state")
        detail = validate_detail(args.get("detail", 0))
        obj, resolution, lookup_native = await self._resolve(original)
        if obj is None:
            return blocked_reading("look", original, resolution, lookup_native)
        plan = plan_look(self.surface, obj, aspects, detail)
        offset = args.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= 8192:
            raise SurfaceError("offset must be 0..8192")
        plan.request["offset"] = offset
        native = await self.execute_native(plan.request)
        result = profile_trait_semantics(render(plan, native, resolution))
        result["page"] = copy.deepcopy((native.get("pages") or [None])[0] or {})
        return result

    async def count(self, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict) or "object" not in args or "metric" not in args:
            raise SurfaceError("count requires object and metric")
        original = normalize_object(args["object"])
        self._validate_capabilities(original)
        metric = validate_metric(original["kind"], args["metric"])
        by = validate_by(original["kind"], args.get("by"))
        detail = validate_detail(args.get("detail", 0))
        obj, resolution, lookup_native = await self._resolve(original)
        if obj is None:
            return blocked_reading("count", original, resolution, lookup_native)
        plan = plan_count(self.surface, obj, metric, by, detail)
        native = await self.execute_native(plan.request)
        return render(plan, native, resolution)
