"""主聊天工具注册表的轻量巡检、恢复与诊断。"""

from __future__ import annotations

import asyncio
import inspect
import threading
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Any, Callable

from .local_http import read_local_json

__all__ = ["TOOL_WATCH_INTERVAL_SEC", "ToolWatch", "missing_tool_names"]

TOOL_WATCH_INTERVAL_SEC = 10.0
_REISSUE_INTERVAL_SEC = 5.0
_PREFLIGHT_CACHE_SEC = 2.0
_HTTP_TIMEOUT_SEC = 2.0


def _groups(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping) or payload.get("ok") is False:
        return None
    groups = payload.get("tools_by_role")
    if groups is None:
        if not payload or not all(
            isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping))
            for value in payload.values()
        ):
            return None
        groups = payload
    if not isinstance(groups, Mapping) or not groups:
        return None
    for tools in groups.values():
        if not isinstance(tools, Iterable) or isinstance(tools, (str, bytes, Mapping)):
            return None
    return groups


def _missing(
    tools: Iterable[Any],
    wanted: set[str],
    *,
    expected_source: str = "",
) -> list[str]:
    present = {
        str(entry.get("name")).strip()
        for entry in tools
        if (
            isinstance(entry, Mapping)
            and entry.get("name")
            and (
                not expected_source
                or not entry.get("source")
                or str(entry.get("source")) == expected_source
            )
        )
    }
    return sorted(wanted - present)


def missing_tool_names(
    payload: Any,
    declared: Iterable[str],
    *,
    role: str | None = None,
) -> list[str]:
    """Return declared tools missing from a role or from all returned roles.

    Unknown response shapes retain the historical conservative result (all names
    missing). ``ToolWatch`` validates the shape before it decides to reissue.
    """
    wanted = {name for raw in declared if (name := str(raw).strip())}
    if not wanted:
        return []
    groups = _groups(payload)
    if groups is None:
        return sorted(wanted)
    if role is not None:
        tools = groups.get(role)
        return sorted(wanted) if tools is None else _missing(tools, wanted)
    present: set[str] = set()
    for tools in groups.values():
        present.update(
            str(entry.get("name")).strip()
            for entry in tools
            if isinstance(entry, Mapping) and entry.get("name")
        )
    return sorted(wanted - present)


async def _default_fetch(url: str) -> dict[str, Any] | None:
    port = 48911
    try:
        from config import MAIN_SERVER_PORT

        port = int(MAIN_SERVER_PORT)
    except Exception:  # noqa: BLE001
        pass
    base = f"http://127.0.0.1:{port}{url}"

    def _do() -> dict[str, Any]:
        value = read_local_json(base, timeout=_HTTP_TIMEOUT_SEC)
        return value if isinstance(value, dict) else {}

    try:
        return await asyncio.to_thread(_do)
    except Exception:  # noqa: BLE001
        return None


