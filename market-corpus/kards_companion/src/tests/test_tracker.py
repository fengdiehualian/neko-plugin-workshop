"""Tests for the state tracker: confirmation gates, identity and lifecycle."""

from __future__ import annotations

import json

from kards_companion.runtime.contracts import event_identity
from kards_companion.state.model import (
    E_HQ_CRITICAL,
    E_HQ_DAMAGE,
    E_MATCH_END,
    E_MATCH_START,
    E_ORDER_CARD,
    E_TURN_SWITCH,
    E_UNIT_ADVANCED,
    E_UNIT_DIED,
    E_UNIT_PLAYED,
    SCENE_DEFEAT,
    SCENE_DRAFT,
    SCENE_MATCH,
    SCENE_MENU,
    SCENE_UNKNOWN,
    SCENE_VICTORY,
    SIDE_ENEMY,
    SIDE_MINE,
    SIDE_UNKNOWN,
    BoardUnit,
    GameEvent,
    MatchState,
)
from kards_companion.state.tracker import (
    HQ_CONFIRM_FRAMES,
    TURN_CONFIRM_FRAMES,
    UNIT_CONFIRM_FRAMES,
    UNIT_MISS_GRACE,
    MatchTracker,
)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_tracker(**kwargs) -> tuple[MatchTracker, Clock]:
    clock = Clock()
    return MatchTracker(now_fn=clock, **kwargs), clock


def in_match(**kw) -> MatchState:
    base: dict = {"scene": SCENE_MATCH, "in_match": True, "my_hq": 20, "enemy_hq": 20}
    base.update(kw)
    return MatchState(**base)


def unit(uid: int, side: str, lane: str = "support", **kw) -> BoardUnit:
    return BoardUnit(instance_id=uid, side=side, lane=lane, **kw)


def kinds(events: list[GameEvent]) -> list[str]:
    return [event.kind for event in events]


def baseline(tracker: MatchTracker, clock: Clock, *, hq: int = 20,
             enemy_hq: int = 20, my_turn: bool = False) -> None:
    clock.t += 0.3
    assert tracker.update(in_match(my_hq=hq, enemy_hq=enemy_hq,
                                   my_turn=my_turn)) == []


def tracked_unit(uid: int, side: str = SIDE_MINE, lane: str = "support", *,
                 confirmed: bool = True, **kw) -> BoardUnit:
    """Trajectory-produced unit with the approved defaults/evidence shape."""
    base: dict = {
        "instance_id": uid,
        "side": side,
        "lane": lane,
        "trajectory": True,
        "track_confirmed": confirmed,
        "side_confidence": 0.9 if side in (SIDE_MINE, SIDE_ENEMY) else 0.45,
        "detection_confidence": 0.9,
        "bbox": (0.1, 0.8, 0.2, 0.9),
        "slot": 0,
        "first_seen_frame": 1,
        "last_seen_frame": 2,
        "confirm_count": 2,
        "observed": True,
    }
    base.update(kw)
    return BoardUnit(**base)


def unit_evidence(*, frames: list[int] | None = None,
                  before: dict | None = None,
                  after: dict | None = None) -> dict:
    return {
        "before": before,
        "after": after or {"side": SIDE_MINE, "lane": "support", "slot": 0,
                           "bbox": [0.1, 0.8, 0.2, 0.9]},
        "frame_ids": [1, 2] if frames is None else frames,
        "first_seen": 1,
        "last_seen": 2,
        "confirm_count": 2,
        "confidence": 0.9,
    }


def assert_bounded_evidence(evidence: dict) -> None:
    assert set(evidence) == {
        "before", "after", "frame_ids", "first_seen", "last_seen",
        "confirm_count", "confidence",
    }
    assert len(evidence["frame_ids"]) <= 4
    assert 0.0 <= float(evidence["confidence"]) <= 1.0
    json.dumps(evidence)  # JSON-safe, no image bytes or OCR bodies
    text = json.dumps(evidence).lower()
    for forbidden in ("image", "crop", "ocr", "raw_pixels"):
        assert forbidden not in text


# --------------------------------------------------------------- lifecycle

def test_match_start_and_end_lifecycle() -> None:
    tracker, clock = make_tracker()
    clock.t = 10.0
    events = tracker.update(in_match(my_hq=20, enemy_hq=20))
    assert kinds(events) == [E_MATCH_START]
    assert events[0].priority == 5
    assert events[0].ts == 10.0
    assert events[0].details["game"] == 1
    assert events[0].details["turn"] == 1
    assert tracker.in_match

    clock.t = 11.0
    events = tracker.update(MatchState(scene=SCENE_VICTORY))
    end = events[-1]
    assert end.kind == E_MATCH_END and end.priority == 9
    assert end.details["result"] == "won"
    assert end.ts == 11.0
    assert not tracker.in_match
    assert tracker.game_number == 1


def test_no_events_without_transition() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)
    assert tracker.update(in_match(my_hq=20, enemy_hq=20)) == []


