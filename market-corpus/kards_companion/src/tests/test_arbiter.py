"""Behavioral tests for batch selection, hard gates and timing policy."""

import json

from kards_companion.delivery.arbiter import (
    ArbiterConfig,
    CommentaryArbiter,
    Decision,
    Selection,
)
from kards_companion.runtime.contracts import (
    ENDED,
    IN_MATCH,
    OPENING,
    category_allowed,
    event_spec,
)
from kards_companion.state.model import (
    E_CHATTER,
    E_HQ_CRITICAL,
    E_HQ_DAMAGE,
    E_MATCH_END,
    E_MATCH_START,
    E_UNIT_DIED,
    E_UNIT_PLAYED,
    GameEvent,
    MatchState,
)


class FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.t = value

    def __call__(self) -> float:
        return self.t

    def at(self, offset: float) -> float:
        return self.t + offset


def make_config(**overrides) -> ArbiterConfig:
    base = dict(
        mode="companion",
        do_not_disturb=False,
        llm_data_consent=True,
        min_priority=4,
        cooldown_seconds=0.0,
        critical_cooldown_seconds=0.0,
        max_event_age_seconds=12.0,
    )
    base.update(overrides)
    return ArbiterConfig(**base)


def make_arbiter(clock: FakeClock, **overrides) -> CommentaryArbiter:
    return CommentaryArbiter(make_config(**overrides), now_fn=clock)


def event(kind: str, *, ts: float | None, priority: int = 5,
          game: int = 1, **details) -> GameEvent:
    if ts is None:
        ts = 0.0
    return GameEvent(
        kind=kind,
        priority=priority,
        summary="s",
        ts=ts,
        details={"game": game, **details},
    )


def match_state(**overrides) -> MatchState:
    base = dict(scene="match", in_match=True, turn_number=4, my_hq=20,
                enemy_hq=20)
    base.update(overrides)
    return MatchState(**base)


# ---------------------------------------------------------------- select purity

def test_select_is_pure_and_repeatable() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    candidates = [
        event(E_UNIT_PLAYED, ts=clock.t, entity=1),
        event(E_UNIT_DIED, ts=clock.t, entity=2),
    ]
    before = arbiter.snapshot(now=clock.t)

    first = arbiter.select(candidates, state, scenario=IN_MATCH, now=clock.t)
    second = arbiter.select(candidates, state, scenario=IN_MATCH, now=clock.t)

    assert first == second
    assert arbiter.snapshot(now=clock.t) == before
    # Selecting does not consume the event: a later tick can select it again.
    assert first.event is not None


def test_select_returns_one_winner_and_marks_others_lost() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    low = event(E_UNIT_PLAYED, ts=clock.t, entity=1)
    high = event(E_UNIT_DIED, ts=clock.t, entity=2)

    selection = arbiter.select([low, high], state, scenario=IN_MATCH,
                               now=clock.t)

    assert isinstance(selection, Selection)
    assert selection.event is high
    assert len(selection.decisions) == 2
    assert selection.decisions[0] == Decision(False, "lost_in_window")
    assert selection.decisions[1].allowed


def test_select_prefers_priority_then_newest_timestamp() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    older = event(E_UNIT_DIED, ts=clock.t - 1, entity=1)
    newer = event(E_UNIT_DIED, ts=clock.t, entity=2)
    lower = event(E_UNIT_PLAYED, ts=clock.t, entity=3)

    selection = arbiter.select([older, lower, newer], state,
                               scenario=IN_MATCH, now=clock.t)
    assert selection.event is newer  # priority 6 > 5, then newest ts

    both = arbiter.select([newer, event(E_UNIT_DIED, ts=clock.t - 2,
                                        entity=4)],
                          state, scenario=IN_MATCH, now=clock.t)
    assert both.event is newer


def test_terminal_wins_even_below_preempt_and_priority() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    end = event(E_MATCH_END, ts=clock.t, result="won")
    critical = event(E_HQ_CRITICAL, ts=clock.t, side="mine", hp=3, band=1)
    selection = arbiter.select([critical, end], state, scenario=IN_MATCH,
                               now=clock.t)
    assert selection.event is end


def test_confirmed_own_critical_preempts_higher_priority_unknown() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    own = event(E_HQ_CRITICAL, ts=clock.t, side="mine", hp=3, band=1)
    custom = event("custom_alarm", ts=clock.t, priority=9, tag="x")
    assert event_spec(own).preempt is True
    assert event_spec(custom).preempt is False

    selection = arbiter.select([custom, own], state, scenario=IN_MATCH,
                               now=clock.t)
    assert selection.event is own


