import json

from kards_companion.perception.detector import (
    TRACK_CONFIRM_FRAMES,
    TRACK_HISTORY_LIMIT,
    TRACK_MISS_LIMIT,
    TRACK_MOVE_CONFIRM_FRAMES,
    Detection,
    DetectionFrame,
    TrackedUnit,
    UnitTracker,
    assign_rows,
    build_unit_observations,
)
from kards_companion.state.model import (
    SCENE_MATCH,
    SIDE_ENEMY,
    SIDE_MINE,
    SIDE_UNKNOWN,
    MatchState,
)
from kards_companion.state.tracker import MatchTracker

# 类别表不再内嵌于 detector：与 knowledge/ui_classes.json v1（10 类）保持一致。
UI_CLASS_NAMES = (
    "unit_board", "card_hand", "card_back", "played_card", "hq",
    "frontline", "end_turn_button", "end_turn_button_active",
    "screen_victory", "screen_defeat",
)
CLASS_TO_ID = {name: index for index, name in enumerate(UI_CLASS_NAMES)}


# --------------------------------------------------------------- trajectory

IMAGE_SIZE = (1920, 1080)


def detection(name: str, x1: float, y1: float, x2: float, y2: float,
              conf: float = 0.9) -> Detection:
    return Detection(CLASS_TO_ID[name], name, conf, x1, y1, x2, y2)


def front_line() -> Detection:
    return detection("frontline", 200, 530, 1700, 550)


def unit_box(x: float = 500, y: float = 900,
             conf: float = 0.9) -> Detection:
    return detection("unit_board", x, y, x + 60, y + 80, conf)


def evidence_shape(evidence: dict) -> None:
    assert set(evidence) == {
        "before", "after", "frame_ids", "first_seen", "last_seen",
        "confirm_count", "confidence",
    }
    assert len(evidence["frame_ids"]) <= 4
    assert 0.0 <= float(evidence["confidence"]) <= 1.0
    payload = json.dumps(evidence).lower()
    for forbidden in ("image", "crop", "ocr", "pixel", "base64"):
        assert forbidden not in payload


def enter_unit(tracker: UnitTracker, *, frame_id: int = 1,
               x: float = 500, y: float = 900):
    first = tracker.update_observations(
        [front_line(), unit_box(x=x, y=y)], IMAGE_SIZE,
        frame_id=frame_id, observed_at=float(frame_id))
    second = tracker.update_observations(
        [front_line(), unit_box(x=x, y=y)], IMAGE_SIZE,
        frame_id=frame_id + 1, observed_at=float(frame_id + 1))
    return first, second


def test_trajectory_two_frame_entry_and_normalized_bbox() -> None:
    assert TRACK_CONFIRM_FRAMES == 2
    tracker = UnitTracker()
    first_units, first_diag = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=1, observed_at=1.0)
    assert len(first_units) == 1
    pending = first_units[0]
    assert pending.instance_id == 1
    assert pending.side == SIDE_MINE and pending.lane == "support"
    assert pending.slot == 0
    assert pending.track_confirmed is False
    assert pending.trajectory is True
    assert pending.enter_evidence is None
    assert first_diag["entered"] == []
    assert all(0.0 <= value <= 1.0 for value in pending.bbox or ())

    second_units, second_diag = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=2, observed_at=2.0)
    assert len(second_units) == 1
    unit = second_units[0]
    assert unit.instance_id == 1
    assert unit.track_confirmed is True
    assert second_diag["entered"] == [1]
    entry = unit.enter_evidence
    assert entry is not None
    evidence_shape(entry)
    assert entry["frame_ids"] == [1, 2]
    assert entry["first_seen"] == 1 and entry["last_seen"] == 2
    assert entry["confirm_count"] == 2
    assert entry["before"] is None
    assert entry["after"]["side"] == SIDE_MINE
    assert 0.0 <= unit.bbox[0] < unit.bbox[2] <= 1.0
    assert 0.0 <= unit.bbox[1] < unit.bbox[3] <= 1.0


