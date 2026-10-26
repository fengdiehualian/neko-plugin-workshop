"""Tests for the MatchTracker runtime adapter and detector registry wiring."""

from __future__ import annotations

from collections.abc import Callable

from kards_companion.runtime.contracts import (
    event_max_age_seconds,
    event_spec,
)
from kards_companion.runtime.detectors import CandidateEvent, DetectorRegistry
from kards_companion.runtime.match_adapter import MatchTrackerAdapter
from kards_companion.runtime.snapshot import FrameMeta, RuntimeSnapshot
from kards_companion.state.model import (
    E_CHATTER,
    E_HQ_CRITICAL,
    E_HQ_DAMAGE,
    E_MATCH_END,
    E_MATCH_START,
    E_TURN_SWITCH,
    E_UNIT_ADVANCED,
    E_UNIT_DIED,
    E_UNIT_PLAYED,
    SCENE_MATCH,
    SCENE_VICTORY,
    SIDE_ENEMY,
    SIDE_MINE,
    BoardUnit,
    GameEvent,
    MatchState,
)
from kards_companion.state.tracker import MatchTracker


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _Health:
    """Minimal capture-health stand-in for snapshot construction."""

    def __init__(self, reading: bool = True) -> None:
        self.reading = reading

    def to_dict(self) -> dict[str, bool]:
        return {"reading": self.reading}


def make_snapshot(
    state: MatchState,
    *,
    seq: int = 1,
    observed_at: float = 0.0,
    reading: bool = True,
    valid: bool = True,
    with_image: bool = True,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        seq=seq,
        observed_at=observed_at,
        health=_Health(reading),
        frame=FrameMeta(frame_id=seq, captured_at=observed_at, valid=valid),
        state=state,
        image=object() if with_image else None,
    )


def match_state(step: int, **overrides: object) -> MatchState:
    base: dict[str, object] = {
        "scene": SCENE_MATCH,
        "in_match": True,
        "turn_number": 0,
        "my_hq": 20,
        "enemy_hq": 20,
        "my_credits": step,
        "my_credit_cap": 6,
        "hand_count": step + 1,
        "enemy_hand_count": 2,
        "detected_at": 1000.0 + step,
    }
    base.update(overrides)
    return MatchState(**base)  # type: ignore[arg-type]


def named_unit() -> BoardUnit:
    return BoardUnit(instance_id=1, side=SIDE_MINE, lane="support", attack=2,
                     health=3, cost=2, card_name="喀秋莎", name_confidence=0.9)


def advanced_unit() -> BoardUnit:
    # Name deliberately omitted: the tracker must inherit it from the prior frame.
    return BoardUnit(instance_id=1, side=SIDE_MINE, lane="front", attack=2,
                     health=3, cost=2)


def event_key(event: GameEvent) -> tuple[object, ...]:
    return (event.kind, event.priority, event.summary, event.ts, event.details)


def scenario_frames() -> list[tuple[float, Callable[[], MatchState]]]:
    return [
        (0.0, lambda: match_state(1)),
        (0.3, lambda: match_state(2)),
        (0.6, lambda: match_state(3, my_turn=True, units=[named_unit()])),
        (0.9, lambda: match_state(4, my_turn=True, units=[named_unit()])),
        (1.2, lambda: match_state(5, my_turn=True, units=[advanced_unit()])),
        (1.5, lambda: match_state(6, my_turn=True, my_hq=14, units=[advanced_unit()])),
        (1.8, lambda: match_state(7, my_turn=True, my_hq=14, units=[advanced_unit()])),
        (2.1, lambda: match_state(8, my_turn=True, my_hq=4, enemy_hq=4,
                                  units=[advanced_unit()])),
        (2.4, lambda: match_state(9, my_turn=True, my_hq=4, enemy_hq=4,
                                  units=[advanced_unit()])),
        (2.7, lambda: match_state(10, my_turn=True, my_hq=4, enemy_hq=4)),
        (3.0, lambda: match_state(11, my_turn=True, my_hq=4, enemy_hq=4)),
        (3.3, lambda: match_state(12, my_turn=True, my_hq=4, enemy_hq=4)),
        (70.0, lambda: match_state(13, my_turn=True, my_hq=4, enemy_hq=4)),
        (71.0, lambda: match_state(14, scene=SCENE_VICTORY, my_hq=4, enemy_hq=4)),
    ]


# --------------------------------------------------------------- adapter

