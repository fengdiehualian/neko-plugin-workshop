from __future__ import annotations

import asyncio
from pathlib import Path

from market_plugins.galgame_plugin.plugin_core import GalgamePlugin


class SceneCapsulePlugin(GalgamePlugin):
    """Plugin under test with the scoped host push contract supplied."""

    def push_message(self, **kwargs: object) -> dict[str, object]:
        return self.ctx.push_message(**kwargs)


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _Ctx:
    plugin_id = "galgame_plugin"
    metadata = {}
    bus = None

    def __init__(self, plugin_dir: Path, effective_config: dict[str, object]) -> None:
        self.logger = _Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self._config = effective_config
        self.pushed_messages: list[dict[str, object]] = []
        self.entry_calls: list[dict[str, object]] = []
        self.entry_handler = None

    async def get_own_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_base_config(self, timeout: float = 5.0):
        return {"config": self._config}

    async def get_own_profiles_state(self, timeout: float = 5.0):
        return {"profiles": [], "active": None}

    async def get_own_profile_config(self, profile_name: str, timeout: float = 5.0):
        return {"profile_name": profile_name, "config": self._config}

    async def get_own_effective_config(self, profile_name: str | None = None, timeout: float = 5.0):
        return {"config": self._config}

    async def update_own_config(self, updates, timeout: float = 10.0):
        self._config = dict(self._config)
        self._config.update(dict(updates or {}))
        return {"config": self._config}

    async def query_plugins(self, filters, timeout: float = 5.0):
        return {"plugins": []}

    async def trigger_plugin_event(self, **kwargs):
        self.entry_calls.append(dict(kwargs))
        if self.entry_handler is None:
            raise RuntimeError("no fake trigger_plugin_event configured")
        handler = self.entry_handler
        if callable(handler):
            result = handler(**kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result
        return handler

    async def get_system_config(self, timeout: float = 5.0):
        return {}

    async def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0):
        return {"items": []}

    async def run_update(self, **kwargs):
        return {"ok": True}

    async def export_push(self, **kwargs):
        return {"ok": True}

    async def finish(self, **kwargs):
        return {"ok": True}

    def push_message(self, **kwargs):
        self.pushed_messages.append(dict(kwargs))
        return {"submitted": True}

    def update_status(self, status):
        return None


def _session_state(
    *,
    speaker: str = "",
    text: str = "",
    choices: list[dict[str, object]] | None = None,
    scene_id: str = "boot",
    line_id: str = "",
    route_id: str = "",
    is_menu_open: bool = False,
    screen_type: str = "",
    screen_ui_elements: list[dict[str, object]] | None = None,
    screen_confidence: float = 0.0,
    ts: str = "2026-04-21T08:30:00Z",
) -> dict[str, object]:
    return {
        "speaker": speaker,
        "text": text,
        "choices": list(choices or []),
        "scene_id": scene_id,
        "line_id": line_id,
        "route_id": route_id,
        "is_menu_open": is_menu_open,
        "save_context": {
            "kind": "unknown",
            "slot_id": "",
            "display_name": "",
        },
        "screen_type": screen_type,
        "screen_ui_elements": list(screen_ui_elements or []),
        "screen_confidence": screen_confidence,
        "ts": ts,
    }


def _event(
    *,
    seq: int,
    event_type: str,
    session_id: str,
    game_id: str,
    payload: dict[str, object],
    ts: str,
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "seq": seq,
        "ts": ts,
        "type": event_type,
        "session_id": session_id,
        "game_id": game_id,
        "payload": payload,
    }


def _make_plugin_dirs(tmp_path: Path) -> tuple[Path, Path]:
    plugin_dir = tmp_path / "plugin_cfg"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text("", encoding="utf-8")
    static_dir = plugin_dir / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    bridge_root = tmp_path / "bridge_root"
    bridge_root.mkdir()
    return plugin_dir, bridge_root