def test_turn_switch_canonical_priority_and_increasing_turn() -> None:
    assert TURN_CONFIRM_FRAMES == 2
    tracker, clock = make_tracker()
    tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20))
    baseline(tracker, clock, my_turn=True)

    clock.t += 1.0
    assert tracker.update(in_match(my_turn=False, my_hq=20, enemy_hq=20)) == []
    clock.t += 1.0
    events = tracker.update(in_match(my_turn=False, my_hq=20, enemy_hq=20))
    assert kinds(events) == [E_TURN_SWITCH]
    assert events[0].priority == 4
    assert events[0].details["my_turn"] is False
    first_turn = events[0].details["turn"]

    clock.t += 1.0
    assert tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20,
                                   turn_number=0)) == []
    clock.t += 1.0
    events = tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20,
                                     turn_number=0))
    assert kinds(events) == [E_TURN_SWITCH]
    assert events[0].details["turn"] > first_turn


def test_single_frame_turn_flip_is_ignored() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20))
    baseline(tracker, clock, my_turn=True)

    clock.t += 1.0
    assert tracker.update(in_match(my_turn=False, my_hq=20, enemy_hq=20)) == []
    clock.t += 1.0
    assert tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20)) == []
    assert not any(
        event.kind == E_TURN_SWITCH
        for event in tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20))
    )


def test_two_frame_turn_flip_emits_exactly_one_switch() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20))
    baseline(tracker, clock, my_turn=True)

    clock.t += 1.0
    assert tracker.update(in_match(my_turn=False, my_hq=20, enemy_hq=20)) == []
    clock.t += 1.0
    first = tracker.update(in_match(my_turn=False, my_hq=20, enemy_hq=20))
    assert kinds(first) == [E_TURN_SWITCH]
    turn = first[0].details["turn"]

    clock.t += 1.0
    assert tracker.update(in_match(my_turn=False, my_hq=20, enemy_hq=20)) == []
    assert tracker.snapshot()["turn"] == turn


def test_true_then_unknown_never_switches_turn() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20))
    baseline(tracker, clock, my_turn=True)

    for _ in range(3):
        clock.t += 1.0
        events = tracker.update(in_match(my_turn=None, my_hq=20, enemy_hq=20))
        assert not any(event.kind == E_TURN_SWITCH for event in events)
    assert tracker.snapshot()["turn"] == 1


def test_carried_false_is_not_an_independent_turn_observation() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20,
                            unit_observation_valid=True, unit_observation_frame=1))
    baseline(tracker, clock, my_turn=True)

    # Same detector frame repeated, then carried ticks: all are one observation.
    for _ in range(4):
        clock.t += 1.0
        events = tracker.update(in_match(
            my_turn=False, my_hq=20, enemy_hq=20,
            unit_observation_valid=False, unit_observation_frame=2))
        assert not any(event.kind == E_TURN_SWITCH for event in events)

    # Two genuinely fresh frames are required before the switch is committed.
    clock.t += 1.0
    assert tracker.update(in_match(
        my_turn=False, my_hq=20, enemy_hq=20,
        unit_observation_valid=True, unit_observation_frame=3)) == []
    assert tracker.update(in_match(
        my_turn=False, my_hq=20, enemy_hq=20,
        unit_observation_valid=True, unit_observation_frame=3)) == []
    clock.t += 1.0
    events = tracker.update(in_match(
        my_turn=False, my_hq=20, enemy_hq=20,
        unit_observation_valid=True, unit_observation_frame=4))
    assert kinds(events) == [E_TURN_SWITCH]
    assert events[0].details["my_turn"] is False


# --------------------------------------------------------------- HQ gates

def test_hq_two_frame_confirmation_filters_spike() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    clock.t += 1.0
    assert tracker.update(in_match(my_hq=2, enemy_hq=20)) == []  # single-frame spike
    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20)) == []
    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20)) == []
    assert tracker.confirmed_hq(SIDE_MINE) == 20
    assert tracker.snapshot()["crisis_band"][SIDE_MINE] == 0


def test_hq_big_hit_commits_after_two_equal_frames() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=21, enemy_hq=20))
    baseline(tracker, clock, hq=21)

    clock.t += 1.0
    assert tracker.update(in_match(my_hq=15, enemy_hq=20)) == []
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=15, enemy_hq=20))
    assert kinds(events) == [E_HQ_DAMAGE]
    assert events[0].details["loss"] == 6
    assert events[0].details["hp"] == 15
    assert events[0].details["side"] == SIDE_MINE
    assert events[0].priority == 7