class ToolWatch:
    """Cross-event-loop-safe registry checker.

    Host timer callbacks can run in separate threads/event loops, so the
    in-flight guard is a regular non-blocking ``threading.Lock`` rather than
    an ``asyncio.Lock``.
    """

    def __init__(
        self,
        plugin: Any,
        *,
        logger: Any = None,
        fetch: Callable[[str], Any] | None = None,
        interval_sec: float = TOOL_WATCH_INTERVAL_SEC,
    ):
        self._plugin = plugin
        self._logger = logger
        self._fetch = fetch or _default_fetch
        self._interval = max(1.0, float(interval_sec))
        self._last_run_at = 0.0
        self._last_reissue_at = 0.0
        self._lock = threading.Lock()
        self._inflight = False
        self._role_states: dict[str, dict[str, Any]] = {}
        self._last_status = "unknown"
        self._last_checked_at: float | None = None
        self._last_missing_by_role: dict[str, list[str]] = {}
        self._last_missing: list[str] = []
        self._last_reissued = 0
        self.checks = 0
        self.reissues = 0
        self.confirmations = 0
        self.gaps = 0
        self.gap_seconds_max = 0.0
        self.unreachable = 0
        self.last_healthy_at: float | None = None
        self.last_gap_at = 0.0
        self._healthy_baseline: dict[str, float] = {}
        self._gap_open: set[str] = set()

    def _expected_source(self) -> str:
        plugin_id = str(getattr(self._plugin, "plugin_id", "") or "").strip()
        return f"plugin:{plugin_id}" if plugin_id else ""

    def _log(self, message: str, *args: Any, exc: bool = False) -> None:
        if self._logger is None:
            return
        try:
            (self._logger.exception if exc else self._logger.info)(message, *args)
        except Exception:  # noqa: BLE001
            pass

    def _declared_names(self) -> list[str]:
        lister = getattr(self._plugin, "list_llm_tools", None)
        if not callable(lister):
            return []
        try:
            tools = lister()
        except Exception:  # noqa: BLE001
            return []
        return sorted(
            {
                str(entry.get("name")).strip()
                for entry in (tools or [])
                if isinstance(entry, Mapping) and entry.get("name")
            }
        )

    @staticmethod
    def _url(role: str | None) -> str:
        if role is None:
            return "/api/tools"
        return f"/api/tools?{urllib.parse.urlencode({'role': role})}"

    async def _fetch_payload(self, url: str) -> Any:
        result = self._fetch(url)
        return await result if inspect.isawaitable(result) else result

    def _begin(self) -> bool:
        with self._lock:
            if self._inflight:
                return False
            self._inflight = True
            return True

    def _end(self) -> None:
        with self._lock:
            self._inflight = False

    def _update_gap(self, role: str, missing: list[str], now: float) -> None:
        if not missing:
            if role in self._gap_open:
                self.confirmations += 1
            self._gap_open.discard(role)
            self._healthy_baseline[role] = now
            self.last_healthy_at = now
            return
        if role not in self._gap_open:
            self.gaps += 1
            self.last_gap_at = now
            baseline = self._healthy_baseline.get(role)
            if baseline is not None:
                self.gap_seconds_max += max(0.0, now - baseline)
            self._gap_open.add(role)

    @staticmethod
    def _state(
        role: str,
        *,
        status: str,
        checked_at: float | None,
        missing: list[str] | None = None,
        confirmed: bool = False,
        cached: bool = False,
        now: float | None = None,
    ) -> dict[str, Any]:
        age = (
            round(max(0.0, float(now) - float(checked_at)), 3)
            if now is not None and isinstance(checked_at, (int, float))
            and not isinstance(checked_at, bool)
            else None
        )
        return {
            "status": status,
            "scope": "registry",
            "role": role,
            "checked_at": checked_at,
            "age_sec": age,
            "missing": list(missing or []),
            "confirmed": bool(confirmed),
            "cached": bool(cached),
        }

    async def _reissue(self, names: list[str], *, now: float) -> int:
        if not names or now - self._last_reissue_at < _REISSUE_INTERVAL_SEC:
            return 0
        store = getattr(self._plugin, "_llm_tools", None)
        notify = getattr(self._plugin, "_notify_llm_tool_registered", None)
        if not isinstance(store, dict) or not callable(notify):
            return 0
        sent = 0
        for name in names:
            metadata = store.get(name)
            if metadata is None:
                continue
            try:
                result = notify(metadata)
                if inspect.isawaitable(result):
                    await result
                sent += 1
            except Exception:  # noqa: BLE001
                self._log("sticker_manager tool watch: re-emit failed for {}", name, exc=True)
        if sent:
            self._last_reissue_at = now
            self.reissues += sent
        return sent

    async def _check(self, *, now: float, role: str | None) -> dict[str, Any]:
        declared = self._declared_names()
        if not declared:
            return {"status": "no_tools", "scope": "registry", "role": role, "confirmed": False}
        if not self._begin():
            return {"status": "busy", "scope": "registry", "role": role, "confirmed": False}
        try:
            payload = await self._fetch_payload(self._url(role))
            if payload is None:
                self.unreachable += 1
                if role is not None:
                    self._role_states[role] = self._state(
                        role, status="unreachable", checked_at=None, now=now
                    )
                    self._healthy_baseline.pop(role, None)
                return {
                    "status": "unreachable",
                    "scope": "registry",
                    "role": role,
                    "confirmed": False,
                    "cached": False,
                }
            groups = _groups(payload)
            if groups is None:
                if role is not None:
                    self._role_states[role] = self._state(
                        role, status="unknown", checked_at=None, now=now
                    )
                    self._healthy_baseline.pop(role, None)
                return {
                    "status": "unknown",
                    "scope": "registry",
                    "role": role,
                    "confirmed": False,
                    "cached": False,
                }

            checked_at = float(now)
            self.checks += 1
            wanted = set(declared)
            expected_source = self._expected_source()
            if role is not None:
                if role not in groups:
                    state = self._state(role, status="unknown", checked_at=None, now=now)
                    with self._lock:
                        self._role_states[role] = state
                        self._healthy_baseline.pop(role, None)
                    return state
                missing = _missing(groups[role], wanted, expected_source=expected_source)
                reissued = await self._reissue(missing, now=now)
                status = "healthy" if not missing else (
                    "recovering" if reissued else "repair_pending"
                )
                state = self._state(
                    role,
                    status=status,
                    checked_at=checked_at,
                    missing=missing,
                    confirmed=not missing,
                    now=now,
                )
                with self._lock:
                    self._update_gap(role, missing, now)
                    self._role_states[role] = state
                return state

            by_role = {
                str(name): _missing(tools, wanted, expected_source=expected_source)
                for name, tools in groups.items()
            }
            all_missing = sorted({
                name for missing in by_role.values() for name in missing
            })
            reissued = await self._reissue(all_missing, now=now)
            with self._lock:
                self._last_checked_at = checked_at
                self._last_missing_by_role = {
                    name: missing for name, missing in by_role.items() if missing
                }
                self._last_missing = list(all_missing)
                self._last_reissued = reissued
                for name, missing in by_role.items():
                    self._update_gap(name, missing, now)
                    status = "healthy" if not missing else (
                        "recovering" if reissued else "repair_pending"
                    )
                    self._role_states[name] = self._state(
                        name,
                        status=status,
                        checked_at=checked_at,
                        missing=missing,
                        confirmed=not missing,
                        now=now,
                    )
                self._last_status = "healthy" if not all_missing else (
                    "recovering" if reissued else "repair_pending"
                )
            if all_missing:
                self._log(
                    "sticker_manager tool watch: missing roles={} reissued={} tools={}",
                    len(self._last_missing_by_role),
                    reissued,
                    ", ".join(all_missing),
                )
            return self.snapshot(now=now)
        except Exception:  # noqa: BLE001
            self._last_status = "watch_failed"
            self._log("sticker_manager tool watch failed", exc=True)
            return {
                "status": "watch_failed",
                "scope": "registry",
                "role": role,
                "confirmed": False,
            }
        finally:
            self._end()

    async def maybe_run(self, *, now: float) -> dict[str, Any]:
        with self._lock:
            if now - self._last_run_at < self._interval:
                return {"status": "waiting"}
            self._last_run_at = now
        return await self._check(now=now, role=None)

    async def before_reminder(self, *, role: str, now: float) -> dict[str, Any]:
        target = str(role or "").strip()
        if not target:
            return {"status": "unknown", "scope": "registry", "role": target, "confirmed": False}
        with self._lock:
            cached = dict(self._role_states.get(target, {}))
        checked_at = cached.get("checked_at") if isinstance(cached, Mapping) else None
        if (
            isinstance(cached, Mapping)
            and isinstance(checked_at, (int, float))
            and not isinstance(checked_at, bool)
            and now - float(checked_at) <= _PREFLIGHT_CACHE_SEC
            and cached.get("status") not in {"unknown", "unreachable", "busy"}
        ):
            result = dict(cached)
            result["cached"] = True
            result["age_sec"] = round(max(0.0, now - float(checked_at)), 3)
            return result
        result = await self._check(now=now, role=target)
        result = dict(result)
        result["cached"] = False
        return result

    def snapshot(self, *, now: float) -> dict[str, Any]:
        with self._lock:
            checked_at = self._last_checked_at
            status = self._last_status
            missing_by_role = {
                role: list(missing)
                for role, missing in self._last_missing_by_role.items()
            }
            missing = list(self._last_missing)
            reissued = self._last_reissued
            checks = self.checks
            confirmations = self.confirmations
            gaps = self.gaps
            gap_seconds_max = self.gap_seconds_max
            unreachable = self.unreachable
            last_healthy_at = self.last_healthy_at
            last_gap_at = self.last_gap_at
        age = (
            round(max(0.0, now - checked_at), 3)
            if isinstance(checked_at, (int, float)) and not isinstance(checked_at, bool)
            else None
        )
        return {
            "status": status,
            "scope": "registry",
            "checked_at": checked_at,
            "age_sec": age,
            "missing_by_role": missing_by_role,
            "missing": missing,
            "reissued": reissued,
            "checks": checks,
            "reissues": self.reissues,
            "confirmations": confirmations,
            "gaps": gaps,
            "gap_seconds_max": round(gap_seconds_max, 1),
            "unreachable": unreachable,
            "last_healthy_at": last_healthy_at,
            "last_gap_at": last_gap_at,
        }
