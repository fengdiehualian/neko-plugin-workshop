"""Pipeline integration contracts across runtime and policy layers.

These tests pin the fail-closed boundaries that are easy to regress while the
main plugin entry is still being wired:

* an untrusted snapshot never reaches a detector;
* a rejected push receipt is never treated as submitted;
* TTL, bounded queue and the safety breaker interact deterministically;
* timeline/public snapshot payloads stay redacted.

Everything runs with injected clocks and fake pushes; no host or game window is
touched.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from kards_companion.delivery.arbiter import ArbiterConfig, CommentaryArbiter
from kards_companion.delivery.publisher import build_live_state_text, push_was_submitted
from kards_companion.policy.dispatcher import DeliveryDispatcher, DispatcherConfig
from kards_companion.policy.safety import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    SafetyCircuitBreaker,
    SafetyConfig,
)
from kards_companion.runtime.detectors import DetectorRegistry
from kards_companion.runtime.health import CaptureHealthSnapshot
from kards_companion.runtime.match_adapter import MatchTrackerAdapter
from kards_companion.runtime.snapshot import FrameMeta, RuntimeSnapshot
from kards_companion.runtime.timeline import RuntimeTimeline
from kards_companion.state.model import (
    E_HQ_DAMAGE,
    E_MATCH_START,
    E_TURN_SWITCH,
    E_UNIT_DIED,
    SCENE_MATCH,
    GameEvent,
    MatchState,
)
from kards_companion.state.tracker import MatchTracker


class FakeClock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class ReceiptPush:
    def __init__(self, receipt: Any = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.receipt = receipt

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.receipt(kwargs) if callable(self.receipt) else self.receipt


class ObjectReceipt:
    def __init__(self, **attributes: Any) -> None:
        self.__dict__.update(attributes)


def make_snapshot(
    *,
    seq: int = 1,
    observed_at: float = 0.0,
    reading: bool = True,
    valid: bool = True,
    with_image: bool = True,
    status: str = "capturing",
    state: MatchState | None = None,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        seq=seq,
        observed_at=observed_at,
        health=CaptureHealthSnapshot(status=status, reading=reading),
        frame=FrameMeta(frame_id=seq, captured_at=observed_at, valid=valid),
        state=state if state is not None else MatchState(),
        image=object() if with_image else None,
    )


class SpyDetector:
    name = "spy"

    def __init__(self) -> None:
        self.calls: list[tuple[RuntimeSnapshot | None, RuntimeSnapshot]] = []
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1

    def update(
        self, previous: RuntimeSnapshot | None, current: RuntimeSnapshot
    ) -> list[Any]:
        self.calls.append((previous, current))
        return []


class ExplodingDetector:
    name = "boom"

    def reset(self) -> None:
        return None

    def update(
        self, previous: RuntimeSnapshot | None, current: RuntimeSnapshot
    ) -> list[Any]:
        raise RuntimeError("detector exploded")


# ------------------------------------------------------- snapshot trust gate

@pytest.mark.parametrize(
    ("reading", "valid", "with_image", "expected"),
    [
        (True, True, True, True),
        (False, True, True, False),
        (True, False, True, False),
        (True, True, False, False),
        (False, False, False, False),
    ],
)
def test_snapshot_trusted_requires_reading_frame_and_image(
    reading: bool, valid: bool, with_image: bool, expected: bool
) -> None:
    snapshot = make_snapshot(
        reading=reading, valid=valid, with_image=with_image
    )
    assert snapshot.trusted is expected


@pytest.mark.parametrize(
    "variant",
    [
        {"reading": False},
        {"valid": False},
        {"with_image": False},
    ],
)
def test_untrusted_snapshot_never_reaches_any_detector(
    variant: dict[str, Any],
) -> None:
    spy = SpyDetector()
    registry = DetectorRegistry([spy])
    previous = make_snapshot(seq=1, observed_at=1.0)
    current = make_snapshot(seq=2, observed_at=2.0, **variant)

    events, diagnostics = registry.update(previous, current)

    assert events == []
    assert spy.calls == []
    assert [
        (diag.name, diag.outcome, diag.error) for diag in diagnostics
    ] == [("registry", "skipped", "capture_not_trusted")]


def test_trusted_snapshot_is_forwarded_in_registry_order() -> None:
    first, second = SpyDetector(), SpyDetector()
    registry = DetectorRegistry([first, second])
    previous = make_snapshot(seq=1, observed_at=1.0)
    current = make_snapshot(seq=2, observed_at=2.0)

    events, diagnostics = registry.update(previous, current)

    assert events == []
    assert first.calls == [(previous, current)]
    assert second.calls == [(previous, current)]
    assert [(diag.name, diag.outcome) for diag in diagnostics] == [
        ("spy", "ok"),
        ("spy", "ok"),
    ]


def test_live_state_keeps_unknown_turn_as_null() -> None:
    state = MatchState(scene=SCENE_MATCH, in_match=True)
    assert state.my_turn is None
    payload = json.loads(build_live_state_text(state).removeprefix("KARDS:"))
    assert payload["my_turn"] is None


def test_one_detector_failure_does_not_block_the_others() -> None:
    spy = SpyDetector()
    registry = DetectorRegistry([ExplodingDetector(), spy])
    current = make_snapshot()

    events, diagnostics = registry.update(None, current)

    assert events == []
    assert len(spy.calls) == 1
    outcomes = {diag.name: diag.outcome for diag in diagnostics}
    assert outcomes == {"boom": "error", "spy": "ok"}
    boom = next(diag for diag in diagnostics if diag.name == "boom")
    assert "RuntimeError" in boom.error


def test_hostile_untrusted_state_cannot_start_a_match() -> None:
    clock = FakeClock(0.0)
    adapter = MatchTrackerAdapter(MatchTracker(now_fn=clock))
    registry = DetectorRegistry([adapter])
    hostile = MatchState(
        scene=SCENE_MATCH,
        in_match=True,
        game_number=99,
        turn_number=9,
        my_hq=1,
        enemy_hq=1,
        detected_at=1.0,
    )

    for variant in (
        {"reading": False},
        {"valid": False},
        {"with_image": False},
    ):
        events, _diagnostics = registry.update(
            None,
            make_snapshot(state=hostile, observed_at=1.0, **variant),
        )
        assert events == []
        assert not adapter.tracker.in_match
        assert adapter.tracker.game_number == 0

    trusted = MatchState(scene=SCENE_MATCH, in_match=True, detected_at=2.0)
    events, _diagnostics = registry.update(
        None, make_snapshot(state=trusted, observed_at=2.0)
    )
    assert [candidate.event.kind for candidate in events] == [E_MATCH_START]
    assert adapter.tracker.in_match


# ------------------------------------------------------- dispatcher receipts

def make_arbiter(**overrides: Any) -> CommentaryArbiter:
    base: dict[str, Any] = {
        "mode": "companion",
        "min_priority": 1,
        "cooldown_seconds": 0.0,
        "critical_cooldown_seconds": 0.0,
    }
    base.update(overrides)
    return CommentaryArbiter(ArbiterConfig(**base))


def make_dispatcher(
    clock: FakeClock,
    *,
    receipt: Any = True,
    arbiter: CommentaryArbiter | None = None,
    config: DispatcherConfig | None = None,
    breaker: SafetyCircuitBreaker | None = None,
    timeline: RuntimeTimeline | None = None,
) -> tuple[DeliveryDispatcher, ReceiptPush]:
    push = ReceiptPush(receipt)
    dispatcher = DeliveryDispatcher(
        arbiter or make_arbiter(),
        push_message=push,
        breaker=breaker,
        config=config or DispatcherConfig(),
        now_fn=clock,
        timeline=timeline,
        prompt_builder=lambda summary, state: summary,
    )
    return dispatcher, push


def make_event(
    kind: str = E_HQ_DAMAGE,
    priority: int = 5,
    tag: str = "event",
    **details: Any,
) -> GameEvent:
    return GameEvent(
        kind=kind,
        priority=priority,
        summary=f"{kind}:{tag}",
        ts=0.0,
        details={"game": 1, "tag": tag, **details},
    )


def spy_mark_submitted(
    dispatcher: DeliveryDispatcher, monkeypatch: pytest.MonkeyPatch
) -> list[GameEvent]:
    marks: list[GameEvent] = []
    original = dispatcher.arbiter.mark_submitted

    def wrapped(event: GameEvent, *, now: float | None = None) -> None:
        marks.append(event)
        original(event, now=now)

    monkeypatch.setattr(dispatcher.arbiter, "mark_submitted", wrapped)
    return marks


@pytest.mark.parametrize(
    "receipt",
    [
        False,
        {"submitted": False},
        {"ok": False},
        {"submitted": False, "ok": True},
        {},
        ObjectReceipt(submitted=False),
        ObjectReceipt(is_ok=lambda: False),
    ],
)
def test_false_receipt_is_never_marked_submitted(
    receipt: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    dispatcher, push = make_dispatcher(
        clock, receipt=receipt, config=DispatcherConfig(max_attempts=1)
    )
    marks = spy_mark_submitted(dispatcher, monkeypatch)
    state = MatchState()
    target = make_event(tag="rejected")

    assert dispatcher.submit(target, state, now=clock.t).accepted
    outcomes = dispatcher.drain(now=clock.t)

    status = outcomes[0].status
    assert status in {"failed", "unknown"}, status
    assert status != "submitted"
    assert outcomes[0].reason in {"host_rejected", "receipt_unknown", "unknown"}
    assert len(push.calls) == 1
    assert marks == []
    assert dispatcher.pending == 0
    # A rejected receipt is not a submission: arbitration state is untouched.
    assert dispatcher.arbiter.decide(target, state, now=clock.t + 1).allowed


def test_unconfirmed_receipt_is_terminal_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    dispatcher, push = make_dispatcher(
        clock, receipt=None, config=DispatcherConfig(max_attempts=3)
    )
    marks = spy_mark_submitted(dispatcher, monkeypatch)
    state = MatchState()
    target = make_event(tag="legacy")

    dispatcher.submit(target, state, now=clock.t)
    first = dispatcher.drain(now=clock.t)
    assert [outcome.status for outcome in first] == ["unconfirmed"]
    assert first[0].reason == "legacy_unconfirmed"
    assert len(push.calls) == 1
    assert marks == []
    assert dispatcher.pending == 0

    # No blind retry after an unconfirmed legacy receipt.
    assert dispatcher.drain(now=clock.t + 1) == []
    assert len(push.calls) == 1


def test_push_exception_is_not_a_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**kwargs: Any) -> Any:
        raise RuntimeError("host down")

    clock = FakeClock()
    dispatcher = DeliveryDispatcher(
        make_arbiter(),
        push_message=boom,
        config=DispatcherConfig(max_attempts=1),
        now_fn=clock,
        prompt_builder=lambda summary, state: summary,
    )
    marks = spy_mark_submitted(dispatcher, monkeypatch)
    state = MatchState()
    target = make_event(tag="exception")
    dispatcher.submit(target, state, now=clock.t)

    outcomes = dispatcher.drain(now=clock.t)

    assert [outcome.status for outcome in outcomes] == ["failed"]
    assert outcomes[0].reason in {"transport", "push_exception:RuntimeError"}
    assert marks == []
    assert dispatcher.arbiter.decide(target, state, now=clock.t + 1).allowed


def test_confirmed_receipt_marks_submitted_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    dispatcher, push = make_dispatcher(clock, receipt={"submitted": True})
    marks = spy_mark_submitted(dispatcher, monkeypatch)
    state = MatchState()
    target = make_event(tag="confirmed")

    dispatcher.submit(target, state, now=clock.t)
    outcomes = dispatcher.drain(now=clock.t)

    assert [outcome.status for outcome in outcomes] == ["submitted"]
    assert outcomes[0].reason == "push_confirmed"
    assert len(push.calls) == 1
    assert [event.kind for event in marks] == [target.kind]
    assert marks[0].details.get("identity")
    assert (
        dispatcher.arbiter.decide(target, state, now=clock.t + 1).reason
        == "semantic_duplicate"
    )


def test_push_was_submitted_contract_for_receipt_shapes() -> None:
    # Strict receipt contract: only an explicit acceptance is True.  A legacy
    # ``None`` receipt is unconfirmed, never "submitted".
    assert push_was_submitted(None) is False
    assert push_was_submitted(True) is True
    assert push_was_submitted(False) is False
    assert push_was_submitted({"submitted": True}) is True
    assert push_was_submitted({"ok": True}) is True
    assert push_was_submitted({"submitted": False}) is False
    assert push_was_submitted({"ok": False}) is False
    assert push_was_submitted({}) is False
    assert push_was_submitted(ObjectReceipt(submitted=True)) is True
    assert push_was_submitted(ObjectReceipt(submitted=False)) is False
    assert push_was_submitted(ObjectReceipt(is_ok=lambda: True)) is True


# ------------------------------------------------------- TTL / queue / breaker

def test_ttl_boundary_expires_at_deadline_and_submits_just_before() -> None:
    clock = FakeClock(100.0)
    dispatcher, push = make_dispatcher(
        clock, config=DispatcherConfig(ttl_seconds=5.0)
    )
    state = MatchState()
    target = make_event(tag="exact")
    dispatcher.submit(target, state, now=clock.t)

    outcomes = dispatcher.drain(now=clock.t + 5.0)
    assert [outcome.status for outcome in outcomes] == ["expired"]
    assert outcomes[0].reason == "ttl_expired"
    assert push.calls == []
    assert dispatcher.pending == 0
    assert dispatcher.stats()["expired"] == 1
    assert dispatcher.arbiter.decide(target, state, now=clock.t + 5.0).allowed

    clock = FakeClock(100.0)
    dispatcher, push = make_dispatcher(
        clock, config=DispatcherConfig(ttl_seconds=5.0)
    )
    target = make_event(tag="just-in-time")
    dispatcher.submit(target, state, now=clock.t)
    outcomes = dispatcher.drain(now=clock.t + 4.999)
    assert [outcome.status for outcome in outcomes] == ["submitted"]
    assert len(push.calls) == 1


def test_single_slot_replaces_lower_priority_and_keeps_one_pending() -> None:
    clock = FakeClock()
    dispatcher, push = make_dispatcher(clock)
    state = MatchState()

    low = make_event(kind=E_TURN_SWITCH, priority=3, tag="low")
    high = make_event(kind=E_UNIT_DIED, priority=6, tag="high", side="enemy")

    assert dispatcher.submit(low, state, now=clock.t).accepted
    second = dispatcher.submit(high, state, now=clock.t)
    assert second.accepted and second.queue_depth == 1
    assert dispatcher.pending == 1
    assert dispatcher.stats()["replaced"] == 1

    outcomes = dispatcher.drain(now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["submitted"]
    assert [call["metadata"]["kind"] for call in push.calls] == [E_UNIT_DIED]
    assert dispatcher.pending == 0


def test_breaker_opens_blocks_delivery_and_half_open_probe_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    breaker = SafetyCircuitBreaker(
        SafetyConfig(
            failure_threshold=2,
            recovery_seconds=2.0,
            half_open_max_probes=1,
        ),
        now_fn=clock,
    )
    dispatcher, push = make_dispatcher(
        clock,
        receipt={"submitted": False},
        breaker=breaker,
        config=DispatcherConfig(max_attempts=1),
    )
    marks = spy_mark_submitted(dispatcher, monkeypatch)
    state = MatchState()

    dispatcher.submit(make_event(tag="f1"), state, now=clock.t)
    assert dispatcher.drain(now=clock.t)[0].status == "failed"
    dispatcher.submit(make_event(tag="f2"), state, now=clock.t)
    assert dispatcher.drain(now=clock.t)[0].status == "failed"
    assert breaker.state(now=clock.t) == OPEN

    blocked = make_event(tag="blocked")
    assert dispatcher.submit(blocked, state, now=clock.t).accepted
    outcomes = dispatcher.drain(now=clock.t)
    assert [outcome.status for outcome in outcomes] == ["deferred"]
    assert outcomes[0].reason == "safety_circuit_open"
    assert dispatcher.pending == 1
    assert len(push.calls) == 2
    assert marks == []
    assert dispatcher.arbiter.decide(blocked, state, now=clock.t + 1).allowed

    push.receipt = {"submitted": True}
    assert breaker.state(now=clock.t + 2.0) == HALF_OPEN
    recovered = dispatcher.drain(now=clock.t + 2.0)
    assert [outcome.status for outcome in recovered] == ["submitted"]
    assert breaker.state(now=clock.t + 2.0) == CLOSED
    assert dispatcher.pending == 0
    assert [event.kind for event in marks] == [blocked.kind]
    assert marks[0].details.get("identity")


def test_breaker_open_item_expires_without_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    breaker = SafetyCircuitBreaker(
        SafetyConfig(failure_threshold=1, recovery_seconds=30.0),
        now_fn=clock,
    )
    dispatcher, push = make_dispatcher(
        clock,
        receipt={"submitted": False},
        breaker=breaker,
        config=DispatcherConfig(ttl_seconds=5.0, max_attempts=1),
    )
    marks = spy_mark_submitted(dispatcher, monkeypatch)
    state = MatchState()

    dispatcher.submit(make_event(tag="trip"), state, now=clock.t)
    assert dispatcher.drain(now=clock.t)[0].status == "failed"
    assert breaker.state(now=clock.t) == OPEN

    queued = make_event(tag="queued-open")
    assert dispatcher.submit(queued, state, now=clock.t).accepted
    assert dispatcher.drain(now=clock.t + 1.0)[0].status == "deferred"
    assert dispatcher.pending == 1

    expired = dispatcher.drain(now=clock.t + 5.0)
    assert [outcome.status for outcome in expired] == ["expired"]
    assert dispatcher.pending == 0
    assert len(push.calls) == 1
    assert marks == []


# ------------------------------------------------------------- redaction

def test_timeline_drops_sensitive_keys_and_is_bounded() -> None:
    timeline = RuntimeTimeline(max_entries=3)
    for index in range(5):
        timeline.record(
            "dispatcher",
            "queued",
            ts=float(index),
            reason="r" * 200,
            detail={
                "kind": E_HQ_DAMAGE,
                "priority": 5,
                "image": b"pixels",
                "pixels": [1, 2, 3],
                "raw_ocr": "SECRET_RAW_OCR",
                "screenshot": "SECRET_SHOT",
                "api_" + "key": "SECRET_" + "KEY",
            },
        )

    data = timeline.as_dict(limit=10)
    assert data["dropped"] == 2
    assert len(data["recent"]) == 3
    for entry in data["recent"]:
        assert set(entry["detail"]) == {"kind", "priority"}
        assert len(entry["reason"]) == 160
    rendered = json.dumps(data, ensure_ascii=False)
    assert "SECRET" not in rendered
    assert "pixels" not in rendered


def test_dispatcher_timeline_never_records_event_details() -> None:
    clock = FakeClock()
    timeline = RuntimeTimeline(max_entries=20)
    dispatcher, _push = make_dispatcher(clock, timeline=timeline)
    state = MatchState()
    target = make_event(
        tag="secret",
        raw_ocr="SECRET_RAW_OCR",
        image=b"pixels",
        **{"api_" + "key": "SECRET_" + "KEY"},
    )

    dispatcher.submit(target, state, now=clock.t)
    dispatcher.drain(now=clock.t)

    data = timeline.as_dict(limit=20)
    assert data["recent"]
    allowed_keys = {"kind", "priority", "match_id", "attempts", "queue_depth",
                    "identity", "edge", "level"}
    for entry in data["recent"]:
        assert set(entry["detail"]) <= allowed_keys
    rendered = json.dumps(data, ensure_ascii=False)
    assert "SECRET" not in rendered
    assert "pixels" not in rendered


def test_public_snapshot_excludes_image_and_is_json_safe() -> None:
    state = MatchState(scene=SCENE_MATCH, in_match=True, my_hq=20, detected_at=12.0)
    snapshot = make_snapshot(
        seq=7, observed_at=12.0, status="degraded", state=state,
        with_image=True,
    )
    snapshot.image = b"SECRET_PIXELS"  # type: ignore[misc]

    payload = snapshot.to_public_dict()

    assert set(payload) == {
        "schema_version",
        "seq",
        "observed_at",
        "capture",
        "frame",
        "state",
    }
    assert payload["schema_version"] == "kards_runtime_snapshot_v1"
    assert "image" not in payload
    assert "secret_at" not in payload
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "SECRET_PIXELS" not in rendered
    # Health evidence is still visible; only pixels are withheld.
    assert payload["capture"]["status"] == "degraded"
    assert payload["capture"]["reading"] is True
    assert payload["frame"]["valid"] is True


def test_unit_tracker_none_is_not_empty_and_loss_is_never_death() -> None:
    """The detector-main seam distinguishes model failure from a real empty board."""
    from kards_companion.perception.detector import Detection, UnitTracker

    unit_tracker = UnitTracker()
    detection = Detection(0, "unit_board", 0.9, 2.0, 2.0, 5.0, 5.0)
    units, diag = unit_tracker.update_observations(
        [detection], (8, 8), frame_id=1, observed_at=1.0)
    assert units and units[0].bbox is not None
    assert units[0].side == "unknown"
    units, diag = unit_tracker.update_observations(
        [detection], (8, 8), frame_id=2, observed_at=2.0)
    assert units and units[0].track_confirmed is True
    unit_id = units[0].instance_id

    # None means the model was unavailable: retain last-known, no miss.
    units, diag = unit_tracker.update_observations(
        None, (8, 8), frame_id=3, observed_at=3.0,
        detector_available=False, observation_valid=False)
    assert units and units[0].instance_id == unit_id
    assert units[0].observed is False
    assert units[0].miss_count == 0
    assert diag["status"] == "model_unavailable"
    assert diag["lost"] == []
    assert unit_tracker.recent_losses() == []

    # [] is a successful empty observation; three valid misses produce only a
    # diagnostics/ledger loss, never a synthetic unit_died event.
    for frame_id in (4, 5, 6):
        _units, diag = unit_tracker.update_observations(
            [], (8, 8), frame_id=frame_id, observed_at=float(frame_id),
            detector_available=True, observation_valid=True)
    losses = unit_tracker.recent_losses()
    assert losses and all(item["kind"] == "unit_lost" for item in losses)
    assert diag["lost"]
    assert "unit_died" not in {item["kind"] for item in unit_tracker.recent_ledger()}
