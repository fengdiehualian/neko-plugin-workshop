"""Read screen frames the host already delivered to the model provider.

The provider-frame bus is deliberately lossy. The plugin only reuses a recent
screen frame for the target character; missing, stale, malformed, or unrelated
records all fail closed and leave the screenshot tool to capture a new image.

Reads on the telemetry path never block. A small per-character cache is
refreshed on a worker thread, and an expired cache reports inactive until that
refresh completes.
"""

from __future__ import annotations

import base64
import math
import threading
import time
from collections.abc import Iterable, Mapping
from typing import Any, Callable

SOURCE_SCREEN = "screen"


def _spawn_thread(fn: Callable[[], None]) -> None:
    threading.Thread(
        target=fn,
        name="wows-provider-frame-probe",
        daemon=True,
    ).start()


def _record_value(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


class ProviderFrameProbe:
    """Cached view of screen frames the host already sent to a provider."""

    def __init__(
        self,
        fetch: Callable[[], Any],
        *,
        ttl: float = 2.0,
        max_age: float = 5.0,
        logger=None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        spawn: Callable[[Callable[[], None]], None] = _spawn_thread,
    ) -> None:
        self._fetch = fetch
        self._ttl = max(0.0, float(ttl))
        self._max_age = max(0.0, float(max_age))
        self._logger = logger
        self._clock = clock
        self._wall_clock = wall_clock
        self._spawn = spawn
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._states: dict[str, dict[str, Any]] = {}
        self._records: dict[str, Any] = {}
        self._fetched_at: dict[str, float] = {}
        self._refreshing: set[str] = set()

    def snapshot(self, *, role: str = "") -> dict[str, Any]:
        """Return fresh cached state, or inactive while refreshing it."""
        role = str(role or "")
        with self._lock:
            fetched_at = self._fetched_at.get(role)
            due = fetched_at is None or self._clock() - fetched_at >= self._ttl
            state = self._cached_state(role) if not due else self._inactive()
            start = due and role not in self._refreshing
            if start:
                self._refreshing.add(role)
        if start:
            try:
                self._spawn(lambda: self._refresh(role))
            except Exception as exc:
                with self._condition:
                    self._refreshing.discard(role)
                    self._condition.notify_all()
                self._log("debug", f"provider frame refresh could not start: {exc}")
            else:
                # Tests and in-process callers may use a synchronous spawner.
                with self._lock:
                    if role not in self._refreshing:
                        state = self._cached_state(role)
        return state

    def is_active(self, *, role: str = "") -> bool:
        return bool(self.snapshot(role=role)["active"])

    def fetch_frame(self, *, role: str = "") -> bytes | None:
        """Synchronously refresh and decode a frame for the screenshot tool."""
        role = str(role or "")
        self._refresh_synchronously_if_due(role)
        with self._lock:
            if not self._cached_state(role)["active"]:
                return None
            record = self._records.get(role)
        encoded = _record_value(record, "image_base64")
        if not isinstance(encoded, str) or not encoded:
            return None
        try:
            return base64.b64decode(encoded, validate=True)
        except Exception as exc:
            self._log("debug", f"provider frame was not valid base64: {exc}")
            return None

    def status(self, *, role: str = "") -> dict[str, Any]:
        role = str(role or "")
        state = self.snapshot(role=role)
        with self._lock:
            state["polled"] = role in self._fetched_at
        state["usable"] = bool(state["active"])
        return state

    def _refresh(self, role: str) -> None:
        try:
            records = self._fetch()
            state, record = self._select(records, role=role)
        except Exception as exc:
            state = self._inactive()
            record = None
            self._log("debug", f"provider frame query failed: {exc}")
        with self._condition:
            self._states[role] = state
            self._records[role] = record
            self._fetched_at[role] = self._clock()
            self._refreshing.discard(role)
            self._condition.notify_all()

    def _refresh_synchronously_if_due(self, role: str) -> None:
        with self._condition:
            while True:
                fetched_at = self._fetched_at.get(role)
                due = fetched_at is None or self._clock() - fetched_at >= self._ttl
                if not due:
                    return
                if role not in self._refreshing:
                    self._refreshing.add(role)
                    break
                self._condition.wait()
        self._refresh(role)

    def _cached_state(self, role: str) -> dict[str, Any]:
        state = dict(self._states.get(role, self._inactive()))
        if not state["active"]:
            return state
        captured_at = self._captured_at(self._records.get(role))
        if captured_at is None:
            return self._inactive()
        age = self._wall_clock() - captured_at
        if age < 0.0 or age > self._max_age:
            return self._inactive()
        state["age_seconds"] = age
        return state

    def _select(self, records: Any, *, role: str) -> tuple[dict[str, Any], Any]:
        if not isinstance(records, Iterable) or isinstance(
            records, (str, bytes, bytearray, Mapping)
        ):
            return self._inactive(), None
        now = self._wall_clock()
        candidates: list[tuple[float, Any]] = []
        for record in records:
            if _record_value(record, "source") != SOURCE_SCREEN:
                continue
            record_role = str(_record_value(record, "lanlan_name") or "")
            if record_role != role:
                continue
            captured_at = self._captured_at(record)
            if captured_at is None:
                continue
            age = now - captured_at
            if age < 0.0 or age > self._max_age:
                continue
            candidates.append((captured_at, record))
        if not candidates:
            return self._inactive(), None
        captured_at, record = max(candidates, key=lambda item: item[0])
        generation = _record_value(record, "generation")
        return {
            "active": True,
            "source": SOURCE_SCREEN,
            "age_seconds": now - captured_at,
            "role": str(_record_value(record, "lanlan_name") or ""),
            "frame_id": str(_record_value(record, "frame_id") or ""),
            "generation": generation if isinstance(generation, int) else None,
            "provider_delivered": True,
        }, record

    @staticmethod
    def _captured_at(record: Any) -> float | None:
        captured_at = _record_value(record, "captured_at")
        if not isinstance(captured_at, (int, float)) or isinstance(captured_at, bool):
            captured_at = _record_value(record, "timestamp")
        if not isinstance(captured_at, (int, float)) or isinstance(captured_at, bool):
            return None
        try:
            value = float(captured_at)
        except (OverflowError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    @staticmethod
    def _inactive() -> dict[str, Any]:
        return {
            "active": False,
            "source": "",
            "age_seconds": None,
            "role": "",
            "frame_id": "",
            "generation": None,
            "provider_delivered": False,
        }

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        method = getattr(self._logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:
                pass


__all__ = ["SOURCE_SCREEN", "ProviderFrameProbe"]