def test_hq_consecutive_values_commit_diff_and_crisis_bands() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    clock.t += 1.0
    tracker.update(in_match(my_hq=4, enemy_hq=20))
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=4, enemy_hq=20))
    assert kinds(events) == [E_HQ_DAMAGE, E_HQ_CRITICAL]
    crit = events[1]
    assert crit.details["band"] == 1 and crit.details["edge"] == "enter"
    episode = crit.details["episode"]

    clock.t += 1.0
    tracker.update(in_match(my_hq=3, enemy_hq=20))
    clock.t += 1.0
    assert tracker.update(in_match(my_hq=3, enemy_hq=20)) == []  # same band

    clock.t += 1.0
    tracker.update(in_match(my_hq=2, enemy_hq=20))
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=2, enemy_hq=20))
    assert kinds(events) == [E_HQ_CRITICAL]
    assert events[0].details["band"] == 2
    assert events[0].details["edge"] == "upgrade"
    assert events[0].details["episode"] == episode


def test_hq_recovery_above_seven_and_new_episode_on_redip() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    clock.t += 1.0
    tracker.update(in_match(my_hq=4, enemy_hq=20))
    clock.t += 1.0
    first = [e for e in tracker.update(in_match(my_hq=4, enemy_hq=20))
             if e.kind == E_HQ_CRITICAL][0]
    first_episode = first.details["episode"]

    clock.t += 1.0
    tracker.update(in_match(my_hq=8, enemy_hq=20))
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=8, enemy_hq=20))
    recovery = [e for e in events if e.kind == E_HQ_CRITICAL][0]
    assert recovery.priority == 4
    assert recovery.details["edge"] == "recovery"
    assert recovery.details["band"] == 0
    assert recovery.details["episode"] == first_episode

    clock.t += 1.0
    tracker.update(in_match(my_hq=4, enemy_hq=20))
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=4, enemy_hq=20))
    crit = [e for e in events if e.kind == E_HQ_CRITICAL][0]
    assert crit.details["edge"] == "enter"
    assert crit.details["episode"] != first_episode


def test_missing_hq_read_does_not_advance_or_reset_healing() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    clock.t += 1.0
    tracker.update(in_match(my_hq=4, enemy_hq=20))
    clock.t += 1.0
    tracker.update(in_match(my_hq=None, enemy_hq=20))
    clock.t += 1.0
    assert tracker.update(in_match(my_hq=4, enemy_hq=20)) == []  # candidate restarted
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=4, enemy_hq=20))
    assert kinds(events) == [E_HQ_DAMAGE, E_HQ_CRITICAL]

    clock.t += 1.0
    events = tracker.update(in_match(my_hq=None, enemy_hq=20))
    assert events == []
    assert tracker.snapshot()["crisis_band"][SIDE_MINE] == 1
    assert tracker.confirmed_hq(SIDE_MINE) == 4


# --------------------------------------------------------------- units

def test_unit_confirmation_and_miss_grace_thresholds() -> None:
    assert UNIT_CONFIRM_FRAMES == 2
    assert UNIT_MISS_GRACE == 3
    assert HQ_CONFIRM_FRAMES == 2

    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)
    one = unit(1, SIDE_MINE, attack=2, health=3, cost=2)

    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20, units=[one])) == []
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=20, enemy_hq=20,
                                     units=[unit(1, SIDE_MINE, attack=2, health=3, cost=2)]))
    assert kinds(events) == [E_UNIT_PLAYED]
    assert events[0].priority == 5
    assert events[0].details["entity"] == "1"
    identity = event_identity(events[0]).split("|")
    assert identity[2] == E_UNIT_PLAYED
    assert identity[3] == SIDE_MINE
    assert identity[4] == "1"

    for _ in range(3):
        clock.t += 1.0
        events = tracker.update(in_match(my_hq=20, enemy_hq=20))
    # Three missing frames prove loss of observation, never death.
    assert kinds(events) == []
    losses = tracker.recent_unit_losses()
    assert len(losses) == 1
    assert losses[0]["kind"] == "unit_lost"
    assert losses[0]["instance_id"] == 1
    assert losses[0]["miss_count"] == 3
    assert losses[0]["side"] == SIDE_MINE
    assert_bounded_evidence(losses[0]["evidence"])
    ledger = tracker.current_ledger()
    assert ledger and ledger["died"] == []
    assert ledger["lost"][0]["kind"] == "unit_lost"
    assert tracker.snapshot()["unit_losses"] == 1


def test_start_baseline_units_do_not_storm() -> None:
    tracker, clock = make_tracker()
    units = [unit(1, SIDE_MINE, cost=1), unit(2, SIDE_ENEMY, cost=2)]
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, units=units))
    assert kinds(events) == [E_MATCH_START]

    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20,
                                   units=[unit(1, SIDE_MINE, cost=1),
                                          unit(2, SIDE_ENEMY, cost=2)])) == []


def test_unit_advance_only_support_to_front() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[unit(7, SIDE_MINE)]))
    clock.t += 1.0
    assert kinds(tracker.update(in_match(my_hq=20, enemy_hq=20,
                                        units=[unit(7, SIDE_MINE)]))) == [E_UNIT_PLAYED]

    front = unit(7, SIDE_MINE, lane="front")
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, units=[front]))
    assert kinds(events) == [E_UNIT_ADVANCED]
    assert events[0].priority == 4
    assert events[0].details["entity"] == "7"

    back = unit(7, SIDE_MINE, lane="support")
    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20, units=[back])) == []
    assert "撤回支援线" in back.lineage
    assert back.lineage.count("推进到前线") == 1