def test_adapter_event_sequence_matches_direct_tracker() -> None:
    clock = Clock()
    direct = MatchTracker(now_fn=clock)
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock), chatter_interval_s=60.0)
    previous: RuntimeSnapshot | None = None
    seen: list[str] = []
    losses_before_terminal: list[dict] = []

    for seq, (observed_at, factory) in enumerate(scenario_frames(), start=1):
        clock.t = observed_at
        direct_state = factory()
        adapter_state = factory()
        expected_detected_at = adapter_state.detected_at

        expected = direct.update(direct_state)
        chatter = direct.maybe_chatter(60.0)
        if chatter is not None:
            expected.append(chatter)

        snapshot = make_snapshot(adapter_state, seq=seq, observed_at=observed_at)
        actual = adapter.update(previous, snapshot)
        previous = snapshot

        assert [event_key(c.event) for c in actual] == [event_key(e) for e in expected]
        for candidate in actual:
            assert isinstance(candidate, CandidateEvent)
            assert candidate.source == "match_tracker"
            assert candidate.enqueued_at == observed_at
            assert candidate.ttl_s == event_max_age_seconds(candidate.event)
            assert candidate.critical is event_spec(candidate.event).preempt

        # Adapter feeds the original MatchState through, not a reconstruction.
        assert adapter_state.detected_at == expected_detected_at
        assert adapter_state.to_public_dict() == direct_state.to_public_dict()
        seen.extend(event.kind for event in expected)
        if seq == 12:  # third missing match frame, before the terminal frame
            losses_before_terminal = direct.recent_unit_losses()

    assert set(seen) == {
        E_MATCH_START,
        E_TURN_SWITCH,
        E_HQ_DAMAGE,
        E_HQ_CRITICAL,
        E_UNIT_PLAYED,
        E_UNIT_ADVANCED,
        E_CHATTER,
        E_MATCH_END,
    }
    # Disappearance is observation loss, not a death claim.  Match close
    # clears current-match diagnostics, so capture them before the terminal.
    assert E_UNIT_DIED not in seen
    assert losses_before_terminal
    assert losses_before_terminal[-1]["kind"] == "unit_lost"
    assert losses_before_terminal[-1]["instance_id"] == 1


def test_adapter_forwards_loss_diagnostics_without_death_event() -> None:
    clock = Clock()
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock))

    tracker_unit = BoardUnit(
        instance_id=11, side=SIDE_MINE, lane="support",
        trajectory=True, track_confirmed=True, side_confidence=0.9,
        detection_confidence=0.9, bbox=(0.1, 0.8, 0.2, 0.9), slot=0,
        first_seen_frame=2, last_seen_frame=2, confirm_count=2,
        enter_evidence={
            "before": None,
            "after": {"side": SIDE_MINE, "lane": "support", "slot": 0,
                      "bbox": [0.1, 0.8, 0.2, 0.9]},
            "frame_ids": [1, 2], "first_seen": 1, "last_seen": 2,
            "confirm_count": 2, "confidence": 0.9,
        },
    )

    clock.t = 0.5
    first = adapter.update(None, make_snapshot(match_state(1), seq=1, observed_at=0.5))
    assert [c.event.kind for c in first] == [E_MATCH_START]

    clock.t = 1.0
    produced = adapter.update(
        None,
        make_snapshot(match_state(2, units=[tracker_unit]), seq=2, observed_at=1.0),
    )
    assert [c.event.kind for c in produced] == [E_UNIT_PLAYED]
    assert produced[0].event.details["evidence"]["frame_ids"] == [1, 2]

    for step in range(3):
        clock.t = 2.0 + step
        events = adapter.update(
            None,
            make_snapshot(match_state(step + 3), seq=step + 3,
                          observed_at=clock.t),
        )
        assert E_UNIT_DIED not in [c.event.kind for c in events]

    diagnostics = adapter.unit_diagnostics()
    assert diagnostics["known"] == 0
    assert diagnostics["losses"][-1]["kind"] == "unit_lost"
    assert diagnostics["losses"][-1]["instance_id"] == 11
    assert adapter.recent_unit_losses(1) == [diagnostics["losses"][-1]]
    assert adapter.snapshot()["unit_losses"] == 1


