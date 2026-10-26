"""Tests for KardsScenarioResolver scene gating and hysteresis."""

from __future__ import annotations

import json

from kards_companion.runtime.contracts import (
    CRITICAL,
    ENDED,
    IN_MATCH,
    OPENING,
    OUT_OF_BATTLE,
    PRESSURE,
)
from kards_companion.runtime.scenario import KardsScenarioResolver
from kards_companion.state.model import (
    E_HQ_DAMAGE,
    E_MATCH_START,
    E_TURN_SWITCH,
    E_UNIT_DIED,
    SCENE_DEFEAT,
    SCENE_DRAFT,
    SCENE_MATCH,
    SCENE_MENU,
    SCENE_UNKNOWN,
    SCENE_VICTORY,
    SIDE_ENEMY,
    SIDE_MINE,
    GameEvent,
    MatchState,
)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_resolver(**kwargs) -> tuple[KardsScenarioResolver, Clock]:
    clock = Clock()
    return KardsScenarioResolver(now_fn=clock, **kwargs), clock


def state(
    t: float,
    *,
    scene: str = SCENE_MATCH,
    my_hq: int | None = 20,
    enemy_hq: int | None = 20,
    turn: int = 0,
    game: int = 0,
) -> MatchState:
    return MatchState(
        scene=scene,
        in_match=scene == SCENE_MATCH,
        my_hq=my_hq,
        enemy_hq=enemy_hq,
        turn_number=turn,
        game_number=game,
        detected_at=t,
    )


def tick(resolver: KardsScenarioResolver, t: float, events=(), **kw) -> str:
    return resolver.update(state(t, **kw), events, now=t)


# --------------------------------------------------------------- initial

def test_initial_snapshot_and_reset_are_json_safe() -> None:
    clock = Clock()
    resolver = KardsScenarioResolver(now_fn=clock)
    snap = resolver.snapshot()
    assert set(snap) == {
        "scenario", "reason", "since", "updated_at", "game_number",
        "context_valid", "needs_rebase", "confirmed_exit",
    }
    assert snap["scenario"] == OUT_OF_BATTLE
    assert snap["since"] is None and snap["updated_at"] is None
    assert snap["game_number"] == 0
    assert snap["context_valid"] is False
    assert snap["needs_rebase"] is False and snap["confirmed_exit"] is False
    json.dumps(snap)

    assert tick(resolver, 1.0, scene=SCENE_UNKNOWN) == OUT_OF_BATTLE
    assert resolver.snapshot()["context_valid"] is False
    assert tick(resolver, 2.0, scene=SCENE_MENU) == OUT_OF_BATTLE
    assert resolver.snapshot()["context_valid"] is False

    resolver.reset()
    assert resolver.snapshot()["scenario"] == OUT_OF_BATTLE
    assert resolver.snapshot()["since"] is None


# --------------------------------------------------------------- opening

def test_opening_is_not_forever_and_grace_ends_it() -> None:
    resolver, _clock = make_resolver()
    assert tick(resolver, 0.0) == OPENING
    assert tick(resolver, 1.0) == OPENING
    assert tick(resolver, 5.0) == OPENING
    assert tick(resolver, 6.1) == IN_MATCH
    snap = resolver.snapshot()
    assert snap["since"] == 6.1
    assert json.dumps(snap)


def test_match_start_event_alone_does_not_end_opening() -> None:
    resolver, _clock = make_resolver()
    start = GameEvent(E_MATCH_START, 5, "start", ts=0.0, details={"game": 1})
    assert tick(resolver, 0.0, events=[start]) == OPENING
    assert tick(resolver, 1.0) == OPENING


