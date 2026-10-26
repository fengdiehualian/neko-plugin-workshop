"""Production interaction that exposes look/count instead of the raw query tool.

The Mod-owned directory stays behind the semantic vocabulary. The model does
not see the native query backend. Unsupported directories fail before dispatch.

Results use request-scoped ordinary JSON with scope, coverage, unknown states
and native semantics. Same-read identities may refer to their retained native
ID; there is no numbered fact vocabulary. Raw evidence remains in the audit.

The runtime uses this single interaction's prompt, tools, submission, snapshot
and finalizing state.
"""

from __future__ import annotations

import copy
from typing import Any

from ...tool_contracts import PUBLIC_QUERY_VERSION, ToolExecutionError, ToolSpec
from ..delivery.research_surface import ReadOnlyResearchSurface
from .activity import make_activity_tool
from .colony_tools import computation_tools
from .count_tools import count_spec
from .look_tools import look_spec, read_look
from .native_state import NativeStateReader, state_contract
from .query_readings import QueryReadings
from .query_surface import Surface, SurfaceError
from .semantic_surface import pack
from .survey_tools import survey_spec
from .world_order import order_world_fields

# Only this explicitly approved non-observation tool survives the adapter.
PASSTHROUGH_TOOLS = frozenset({"wiki", "inspect_actions", "inspect_threats"})



def _compile_supported_surface(setup):
    directory = getattr(setup, "directory", None)
    tools = {getattr(tool, "name", None): tool for tool in setup.tools}
    required = {"query", "survey_space", "inspect_route", "inspect_state"}
    if (
        not isinstance(directory, dict)
        or directory.get("public_query_version") != PUBLIC_QUERY_VERSION
        or not required <= tools.keys()
        or state_contract(directory) is None
    ):
        raise ToolExecutionError(
            "semantic_surface_unavailable",
            "Current consultation requires public_query.v2 and world_observation.v2.3 with complete native state; update the RimWorld Mod.",
            recoverable=False,
        )
    try:
        surface = Surface(directory)
        surface.add_query_schema(tools["query"].parameters)
    except (KeyError, TypeError, ValueError) as error:
        raise ToolExecutionError("semantic_surface_unavailable", "The current public query directory cannot be compiled.", recoverable=False) from error
    return surface