def test_candidate_spec_ttl_and_confirmed_mine_critical() -> None:
    start = GameEvent(E_MATCH_START, 1, "start", ts=1.0, details={})
    damage = GameEvent(E_HQ_DAMAGE, 1, "hit", ts=1.0,
                       details={"side": SIDE_MINE, "loss": 6})
    mine = GameEvent(E_HQ_CRITICAL, 1, "crit", ts=1.0,
                     details={"side": SIDE_MINE, "hp": 4, "band": 1})
    enemy = GameEvent(E_HQ_CRITICAL, 1, "crit", ts=1.0,
                      details={"side": SIDE_ENEMY, "hp": 4, "band": 1})
    end = GameEvent(E_MATCH_END, 1, "end", ts=1.0, details={"result": "won"})

    start_candidate = CandidateEvent.from_event(start, source="x", enqueued_at=1.0)
    assert start_candidate.ttl_s == 4.0
    assert start_candidate.critical is False

    damage_candidate = CandidateEvent.from_event(damage, source="x", enqueued_at=1.0)
    assert damage_candidate.ttl_s == 6.0
    assert damage_candidate.critical is False

    mine_candidate = CandidateEvent.from_event(mine, source="x", enqueued_at=1.0)
    assert mine_candidate.ttl_s == 6.0
    assert mine_candidate.critical is True

    enemy_candidate = CandidateEvent.from_event(enemy, source="x", enqueued_at=1.0)
    assert enemy_candidate.critical is False

    end_candidate = CandidateEvent.from_event(end, source="x", enqueued_at=1.0)
    assert end_candidate.ttl_s == 8.0
    assert end_candidate.critical is False

    capped = CandidateEvent.from_event(start, source="x", enqueued_at=1.0,
                                       configured_ttl=2.0)
    assert capped.ttl_s == 2.0
    default = CandidateEvent(start, "x", 1.0)
    assert default.ttl_s == 4.0


def test_adapter_requires_trusted_snapshot() -> None:
    clock = Clock()
    tracker = MatchTracker(now_fn=clock)
    adapter = MatchTrackerAdapter(tracker)

    for variant in (
        {"reading": False},
        {"valid": False},
        {"with_image": False},
    ):
        state = match_state(1)
        assert adapter.update(None, make_snapshot(state, observed_at=1.0, **variant)) == []
        assert state.game_number == 0
        assert not tracker.in_match

    trusted_state = match_state(1)
    events = adapter.update(None, make_snapshot(trusted_state, observed_at=2.0))
    assert [c.event.kind for c in events] == [E_MATCH_START]
    assert tracker.in_match


def test_registry_gates_untrusted_and_forwards_adapter_events() -> None:
    clock = Clock()
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock))
    registry = DetectorRegistry([adapter])

    events, diagnostics = registry.update(
        None, make_snapshot(match_state(1), observed_at=0.5, valid=False)
    )
    assert events == []
    assert len(diagnostics) == 1
    assert diagnostics[0].name == "registry"
    assert diagnostics[0].outcome == "skipped"
    assert diagnostics[0].error == "capture_not_trusted"
    assert not adapter.tracker.in_match

    events, diagnostics = registry.update(
        None, make_snapshot(match_state(1), observed_at=1.0)
    )
    assert [c.event.kind for c in events] == [E_MATCH_START]
    assert all(isinstance(c, CandidateEvent) for c in events)
    assert events[0].enqueued_at == 1.0
    assert len(diagnostics) == 1
    assert diagnostics[0].name == "match_tracker"
    assert diagnostics[0].outcome == "ok"


def test_adapter_passes_state_through_with_clock_and_metadata_intact() -> None:
    clock = Clock()
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock))
    unit = BoardUnit(instance_id=7, side=SIDE_ENEMY, lane="front", attack=4,
                     health=5, cost=3, card_name="虎式", name_confidence=0.95)
    order = {"name": "空袭", "side": SIDE_MINE, "ts": 3.0, "confidence": 0.8}
    state = match_state(
        7,
        my_turn=True,
        my_hq=18,
        enemy_hq=11,
        my_credits=4,
        my_credit_cap=7,
        hand_count=5,
        enemy_hand_count=3,
        units=[unit],
        last_order_card=order,
        detected_at=4321.25,
    )
    snapshot = make_snapshot(state, observed_at=99.5)

    candidates = adapter.update(None, snapshot)

    assert [c.event.kind for c in candidates] == [E_MATCH_START]
    assert adapter.tracker.current is state
    assert state.detected_at == 4321.25
    assert state.turn_number == 0
    assert state.my_turn is True
    assert state.units[0] is unit
    assert state.hand_count == 5
    assert state.enemy_hand_count == 3
    assert state.my_credits == 4
    assert state.my_credit_cap == 7
    assert state.last_order_card is order
    assert state.game_number == 1
    assert candidates[0].enqueued_at == 99.5


def test_adapter_emits_chatter_as_candidate_event() -> None:
    clock = Clock()
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock), chatter_interval_s=30.0)

    clock.t = 0.0
    first = adapter.update(None, make_snapshot(match_state(1), seq=1, observed_at=0.0))
    assert [c.event.kind for c in first] == [E_MATCH_START]

    clock.t = 31.0
    quiet = adapter.update(None, make_snapshot(match_state(2), seq=2, observed_at=31.0))

    assert [c.event.kind for c in quiet] == [E_CHATTER]
    candidate = quiet[0]
    assert candidate.event.priority == 5
    assert candidate.event.details["game"] == 1
    assert candidate.enqueued_at == 31.0
    assert candidate.source == "match_tracker"
    assert candidate.ttl_s == 6.0
    assert candidate.critical is False


