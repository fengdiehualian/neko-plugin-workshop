from __future__ import annotations

from pathlib import Path

import pytest
from market_plugins.galgame_plugin.reader import (
    read_session_json,
    tail_events_jsonl,
    warmup_replay_events,
)
from tests.support.bridge_fixtures import bridge_fixture_game_dir

pytestmark = pytest.mark.plugin_unit


@pytest.mark.parametrize(
    ("scenario", "session_id", "last_seq", "save_kind"),
    [
        ("manual_load", "sdk-manual-load-sess", 13, "manual"),
        ("rollback", "sdk-rollback-sess", 14, "rollback"),
    ],
)
def test_bridge_fixture_session_snapshot_is_readable(
    scenario: str,
    session_id: str,
    last_seq: int,
    save_kind: str,
) -> None:
    game_dir = bridge_fixture_game_dir(scenario)

    result = read_session_json(game_dir / "session.json")

    assert result.error == ""
    assert result.session is not None
    assert result.session["session_id"] == session_id
    assert result.session["last_seq"] == last_seq
    assert result.session["state"]["scene_id"] == "after_school"
    assert result.session["state"]["is_menu_open"] is True
    assert result.session["state"]["save_context"]["kind"] == save_kind
    assert len(result.session["state"]["choices"]) == 2


@pytest.mark.parametrize(
    ("scenario", "event_count", "load_reason"),
    [
        ("manual_load", 13, "load"),
        ("rollback", 14, "rollback"),
    ],
)
def test_bridge_fixture_event_stream_tails_cleanly(
    scenario: str,
    event_count: int,
    load_reason: str,
) -> None:
    events_path = bridge_fixture_game_dir(scenario) / "events.jsonl"

    result = tail_events_jsonl(events_path, offset=0, line_buffer=b"")

    assert result.errors == []
    assert result.reset_detected is False
    assert result.line_buffer == b""
    assert result.next_offset == events_path.stat().st_size
    assert len(result.events) == event_count
    assert [event["seq"] for event in result.events] == list(
        range(1, event_count + 1)
    )
    load_event = next(
        event for event in result.events if event["type"] == "save_loaded"
    )
    assert load_event["payload"]["reason"] == load_reason
    assert result.events[-1]["type"] == "choices_shown"
    assert result.events[-1]["payload"]["line_id"] == "script.rpy:28"


@pytest.mark.parametrize(
    ("scenario", "expected_types"),
    [
        ("manual_load", ["line_changed", "save_loaded", "choices_shown"]),
        ("rollback", ["save_loaded", "line_changed", "choices_shown"]),
    ],
)
def test_bridge_fixture_warmup_replay_keeps_bounded_tail(
    scenario: str,
    expected_types: list[str],
) -> None:
    events_path: Path = bridge_fixture_game_dir(scenario) / "events.jsonl"

    events = warmup_replay_events(
        events_path,
        bytes_limit=events_path.stat().st_size,
        events_limit=3,
    )

    assert [event["type"] for event in events] == expected_types