def test_unit_identity_is_inherited_across_frames() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    named = unit(1, SIDE_MINE, attack=2, health=2, cost=1,
                 card_name="步兵", name_confidence=0.9)
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[named]))
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[named.copy()]))

    blank = unit(1, SIDE_MINE, attack=2, health=2, cost=1)
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[blank]))
    assert blank.card_name == "步兵" and blank.name_confidence == 0.9


# --------------------------------------------------------------- trajectory

def test_board_unit_trajectory_fields_are_backward_compatible() -> None:
    legacy = BoardUnit(instance_id=1, side=SIDE_MINE, lane="front")
    assert legacy.slot is None
    assert legacy.bbox is None
    assert legacy.trajectory is False
    assert legacy.track_confirmed is False
    assert legacy.enter_evidence is None
    assert legacy.position_history == []

    legacy.position_history.append({"frame_id": 1})
    clone = legacy.copy()
    clone.position_history.append({"frame_id": 2})
    assert len(legacy.position_history) == 1
    assert len(clone.position_history) == 2


def test_trajectory_entry_emits_on_second_frame_with_bounded_evidence() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    pending = tracked_unit(9, confirmed=False)
    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20, units=[pending])) == []

    evidence = unit_evidence()
    confirmed = tracked_unit(9, confirmed=True, enter_evidence=evidence)
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, units=[confirmed]))
    assert kinds(events) == [E_UNIT_PLAYED]
    assert events[0].details["entity"] == "9"
    assert events[0].details["side"] == SIDE_MINE
    assert events[0].details["evidence"] == evidence
    assert_bounded_evidence(events[0].details["evidence"])
    assert tracker.snapshot()["units"] == 1


def test_trajectory_lane_move_event_carries_before_after_evidence() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    support = tracked_unit(7, lane="support", confirmed=True,
                           enter_evidence=unit_evidence())
    clock.t += 1.0
    assert kinds(tracker.update(in_match(my_hq=20, enemy_hq=20,
                                         units=[support]))) == [E_UNIT_PLAYED]

    before = {"side": SIDE_MINE, "lane": "support", "slot": 0,
              "bbox": [0.1, 0.8, 0.2, 0.9]}
    after = {"side": SIDE_MINE, "lane": "front", "slot": 0,
             "bbox": [0.1, 0.55, 0.2, 0.65]}
    move = unit_evidence(frames=[3, 4], before=before, after=after)
    front = tracked_unit(7, lane="front", confirmed=True, move_evidence=move,
                         bbox=(0.1, 0.55, 0.2, 0.65))
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, units=[front]))
    assert kinds(events) == [E_UNIT_ADVANCED]
    assert events[0].details["evidence"] == move
    assert events[0].details["evidence"]["before"] == before
    assert events[0].details["evidence"]["after"] == after
    assert events[0].details["evidence"]["frame_ids"] == [3, 4]
    assert front.lineage.count("推进到前线") == 1


def test_trajectory_unknown_side_never_emits_enemy_event() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    unknown = tracked_unit(5, side=SIDE_UNKNOWN, confirmed=True,
                           side_confidence=0.45, enter_evidence=unit_evidence())
    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20,
                                   units=[unknown])) == []
    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20,
                                   units=[unknown])) == []
    assert tracker.snapshot()["units"] == 1

    # Observation later resolves the side; attribution is now evidence-backed.
    resolved = tracked_unit(5, side=SIDE_ENEMY, confirmed=True,
                            side_confidence=0.9,
                            enter_evidence=unit_evidence())
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, units=[resolved]))
    assert kinds(events) == [E_UNIT_PLAYED]
    assert events[0].details["side"] == SIDE_ENEMY


def test_only_reliable_hp_zero_may_emit_unit_died() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    clock.t += 1.0
    pending = tracked_unit(3, side=SIDE_ENEMY, confirmed=False, health=0)
    assert tracker.update(in_match(my_hq=20, enemy_hq=20,
                                   units=[pending])) == []
    clock.t += 1.0
    confirmed = tracked_unit(3, side=SIDE_ENEMY, confirmed=True, health=0,
                             enter_evidence=unit_evidence())
    events = tracker.update(in_match(my_hq=20, enemy_hq=20,
                                     units=[confirmed]))
    assert kinds(events) == [E_UNIT_PLAYED]  # one HP=0 frame is not enough

    clock.t += 1.0
    # Same carried frame 2 must not count twice; a new observation frame does.
    assert tracker.update(in_match(my_hq=20, enemy_hq=20,
                                   units=[confirmed])) == []
    clock.t += 1.0
    confirmed_next = tracked_unit(3, side=SIDE_ENEMY, confirmed=True, health=0,
                                  last_seen_frame=4, enter_evidence=unit_evidence())
    events = tracker.update(in_match(my_hq=20, enemy_hq=20,
                                     units=[confirmed_next]))
    assert kinds(events) == [E_UNIT_DIED]
    assert events[0].details["cause"] == "hp_zero"
    assert events[0].details["side"] == SIDE_ENEMY
    assert_bounded_evidence(events[0].details["evidence"])

    for _ in range(3):
        clock.t += 1.0
        events = tracker.update(in_match(my_hq=20, enemy_hq=20))
    assert kinds(events) == []  # missing after a real death never re-emits