def test_trajectory_lane_move_needs_two_frames_and_keeps_identity() -> None:
    assert TRACK_MOVE_CONFIRM_FRAMES == 2
    tracker = UnitTracker()
    enter_unit(tracker)

    moved_once = tracker.update_observations(
        [front_line(), unit_box(y=600)], IMAGE_SIZE,
        frame_id=3, observed_at=3.0)
    assert len(moved_once[0]) == 1
    unit = moved_once[0][0]
    assert unit.instance_id == 1
    assert unit.lane == "support"  # not committed after one frame
    assert unit.move_evidence is None
    assert moved_once[1]["moved"] == []

    moved = tracker.update_observations(
        [front_line(), unit_box(y=600)], IMAGE_SIZE,
        frame_id=4, observed_at=4.0)
    unit = moved[0][0]
    assert unit.instance_id == 1
    assert unit.lane == "front"
    assert len(moved[1]["moved"]) == 1
    move = unit.move_evidence
    assert move is not None
    evidence_shape(move)
    assert move["frame_ids"] == [3, 4]
    assert move["before"]["lane"] == "support"
    assert move["after"]["lane"] == "front"
    assert move["confirm_count"] == 2


def test_trajectory_single_frame_lane_flicker_is_not_committed() -> None:
    tracker = UnitTracker()
    enter_unit(tracker)
    tracker.update_observations([front_line(), unit_box(y=600)], IMAGE_SIZE,
                                frame_id=3, observed_at=3.0)
    back = tracker.update_observations([front_line(), unit_box(y=900)], IMAGE_SIZE,
                                       frame_id=4, observed_at=4.0)
    assert back[0][0].lane == "support"
    assert back[0][0].move_evidence is None
    assert back[1]["moved"] == []


def test_trajectory_three_valid_missing_frames_record_unit_lost() -> None:
    assert TRACK_MISS_LIMIT == 3
    tracker = UnitTracker()
    enter_unit(tracker)

    for offset in range(2):
        units, diag = tracker.update_observations(
            [], IMAGE_SIZE, frame_id=10 + offset, observed_at=10.0 + offset)
        assert units == []
        assert diag["lost"] == []
        assert diag["alive"] == 1

    units, diag = tracker.update_observations(
        [], IMAGE_SIZE, frame_id=12, observed_at=12.0)
    assert units == []
    assert diag["alive"] == 0
    assert len(diag["lost"]) == 1
    lost = diag["lost"][0]
    assert lost["kind"] == "unit_lost"
    assert lost["instance_id"] == 1
    assert lost["miss_count"] == 3
    assert lost["reason"] == "missing_frames"
    assert lost["evidence"]["after"] is None
    evidence_shape(lost["evidence"])
    assert tracker.recent_losses() == [lost]

    for offset in range(5):
        _units, _diag = tracker.update_observations(
            [], IMAGE_SIZE, frame_id=20 + offset, observed_at=20.0 + offset)
    assert tracker.recent_losses() == [lost]  # no duplicate unit_lost


def test_trajectory_invalid_observation_never_counts_as_miss() -> None:
    tracker = UnitTracker()
    enter_unit(tracker)
    for offset in range(5):
        units, diag = tracker.update_observations(
            [], IMAGE_SIZE, frame_id=10 + offset, observed_at=10.0 + offset,
            detector_available=True, observation_valid=False)
        assert diag["detector_available"] is True
        assert diag["observation_valid"] is False
        assert diag["status"] == "observation_invalid"
        assert diag["lost"] == []
        assert diag["alive"] == 1
        assert units and units[0].observed is False
    assert tracker.recent_losses() == []


def test_trajectory_invalid_frame_never_confirms_a_pending_ghost() -> None:
    tracker = UnitTracker()
    first, _diag = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=1, observed_at=1.0)
    assert first[0].track_confirmed is False

    retained, diag = tracker.update_observations(
        [], IMAGE_SIZE, frame_id=2, observed_at=2.0,
        detector_available=True, observation_valid=False)
    assert retained == []  # unconfirmed units are not carried into bad frames
    assert diag["alive"] == 1

    confirmed, diag = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=3, observed_at=3.0)
    assert confirmed[0].instance_id == 1
    assert confirmed[0].track_confirmed is True
    assert diag["entered"] == [1]
    assert confirmed[0].enter_evidence["frame_ids"] == [1, 3]


