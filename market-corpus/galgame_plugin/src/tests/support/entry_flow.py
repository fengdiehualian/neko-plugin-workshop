from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.support.memory_flow import _Ctx, _make_effective_config, _make_plugin_dirs, _session, _session_state
from tests.support.plugin_runtime_stubs import install_plugin_runtime_stubs

install_plugin_runtime_stubs()

plugin_core = import_module("market_plugins.galgame_plugin.plugin_core")
service = import_module("market_plugins.galgame_plugin.service")
GalgamePlugin = plugin_core.GalgamePlugin


class EntryContext(_Ctx):
    async def register_static_ui(self, **kwargs: object):
        return dict(kwargs)

    async def set_list_actions(self, actions: list[dict[str, object]]):
        return {"actions": actions}


def shared_state(
    *,
    game_id: str = "demo.alpha",
    session_id: str = "sess-a",
    last_seq: int = 2,
    snapshot: dict[str, object] | None = None,
    history_events: list[dict[str, object]] | None = None,
    history_lines: list[dict[str, object]] | None = None,
    history_observed_lines: list[dict[str, object]] | None = None,
    history_choices: list[dict[str, object]] | None = None,
    active_data_source: str | None = None,
) -> dict[str, object]:
    snap = snapshot or _session_state(scene_id="scene-a", line_id="line-1", text="line")
    state = {
        "mode": "choice_advisor",
        "push_notifications": True,
        "current_connection_state": "active",
        "active_game_id": game_id,
        "active_session_id": session_id,
        "last_seq": last_seq,
        "latest_snapshot": snap,
        "history_events": list(history_events or []),
        "history_lines": list(history_lines or []),
        "history_observed_lines": list(history_observed_lines or []),
        "history_choices": list(history_choices or []),
    }
    if active_data_source is not None:
        state["active_data_source"] = active_data_source
    return state


def apply_shared(plugin: Any, shared: dict[str, object]) -> None:
    with plugin._state_lock:
        for key, value in shared.items():
            if hasattr(plugin._state, key):
                setattr(plugin._state, key, value)
        plugin._state_dirty = True
        plugin._cached_snapshot = None


class DegradedGateway:
    async def explain_line(self, context: dict[str, Any]):
        return service.build_explain_degraded_result(context, diagnostic="gateway_unavailable")

    async def summarize_scene(self, context: dict[str, Any]):
        return service.build_summarize_degraded_result(context, diagnostic="gateway_unavailable")

    async def suggest_choice(self, context: dict[str, Any]):
        return service.build_suggest_degraded_result(context, diagnostic="gateway_unavailable")


class EntryAgent:
    async def query_status(self, shared: dict[str, Any]):
        del shared
        return {"action": "query_status", "status": "standby", "recent_pushes": []}

    async def query_context(self, shared: dict[str, Any], *, context_query: str):
        del shared
        return {"action": "query_context", "result": context_query, "degraded": True, "status": "standby"}


def make_phase2_plugin(tmp_path: Path, *, shared: dict[str, object] | None = None):
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    raw = _make_effective_config(bridge_root)
    plugin = GalgamePlugin(EntryContext(plugin_dir, raw))
    plugin._cfg = service.build_config(raw)
    plugin._llm_gateway = DegradedGateway()
    plugin._game_agent = EntryAgent()
    plugin._persist = SimpleNamespace(load_context_snapshot=lambda **_k: {}, persist_context_snapshot=lambda **_k: {})
    apply_shared(plugin, shared or shared_state())
    return plugin


def make_install_plugin(tmp_path: Path, *, install_root: Path):
    plugin_dir, bridge_root = _make_plugin_dirs(tmp_path)
    raw = _make_effective_config(bridge_root, memory_reader={"enabled": True, "install_target_dir": str(install_root)})
    plugin = GalgamePlugin(EntryContext(plugin_dir, raw))
    plugin._cfg = service.build_config(raw)
    plugin._refresh_dependency_status = lambda: None

    async def status():
        executable = install_root / "TextractorCLI.exe"
        return {"textractor": {"installed": executable.exists(), "detected_path": str(executable) if executable.exists() else ""}}

    async def poll(**_kwargs: object):
        return None

    plugin._build_status_payload_async = status
    plugin._poll_bridge = poll
    return plugin


def memory_reader_session(*, game_id: str, session_id: str, state: dict[str, object], last_seq: int):
    payload = _session(game_id=game_id, session_id=session_id, last_seq=last_seq, state=state)
    payload.update({"bridge_sdk_version": "memory-reader-0.1.0", "metadata": {"source": "memory_reader"}})
    return payload


def ocr_reader_session(*, game_id: str, session_id: str, state: dict[str, object], last_seq: int):
    payload = _session(game_id=game_id, session_id=session_id, last_seq=last_seq, state=state)
    payload.update({"bridge_sdk_version": "ocr-reader-0.1.0", "metadata": {"source": "ocr_reader"}})
    return payload
