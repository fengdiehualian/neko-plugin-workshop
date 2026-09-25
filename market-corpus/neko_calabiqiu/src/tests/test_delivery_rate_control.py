from __future__ import annotations

from neko_calabiqiu.core.arbiter import Arbiter
from neko_calabiqiu.core.contracts import IN_GAME, CbqConfig, CompanionEvent, SceneState
from neko_calabiqiu.core.safety_guard import SafetyGuard
from neko_calabiqiu.detectors.vision import VisionDetector


def _vision_event(
    event_id: str,
    *,
    created_at: float,
    level: str = "warning",
    bearing: str = "screen_left",
) -> CompanionEvent:
    return CompanionEvent(
        event_id,
        level=level,
        created_at=created_at,
        payload={"bearing": bearing},
    )


def _state(*, target_in_view: bool, events_tail: list[dict] | None = None) -> SceneState:
    return SceneState(
        connected=True,
        game_running=True,
        scenario=IN_GAME,
        flags={"target_in_view": target_in_view},
        events_tail=list(events_tail or []),
    )


def test_track_id_churn_does_not_repeat_spotted_while_target_remains_visible():
    detector = VisionDetector()
    empty = _state(target_in_view=False)
    first_visible = _state(
        target_in_view=True,
        events_tail=[
            {
                "event_seq": seq,
                "type": "target_appeared",
                "ts": 100.0,
                "payload": {"track_id": f"t_{seq}"},
            }
            for seq in range(1, 21)
        ],
    )

    first = detector.feed(empty, first_visible)
    assert [event.event_id for event in first] == ["enemy_spotted"]

    churn = _state(
        target_in_view=True,
        events_tail=[
            {
                "event_seq": 21,
                "type": "target_appeared",
                "ts": 101.0,
                "payload": {"track_id": "replacement"},
            }
        ],
    )
    assert detector.feed(first_visible, churn) == []


def test_lost_track_is_not_announced_while_another_target_remains_visible():
    detector = VisionDetector()
    visible = _state(target_in_view=True)
    one_track_lost = _state(
        target_in_view=True,
        events_tail=[
            {
                "event_seq": 1,
                "type": "target_lost",
                "ts": 100.0,
                "payload": {"track_id": "t_1"},
            }
        ],
    )

    assert detector.feed(visible, one_track_lost) == []


def test_rate_window_keeps_latest_equal_rank_event_and_drops_it_when_stale():
    cfg = CbqConfig(
        global_rate_limit_seconds=6.0,
        vision_output_event_max_age_seconds=3.0,
    )
    arbiter = Arbiter(SafetyGuard(cfg))

    first, _ = arbiter.decide(
        [_vision_event("enemy_spotted", created_at=100.0)], IN_GAME, 100.0
    )
    assert first is not None

    blocked, _ = arbiter.decide(
        [_vision_event("multi_threat", created_at=101.0, bearing="screen_left")],
        IN_GAME,
        101.0,
    )
    assert blocked is None
    blocked, _ = arbiter.decide(
        [_vision_event("enemy_nearby", created_at=103.0, bearing="screen_right")],
        IN_GAME,
        103.0,
    )
    assert blocked is None

    expired, chain = arbiter.decide([], IN_GAME, 107.0)
    assert expired is None
    assert any(item["reason"] == "window_event_expired" for item in chain)


def test_fresh_event_can_fill_slot_after_stale_window_candidate_is_evicted():
    cfg = CbqConfig(
        global_rate_limit_seconds=6.0,
        vision_output_event_max_age_seconds=3.0,
    )
    arbiter = Arbiter(SafetyGuard(cfg))

    assert arbiter.decide(
        [_vision_event("enemy_spotted", created_at=100.0)], IN_GAME, 100.0
    )[0]
    assert (
        arbiter.decide(
            [_vision_event("multi_threat", created_at=101.0)], IN_GAME, 101.0
        )[0]
        is None
    )

    chosen, chain = arbiter.decide(
        [_vision_event("enemy_nearby", created_at=106.0, bearing="screen_right")],
        IN_GAME,
        106.0,
    )
    assert chosen is not None
    assert chosen.event_id == "enemy_nearby"
    assert any(item["reason"] == "window_event_expired" for item in chain)


def test_repeated_critical_vision_alert_obeys_preempt_cooldown():
    cfg = CbqConfig(critical_preempt_cooldown_seconds=4.0)
    arbiter = Arbiter(SafetyGuard(cfg))

    first, _ = arbiter.decide(
        [_vision_event("enemy_nearby", created_at=100.0, level="critical")],
        IN_GAME,
        100.0,
    )
    assert first is not None

    repeated, chain = arbiter.decide(
        [_vision_event("enemy_nearby", created_at=102.0, level="critical")],
        IN_GAME,
        102.0,
    )
    assert repeated is None
    assert any(item["reason"] == "cooldown" for item in chain)


def test_non_vision_critical_event_is_not_blocked_by_vision_preempt_guard():
    cfg = CbqConfig(critical_preempt_cooldown_seconds=30.0)
    arbiter = Arbiter(SafetyGuard(cfg))

    assert arbiter.decide(
        [_vision_event("enemy_nearby", created_at=100.0, level="critical")],
        IN_GAME,
        100.0,
    )[0]
    death, _ = arbiter.decide(
        [CompanionEvent("player_death", level="critical", created_at=101.0)],
        IN_GAME,
        101.0,
    )
    assert death is not None
    assert death.event_id == "player_death"


def test_lifecycle_event_does_not_consume_visual_preempt_cooldown():
    cfg = CbqConfig(critical_preempt_cooldown_seconds=30.0)
    arbiter = Arbiter(SafetyGuard(cfg))

    started, _ = arbiter.decide(
        [CompanionEvent("game_started", level="critical", created_at=100.0)],
        IN_GAME,
        100.0,
    )
    assert started is not None

    nearby, _ = arbiter.decide(
        [_vision_event("enemy_nearby", created_at=101.0, level="critical")],
        IN_GAME,
        101.0,
    )
    assert nearby is not None