def test_adapter_reset_preserves_tracker_object_and_clock() -> None:
    clock = Clock()
    seed = MatchTracker(now_fn=clock, session_id="s")
    adapter = MatchTrackerAdapter(seed)

    adapter.update(None, make_snapshot(match_state(1), observed_at=0.0))
    clock.t = 5.0
    adapter.update(
        None,
        make_snapshot(MatchState(scene=SCENE_VICTORY, detected_at=5.0), observed_at=5.0),
    )
    assert not adapter.tracker.in_match

    adapter.reset()
    assert adapter.tracker is seed
    assert clock.t == 5.0

    clock.t = 10.0
    candidates = adapter.update(None, make_snapshot(match_state(2), observed_at=10.0))
    assert [c.event.kind for c in candidates] == [E_MATCH_START]
    assert adapter.tracker.game_number == 1


# --------------------------------------------------------------- registry fanout

def _drive_to_crisis(adapter: MatchTrackerAdapter, clock: Clock) -> list[CandidateEvent]:
    produced: list[CandidateEvent] = []
    for t, hp in ((0.0, 20), (0.3, 20), (0.6, 4), (0.9, 4)):
        clock.t = t
        produced = adapter.update(
            None,
            make_snapshot(match_state(int(t * 10) + 1, my_hq=hp), observed_at=t),
        )
    return produced


def test_registry_mark_delivered_and_rearm_fanout() -> None:
    clock = Clock()
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock))
    registry = DetectorRegistry([adapter])

    produced = _drive_to_crisis(adapter, clock)
    crisis = [c for c in produced if c.event.kind == E_HQ_CRITICAL][0]
    assert crisis.critical is True
    assert crisis.event.details["side"] == SIDE_MINE

    rearmed = registry.rearm(now=clock.t)
    assert [c.event.kind for c in rearmed] == [E_HQ_CRITICAL]
    assert rearmed[0].event.ts == crisis.event.ts
    assert rearmed[0].enqueued_at == clock.t
    assert rearmed[0].critical is True

    registry.mark_delivered(crisis.event)
    assert registry.rearm(now=clock.t) == []


def test_registry_reset_match_keeps_cumulative_game_number() -> None:
    clock = Clock()
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock))
    registry = DetectorRegistry([adapter])

    _drive_to_crisis(adapter, clock)
    assert adapter.game_number == 1

    registry.reset_match()
    assert not adapter.in_match
    assert adapter.game_number == 1

    clock.t += 1.0
    events, _diagnostics = registry.update(
        None, make_snapshot(match_state(1), observed_at=clock.t)
    )
    assert [c.event.kind for c in events] == [E_MATCH_START]
    assert adapter.game_number == 2

    registry.reset_session()
    assert not adapter.in_match
    assert adapter.game_number == 0

    clock.t += 1.0
    events, _diagnostics = registry.update(
        None, make_snapshot(match_state(1), observed_at=clock.t)
    )
    assert adapter.game_number == 1


def test_registry_rebase_prevents_gap_diff_storm() -> None:
    clock = Clock()
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock))
    registry = DetectorRegistry([adapter])

    for t, hp in ((0.0, 20), (0.3, 20)):
        clock.t = t
        registry.update(None, make_snapshot(match_state(1, my_hq=hp), observed_at=t))

    registry.rebase()
    clock.t = 1.0
    events, _diagnostics = registry.update(
        None, make_snapshot(match_state(2, my_hq=9), observed_at=1.0)
    )
    assert events == []
    assert adapter.tracker.current is not None
    assert adapter.tracker.current.my_hq == 9

    registry.reset_session()
    assert adapter.tracker.delivered_count == 0


def test_registry_still_isolates_detector_exceptions() -> None:
    class Boom:
        name = "boom"

        def reset(self) -> None:
            raise RuntimeError("reset")

        def update(self, previous, current):
            raise RuntimeError("boom")

    class Spy:
        name = "spy"

        def __init__(self) -> None:
            self.calls = 0

        def reset(self) -> None:
            pass

        def update(self, previous, current):
            self.calls += 1
            return []

    spy = Spy()
    registry = DetectorRegistry([Boom(), spy])
    events, diagnostics = registry.update(None, make_snapshot(match_state(1)))
    assert events == []
    assert spy.calls == 1
    assert {d.name: d.outcome for d in diagnostics} == {"boom": "error", "spy": "ok"}

    registry.reset()  # boom reset failure must not stop the registry