def test_unit_loss_ledger_is_bounded_and_structured() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    many = [tracked_unit(uid, confirmed=True, enter_evidence=unit_evidence())
            for uid in range(1, 31)]
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=many))
    for _ in range(3):
        clock.t += 1.0
        assert tracker.update(in_match(my_hq=20, enemy_hq=20)) == []

    losses = tracker.recent_unit_losses(50)
    assert len(losses) == 20
    assert [item["instance_id"] for item in losses] == list(range(11, 31))
    for item in losses:
        assert item["kind"] == "unit_lost"
        assert item["miss_count"] == 3
        assert item["reason"] == "missing_frames"
        assert_bounded_evidence(item["evidence"])


def test_carried_same_detection_frame_does_not_satisfy_entry_gate() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    pending = tracked_unit(9, confirmed=False, last_seen_frame=2,
                           first_seen_frame=2)
    clock.t += 1.0
    assert tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[pending],
        unit_observation_valid=True, unit_observation_frame=2)) == []

    # A duplicate explicit marker for the same frame is also carried state.
    clock.t += 1.0
    assert tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[pending.copy()],
        unit_observation_valid=True, unit_observation_frame=2)) == []

    # Carried state on non-detector ticks: explicit invalid marker or even the
    # legacy None marker must not turn one sampled frame into two.
    for _ in range(3):
        clock.t += 1.0
        carried = pending.copy()
        assert tracker.update(in_match(
            my_hq=20, enemy_hq=20, units=[carried],
            unit_observation_valid=False)) == []
    clock.t += 1.0
    assert tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[pending.copy()])) == []

    confirmed = tracked_unit(9, confirmed=True, last_seen_frame=9,
                             first_seen_frame=2,
                             enter_evidence=unit_evidence())
    clock.t += 1.0
    events = tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[confirmed],
        unit_observation_valid=True, unit_observation_frame=9))
    assert kinds(events) == [E_UNIT_PLAYED]


def test_non_unit_tick_skips_unit_gates_but_keeps_turn_and_hq() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20, my_turn=True))
    baseline(tracker, clock, my_turn=True)

    unit = tracked_unit(9, confirmed=True, first_seen_frame=5,
                        last_seen_frame=5, enter_evidence=unit_evidence())
    clock.t += 1.0
    assert kinds(tracker.update(in_match(
        my_hq=20, enemy_hq=20, my_turn=True, units=[unit],
        unit_observation_valid=True,
        unit_observation_frame=5))) == [E_UNIT_PLAYED]

    # Non-unit ticks carry the last board but no fresh unit evidence.  They
    # must not advance miss/loss gates even with a newer capture frame id,
    # and carried my_turn is not an independent observation: HQ facts reach
    # the diff, but a turn switch needs two fresh observations.
    clock.t += 1.0
    first_carried = tracker.update(in_match(
        my_hq=15, enemy_hq=20, my_turn=False, units=[],
        unit_observation_valid=False, unit_observation_frame=6))
    assert kinds(first_carried) == []
    assert tracker.unit_diagnostics()["misses"] == {}
    clock.t += 1.0
    second_carried = tracker.update(in_match(
        my_hq=15, enemy_hq=20, my_turn=False, units=[],
        unit_observation_valid=False, unit_observation_frame=7))
    assert kinds(second_carried) == [E_HQ_DAMAGE]
    assert tracker.unit_diagnostics()["misses"] == {}

    # A real empty observation is the only thing that advances the miss count.
    clock.t += 1.0
    tracker.update(in_match(
        my_hq=15, enemy_hq=20, my_turn=False, units=[],
        unit_observation_valid=True, unit_observation_frame=8))
    assert tracker.unit_diagnostics()["misses"] == {"9": 1}
    clock.t += 1.0
    events = tracker.update(in_match(
        my_hq=15, enemy_hq=20, my_turn=False, units=[],
        unit_observation_valid=True, unit_observation_frame=9))
    assert kinds(events) == [E_TURN_SWITCH]


