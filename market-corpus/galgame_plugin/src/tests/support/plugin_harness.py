from __future__ import annotations

import time
from pathlib import Path

from market_plugins.galgame_plugin.plugin_core import GalgameBridgePlugin
from tests.support.bridge_fixtures import session_snapshot, session_state, write_session


class Logger:
    def info(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def warning(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def error(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def debug(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def exception(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


class PluginContext:
    plugin_id = "galgame_plugin"
    metadata: dict[str, object] = {}
    bus = None

    def __init__(self, plugin_dir: Path, config: dict[str, object]) -> None:
        self.logger = Logger()
        self.config_path = plugin_dir / "plugin.toml"
        self._effective_config = {
            "plugin": {"store": {"enabled": True}, "database": {"enabled": False}},
            "plugin_state": {"backend": "memory"},
        }
        self._config = config
        self.pushed_messages: list[dict[str, object]] = []

    async def get_own_config(self, timeout: float = 5.0) -> dict[str, object]:
        del timeout
        return {"config": self._config}

    async def get_own_base_config(self, timeout: float = 5.0) -> dict[str, object]:
        del timeout
        return {"config": self._config}

    async def get_own_profiles_state(self, timeout: float = 5.0) -> dict[str, object]:
        del timeout
        return {"profiles": [], "active": None}

    async def get_own_profile_config(
        self, profile_name: str, timeout: float = 5.0
    ) -> dict[str, object]:
        del timeout
        return {"profile_name": profile_name, "config": self._config}

    async def get_own_effective_config(
        self, profile_name: str | None = None, timeout: float = 5.0
    ) -> dict[str, object]:
        del profile_name, timeout
        return {"config": self._config}

    async def update_own_config(
        self, updates: dict[str, object], timeout: float = 10.0
    ) -> dict[str, object]:
        del timeout
        self._config = {**self._config, **dict(updates or {})}
        return {"config": self._config}

    async def query_plugins(
        self, filters: dict[str, object], timeout: float = 5.0
    ) -> dict[str, object]:
        del filters, timeout
        return {"plugins": []}

    async def trigger_plugin_event(self, **kwargs: object) -> None:
        raise RuntimeError(f"unexpected trigger_plugin_event: {kwargs}")

    async def get_system_config(self, timeout: float = 5.0) -> dict[str, object]:
        del timeout
        return {}

    async def query_memory(
        self, bucket_id: str, query: str, timeout: float = 5.0
    ) -> dict[str, object]:
        del bucket_id, query, timeout
        return {"items": []}

    async def run_update(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {"ok": True}

    async def export_push(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {"ok": True}

    async def finish(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {"ok": True}

    def push_message(self, **kwargs: object) -> dict[str, object]:
        self.pushed_messages.append(dict(kwargs))
        return {"ok": True}

    def update_status(self, status: object) -> None:
        del status


class FakeHostAdapter:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.tasks: dict[str, dict[str, object]] = {}
        self._counter = 0

    async def get_computer_use_availability(
        self, *, timeout: float = 1.5
    ) -> dict[str, object]:
        del timeout
        if self.ready:
            return {"ready": True, "reasons": []}
        return {"ready": False, "reasons": ["computer_use unavailable"]}

    async def run_computer_use_instruction(
        self, instruction: str, *, lanlan_name: str = "", timeout: float = 5.0
    ) -> dict[str, object]:
        del lanlan_name, timeout
        self._counter += 1
        task_id = f"task-{self._counter}"
        self.started.append(instruction)
        self.tasks[task_id] = {"id": task_id, "status": "running", "result": None}
        return {"task_id": task_id, "status": "running"}

    async def get_task(
        self, task_id: str, *, timeout: float = 2.0
    ) -> dict[str, object]:
        del timeout
        return dict(self.tasks[task_id])

    async def cancel_task(
        self, task_id: str, *, timeout: float = 5.0
    ) -> dict[str, object]:
        del timeout
        self.cancelled.append(task_id)
        self.tasks[task_id] = {
            "id": task_id,
            "status": "cancelled",
            "error": "Cancelled by test",
        }
        return {"success": True, "task_id": task_id, "status": "cancelled"}

    async def shutdown(self) -> None:
        return None


class FakeLLMGateway:
    def __init__(self, *, reply_text: str) -> None:
        self.reply_text = reply_text

    async def suggest_choice(self, context: dict[str, object]) -> dict[str, object]:
        del context
        return {"degraded": True, "choices": [], "diagnostic": "not needed"}

    async def agent_reply(self, context: dict[str, object]) -> dict[str, object]:
        del context
        return {"degraded": False, "reply": self.reply_text, "diagnostic": ""}


def make_effective_config(bridge_root: Path) -> dict[str, object]:
    return {
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


def make_plugin_dirs(tmp_path: Path) -> tuple[Path, Path]:
    plugin_dir = tmp_path / "plugin cfg 中文"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.toml").write_text("", encoding="utf-8")
    static_dir = plugin_dir / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<!doctype html><title>ui</title>", encoding="utf-8"
    )
    bridge_root = tmp_path / "bridge root 中文"
    bridge_root.mkdir()
    return plugin_dir, bridge_root


async def make_active_plugin(
    tmp_path: Path,
) -> tuple[GalgameBridgePlugin, PluginContext]:
    plugin_dir, bridge_root = make_plugin_dirs(tmp_path)
    game_dir = bridge_root / "demo.alpha"
    game_dir.mkdir(parents=True)
    write_session(
        game_dir / "session.json",
        session_snapshot(
            game_id="demo.alpha",
            session_id="sess-a",
            last_seq=1,
            state={
                **session_state(speaker="雪乃", text="继续前进。", scene_id="scene-a"),
                "line_id": "line-1",
            },
        ),
    )
    (game_dir / "events.jsonl").write_text("", encoding="utf-8")
    ctx = PluginContext(plugin_dir, make_effective_config(bridge_root))
    plugin = GalgameBridgePlugin(ctx)
    await plugin.startup()
    await plugin._poll_bridge(force=True)
    local = plugin._snapshot_state()
    local.update(
        {
            "active_game_id": "demo.alpha",
            "active_session_id": "sess-a",
            "current_connection_state": "active",
            "stream_reset_pending": False,
            "latest_snapshot": session_state(
                speaker="雪乃", text="继续前进。", scene_id="scene-a"
            ),
            "last_seen_data_monotonic": time.monotonic(),
            "next_poll_at_monotonic": time.monotonic() + 3600.0,
        }
    )
    plugin._commit_state(local)
    return plugin, ctx


__all__ = [
    "FakeHostAdapter",
    "FakeLLMGateway",
    "Logger",
    "PluginContext",
    "make_active_plugin",
    "make_effective_config",
    "make_plugin_dirs",
]