def test_trajectory_detector_unavailable_is_not_empty_detections() -> None:
    tracker = UnitTracker()
    enter_unit(tracker)

    for offset in range(3):
        units, diag = tracker.update_observations(
            None, IMAGE_SIZE, frame_id=10 + offset, observed_at=10.0 + offset)
        assert diag["detector_available"] is False
        assert diag["observation_valid"] is False
        assert diag["status"] == "model_unavailable"
        assert diag["alive"] == 1 and diag["lost"] == []
        assert len(units) == 1 and units[0].observed is False

    # A real model returning zero detections is valid absence evidence.
    for offset in range(3):
        units, diag = tracker.update_observations(
            [], IMAGE_SIZE, frame_id=20 + offset, observed_at=20.0 + offset)
        assert diag["detector_available"] is True
        assert diag["observation_valid"] is True
        assert units == []
    assert diag["alive"] == 0
    assert len(tracker.recent_losses()) == 1


def test_trajectory_weak_detection_does_not_advance_miss() -> None:
    tracker = UnitTracker()
    enter_unit(tracker)
    for offset in range(3):
        units, diag = tracker.update_observations(
            [front_line(), unit_box(conf=0.32)], IMAGE_SIZE,
            frame_id=10 + offset, observed_at=10.0 + offset)
        assert diag["weak_matched"] == 1
        assert diag["miss_advanced"] == {}
        assert diag["lost"] == []
        assert len(units) == 1
    assert tracker.recent_losses() == []


def test_trajectory_fallback_side_is_unknown_not_guessed() -> None:
    tracker = UnitTracker()
    # No frontline detection -> fallback line is a layout guess, not evidence.
    first, diag = tracker.update_observations(
        [unit_box()], IMAGE_SIZE, frame_id=1, observed_at=1.0)
    assert diag["unknown_side_candidates"] == 1
    assert first[0].side == SIDE_UNKNOWN
    assert first[0].side_confidence < 0.6

    second, _ = tracker.update_observations(
        [unit_box()], IMAGE_SIZE, frame_id=2, observed_at=2.0)
    assert second[0].side == SIDE_UNKNOWN
    assert second[0].track_confirmed is True
    assert second[0].side_confidence < 0.6

    # A low-confidence frontline box is not trusted as side evidence either.
    weak_line = detection("frontline", 200, 530, 1700, 550, conf=0.4)
    observations = build_unit_observations([weak_line, unit_box()], IMAGE_SIZE)
    assert observations[0].side == SIDE_UNKNOWN
    assert observations[0].side_confidence < 0.6


def test_trajectory_slots_follow_x_order_within_side_lane() -> None:
    observations = build_unit_observations(
        [front_line(), unit_box(x=800), unit_box(x=500)], IMAGE_SIZE)
    mine = [item for item in observations if item.side == SIDE_MINE]
    assert [(item.det.cx, item.slot) for item in mine] == [(530.0, 0), (830.0, 1)]
    assert [item.lane for item in mine] == ["support", "support"]

    enemy_obs = build_unit_observations(
        [front_line(),
         detection("unit_board", 700, 300, 760, 380),
         detection("unit_board", 400, 300, 460, 380)], IMAGE_SIZE)
    enemy = [item for item in enemy_obs if item.side == SIDE_ENEMY]
    assert [item.slot for item in enemy] == [0, 1]
    assert [item.det.cx for item in enemy] == [430.0, 730.0]


def test_trajectory_identity_survives_bbox_and_slot_changes() -> None:
    tracker = UnitTracker()
    ids: list[int] = []
    slots: list[int] = []
    for index, x in enumerate((500, 530, 560, 590, 650, 710)):
        units, _diag = tracker.update_observations(
            [front_line(), unit_box(x=x)], IMAGE_SIZE,
            frame_id=index + 1, observed_at=float(index + 1))
        ids.append(units[0].instance_id)
        slots.append(units[0].slot)
    assert ids == [1, 1, 1, 1, 1, 1]
    assert slots == [0] * len(slots)