def test_turn_event_and_turn_progress_end_opening() -> None:
    resolver, _clock = make_resolver()
    tick(resolver, 0.0)
    switch = GameEvent(E_TURN_SWITCH, 4, "turn", ts=1.0)
    assert tick(resolver, 1.0, events=[switch]) == IN_MATCH

    resolver, _clock = make_resolver()
    assert tick(resolver, 0.0, turn=1) == OPENING
    assert tick(resolver, 1.0, turn=2) == IN_MATCH


# --------------------------------------------------------------- crisis

def test_confirmed_own_crisis_beats_opening_but_enemy_low_does_not() -> None:
    resolver, _clock = make_resolver()
    assert tick(resolver, 0.0, my_hq=20) == OPENING
    assert tick(resolver, 1.0, my_hq=20) == OPENING
    assert tick(resolver, 2.0, my_hq=3) == OPENING  # single low frame filtered
    assert tick(resolver, 3.0, my_hq=3) == CRITICAL
    assert resolver.snapshot()["reason"] == "own_hq_critical"

    resolver, _clock = make_resolver()
    for t, enemy in ((0.0, 20), (1.0, 20), (2.0, 1), (3.0, 1), (6.1, 1)):
        result = tick(resolver, t, enemy_hq=enemy)
    assert result == IN_MATCH
    assert result != CRITICAL


def test_single_frame_crisis_spike_and_missing_read_do_not_confirm() -> None:
    resolver, _clock = make_resolver()
    assert tick(resolver, 0.0, my_hq=20) == OPENING
    assert tick(resolver, 1.0, my_hq=3) == OPENING
    assert tick(resolver, 2.0, my_hq=20) == OPENING
    assert tick(resolver, 3.0, my_hq=20) == OPENING
    assert tick(resolver, 6.1, my_hq=20) == IN_MATCH

    resolver, _clock = make_resolver()
    assert tick(resolver, 0.0, my_hq=20) == OPENING
    assert tick(resolver, 1.0, my_hq=3) == OPENING
    assert tick(resolver, 2.0, my_hq=None) == OPENING
    assert tick(resolver, 3.0, my_hq=20) == OPENING
    assert tick(resolver, 6.1, my_hq=20) == IN_MATCH


def test_critical_exit_requires_hysteresis_seconds() -> None:
    resolver, _clock = make_resolver()
    for t, hp in ((0.0, 20), (1.0, 20), (2.0, 3), (3.0, 3)):
        assert tick(resolver, t, my_hq=hp) in (OPENING, CRITICAL)
    assert resolver.snapshot()["scenario"] == CRITICAL

    assert tick(resolver, 3.5, my_hq=8) == CRITICAL
    assert tick(resolver, 4.5, my_hq=8) == CRITICAL  # only 1s above threshold
    assert tick(resolver, 5.6, my_hq=8) == OPENING   # hysteresis met, grace not
    assert tick(resolver, 6.7, my_hq=8) == IN_MATCH


# --------------------------------------------------------------- pressure

def test_pressure_window_from_confirmed_own_loss_is_not_permanent() -> None:
    resolver, _clock = make_resolver()
    assert tick(resolver, 0.0) == OPENING
    loss = GameEvent(E_UNIT_DIED, 6, "lost", ts=1.0,
                     details={"side": SIDE_MINE, "entity": "7"})
    assert tick(resolver, 1.0, events=[loss]) == PRESSURE
    assert tick(resolver, 6.1) == PRESSURE
    assert resolver.snapshot()["reason"] == "unit_loss"
    assert tick(resolver, 9.1) == IN_MATCH


def test_pressure_ignores_enemy_loss_and_stale_events() -> None:
    resolver, _clock = make_resolver()
    tick(resolver, 0.0)
    enemy = GameEvent(E_UNIT_DIED, 6, "lost", ts=1.0,
                      details={"side": SIDE_ENEMY})
    tick(resolver, 1.0, events=[enemy])
    assert tick(resolver, 6.1) == IN_MATCH

    resolver, _clock = make_resolver()
    tick(resolver, 0.0)
    stale = GameEvent(E_HQ_DAMAGE, 7, "old", ts=-500.0,
                      details={"side": SIDE_MINE, "loss": 6})
    tick(resolver, 1.0, events=[stale])
    assert tick(resolver, 6.1) == IN_MATCH