def test_unit_misses_advance_only_on_fresh_observations() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    unit = tracked_unit(5, confirmed=True, first_seen_frame=3,
                        last_seen_frame=3, enter_evidence=unit_evidence())
    clock.t += 1.0
    assert kinds(tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[unit],
        unit_observation_valid=True, unit_observation_frame=3))) == [E_UNIT_PLAYED]

    clock.t += 1.0
    assert tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[],
        unit_observation_valid=True, unit_observation_frame=4)) == []
    assert tracker.snapshot()["units"] == 1
    assert tracker.unit_diagnostics()["misses"] == {"5": 1}

    for _ in range(6):
        clock.t += 1.0
        assert tracker.update(in_match(
            my_hq=20, enemy_hq=20, units=[],
            unit_observation_valid=False)) == []
    assert tracker.unit_diagnostics()["misses"] == {"5": 1}

    clock.t += 1.0
    assert tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[],
        unit_observation_valid=True, unit_observation_frame=5)) == []
    assert tracker.unit_diagnostics()["misses"] == {"5": 2}

    clock.t += 1.0
    events = tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[],
        unit_observation_valid=True, unit_observation_frame=6))
    assert kinds(events) == []
    assert tracker.snapshot()["units"] == 0
    assert len(tracker.recent_unit_losses()) == 1

    for _ in range(5):
        clock.t += 1.0
        tracker.update(in_match(my_hq=20, enemy_hq=20, units=[],
                                unit_observation_valid=False))
    assert len(tracker.recent_unit_losses()) == 1  # deduplicated


def test_carried_unit_move_waits_for_fresh_observation() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    support = tracked_unit(6, lane="support", confirmed=True,
                           first_seen_frame=2, last_seen_frame=2,
                           enter_evidence=unit_evidence())
    clock.t += 1.0
    assert kinds(tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[support],
        unit_observation_valid=True))) == [E_UNIT_PLAYED]

    before = {"side": SIDE_MINE, "lane": "support", "slot": 0,
              "bbox": [0.1, 0.8, 0.2, 0.9]}
    after = {"side": SIDE_MINE, "lane": "front", "slot": 0,
             "bbox": [0.1, 0.55, 0.2, 0.65]}
    move = unit_evidence(frames=[5, 6], before=before, after=after)
    front = tracked_unit(6, lane="front", confirmed=True, move_evidence=move,
                         bbox=(0.1, 0.55, 0.2, 0.65),
                         first_seen_frame=2, last_seen_frame=5)

    clock.t += 1.0
    assert tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[front],
        unit_observation_valid=False)) == []
    clock.t += 1.0
    events = tracker.update(in_match(
        my_hq=20, enemy_hq=20, units=[front],
        unit_observation_valid=True))
    assert kinds(events) == [E_UNIT_ADVANCED]


def test_unit_loss_record_dedupes_same_identity() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)
    unit = tracked_unit(8, confirmed=True, first_seen_frame=2,
                        last_seen_frame=2)
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[unit],
                            unit_observation_valid=True))
    for _ in range(3):
        clock.t += 1.0
        tracker.update(in_match(my_hq=20, enemy_hq=20, units=[],
                                unit_observation_valid=True))
    assert len(tracker.recent_unit_losses()) == 1
    tracker._record_unit_loss(unit, clock.t, misses=3)  # replay same identity
    assert len(tracker.recent_unit_losses()) == 1


def test_event_evidence_frame_ids_are_bounded() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    huge = unit_evidence(frames=list(range(20)))
    clock.t += 1.0
    unit = tracked_unit(4, confirmed=True, enter_evidence=huge)
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, units=[unit]))
    evidence = events[0].details["evidence"]
    assert evidence["frame_ids"] == [16, 17, 18, 19]
    assert_bounded_evidence(evidence)


# --------------------------------------------------------------- orders

def test_order_card_requires_side_and_recognition_evidence() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    clock.t += 1.0
    order = {"name": "闪电战", "side": SIDE_ENEMY, "ts": 5.0, "confidence": 0.95}
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, last_order_card=order))
    assert kinds(events) == [E_ORDER_CARD]
    assert events[0].priority == 5
    assert events[0].details["side"] == SIDE_ENEMY
    assert "闪电战" in events[0].summary

    clock.t += 1.0
    assert tracker.update(in_match(my_hq=20, enemy_hq=20, last_order_card=order)) == []

    for bad in (
        {"name": "闪电战", "side": "unknown", "ts": 6.0, "confidence": 0.99},
        {"name": "played_card", "side": SIDE_ENEMY, "ts": 7.0, "confidence": 0.99},
        {"name": "闪电战", "side": SIDE_ENEMY, "ts": 8.0, "confidence": 0.3},
        {"name": "", "side": SIDE_ENEMY, "ts": 9.0, "confidence": 0.99},
    ):
        clock.t += 1.0
        assert tracker.update(in_match(my_hq=20, enemy_hq=20,
                                       last_order_card=bad)) == []

    clock.t += 1.0
    mine = {"name": "空袭", "side": SIDE_MINE, "ts": 10.0, "confidence": 0.8}
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, last_order_card=mine))
    assert kinds(events) == [E_ORDER_CARD]
    assert events[0].details["side"] == SIDE_MINE
    first_order = events[0]

    clock.t += 1.0
    second = {"name": "战术打击", "side": SIDE_MINE, "ts": 11.0, "confidence": 0.8}
    events = tracker.update(in_match(my_hq=20, enemy_hq=20, last_order_card=second))
    assert events[0].details["occurrence"] == first_order.details["occurrence"] + 1
    assert event_identity(events[0]) != event_identity(first_order)