def _make_effective_config(bridge_root: Path, **overrides: object) -> dict[str, object]:
    config: dict[str, object] = {
        "galgame": {
            "bridge_root": str(bridge_root),
            "active_poll_interval_seconds": 0.1,
            "idle_poll_interval_seconds": 0.1,
            "stale_after_seconds": 0.2,
            "history_events_limit": 500,
            "history_lines_limit": 200,
            "history_choices_limit": 50,
            "dedupe_window_limit": 64,
            "warmup_replay_bytes_limit": 65536,
            "warmup_replay_events_limit": 50,
            "default_mode": "companion",
            "push_notifications": True,
        },
        "llm": {
            "llm_call_timeout_seconds": 15,
            "llm_max_in_flight": 2,
            "llm_request_cache_ttl_seconds": 2,
            "target_entry_ref": "",
        },
        "memory_reader": {
            "enabled": False,
            "textractor_path": "",
            "auto_detect": True,
            "poll_interval_seconds": 1,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            merged = dict(config[key])  # type: ignore[index]
            merged.update(value)
            config[key] = merged
        else:
            config[key] = value
    return config


def _shared_state(
    *,
    mode: str = "choice_advisor",
    push_notifications: bool = True,
    connection_state: str = "active",
    stream_reset_pending: bool = False,
    game_id: str = "demo.alpha",
    session_id: str = "sess-a",
    last_seq: int = 2,
    snapshot: dict[str, object] | None = None,
    history_lines: list[dict[str, object]] | None = None,
    history_observed_lines: list[dict[str, object]] | None = None,
    history_choices: list[dict[str, object]] | None = None,
    history_events: list[dict[str, object]] | None = None,
    active_data_source: str | None = None,
    ocr_reader_runtime: dict[str, object] | None = None,
    memory_reader_runtime: dict[str, object] | None = None,
) -> dict[str, object]:
    snapshot_value = snapshot or _session_state(
        speaker="雪乃",
        text="当前台词",
        scene_id="scene-a",
        line_id="line-1",
        ts="2026-04-21T08:30:02Z",
    )
    shared = {
        "mode": mode,
        "push_notifications": push_notifications,
        "current_connection_state": connection_state,
        "stream_reset_pending": stream_reset_pending,
        "active_game_id": game_id,
        "active_session_id": session_id,
        "last_seq": last_seq,
        "latest_snapshot": snapshot_value,
        "history_events": list(history_events or []),
        "history_lines": list(history_lines or []),
        "history_observed_lines": list(history_observed_lines or []),
        "history_choices": list(history_choices or []),
        "screen_type": str(snapshot_value.get("screen_type") or ""),
        "screen_ui_elements": list(snapshot_value.get("screen_ui_elements") or []),
        "screen_confidence": float(snapshot_value.get("screen_confidence") or 0.0),
        "ocr_reader_runtime": dict(ocr_reader_runtime or {}),
        "memory_reader_runtime": dict(memory_reader_runtime or {}),
    }
    if active_data_source is not None:
        shared["active_data_source"] = active_data_source
    return shared


class _FakeHostAdapter:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.tasks: dict[str, dict[str, object]] = {}
        self._counter = 0

    async def get_computer_use_availability(self, *, timeout: float = 1.5):
        if self.ready:
            return {"ready": True, "reasons": []}
        return {"ready": False, "reasons": ["computer_use unavailable"]}

    async def run_computer_use_instruction(self, instruction: str, *, lanlan_name: str = "", timeout: float = 5.0):
        self._counter += 1
        task_id = f"task-{self._counter}"
        self.started.append(instruction)
        self.tasks[task_id] = {"id": task_id, "status": "running", "result": None}
        return {"task_id": task_id, "status": "running"}

    async def get_task(self, task_id: str, *, timeout: float = 2.0):
        return dict(self.tasks[task_id])

    async def cancel_task(self, task_id: str, *, timeout: float = 5.0):
        self.cancelled.append(task_id)
        self.tasks[task_id] = {"id": task_id, "status": "cancelled", "error": "Cancelled by test"}
        return {"success": True, "task_id": task_id, "status": "cancelled"}

    async def shutdown(self) -> None:
        return None


class _FakeLLMGateway:
    def __init__(
        self,
        *,
        suggest_payload: dict[str, object] | None = None,
        reply_payload: dict[str, object] | None = None,
        summarize_payload: dict[str, object] | None = None,
        delay: float = 0.0,
        summary_delay: float = 0.0,
    ) -> None:
        self.suggest_payload = suggest_payload or {"degraded": True, "choices": [], "diagnostic": "no llm"}
        self.reply_payload = reply_payload or {"degraded": True, "reply": "fallback", "diagnostic": "no llm"}
        self.summarize_payload = summarize_payload or {
            "degraded": True,
            "summary": "",
            "diagnostic": "no llm",
        }
        self.delay = delay
        self.summary_delay = summary_delay
        self.suggest_calls: list[dict[str, object]] = []
        self.reply_calls: list[dict[str, object]] = []
        self.summarize_calls: list[dict[str, object]] = []

    async def suggest_choice(self, context: dict[str, object]):
        self.suggest_calls.append(dict(context))
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.suggest_payload)

    async def agent_reply(self, context: dict[str, object]):
        self.reply_calls.append(dict(context))
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.reply_payload)

    async def summarize_scene(self, context: dict[str, object]):
        self.summarize_calls.append(dict(context))
        if self.summary_delay:
            await asyncio.sleep(self.summary_delay)
        return dict(self.summarize_payload)


class _BlockingSummaryGateway(_FakeLLMGateway):
    def __init__(self) -> None:
        super().__init__()
        self.summary_started = asyncio.Event()
        self.release_summary = asyncio.Event()

    async def summarize_scene(self, context: dict[str, object]):
        self.summarize_calls.append(dict(context))
        self.summary_started.set()
        await self.release_summary.wait()
        scene_id = str(context.get("scene_id") or "unknown")
        return {"degraded": False, "summary": f"llm summary for {scene_id}", "diagnostic": ""}


def _summary_test_line(scene_id: str, index: int, *, session_id: str = "sess-a") -> dict[str, object]:
    del session_id
    return {
        "line_id": f"{scene_id}-line-{index}",
        "speaker": "Yukino",
        "text": f"{scene_id} dialogue line {index}.",
        "scene_id": scene_id,
        "route_id": "",
        "ts": f"2026-04-21T08:35:{index:02d}Z",
    }


def _summary_test_line_event(
    scene_id: str,
    index: int,
    *,
    seq: int,
    session_id: str = "sess-a",
) -> dict[str, object]:
    line = _summary_test_line(scene_id, index)
    return _event(
        seq=seq,
        event_type="line_changed",
        session_id=session_id,
        game_id="demo.alpha",
        payload={**line, "stability": "stable"},
        ts=str(line["ts"]),
    )