def test_unknown_side_never_preempts() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    unknown_crisis = event(E_HQ_CRITICAL, ts=clock.t, hp=3, band=1)
    ordinary = event("custom_alarm", ts=clock.t, priority=7, tag="x")
    assert event_spec(unknown_crisis).preempt is False

    selection = arbiter.select([unknown_crisis, ordinary], state,
                               scenario=IN_MATCH, now=clock.t)
    assert selection.event is ordinary


# ---------------------------------------------------------------- hard gates

def test_ttl_future_and_nan_are_rejected() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()

    stale = event(E_UNIT_PLAYED, ts=clock.t - 10, entity=1)
    assert arbiter.decide(stale, state, scenario=IN_MATCH,
                          now=clock.t).reason == "event_expired"

    future = event(E_HQ_DAMAGE, ts=clock.t + 5, side="mine")
    assert arbiter.decide(future, state, scenario=IN_MATCH,
                          now=clock.t).reason == "event_in_future"

    broken = event(E_HQ_DAMAGE, ts=float("nan"), side="mine")
    assert arbiter.decide(broken, state, scenario=IN_MATCH,
                          now=clock.t).reason == "invalid_timestamp"

    # Strict future rejection applies even when the injected clock is a small
    # synthetic clock (no wall-clock/synthetic-clock exemption).
    synthetic_future = event(E_HQ_DAMAGE, ts=clock.t + 5000, side="mine")
    assert arbiter.decide(synthetic_future, state, scenario=IN_MATCH,
                          now=clock.t).reason == "event_in_future"

    # A dataclass-default wall-clock timestamp is a future event under this
    # synthetic clock and is rejected as well.
    wall_clock_default = GameEvent(
        kind=E_HQ_DAMAGE, priority=6, summary="s",
        details={"game": 1, "side": "mine"})
    assert arbiter.decide(wall_clock_default, state, scenario=IN_MATCH,
                          now=clock.t).reason == "event_in_future"

    # An absent timestamp is left to the dispatcher's queue TTL fallback.
    untimed = event(E_HQ_DAMAGE, ts=0.0, side="mine")
    assert arbiter.decide(untimed, state, scenario=IN_MATCH,
                          now=clock.t).allowed


def test_terminal_still_respects_ttl_and_privacy() -> None:
    clock = FakeClock()
    state = match_state()
    stale_end = event(E_MATCH_END, ts=clock.t - 30, result="won")
    arbiter = make_arbiter(clock)
    assert arbiter.decide(stale_end, state, scenario=ENDED,
                          now=clock.t).reason == "event_expired"

    fresh_end = event(E_MATCH_END, ts=clock.t, result="won")
    silent = make_arbiter(clock, mode="silent")
    assert silent.decide(fresh_end, state, scenario=ENDED,
                         now=clock.t).reason == "mode_silent"
    dnd = make_arbiter(clock, do_not_disturb=True)
    assert dnd.decide(fresh_end, state, scenario=ENDED,
                      now=clock.t).reason == "do_not_disturb"
    no_consent = make_arbiter(clock, llm_data_consent=False)
    assert no_consent.decide(fresh_end, state, scenario=ENDED,
                             now=clock.t).reason == "llm_consent_required"


def test_match_start_is_no_longer_passive() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    start = event(E_MATCH_START, ts=clock.t)
    # scenario=None infers from the event/state, and must not use the legacy
    # "passive state event" denial for a real match start.
    assert arbiter.decide(start, MatchState(), scenario=None,
                          now=clock.t).allowed
    assert arbiter.decide(start, MatchState(scene="menu"), scenario=None,
                          now=clock.t).allowed


def test_short_circuit_events_are_filtered_by_scenario() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = MatchState()
    target = event(E_HQ_DAMAGE, ts=clock.t, side="mine")

    assert arbiter.decide(target, state, scenario=ENDED,
                          now=clock.t).reason == "category_not_allowed"
    assert arbiter.decide(target, state, scenario=IN_MATCH,
                          now=clock.t).allowed

    chatter = event(E_CHATTER, ts=clock.t)
    assert arbiter.decide(chatter, MatchState(scene="menu"), scenario=None,
                          now=clock.t).reason == "category_not_allowed"
    # Unknown/untrusted context is treated as in-match for legacy callers.
    assert arbiter.decide(chatter, MatchState(), scenario=None,
                          now=clock.t).allowed
    assert category_allowed(IN_MATCH, "chatter")


def test_min_priority_uses_canonical_priority() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock, min_priority=4)
    turn = event("turn_switch", ts=clock.t, priority=1)
    # Canonical priority 4 participates even when the producer's raw priority
    # is lower.
    assert event_spec(turn).priority == 4
    assert arbiter.decide(turn, match_state(), scenario=IN_MATCH,
                          now=clock.t).allowed


# ---------------------------------------------------------------- pacing