# --------------------------------------------------------------- lifecycle APIs

def test_reset_session_and_reset_match_keep_object_identity() -> None:
    tracker, clock = make_tracker(session_id="s1")
    original = tracker
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    assert tracker.game_number == 1 and tracker.session_id == "s1"

    tracker.reset_match()
    assert tracker is original
    assert not tracker.in_match and tracker.game_number == 1
    assert tracker.session_id == "s1"
    clock.t += 1.0
    assert kinds(tracker.update(in_match(my_hq=20, enemy_hq=20))) == [E_MATCH_START]
    assert tracker.game_number == 2

    tracker.mark_delivered(GameEvent(E_HQ_CRITICAL, 8, "x",
                                     details={"session_id": "s1", "game": 2}))
    tracker.reset_session()
    assert tracker is original
    assert not tracker.in_match and tracker.game_number == 0
    assert tracker.session_id == "" and tracker.delivered_count == 0
    clock.t += 1.0
    assert kinds(tracker.update(in_match(my_hq=20, enemy_hq=20))) == [E_MATCH_START]
    assert tracker.game_number == 1


def test_rebase_preserves_unit_lineage_and_still_closes_result() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)

    named = unit(1, SIDE_MINE, attack=2, health=3, cost=2,
                 card_name="喀秋莎", name_confidence=0.9)
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[named]))
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[named.copy()]))

    tracker.rebase()
    clock.t += 1.0
    resumed = in_match(my_hq=9, enemy_hq=20,
                       units=[unit(1, SIDE_MINE, lane="front", cost=2)])
    assert tracker.update(resumed) == []
    assert tracker.current is resumed
    assert resumed.units[0].card_name == "喀秋莎"
    assert any("登场" in line for line in resumed.units[0].lineage)

    clock.t += 1.0
    events = tracker.update(MatchState(scene=SCENE_DEFEAT))
    assert kinds(events) == [E_MATCH_END]
    assert events[0].details["result"] == "lost"


def test_start_and_rebase_skip_invalid_unit_ids_without_storm() -> None:
    tracker, clock = make_tracker()
    bad = BoardUnit(instance_id=None, side=SIDE_MINE)  # type: ignore[arg-type]
    valid = unit(1, SIDE_MINE, cost=2)

    events = tracker.update(in_match(my_hq=20, enemy_hq=20, units=[bad, valid]))
    assert kinds(events) == [E_MATCH_START]
    baseline(tracker, clock)
    assert tracker.snapshot()["units"] == 1

    tracker.rebase()
    clock.t += 1.0
    resumed = in_match(
        my_hq=9,
        enemy_hq=20,
        units=[BoardUnit(instance_id=None, side=SIDE_MINE),  # type: ignore[arg-type]
               unit(1, SIDE_MINE, lane="front", cost=2)],
    )
    assert tracker.update(resumed) == []
    assert tracker.current is resumed
    assert tracker.snapshot()["units"] == 1

    clock.t += 1.0
    assert tracker.update(in_match(
        my_hq=9,
        enemy_hq=20,
        units=[BoardUnit(instance_id=None, side=SIDE_MINE),  # type: ignore[arg-type]
               unit(1, SIDE_MINE, lane="front", cost=2)],
    )) == []
    assert tracker.in_match

    clock.t += 1.0
    assert kinds(tracker.update(MatchState(scene=SCENE_VICTORY))) == [E_MATCH_END]


def test_rebase_through_pause_waits_for_first_match_frame() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)
    named = unit(1, SIDE_MINE, cost=2, card_name="喀秋莎", name_confidence=0.9)
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[named]))
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20, units=[named.copy()]))

    tracker.rebase()
    clock.t += 1.0
    assert tracker.update(MatchState(scene=SCENE_MENU, detected_at=clock.t)) == []
    clock.t += 1.0
    resumed = in_match(my_hq=9, enemy_hq=20,
                       units=[unit(1, SIDE_MINE, lane="front", cost=2)])
    assert tracker.update(resumed) == []
    assert tracker.current is resumed
    assert resumed.units[0].card_name == "喀秋莎"