class SemanticInteraction(ReadOnlyResearchSurface):
    """Expose look/count and project native results as ordinary JSON."""

    def __init__(self, setup: Any, session: Any, update: Any) -> None:
        super().__init__(setup, session, update)
        self.original = {tool.name: tool for tool in setup.tools}
        self.surface = _compile_supported_surface(setup)
        self.sample_identity: tuple[Any, Any, Any] | None = None
        self.native_state = NativeStateReader(self)
        self.query_readings = QueryReadings(self)

    # -- runtime contract -------------------------------------------------

    def read_tools(self) -> tuple[Any, ...]:
        async def survey(args):
            return await self._world_read(args, "survey_space")

        handlers = {"look": self.look, "count": self.count, "survey_space": survey}
        specs = []
        semantic_specs = [
            look_spec(self.setup.directory),
            count_spec(self.setup.directory),
            survey_spec(self.original["survey_space"].schema()["function"]),
        ]

        # Activity owns public path comparisons; route/state readers remain internal.
        for tool in semantic_specs:
            handler = handlers[tool["name"]]

            async def guarded(args, handler=handler):
                try:
                    return await handler(args)
                except (SurfaceError, KeyError, TypeError) as error:
                    raise ToolExecutionError("semantic_request_invalid", str(error)) from error

            specs.append(ToolSpec(tool["name"], tool["description"], tool["parameters"], guarded))
        passthrough = tuple(self.original[name] for name in sorted(PASSTHROUGH_TOOLS) if name in self.original)
        specs.append(make_activity_tool(self))
        specs.extend(computation_tools(self))
        return (*specs, *passthrough)

    def snapshot(self) -> dict[str, Any]:
        return dict(
            native_reads=len(self.session.observations),
            finalizing=self.finalizing,
        )

    async def _world_read(self, args: dict[str, Any], operation: str) -> dict[str, Any]:
        """Read one catalog-advertised world operation for semantic and activity adapters."""
        if operation not in self.original:
            raise ToolExecutionError("unsupported", f"The connected Mod does not support {operation}.")
        if operation in ("inspect_route", "survey_space"):
            args = dict(args)
            include_supplied = "include" in args
            include = args.pop("include", [])
            if not isinstance(include, list) or any(
                v not in ("geometry", "environment", "objects") for v in include
            ):
                raise SurfaceError("spatial include accepts geometry, environment and objects")
            if operation == "inspect_route":
                include_objects = args.pop("include_objects", False)
                if type(include_objects) is not bool:
                    raise SurfaceError("include_objects must be a boolean")
                # On-demand native generation: the route object listing the
                # model view would discard is not generated in the first
                # place. A continuation cursor pages object rows, so it keeps
                # objects requested. Environment, gases, threats, coverage
                # and route segments are internal forensics of every native
                # read and never depend on this flag.
                args["objects"] = bool(include_objects or "objects" in include or args.get("cursor"))
            elif include_supplied:
                # A survey without include retains the legacy discovery default.
                # Explicit native objects and object-page cursors keep precedence.
                if "objects" not in args:
                    args["objects"] = bool("objects" in include or args.get("cursor"))
        native = await self.execute_native(args, tool_name=operation)
        data = native.get("data")
        if not isinstance(data, dict):
            raise ToolExecutionError("invalid_world_result", f"The connected Mod returned no {operation} data.")
        ordered = order_world_fields(data)
        ordered["observation_id"] = native.get("observation_id")
        for key in ("colony_id", "load_epoch", "connection_epoch"):
            if native.get(key) is not None:
                ordered[key] = native[key]
        if ordered.get("format") == "colony_state.v1":
            # A successfully read quote may itself be unavailable/incomplete.
            # Keep the native business result distinct from transport success.
            ordered["read_status"] = native.get("status", "ok")
        else:
            ordered["status"] = native.get("status", "ok")
        ordered["issues"] = copy.deepcopy(native.get("issues", []))
        ordered["source_kind"] = "native"
        return ordered

    # -- native read ------------------------------------------------------

    async def execute_native(self, request: dict[str, Any], *, tool_name: str = "query") -> dict[str, Any]:
        """Perform the only native read this adapter makes, and record it once.

        A loaded-world change is fatal rather than merged: facts from two loads
        are not the same colony state, so the run must restart instead of
        answering with a mixture.
        """
        if getattr(self.session, "invalidated_error", None):
            raise ToolExecutionError(
                "world_changed", "The loaded world changed; start a new consultation.", recoverable=False
            )
        observation = await self.original[tool_name].handler(request)
        value = observation.to_dict()
        response = value["response"]
        payload = response["payload"]
        identity = (response["colony_id"], response["load_epoch"], response.get("connection_epoch"))
        expected_colony = getattr(self.session, "colony_id", None)
        if (expected_colony is not None and response["colony_id"] != expected_colony) or (
            self.sample_identity is not None and identity != self.sample_identity
        ):
            self.session.invalidated_error = "world_changed"
            raise ToolExecutionError(
                "world_changed",
                "The loaded world changed; start a new consultation. Do not combine the old facts.",
                recoverable=False,
            )
        self.sample_identity = identity
        self.session.observations.append(value)
        if payload["status"] not in {"ok", "partial"}:
            raise ToolExecutionError(
                payload.get("error_code") or "query_failed",
                payload.get("message") or "Native read failed",
                details={"issues": payload.get("issues", [])},
            )
        return dict(
            observation_id=value["observation_id"],
            colony_id=response.get("colony_id"),
            load_epoch=response.get("load_epoch"),
            connection_epoch=response.get("connection_epoch"),
            status=payload["status"],
            data=pack(payload.get("data"), payload.get("issues", [])),
            issues=payload.get("issues", []),
            pages=[
                copy.deepcopy(row.get("page"))
                for row in (payload.get("data") or {}).get("results", [])
                if isinstance(row, dict)
            ],
        )

    async def look(self, args: dict[str, Any]) -> dict[str, Any]:
        return await read_look(self, args)

    async def count(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self.query_readings.count(args)


def semantic_interaction_factory(setup: Any, session: Any, update: Any):
    """Require the look/count surface; never expose a fallback query vocabulary."""
    return SemanticInteraction(setup, session, update)


__all__ = [
    "SemanticInteraction",
    "semantic_interaction_factory",
]