def test_per_kind_cooldown_does_not_permanently_block_new_units() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    first = event(E_UNIT_PLAYED, ts=clock.t, entity=1)
    arbiter.mark_submitted(first, now=clock.t)

    second = event(E_UNIT_PLAYED, ts=clock.t + 1, entity=2)
    decision = arbiter.decide(second, state, scenario=IN_MATCH,
                              now=clock.t + 1)
    assert decision.reason == "cooldown"
    assert decision.retry_after > 0

    later = event(E_UNIT_PLAYED, ts=clock.t + 3.5, entity=2)
    assert arbiter.decide(later, state, scenario=IN_MATCH,
                          now=clock.t + 3.5).allowed


def test_configured_global_cooldown_applies_to_other_kinds() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock, cooldown_seconds=25.0)
    state = match_state()
    arbiter.mark_submitted(
        event(E_HQ_DAMAGE, ts=clock.t, side="mine"), now=clock.t)

    other = event(E_UNIT_PLAYED, ts=clock.t + 10, entity=1)
    denied = arbiter.decide(other, state, scenario=IN_MATCH, now=clock.t + 10)
    assert denied.reason == "cooldown"
    assert denied.retry_after == 15.0

    ready = event(E_UNIT_PLAYED, ts=clock.t + 26, entity=1)
    assert arbiter.decide(ready, state, scenario=IN_MATCH,
                          now=clock.t + 26).allowed


def test_duplicate_is_short_window_not_120_seconds() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    played = event(E_UNIT_PLAYED, ts=clock.t, entity=7)
    arbiter.mark_submitted(played, now=clock.t)

    duplicate = event(E_UNIT_PLAYED, ts=clock.t + 1, entity=7)
    decision = arbiter.decide(duplicate, state, scenario=IN_MATCH,
                              now=clock.t + 1)
    assert decision.reason == "semantic_duplicate"
    assert decision.retry_after > 0

    later = event(E_UNIT_PLAYED, ts=clock.t + 4, entity=7)
    assert arbiter.decide(later, state, scenario=IN_MATCH,
                          now=clock.t + 4).allowed


def test_own_critical_is_not_blocked_by_a_normal_output() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock, cooldown_seconds=25.0,
                           critical_cooldown_seconds=5.0)
    state = match_state()
    arbiter.mark_submitted(
        event(E_HQ_DAMAGE, ts=clock.t, side="mine"), now=clock.t)

    crisis = event(E_HQ_CRITICAL, ts=clock.t + 1, side="mine", hp=4, band=1)
    decision = arbiter.decide(crisis, state, scenario=IN_MATCH,
                              now=clock.t + 1)
    # The normal output must not consume the independent critical clock.
    assert decision.allowed


def test_enemy_crisis_does_not_block_own_critical() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock, cooldown_seconds=25.0,
                           critical_cooldown_seconds=5.0)
    state = match_state()
    enemy = event(E_HQ_CRITICAL, ts=clock.t, side="enemy", hp=4, band=1)
    assert arbiter.decide(enemy, state, scenario=IN_MATCH,
                          now=clock.t).allowed
    arbiter.mark_submitted(enemy, now=clock.t)

    own = event(E_HQ_CRITICAL, ts=clock.t + 1, side="mine", hp=4, band=1)
    # Per-kind pacing is keyed by session/game/kind/side, so the enemy's
    # warning cannot mute our own confirmed crisis.
    assert arbiter.decide(own, state, scenario=IN_MATCH,
                          now=clock.t + 1).allowed


def test_critical_band_upgrade_still_respects_global_critical_rhythm() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock, critical_cooldown_seconds=5.0)
    state = match_state()
    band1 = event(E_HQ_CRITICAL, ts=clock.t, side="mine", hp=5, band=1,
                  episode="e1")
    arbiter.mark_submitted(band1, now=clock.t)
    assert arbiter.snapshot(now=clock.t)["last_critical_at"] == clock.t

    band2 = event(E_HQ_CRITICAL, ts=clock.t + 4, side="mine", hp=2, band=2,
                  episode="e2")
    too_soon = arbiter.decide(band2, state, scenario=IN_MATCH,
                              now=clock.t + 4)
    assert too_soon.reason == "cooldown"
    assert too_soon.retry_after == 1.0

    at_rhythm = event(E_HQ_CRITICAL, ts=clock.t + 5, side="mine", hp=2,
                      band=2, episode="e2")
    assert arbiter.decide(at_rhythm, state, scenario=IN_MATCH,
                          now=clock.t + 5).allowed


