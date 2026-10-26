from __future__ import annotations

import threading
import time
from importlib import import_module
from types import SimpleNamespace

from tests.support.plugin_runtime_stubs import install_plugin_runtime_stubs

install_plugin_runtime_stubs()

plugin_core_module = import_module("market_plugins.galgame_plugin.plugin_core")
GalgamePlugin = plugin_core_module.GalgamePlugin


def test_load_context_snapshot_for_state_falls_back_to_active_game() -> None:
    calls: list[str] = []

    class _Persist:
        def load_context_snapshot(
            self,
            *,
            current_game_id: str,
            **_: object,
        ) -> dict[str, object]:
            calls.append(current_game_id)
            if current_game_id == "game-active":
                return {
                    "game_id": "game-active",
                    "summary_seed": "restored",
                    "saved_at": time.time(),
                }
            return {}

    plugin = GalgamePlugin.__new__(GalgamePlugin)
    plugin._cfg = SimpleNamespace(
        context_persist_enabled=True,
        context_persist_max_age_seconds=3600.0,
        context_persist_require_game_id=True,
    )
    plugin._state = SimpleNamespace(bound_game_id="", active_game_id="game-active")
    plugin._persist = _Persist()

    assert plugin._load_context_snapshot_for_state()["summary_seed"] == "restored"
    assert calls == ["game-active"]


def test_load_context_snapshot_allows_missing_game_id_when_not_required() -> None:
    calls: list[str] = []

    class _Persist:
        def load_context_snapshot(
            self,
            *,
            current_game_id: str,
            **_: object,
        ) -> dict[str, object]:
            calls.append(current_game_id)
            return {
                "game_id": "",
                "summary_seed": "restored without game id",
                "saved_at": time.time(),
            }

    plugin = GalgamePlugin.__new__(GalgamePlugin)
    plugin._cfg = SimpleNamespace(
        context_persist_enabled=True,
        context_persist_max_age_seconds=3600.0,
        context_persist_require_game_id=False,
    )
    plugin._state = SimpleNamespace(bound_game_id="", active_game_id="")
    plugin._persist = _Persist()

    assert plugin._load_context_snapshot_for_state()["summary_seed"] == (
        "restored without game id"
    )
    assert calls == [""]


def test_persist_context_snapshot_allows_missing_game_id_when_not_required() -> None:
    saved: list[dict[str, object]] = []

    class _Persist:
        def persist_context_snapshot(self, snapshot: dict[str, object]) -> None:
            saved.append(dict(snapshot))

    plugin = GalgamePlugin.__new__(GalgamePlugin)
    plugin._cfg = SimpleNamespace(
        context_persist_enabled=True,
        context_persist_require_game_id=False,
    )
    plugin._state = SimpleNamespace(
        active_game_id="",
        active_session_id="",
        latest_snapshot={"scene_id": "scene-a", "route_id": ""},
        context_snapshot={},
    )
    plugin._state_lock = threading.Lock()
    plugin._state_dirty = False
    plugin._cached_snapshot = {"stale": True}
    plugin._persist = _Persist()

    plugin._persist_context_snapshot_from_summary(
        {
            "game_id": "",
            "scene_id": "scene-a",
            "route_id": "",
            "stable_lines": [{"line_id": "line-1"}],
        },
        {"summary": "summary without game id"},
    )

    assert saved[0]["game_id"] == ""
    assert saved[0]["summary_seed"] == "summary without game id"
    assert plugin._state.context_snapshot["summary_seed"] == "summary without game id"
    assert plugin._state_dirty is True
    assert plugin._cached_snapshot is None


def test_persist_context_snapshot_skips_write_when_session_turns_stale() -> None:
    saved: list[dict[str, object]] = []

    class _Persist:
        def persist_context_snapshot(self, snapshot: dict[str, object]) -> None:
            saved.append(dict(snapshot))

    plugin = GalgamePlugin.__new__(GalgamePlugin)
    plugin._cfg = SimpleNamespace(
        context_persist_enabled=True,
        context_persist_require_game_id=True,
    )
    plugin._state = SimpleNamespace(
        active_game_id="demo.alpha",
        active_session_id="sess-a",
        latest_snapshot={"scene_id": "scene-a", "route_id": "route-a"},
        context_snapshot={},
    )
    plugin._state_lock = threading.Lock()
    plugin._state_dirty = False
    plugin._cached_snapshot = {"stale": True}
    plugin._persist = _Persist()
    plugin.logger = SimpleNamespace(warning=lambda *_args, **_kwargs: None)
    checks = 0
    original_liveness = plugin._context_snapshot_liveness_matches

    def _flip_session_after_first_check(**kwargs: object) -> bool:
        nonlocal checks
        checks += 1
        if checks == 2:
            plugin._state.active_session_id = "sess-b"
        return original_liveness(**kwargs)

    plugin._context_snapshot_liveness_matches = _flip_session_after_first_check

    plugin._persist_context_snapshot_from_summary(
        {
            "game_id": "demo.alpha",
            "session_id": "sess-a",
            "scene_id": "scene-a",
            "route_id": "route-a",
            "stable_lines": [{"line_id": "line-1"}],
        },
        {"summary": "stale during write"},
    )

    assert checks == 2
    assert saved == []
    assert plugin._state.context_snapshot == {}
    assert plugin._state_dirty is False
