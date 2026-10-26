from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

BRIDGE_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "fixtures" / "galgame_plugin"
)


def bridge_fixture_game_dir(scenario: str) -> Path:
    scenario_root = BRIDGE_FIXTURE_ROOT / scenario
    game_dirs = sorted(path for path in scenario_root.iterdir() if path.is_dir())
    if len(game_dirs) != 1:
        raise AssertionError(
            f"expected one game fixture in {scenario_root}, found {len(game_dirs)}"
        )
    return game_dirs[0]


def copy_bridge_fixture_scenario(bridge_root: Path, scenario: str) -> Path:
    source = bridge_fixture_game_dir(scenario)
    target = bridge_root / source.name
    shutil.copytree(source, target)
    return target


def session_state(
    *,
    speaker: str = "",
    text: str = "",
    scene_id: str = "boot",
) -> dict[str, object]:
    return {
        "speaker": speaker,
        "text": text,
        "choices": [],
        "scene_id": scene_id,
        "line_id": "",
        "route_id": "",
        "is_menu_open": False,
        "save_context": {"kind": "unknown", "slot_id": "", "display_name": ""},
        "ts": "2026-04-21T08:30:00Z",
    }


def session_snapshot(
    *,
    game_id: str,
    session_id: str,
    last_seq: int,
    state: dict[str, object],
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "game_id": game_id,
        "game_title": game_id,
        "engine": "renpy",
        "session_id": session_id,
        "started_at": "2026-04-21T08:30:00Z",
        "last_seq": last_seq,
        "locale": "ja-JP",
        "bridge_sdk_version": "1.0.0",
        "state": state,
    }


def bridge_event(
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


def write_session(path: Path, payload: dict[str, object], *, bom: bool = False) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(("\ufeff" if bom else "") + text, encoding="utf-8")


def write_events(
    path: Path,
    events: list[dict[str, Any]],
    *,
    trailing: bytes = b"",
    crlf: bool = False,
) -> int:
    line_end = b"\r\n" if crlf else b"\n"
    data = b"".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + line_end
        for event in events
    ) + trailing
    path.write_bytes(data)
    return len(data)


__all__ = [
    "BRIDGE_FIXTURE_ROOT",
    "bridge_fixture_game_dir",
    "bridge_event",
    "copy_bridge_fixture_scenario",
    "session_snapshot",
    "session_state",
    "write_events",
    "write_session",
]