def test_pressure_from_own_hq_damage() -> None:
    resolver, _clock = make_resolver()
    tick(resolver, 0.0)
    damage = GameEvent(E_HQ_DAMAGE, 7, "hit", ts=1.0,
                       details={"side": SIDE_MINE, "loss": 6, "hp": 14})
    tick(resolver, 1.0, events=[damage])
    assert tick(resolver, 6.1) == PRESSURE
    assert resolver.snapshot()["reason"] == "own_damage"


# --------------------------------------------------------------- interruptions

def _drive_to_in_match(resolver: KardsScenarioResolver) -> None:
    tick(resolver, 0.0)
    tick(resolver, 1.0)
    assert tick(resolver, 6.1) == IN_MATCH


def test_short_unknown_menu_draft_keep_same_match_and_need_rebase() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)

    assert tick(resolver, 7.0, scene=SCENE_UNKNOWN) == IN_MATCH
    snap = resolver.snapshot()
    assert snap["context_valid"] is False
    assert snap["needs_rebase"] is True
    assert snap["confirmed_exit"] is False

    assert tick(resolver, 7.5, scene=SCENE_MENU) == IN_MATCH
    assert tick(resolver, 8.0, scene=SCENE_DRAFT) == IN_MATCH
    assert resolver.snapshot()["confirmed_exit"] is False

    assert tick(resolver, 8.5) == IN_MATCH  # resume, not opening
    snap = resolver.snapshot()
    assert snap["context_valid"] is True
    assert snap["needs_rebase"] is True
    assert snap["scenario"] == IN_MATCH

    assert resolver.consume_rebase() is True
    assert resolver.snapshot()["needs_rebase"] is False


def test_unknown_never_confirms_exit_even_for_many_frames() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)
    for index in range(30):
        assert tick(resolver, 7.0 + index, scene=SCENE_UNKNOWN) == IN_MATCH
    snap = resolver.snapshot()
    assert snap["scenario"] == IN_MATCH
    assert snap["confirmed_exit"] is False


def test_menu_exit_requires_three_consecutive_frames() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)

    assert tick(resolver, 7.0, scene=SCENE_MENU) == IN_MATCH
    assert resolver.snapshot()["context_valid"] is False
    assert tick(resolver, 7.5, scene=SCENE_MENU) == IN_MATCH
    assert tick(resolver, 8.0, scene=SCENE_MENU) == ENDED
    snap = resolver.snapshot()
    assert snap["confirmed_exit"] is True
    assert snap["needs_rebase"] is True
    assert snap["context_valid"] is False
    assert snap["game_number"] == 1


def test_exit_streak_is_broken_by_unknown_and_mixed_menu_draft() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)
    tick(resolver, 7.0, scene=SCENE_MENU)
    tick(resolver, 7.5, scene=SCENE_MENU)
    tick(resolver, 8.0, scene=SCENE_UNKNOWN)
    tick(resolver, 8.5, scene=SCENE_MENU)
    assert tick(resolver, 9.0, scene=SCENE_MENU) == IN_MATCH
    assert resolver.snapshot()["confirmed_exit"] is False

    assert tick(resolver, 9.5, scene=SCENE_MENU) == ENDED


def test_draft_exit_also_confirms_after_three_frames() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)
    for t in (7.0, 7.5):
        assert tick(resolver, t, scene=SCENE_DRAFT) == IN_MATCH
    assert tick(resolver, 8.0, scene=SCENE_DRAFT) == ENDED
    assert resolver.snapshot()["confirmed_exit"] is True


