"""Build query backends from the supported Mod-owned public directory."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ..schema_serialization import schema_contract
from ..tool_contracts import (
    PUBLIC_QUERY_VERSION,
    WORLD_OBSERVATION_VERSION,
    ToolExecutionError,
    ToolHandler,
    ToolSetup,
    ToolSpec,
)
from .bridge import BridgeServer
from .catalog_contract import has_retired_capability_alias
from .observation import Observation
from .situation import SITUATION_VERSION, SituationProvider

COLONY_STATE_VERSION = "colony_state.v1"
COLONY_OPERATIONS = frozenset({"inspect_planet", "inspect_factions", "inspect_quests", "inspect_trade", "inspect_research", "evaluate_journey", "evaluate_trade", "inspect_resources"})


def make_game_tools(catalog: dict[str, Any], query: ToolHandler, *, situation_provider=None, current_session=None) -> ToolSetup:
    """Build only the public-query backend required by look/count."""
    public = catalog.get("public_query")
    if not isinstance(public, dict) or public.get("version") != PUBLIC_QUERY_VERSION:
        raise ToolExecutionError("semantic_surface_unavailable", "A supported public query directory is required; update the RimWorld Mod.", recoverable=False)
    world = catalog.get("world_observation")
    if not isinstance(world, dict) or world.get("version") != WORLD_OBSERVATION_VERSION:
        raise ToolExecutionError("semantic_surface_unavailable", "Current consultation requires world_observation.v2.3 with native state; update the RimWorld Mod.", recoverable=False)
    if has_retired_capability_alias(world):
        raise ToolExecutionError("semantic_surface_unavailable", "The Mod advertises the retired production selection capability; update the RimWorld Mod.", recoverable=False)
    setup = _public_tools(catalog, public, query)
    situation = catalog.get("situation_snapshot")
    if situation is not None:
        if not isinstance(situation, dict) or situation.get("version") != SITUATION_VERSION or situation.get("operation") != "read_situation":
            raise ToolExecutionError("invalid_catalog", "Invalid situation snapshot contract.")
        setup = replace(setup, situation_provider=situation_provider or SituationProvider(query, current_session))
    return setup


def _public_tools(catalog: dict[str, Any], public: dict[str, Any], query: ToolHandler) -> ToolSetup:
    """Current Mod owns field names, query semantics and discovery. Legacy tools are not advertised."""
    if not isinstance(public.get("parameters"), dict) or public["parameters"].get("type") != "object":
        raise ToolExecutionError("invalid_catalog", "Invalid public query parameters.")

    async def read(arguments):
        return await query({"operation": "query", "arguments": copy.deepcopy(arguments)})

    directory = {
        key: public[key]
        for key in ("roots", "rules", "common_fields", "field_index", "field_notes", "value_domains", "examples")
        if key in public
    }
    directory["public_query_version"] = public["version"]
    world_tools = []
    world = catalog.get("world_observation")
    if isinstance(world, dict) and world.get("version") == WORLD_OBSERVATION_VERSION:
        allowed = {"survey_space", "inspect_route", "inspect_state"}
        specs = world.get("tools", [])
        if (
            not isinstance(specs, list)
            or len(specs) != 3
            or {s.get("name") for s in specs if isinstance(s, dict)} != allowed
        ):
            raise ToolExecutionError("invalid_catalog", "Invalid world observation tool set")
        for spec in specs:
            name = spec["name"]
            if spec.get("operation") != name or not isinstance(spec.get("parameters"), dict):
                raise ToolExecutionError("invalid_catalog", "Invalid world observation operation")

            async def observe(arguments, operation=name):
                return await query({"operation": operation, "arguments": copy.deepcopy(arguments)})

            world_tools.append(ToolSpec(name, spec["description"], copy.deepcopy(spec["parameters"]), observe))
        directory["world_observation"] = copy.deepcopy(world)
    behavior = catalog.get("native_behavior")
    if isinstance(behavior, dict) and behavior.get("version") == "native_behavior.v1":
        specs = behavior.get("tools")
        names = {"inspect_actions", "inspect_threats"}
        if not isinstance(specs, list) or len(specs) != 2 or {s.get("name") for s in specs if isinstance(s, dict)} != names:
            raise ToolExecutionError("invalid_catalog", "Invalid native behavior tool set")
        behavior_descriptions = {'inspect_actions': 'Inspect native player-order conditions for the selected actor/target/actions; this performs no action.', 'inspect_threats': 'Inspect current native threats and target/subject checks. Hostility, active classification and attack coverage differ; partial is not safe.'}
        for spec in specs:
            if spec.get("operation") != spec["name"] or not isinstance(spec.get("parameters"), dict) or not isinstance(spec.get("description"), str):
                raise ToolExecutionError("invalid_catalog", "Invalid native behavior operation")

            async def inspect(arguments, operation=spec["name"]):
                return await query({"operation": operation, "arguments": copy.deepcopy(arguments)})

            world_tools.append(ToolSpec(spec["name"], behavior_descriptions[spec["name"]], schema_contract(spec["parameters"]), inspect))
        directory["native_behavior"] = copy.deepcopy(behavior)
    colony = catalog.get("colony_state")
    if colony is not None:
        if not isinstance(colony, dict) or colony.get("version") != COLONY_STATE_VERSION or not isinstance(colony.get("operations"), list):
            raise ToolExecutionError("invalid_catalog", "Invalid colony state contract")
        seen = set()
        for spec in colony["operations"]:
            if not isinstance(spec, dict) or not isinstance(spec.get("operation"), str) or spec["operation"] in seen or not isinstance(spec.get("parameters"), dict) or spec["parameters"].get("type") != "object":
                raise ToolExecutionError("invalid_catalog", "Invalid colony state operation")
            name = spec["operation"]
            seen.add(name)
            if name not in COLONY_OPERATIONS:
                continue

            async def inspect_colony(arguments, operation=name):
                return await query({"operation": operation, "arguments": copy.deepcopy(arguments)})

            world_tools.append(ToolSpec(name, "Internal colony state read.", copy.deepcopy(spec["parameters"]), inspect_colony))
        directory["colony_state"] = copy.deepcopy(colony)
    if isinstance(catalog.get("game_implementation"), dict):
        directory["game_implementation"] = copy.deepcopy(catalog["game_implementation"])
    return ToolSetup(
        tools=(
            ToolSpec(
                "query",
                "Read selected game objects, related facts and Mod-computed aggregates. Same fields and conditions for detail and counts. Batch related requests. No preliminary query required. Use coverage before interpreting missing values.",
                copy.deepcopy(public["parameters"]),
                read,
            ),
        )
        + tuple(world_tools),
        directory=copy.deepcopy(directory),
    )


class CatalogTools:
    """Connection-scoped, single-flight cache of Mod-authored descriptions; not agent memory."""

    def __init__(self, bridge: Callable[[], BridgeServer | None], query: ToolHandler):
        self._bridge = bridge
        self._query = query
        self._lock = asyncio.Lock()
        self._key: tuple[BridgeServer, str] | None = None
        self._catalog: dict[str, Any] | None = None
        self._situation_provider = None

    async def load(self) -> ToolSetup:
        async with self._lock:
            bridge = self._bridge()
            epoch = bridge.connection_key if bridge is not None else None
            if bridge is None or epoch is None:
                self._key = None
                self._catalog = None
                self._situation_provider = None
                return ToolSetup(())
            key = (bridge, epoch)
            if key != self._key:
                self._catalog = None
                self._key = None
                self._situation_provider = None
                try:
                    observation = await self._query({"operation": "query_catalog", "arguments": {}})
                    if not isinstance(observation, Observation):
                        raise ToolExecutionError("invalid_catalog", "The game did not return a query catalog.")
                    response = observation.value["response"]
                    if (
                        self._bridge() is not bridge
                        or bridge.connection_key != epoch
                        or response["connection_epoch"] != epoch
                    ):
                        raise ToolExecutionError(
                            "connection_changed", "The game connection changed while loading its schema."
                        )
                    payload = response["payload"]
                    if payload["status"] != "ok":
                        raise ToolExecutionError(
                            "catalog_unavailable", str(payload.get("message") or "Query catalog is unavailable.")
                        )
                    catalog = payload["data"]
                    self._validate(catalog)
                    self._catalog = copy.deepcopy(catalog)
                    self._key = key
                except ToolExecutionError:
                    return ToolSetup(())
            assert self._catalog is not None

            # Bind tools to this connection. A reconnect requires a new run/schema, not silent reuse.
            async def scoped_query(arguments: dict[str, Any]):
                if self._bridge() is not bridge or bridge.connection_key != epoch:
                    raise ToolExecutionError(
                        "connection_changed",
                        "Game connection changed; start a fresh consultation to reload the schema.",
                    )
                result = await self._query(arguments)
                if isinstance(result, Observation) and result.value["response"]["connection_epoch"] != epoch:
                    raise ToolExecutionError("connection_changed", "The response belongs to a new connection.")
                return result

            if self._situation_provider is None and "situation_snapshot" in self._catalog:
                self._situation_provider = SituationProvider(scoped_query, lambda: bridge.session)
            return make_game_tools(self._catalog, scoped_query, situation_provider=self._situation_provider)

    @staticmethod
    def _validate(catalog: object) -> None:
        if not isinstance(catalog, dict):
            raise ToolExecutionError("invalid_catalog", "Catalog must be an object.")
        if not isinstance(catalog.get("schema_id"), str) or not catalog["schema_id"].strip():
            raise ToolExecutionError("invalid_catalog", "Catalog is missing schema_id.")
        if len(json.dumps(catalog, ensure_ascii=False)) > 256_000:
            raise ToolExecutionError("invalid_catalog", "Catalog exceeds the schema context limit.")
