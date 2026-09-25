from __future__ import annotations

import copy
import threading
from typing import Any, Callable

from plugin.sdk.plugin import unwrap

STATE_KEY = "recommendation_state_v1"


def default_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "profile": {"interests": [], "updated_at": 0.0},
        "processed_message_ids": [],
        "candidates": [],
        "history": [],
        "last_run": {},
        "last_handoff": {},
        "last_discovery_at": 0.0,
        "last_user_message_at": 0.0,
        "processed_platform_event_ids": [],
        "platform_events": {
            "accepted": 0,
            "duplicate": 0,
            "rejected": 0,
            "by_platform": {},
            "last_event_at": 0.0,
        },
        "openbiliclaw_recommendations": {
            "last_sync_at": 0.0,
            "last_error": "",
            "last_fetched": 0,
            "total_imported": 0,
        },
        "openbiliclaw_profile": {
            "last_sync_at": 0.0,
            "last_error": "",
            "endpoint": "",
            "data": {},
        },
    }


class StateRepository:
    """Small serialized state wrapper around the SDK PluginStore."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._state = default_state()

    async def load(self) -> dict[str, Any]:
        value = unwrap(await self._store.get(STATE_KEY, default_state()))
        with self._lock:
            self._state = value if isinstance(value, dict) else default_state()
            return copy.deepcopy(self._state)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    async def update(self, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        # Timer entries run in separate threads/event loops. Holding a regular
        # lock through this short store write keeps their snapshots ordered.
        with self._lock:
            mutate(self._state)
            snapshot = copy.deepcopy(self._state)
            unwrap(await self._store.set(STATE_KEY, snapshot))
            return copy.deepcopy(snapshot)
