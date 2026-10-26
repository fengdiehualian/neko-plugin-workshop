"""One connection-owned native situation cache; no model tools or durable state."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass

from .observation import Observation

REFRESH_TICKS = 30_000
SITUATION_VERSION = "situation_snapshot.v1"


@dataclass(frozen=True, slots=True)
class SituationSnapshot:
    observation: Observation
    cache_hit: bool
    checked_tick: int | None


class SituationProvider:
    def __init__(self, query, current_session=None):
        self._query = query
        self._current_session = current_session
        self._lock = asyncio.Lock()
        self._cached: Observation | None = None

    async def snapshot(self) -> SituationSnapshot:
        # Serial callers recheck the cache after a single in-flight native read.
        async with self._lock:
            current = self._current_session() if self._current_session else None
            tick = getattr(current, "game_tick", None)
            cached = self._cached
            if cached is not None and current is not None:
                response = cached.value["response"]
                sampled = response["payload"]["data"]["sample"]["finished_tick"]
                if (
                    (response["colony_id"], response["load_epoch"])
                    == (current.colony_id, current.load_epoch)
                    and 0 <= tick - sampled < REFRESH_TICKS
                ):
                    return SituationSnapshot(Observation(cached.to_dict()), True, tick)
            observation = await self._query({"operation": "read_situation", "arguments": {}})
            if not isinstance(observation, Observation):
                raise ValueError("Situation source did not return an Observation")
            response = observation.value["response"]
            if current is not None and (response["colony_id"], response["load_epoch"]) != (current.colony_id, current.load_epoch):
                from ..tool_contracts import ToolExecutionError
                raise ToolExecutionError("world_changed", "Situation response belongs to a different loaded world", recoverable=False)
            payload = response["payload"]
            data = payload.get("data")
            if payload.get("status") not in {"ok", "partial"}:
                # Retain the real failed envelope; the runtime can disclose a
                # source failure without disabling otherwise valid research.
                return SituationSnapshot(Observation(observation.to_dict()), False, tick)
            if not isinstance(data, dict) or data.get("format") != SITUATION_VERSION:
                raise ValueError("Situation source returned an invalid snapshot")
            sampled = data.get("sample", {}).get("finished_tick")
            if type(sampled) is not int:
                raise ValueError("Situation source omitted its sampling tick")
            # An unavailable snapshot remains truthful evidence, but is not cached.
            if payload.get("status") in {"ok", "partial"}:
                self._cached = Observation(copy.deepcopy(observation.value))
            return SituationSnapshot(Observation(observation.to_dict()), False, tick)