def test_pause_scenes_do_not_diff_or_advance_miss_or_turn() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20))
    baseline(tracker, clock, my_turn=True)
    one = unit(1, SIDE_MINE, cost=2)
    clock.t += 1.0
    tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20, units=[one]))
    clock.t += 1.0
    tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20,
                            units=[unit(1, SIDE_MINE, cost=2)]))

    for scene in (SCENE_UNKNOWN, SCENE_MENU, SCENE_DRAFT):
        clock.t += 1.0
        assert tracker.update(MatchState(scene=scene, my_hq=1, enemy_hq=20,
                                         detected_at=clock.t)) == []
    assert tracker.snapshot()["units"] == 1
    assert tracker.in_match

    clock.t += 1.0
    events = tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20,
                                     units=[unit(1, SIDE_MINE, cost=2)]))
    assert events == []  # no turn switch / HQ damage / unit death from the pause
    for _ in range(3):
        clock.t += 1.0
        events = tracker.update(in_match(my_turn=True, my_hq=20, enemy_hq=20))
    assert kinds(events) == []
    loss = tracker.recent_unit_losses()[-1]
    assert loss["kind"] == "unit_lost" and loss["miss_count"] == 3
    assert not any(event.kind == E_UNIT_DIED for event in events)


def test_event_ts_and_source_identity_details() -> None:
    tracker, clock = make_tracker()
    tracker.set_session_id("sess-1")
    clock.t = 42.0
    start = tracker.update(in_match(my_hq=20, enemy_hq=20))[0]
    assert start.ts == 42.0
    assert start.details["session_id"] == "sess-1"
    assert start.details["game"] == 1
    assert start.details["turn"] == 1
    assert start.details["occurrence"] == 1
    assert "target" not in start.details

    baseline(tracker, clock)
    clock.t = 45.0
    chat = tracker.maybe_chatter(1.0)
    assert chat is not None and chat.ts == 45.0
    assert chat.details["session_id"] == "sess-1"


# --------------------------------------------------------------- delivery

def _drive_to_crisis(tracker: MatchTracker, clock: Clock) -> list[GameEvent]:
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)
    clock.t += 1.0
    tracker.update(in_match(my_hq=4, enemy_hq=20))
    clock.t += 1.0
    return tracker.update(in_match(my_hq=4, enemy_hq=20))


def test_rearm_uncommitted_returns_only_live_crisis_with_original_ts() -> None:
    tracker, clock = make_tracker()
    events = _drive_to_crisis(tracker, clock)
    crisis = [e for e in events if e.kind == E_HQ_CRITICAL][0]

    rearmed = tracker.rearm_uncommitted()
    assert kinds(rearmed) == [E_HQ_CRITICAL]
    assert rearmed[0].ts == crisis.ts
    assert rearmed[0].details["episode"] == crisis.details["episode"]

    tracker.mark_delivered(crisis)
    assert tracker.rearm_uncommitted() == []

    # 已投递后，新 episode 仍可重新评估条件。
    clock.t += 1.0
    tracker.update(in_match(my_hq=8, enemy_hq=20))
    clock.t += 1.0
    tracker.update(in_match(my_hq=8, enemy_hq=20))
    clock.t += 1.0
    tracker.update(in_match(my_hq=4, enemy_hq=20))
    clock.t += 1.0
    events = tracker.update(in_match(my_hq=4, enemy_hq=20))
    new_crisis = [e for e in events if e.kind == E_HQ_CRITICAL][0]
    assert new_crisis.details["episode"] != crisis.details["episode"]
    assert [e.ts for e in tracker.rearm_uncommitted()] == [new_crisis.ts]


def test_rearm_expires_and_never_replays_discrete_events() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    baseline(tracker, clock)
    assert tracker.rearm_uncommitted() == []  # match_start is not a condition

    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20,
                            units=[unit(3, SIDE_MINE)]))
    clock.t += 1.0
    tracker.update(in_match(my_hq=20, enemy_hq=20,
                            units=[unit(3, SIDE_MINE)]))
    assert tracker.rearm_uncommitted() == []  # unit_played is discrete

    events = _drive_to_crisis(tracker, clock)
    crisis = [e for e in events if e.kind == E_HQ_CRITICAL][0]
    assert tracker.rearm_uncommitted(now=crisis.ts + 6.5) == []


def test_delivered_ledger_is_bounded_fifo() -> None:
    tracker, _clock = make_tracker(delivered_limit=8)
    for index in range(50):
        tracker.mark_delivered(GameEvent(
            E_HQ_DAMAGE, 5, "x",
            details={"session_id": "s", "game": 1, "side": SIDE_MINE,
                     "entity": "hq", "occurrence": index + 1},
        ))
    assert tracker.delivered_count == 8


def test_chatter_gate() -> None:
    tracker, clock = make_tracker()
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    clock.t = 10.0
    assert tracker.maybe_chatter(60) is None
    clock.t = 99.0
    chatter = tracker.maybe_chatter(60)
    assert chatter is not None and chatter.kind == "chatter"
    assert chatter.ts == 99.0
    assert tracker.maybe_chatter(60) is None


def test_menu_scene_produces_nothing_and_defeat_ends() -> None:
    tracker, clock = make_tracker()
    assert tracker.update(MatchState(scene=SCENE_MENU)) == []
    tracker.update(in_match(my_hq=20, enemy_hq=20))
    clock.t = 5.0
    events = tracker.update(MatchState(scene=SCENE_DEFEAT))
    assert events[-1].details["result"] == "lost"