def test_trajectory_history_and_ledger_are_bounded() -> None:
    assert TRACK_HISTORY_LIMIT == 20
    tracker = UnitTracker()
    boxes = [unit_box(x=20 + index * 70) for index in range(25)]
    tracker.update_observations([front_line(), *boxes], IMAGE_SIZE,
                                frame_id=1, observed_at=1.0)
    units, diag = tracker.update_observations([front_line(), *boxes], IMAGE_SIZE,
                                              frame_id=2, observed_at=2.0)
    assert len(diag["entered"]) == 25
    assert len(tracker.recent_ledger(100)) == 20

    for step in range(3, 23):
        units, _diag = tracker.update_observations(
            [front_line(), *boxes], IMAGE_SIZE,
            frame_id=step, observed_at=float(step))
    for unit in units:
        assert len(unit.position_history) <= TRACK_HISTORY_LIMIT
    assert len(tracker.recent_ledger(100)) == 20


def test_trajectory_session_reset_does_not_associate_old_tracks() -> None:
    tracker = UnitTracker()
    enter_unit(tracker)
    old = tracker.update_observations([front_line(), unit_box()], IMAGE_SIZE,
                                      frame_id=3, observed_at=3.0)[0][0]
    assert old.track_confirmed is True
    assert old.enter_evidence is not None

    tracker.reset_session()
    assert tracker.recent_losses() == []
    assert tracker.recent_ledger() == []

    first, diag = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=1, observed_at=1.0)
    assert first[0].instance_id == 1
    assert first[0].track_confirmed is False
    assert first[0].enter_evidence is None
    assert diag["entered"] == []

    second, _diag = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=2, observed_at=2.0)
    assert second[0].instance_id == 1
    assert second[0].track_confirmed is True
    assert second[0].enter_evidence["frame_ids"] == [1, 2]


def test_trajectory_rebase_does_not_count_pause_gap_as_miss() -> None:
    tracker = UnitTracker()
    enter_unit(tracker)
    tracker.rebase()
    units, diag = tracker.update_observations(
        [], IMAGE_SIZE, frame_id=10, observed_at=10.0)
    assert diag["rebased"] is True
    assert diag["alive"] == 1 and diag["lost"] == []
    assert len(units) == 1 and units[0].observed is False

    for offset in range(2):
        _units, diag = tracker.update_observations(
            [], IMAGE_SIZE, frame_id=11 + offset, observed_at=11.0 + offset)
        assert diag["lost"] == []
        assert diag["alive"] == 1
    _units, diag = tracker.update_observations(
        [], IMAGE_SIZE, frame_id=13, observed_at=13.0)
    assert diag["alive"] == 0
    assert len(diag["lost"]) == 1


def test_trajectory_detection_frame_unavailable_flag() -> None:
    tracker = UnitTracker()
    enter_unit(tracker)
    frame = DetectionFrame.unavailable(frame_id=9, observed_at=9.0,
                                       image_size=IMAGE_SIZE)
    units, diag = tracker.update_observations(frame)
    assert diag["detector_available"] is False
    assert diag["observation_valid"] is False
    assert diag["alive"] == 1 and diag["lost"] == []
    assert units[0].observed is False


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_trajectory_zero_or_negative_frame_id_is_generated_unique() -> None:
    tracker = UnitTracker()
    first, diag1 = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=0, observed_at=1.0)
    second, diag2 = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=0, observed_at=2.0)
    assert diag1["frame_id"] == 1
    assert diag2["frame_id"] == 2
    assert first[0].last_seen_frame == 1
    assert second[0].enter_evidence["frame_ids"] == [1, 2]

    _third, diag3 = tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=-9, observed_at=3.0)
    assert diag3["frame_id"] == 3