def test_warning_to_critical_ignores_same_kind_warning_cooldown() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    warning = event(E_HQ_CRITICAL, ts=clock.t, side="enemy", hp=4, band=1)
    assert arbiter.decide(warning, state, scenario=IN_MATCH,
                          now=clock.t).allowed
    arbiter.mark_submitted(warning, now=clock.t)

    critical = event(E_HQ_CRITICAL, ts=clock.t + 1, side="mine", hp=4, band=1)
    assert arbiter.decide(critical, state, scenario=IN_MATCH,
                          now=clock.t + 1).allowed


def test_upgrade_and_recovery_new_episode_can_pass() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()

    band1 = event(E_HQ_CRITICAL, ts=clock.t, side="mine", hp=5, band=1,
                  episode="e1")
    arbiter.mark_submitted(band1, now=clock.t)

    band2 = event(E_HQ_CRITICAL, ts=clock.t + 1, side="mine", hp=2, band=2,
                  episode="e2")
    upgrade = arbiter.decide(band2, state, scenario=IN_MATCH, now=clock.t + 1)
    assert upgrade.allowed
    arbiter.mark_submitted(band2, now=clock.t + 1)

    recovered = event(E_HQ_CRITICAL, ts=clock.t + 2, side="mine", hp=12,
                      edge="recovery", episode="e3")
    recovery = arbiter.decide(recovered, state, scenario=IN_MATCH,
                              now=clock.t + 2)
    assert recovery.allowed


def test_once_per_match_is_scoped_to_session_game_kind() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = MatchState(scene="match", in_match=True, turn_number=1)
    start = event(E_MATCH_START, ts=clock.t, game=1, session_id="s1")
    assert arbiter.select([start], state, scenario=OPENING,
                          now=clock.t).event is start
    arbiter.mark_submitted(start, now=clock.t)

    again = event(E_MATCH_START, ts=clock.t + 1, game=1, session_id="s1")
    assert arbiter.decide(again, state, scenario=OPENING,
                          now=clock.t + 1).reason == "once_per_match"

    next_game = event(E_MATCH_START, ts=clock.t + 2, game=2, session_id="s1")
    assert arbiter.decide(next_game, state, scenario=OPENING,
                          now=clock.t + 2).allowed

    other_session = event(E_MATCH_START, ts=clock.t + 3, game=1,
                          session_id="s2")
    assert arbiter.decide(other_session, state, scenario=OPENING,
                          now=clock.t + 3).allowed


def test_terminal_bypasses_cooldown_but_not_once() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock, cooldown_seconds=25.0)
    state = match_state()
    arbiter.mark_submitted(
        event(E_HQ_DAMAGE, ts=clock.t, side="mine"), now=clock.t)

    end = event(E_MATCH_END, ts=clock.t + 1, result="won")
    assert arbiter.decide(end, state, scenario=ENDED,
                          now=clock.t + 1).allowed
    arbiter.mark_submitted(end, now=clock.t + 1)
    duplicate_end = event(E_MATCH_END, ts=clock.t + 2, result="won")
    assert arbiter.decide(duplicate_end, state, scenario=ENDED,
                          now=clock.t + 2).reason == "once_per_match"


# ---------------------------------------------------------------- ledger/fork

def test_ledger_is_bounded() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    for index in range(600):
        arbiter.mark_submitted(
            event(E_UNIT_PLAYED, ts=clock.t, entity=index), now=clock.t)
    snapshot = arbiter.snapshot(now=clock.t)
    assert snapshot["submitted_count"] <= 512
    assert len(snapshot["recent_submitted"]) <= 30


def test_fork_copies_ledger_and_can_reset() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    state = match_state()
    played = event(E_UNIT_PLAYED, ts=clock.t, entity=1)
    arbiter.mark_submitted(played, now=clock.t)

    clone = arbiter.fork()
    duplicate = event(E_UNIT_PLAYED, ts=clock.t + 1, entity=1)
    assert clone.decide(duplicate, state, scenario=IN_MATCH,
                        now=clock.t + 1).reason == "semantic_duplicate"

    clean = arbiter.fork(reset=True)
    assert clean.snapshot(now=clock.t)["submitted_count"] == 0
    assert clean.decide(duplicate, state, scenario=IN_MATCH,
                        now=clock.t + 1).allowed

    # The clone is independent of the original.
    arbiter.reset()
    assert arbiter.decide(duplicate, state, scenario=IN_MATCH,
                          now=clock.t + 1).allowed
    assert clone.decide(duplicate, state, scenario=IN_MATCH,
                        now=clock.t + 1).reason == "semantic_duplicate"


def test_snapshot_is_json_safe() -> None:
    clock = FakeClock()
    arbiter = make_arbiter(clock)
    arbiter.mark_submitted(event(E_UNIT_PLAYED, ts=clock.t, entity=1),
                           now=clock.t)
    text = json.dumps(arbiter.snapshot(now=clock.t))
    assert "kards_arbiter_v1" in text
    assert "NaN" not in text
