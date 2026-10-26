from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

GalgameBridgePlugin = Any
DetectedGameWindow = Any
_PLUGIN_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "galgame_plugin"

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


def _session(
    *,
    game_id: str,
    session_id: str,
    last_seq: int,
    state: dict[str, object],
    started_at: str = "2026-04-21T08:30:00Z",
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "game_id": game_id,
        "game_title": game_id,
        "engine": "renpy",
        "session_id": session_id,
        "started_at": started_at,
        "last_seq": last_seq,
        "locale": "ja-JP",
        "bridge_sdk_version": "1.0.0",
        "state": state,
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


def _write_session(path: Path, payload: dict[str, object], *, bom: bool = False) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if bom:
        text = "\ufeff" + text
    path.write_text(text, encoding="utf-8")


def _write_events(
    path: Path,
    events: list[dict[str, object]],
    *,
    trailing: bytes = b"",
    crlf: bool = False,
) -> int:
    line_end = b"\r\n" if crlf else b"\n"
    data = b"".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + line_end
        for event in events
    )
    data += trailing
    path.write_bytes(data)
    return len(data)


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


def _clear_bridge_root(bridge_root: Path) -> None:
    for child in bridge_root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_bridge_fixture_scenario(bridge_root: Path, scenario: str) -> Path:
    scenario_root = _PLUGIN_FIXTURE_ROOT / scenario
    if not scenario_root.is_dir():
        raise AssertionError(f"missing bridge fixture scenario: {scenario}")
    copied_game_dir: Path | None = None
    for child in scenario_root.iterdir():
        target = bridge_root / child.name
        if child.is_dir():
            shutil.copytree(child, target)
            copied_game_dir = target
        else:
            shutil.copy2(child, target)
    if copied_game_dir is None:
        raise AssertionError(f"bridge fixture scenario is empty: {scenario}")
    return copied_game_dir


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


def _enable_injected_ocr_reader(plugin: GalgameBridgePlugin, *, trigger_mode: str) -> None:
    assert plugin._cfg is not None
    plugin._cfg.ocr_reader_enabled = True
    plugin._cfg.ocr_reader_trigger_mode = trigger_mode


def _create_game_dir(
    bridge_root: Path,
    *,
    game_id: str,
    session_payload: dict[str, object],
    events: list[dict[str, object]] | None = None,
) -> Path:
    game_dir = bridge_root / game_id
    game_dir.mkdir(parents=True, exist_ok=True)
    _write_session(game_dir / "session.json", session_payload)
    _write_events(game_dir / "events.jsonl", events or [])
    return game_dir


class _FakeTextractorHandle:
    def __init__(self, lines: list[str] | None = None) -> None:
        self.lines = list(lines or [])
        self.writes: list[str] = []
        self.returncode: int | None = None
        self.terminated = False

    async def write(self, payload: str) -> None:
        self.writes.append(payload)

    async def readline(self, timeout: float) -> str | None:
        del timeout
        if not self.lines:
            return None
        return self.lines.pop(0)

    def poll(self) -> int | None:
        return self.returncode

    async def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = 0

    async def wait(self, timeout: float) -> int | None:
        del timeout
        return self.returncode


class _FakeCaptureBackend:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.capture_calls = 0

    def is_available(self) -> bool:
        return self.available

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}:{target.pid}"

    def capture_frame(self, target: DetectedGameWindow, profile) -> str:
        del profile
        self.capture_calls += 1
        return f"frame:{target.hwnd}"


class _FakeBackgroundHashFrame:
    def __init__(self, background_hash: str) -> None:
        self.info = {"galgame_source_background_hash": background_hash}


class _FakeBackgroundHashCaptureBackend:
    def __init__(self, hashes: list[str]) -> None:
        self.hashes = list(hashes)
        self.capture_calls = 0

    def is_available(self) -> bool:
        return True

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}:{target.pid}"

    def capture_frame(self, target: DetectedGameWindow, profile) -> _FakeBackgroundHashFrame:
        del target, profile
        self.capture_calls += 1
        if not self.hashes:
            return _FakeBackgroundHashFrame("")
        if len(self.hashes) == 1:
            return _FakeBackgroundHashFrame(self.hashes[0])
        return _FakeBackgroundHashFrame(self.hashes.pop(0))


class _FakePrintWindowBlankCaptureBackend:
    def __init__(self) -> None:
        self.capture_calls = 0

    def is_available(self) -> bool:
        return True

    def describe_target(self, target: DetectedGameWindow) -> str:
        return f"{target.process_name}:{target.pid}"

    def capture_frame(self, target: DetectedGameWindow, profile):
        del target, profile
        from PIL import Image

        self.capture_calls += 1
        frame = Image.new("RGB", (24, 24), (0, 0, 0))
        frame.info["galgame_capture_backend_kind"] = "printwindow"
        frame.info["galgame_capture_backend_detail"] = "selected"
        return frame


class _FakeOcrBackend:
    def __init__(self, texts: list[str] | None = None) -> None:
        self._texts = list(texts or [])

    def is_available(self) -> bool:
        return True

    def extract_text(self, image: str) -> str:
        del image
        if not self._texts:
            return ""
        if len(self._texts) == 1:
            return self._texts[0]
        return self._texts.pop(0)


class _NoopMemoryReaderManager:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self.target: dict[str, object] = {}

    def update_process_target(self, target: dict[str, object]) -> None:
        self.target = dict(target or {})

    def current_process_target(self) -> dict[str, object]:
        return dict(self.target)

    def update_config(self, config) -> None:
        del config

    async def tick(self, **kwargs) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            warnings=[],
            should_rescan=False,
            runtime={
                "enabled": True,
                "status": "disabled",
                "detail": "test_noop_memory_reader",
            },
        )

    async def shutdown(self) -> None:
        return None


def _prepare_fake_tesseract_install(install_root: Path) -> None:
    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "tesseract.exe").write_text("", encoding="utf-8")
    tessdata_dir = install_root / "tessdata"
    tessdata_dir.mkdir(parents=True, exist_ok=True)
    for language in ("chi_sim", "jpn", "eng"):
        (tessdata_dir / f"{language}.traineddata").write_text("", encoding="utf-8")
