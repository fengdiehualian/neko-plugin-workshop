from __future__ import annotations

import json
from pathlib import Path

from neko_calabiqiu.adapters.neko_dispatcher import NekoDispatcher
from neko_calabiqiu.adapters.scene_client import parse_scene
from neko_calabiqiu.core.contracts import IDLE, IN_GAME, CbqConfig
from neko_calabiqiu.core.runtime import CompanionRuntime
from neko_calabiqiu.core.scenario import ScenarioResolver


class _FakePlugin:
    def __init__(self) -> None:
        self.cfg = CbqConfig(dry_run=True)
        self.messages: list = []
        self.contexts: list = []

    def push_message(self, **kwargs):
        self.messages.append(kwargs)

    def push_context(self, *args, **kwargs):
        self.contexts.append({"args": args, "kwargs": kwargs})


def _load_fixture() -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "contract" / "fixtures" / "offline_session.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_scenario_idle_and_in_game():
    frames = _load_fixture()
    assert ScenarioResolver().resolve(parse_scene(frames[0])) == IDLE
    assert ScenarioResolver().resolve(parse_scene(frames[1])) == IN_GAME


def test_offline_fixture_replay_dry_run():
    frames = _load_fixture()
    plugin = _FakePlugin()
    dispatcher = NekoDispatcher(plugin)
    runtime = CompanionRuntime(
        CbqConfig(dry_run=True),
        push_event=lambda event, dry_run: dispatcher.push_event(event, dry_run=dry_run),
    )
    chosen_ids: list[str] = []
    outputs: list[str] = []
    for i, payload in enumerate(frames):
        result = runtime.step(parse_scene(payload), now=100.0 + i * 20)
        if result.chosen is not None:
            chosen_ids.append(result.chosen.event_id)
        if result.output:
            outputs.append(result.output)
    assert "game_started" in chosen_ids
    assert "game_stopped" in chosen_ids
    assert any(x in chosen_ids for x in ("enemy_spotted", "enemy_nearby", "multi_threat"))
    assert plugin.messages == []
    assert all(o.startswith("dry_run") for o in outputs)