def test_trajectory_occlusion_and_foreground_gate_full_occlusion() -> None:
    tracker = UnitTracker()
    enter_unit(tracker)

    for index, kwargs in enumerate((
        {"occlusion_state": "unknown"},
        {"occlusion_state": "occluded"},
        {"occlusion_state": "unverified"},
        {"foreground": False, "backend": "dxcam"},
        {"foreground": False, "backend": "imagegrab"},
        {"occluded": True, "backend": "host_wgc"},
        {"capture_reading": False, "backend": "host_wgc"},
    )):
        units, diag = tracker.update_observations(
            [], IMAGE_SIZE, frame_id=20 + index, observed_at=20.0 + index,
            **kwargs)
        assert diag["observation_valid"] is False, kwargs
        assert diag["lost"] == [], kwargs
        assert diag["alive"] == 1, kwargs
        assert units and units[0].observed is False

    # Host WGC can capture its window surface while backgrounded; a trusted
    # WGC reading with foreground=False/unknown occlusion is valid absence.
    units, diag = tracker.update_observations(
        [], IMAGE_SIZE, frame_id=40, observed_at=40.0,
        foreground=False, backend="host_wgc", occlusion_state="unknown",
        capture_reading=True)
    assert diag["observation_valid"] is True
    assert diag["alive"] == 1 and diag["lost"] == []
    assert diag["miss_advanced"] == {"1": 1}

    # Explicit occlusion evidence still fails closed even for WGC.
    units, diag = tracker.update_observations(
        [], IMAGE_SIZE, frame_id=41, observed_at=41.0,
        foreground=False, backend="host_wgc", occluded=True)
    assert diag["observation_valid"] is False
    assert diag["lost"] == []
    assert units and units[0].observed is False


def test_trajectory_scheduled_cadence_and_loss_dedup() -> None:
    units_tracker = UnitTracker()
    clock = Clock()
    match_tracker = MatchTracker(now_fn=clock)

    def state(units, valid: bool, frame: int | None = None) -> MatchState:
        return MatchState(
            scene=SCENE_MATCH, in_match=True, my_hq=20, enemy_hq=20,
            units=units, detected_at=clock.t,
            unit_observation_valid=valid, unit_observation_frame=frame,
        )

    clock.t = 0.0
    assert [event.kind for event in match_tracker.update(state([], True))] == [
        "match_start"]

    # Detector observation 1: pending unit.  Carried state on the next 4
    # ticks is NOT a second observation.
    clock.t = 1.0
    units, _diag = units_tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=1, observed_at=1.0)
    assert match_tracker.update(state(units, True, 1)) == []
    for tick in (2.0, 3.0, 4.0, 5.0):
        clock.t = tick
        carried = [unit.copy() for unit in units]
        assert match_tracker.update(state(carried, False)) == []

    # Detector observation 2: now the entry event is allowed.
    clock.t = 6.0
    units, _diag = units_tracker.update_observations(
        [front_line(), unit_box()], IMAGE_SIZE, frame_id=2, observed_at=6.0)
    events = match_tracker.update(state(units, True, 2))
    assert [event.kind for event in events] == ["unit_played"]

    # First valid empty observation -> miss 1.
    clock.t = 7.0
    units, _diag = units_tracker.update_observations(
        [], IMAGE_SIZE, frame_id=3, observed_at=7.0)
    assert match_tracker.update(state(units, True, 3)) == []
    assert match_tracker.unit_diagnostics()["misses"] == {"1": 1}

    # Carried empty states do not advance the miss counter.
    for tick in range(8, 14):
        clock.t = float(tick)
        assert match_tracker.update(state([], False)) == []
    assert match_tracker.unit_diagnostics()["misses"] == {"1": 1}

    clock.t = 14.0
    units, _diag = units_tracker.update_observations(
        [], IMAGE_SIZE, frame_id=4, observed_at=14.0)
    assert match_tracker.update(state(units, True, 4)) == []
    assert match_tracker.unit_diagnostics()["misses"] == {"1": 2}

    clock.t = 15.0
    units, _diag = units_tracker.update_observations(
        [], IMAGE_SIZE, frame_id=5, observed_at=15.0)
    events = match_tracker.update(state(units, True, 5))
    assert events == []
    assert match_tracker.snapshot()["units"] == 0
    assert len(match_tracker.recent_unit_losses()) == 1

    for tick in range(16, 21):
        clock.t = float(tick)
        match_tracker.update(state([], False))
    assert len(match_tracker.recent_unit_losses()) == 1


def test_legacy_unit_tracker_update_contract_is_unchanged() -> None:
    tracker = UnitTracker()
    dets = [front_line(), unit_box()]
    assignment = assign_rows(dets, frame_h=1080)
    tracked = tracker.update(assignment)
    assert len(tracked) == 1
    assert isinstance(tracked[0], TrackedUnit)
    assert tracked[0].instance_id == 1
    assert tracked[0].side == SIDE_MINE
    assert tracked[0].lane == "support"