def test_mixed_menu_and_draft_frames_count_as_consecutive_exit() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)
    assert tick(resolver, 7.0, scene=SCENE_MENU) == IN_MATCH
    assert tick(resolver, 7.5, scene=SCENE_DRAFT) == IN_MATCH
    assert tick(resolver, 8.0, scene=SCENE_MENU) == ENDED


# --------------------------------------------------------------- terminal/new game

def test_victory_terminal_then_new_match_is_opening() -> None:
    resolver, _clock = make_resolver()
    tick(resolver, 0.0, game=1)
    assert tick(resolver, 5.0, scene=SCENE_VICTORY) == ENDED
    snap = resolver.snapshot()
    assert snap["confirmed_exit"] is True and snap["context_valid"] is False
    assert snap["game_number"] == 1

    assert tick(resolver, 6.0, scene=SCENE_UNKNOWN) == ENDED
    assert tick(resolver, 7.0, game=2) == OPENING
    snap = resolver.snapshot()
    assert snap["game_number"] == 2
    assert snap["confirmed_exit"] is False
    assert snap["needs_rebase"] is False
    assert snap["context_valid"] is True


def test_defeat_is_terminal_and_tracker_game_number_starts_new_game() -> None:
    resolver, _clock = make_resolver()
    tick(resolver, 0.0, game=1)
    assert tick(resolver, 4.0, scene=SCENE_DEFEAT) == ENDED
    assert resolver.snapshot()["reason"] == "defeat"

    assert tick(resolver, 5.0, game=4) == OPENING
    assert resolver.snapshot()["game_number"] == 4


def test_in_battle_game_number_jump_starts_new_opening() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)
    assert tick(resolver, 7.0, game=3) == OPENING
    assert resolver.snapshot()["game_number"] == 3


# --------------------------------------------------------------- pre-gate

def test_observe_scene_is_pure_and_does_not_advance_exit_streak() -> None:
    resolver, _clock = make_resolver()
    gate = resolver.observe_scene(state(0.0))
    assert gate["allow_diff"] is True and gate["new_match"] is True
    assert gate["resume"] is False and gate["terminal"] is False

    _drive_to_in_match(resolver)
    before = resolver.snapshot()
    for index in range(10):
        gate = resolver.observe_scene(state(7.0 + index, scene=SCENE_MENU))
        assert gate["allow_diff"] is False
        assert gate["scenario"] == IN_MATCH
    assert resolver.snapshot() == before

    assert tick(resolver, 7.0, scene=SCENE_MENU) == IN_MATCH
    assert tick(resolver, 7.5, scene=SCENE_MENU) == IN_MATCH
    assert tick(resolver, 8.0, scene=SCENE_MENU) == ENDED


def test_observe_scene_reports_resume_after_pause() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)
    tick(resolver, 7.0, scene=SCENE_UNKNOWN)
    gate = resolver.observe_scene(state(7.5))
    assert gate["resume"] is True
    assert gate["allow_diff"] is True
    assert gate["needs_rebase"] is True
    assert gate["context_valid"] is True

    terminal = resolver.observe_scene(state(7.5, scene=SCENE_VICTORY))
    assert terminal["allow_diff"] is True and terminal["terminal"] is True
    assert terminal["context_valid"] is False

    unknown = resolver.observe_scene(state(7.5, scene=SCENE_UNKNOWN))
    assert unknown["allow_diff"] is False
    assert unknown["context_valid"] is False


def test_reset_clears_gate_state() -> None:
    resolver, _clock = make_resolver()
    _drive_to_in_match(resolver)
    tick(resolver, 7.0, scene=SCENE_MENU)
    assert resolver.snapshot()["needs_rebase"] is True
    resolver.reset()
    snap = resolver.snapshot()
    assert snap["scenario"] == OUT_OF_BATTLE
    assert snap["needs_rebase"] is False
    assert snap["confirmed_exit"] is False
    assert snap["game_number"] == 0
    assert snap["since"] is None and snap["updated_at"] is None
