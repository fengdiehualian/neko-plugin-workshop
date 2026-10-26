"""Behavioral tests for OutputSafetyGuard and the breaker probe release."""

import json

from kards_companion.policy.safety import (
    OutputSafetyConfig,
    OutputSafetyGuard,
    SafetyCircuitBreaker,
    SafetyConfig,
)


class FakeClock:
    def __init__(self, value: float = 5000.0) -> None:
        self.t = value

    def __call__(self) -> float:
        return self.t


def make_guard(clock: FakeClock, **overrides) -> OutputSafetyGuard:
    base = dict(
        failure_threshold=5,
        failure_window_seconds=60.0,
        min_output_interval_seconds=12.0,
        critical_cooldown_seconds=5.0,
    )
    base.update(overrides)
    return OutputSafetyGuard(OutputSafetyConfig(**base), now_fn=clock)


def test_default_config_matches_contract() -> None:
    config = OutputSafetyConfig()
    assert config.failure_threshold == 5
    assert config.failure_window_seconds == 60.0
    assert config.min_output_interval_seconds == 12.0
    assert config.critical_cooldown_seconds == 5.0


def test_manual_pause_blocks_normal_critical_and_terminal() -> None:
    clock = FakeClock()
    guard = make_guard(clock)
    guard.pause("maintenance", now=clock.t)

    assert guard.allow(now=clock.t).reason == "manual_paused"
    assert guard.allow(critical=True, now=clock.t).reason == "manual_paused"
    assert guard.allow(terminal=True, now=clock.t).reason == "manual_paused"
    assert guard.remaining(now=clock.t) is None

    snapshot = guard.snapshot(now=clock.t)
    assert snapshot["paused"] is True
    assert snapshot["manual_paused"] is True
    assert snapshot["auto_paused"] is False
    assert snapshot["pause_reason"] == "maintenance"
    assert snapshot["remaining"] is None

    guard.resume(now=clock.t)
    assert guard.allow(now=clock.t).allowed


def test_auto_pause_needs_explicit_resume_and_blocks_terminal() -> None:
    clock = FakeClock()
    guard = make_guard(clock, failure_threshold=3)

    for _ in range(3):
        guard.record_failure(now=clock.t)

    assert guard.allow(now=clock.t).reason == "auto_paused"
    assert guard.allow(critical=True, now=clock.t).reason == "auto_paused"
    assert guard.allow(terminal=True, now=clock.t).reason == "auto_paused"
    assert guard.snapshot(now=clock.t)["auto_paused"] is True

    # No automatic recovery, even after the failure window has passed.
    clock.t += 3600
    assert guard.allow(now=clock.t).reason == "auto_paused"

    guard.resume(auto=True, now=clock.t)
    assert guard.allow(now=clock.t).allowed
    assert guard.snapshot(now=clock.t)["failures"] == 0


def test_manual_and_auto_pauses_are_independent() -> None:
    clock = FakeClock()
    guard = make_guard(clock, failure_threshold=2)
    guard.pause("manual", now=clock.t)
    guard.record_failure(now=clock.t)
    guard.record_failure(now=clock.t)

    snapshot = guard.snapshot(now=clock.t)
    assert snapshot["manual_paused"] is True
    assert snapshot["auto_paused"] is True

    guard.resume(auto=True, now=clock.t)
    snapshot = guard.snapshot(now=clock.t)
    assert snapshot["manual_paused"] is True
    assert snapshot["auto_paused"] is False
    assert guard.allow(now=clock.t).reason == "manual_paused"

    guard.resume(now=clock.t)
    assert guard.allow(now=clock.t).allowed


def test_failure_threshold_is_rolling_and_bounded() -> None:
    clock = FakeClock()
    guard = make_guard(clock, failure_threshold=3,
                       failure_window_seconds=10.0)

    guard.record_failure(now=clock.t)
    guard.record_failure(now=clock.t + 6)
    guard.record_failure(now=clock.t + 11)  # first failure is now outside
    snapshot = guard.snapshot(now=clock.t + 11)
    assert snapshot["auto_paused"] is False
    assert snapshot["failures"] == 2

    guard.record_failure(now=clock.t + 12)
    snapshot = guard.snapshot(now=clock.t + 12)
    assert snapshot["auto_paused"] is True
    assert snapshot["failures"] <= 3


def test_critical_clock_skips_normal_interval() -> None:
    clock = FakeClock()
    guard = make_guard(clock)
    guard.mark_output(now=clock.t)

    normal = guard.allow(now=clock.t + 1)
    assert normal.reason == "output_interval"
    assert normal.retry_after == 11.0

    assert guard.allow(critical=True, now=clock.t + 1).allowed
    guard.mark_output(critical=True, now=clock.t + 1)

    cooling = guard.allow(critical=True, now=clock.t + 3)
    assert cooling.reason == "critical_cooldown"
    assert cooling.retry_after == 3.0

    assert guard.allow(critical=True, now=clock.t + 6).allowed
    # The critical output also consumes the ordinary interval clock.
    paced = guard.allow(now=clock.t + 6)
    assert paced.reason == "output_interval"
    assert paced.retry_after == 7.0


def test_terminal_bypasses_cadence_but_never_pause() -> None:
    clock = FakeClock()
    guard = make_guard(clock)
    guard.mark_output(now=clock.t)

    assert guard.allow(terminal=True, now=clock.t + 1).allowed
    guard.mark_output(terminal=True, now=clock.t + 1)
    assert guard.snapshot(now=clock.t + 1)["terminal_emitted"] is True
    # Terminal once-only is the arbiter/session's job, not the guard's.
    assert guard.allow(terminal=True, now=clock.t + 1).allowed

    guard.pause("stop", now=clock.t + 2)
    assert guard.allow(terminal=True, now=clock.t + 2).reason == "manual_paused"


def test_mark_output_clears_failures_but_not_pause() -> None:
    clock = FakeClock()
    guard = make_guard(clock, failure_threshold=5)
    guard.record_failure(now=clock.t)
    guard.record_failure(now=clock.t)
    guard.pause("manual", now=clock.t)
    guard.mark_output(now=clock.t)

    snapshot = guard.snapshot(now=clock.t)
    assert snapshot["failures"] == 0
    assert snapshot["manual_paused"] is True
    assert guard.allow(now=clock.t + 12).reason == "manual_paused"

    guard.resume(now=clock.t + 12)
    assert guard.snapshot(now=clock.t + 12)["failures"] == 0


def test_remaining_and_snapshot_are_json_safe() -> None:
    clock = FakeClock()
    guard = make_guard(clock)
    assert guard.remaining(now=clock.t) == 0.0

    guard.mark_output(now=clock.t)
    assert guard.remaining(now=clock.t + 3) == 9.0
    guard.pause("hold", now=clock.t + 3)
    assert guard.remaining(now=clock.t + 3) is None

    snapshot = guard.snapshot(now=clock.t + 3)
    assert snapshot["remaining"] is None
    assert snapshot["last_output_at"] == clock.t
    assert snapshot["failure_window_started_at"] is None
    text = json.dumps(snapshot)
    assert "Infinity" not in text
    assert "NaN" not in text


def test_checkpoint_restore_and_fork() -> None:
    clock = FakeClock()
    guard = make_guard(clock)
    guard.mark_output(now=clock.t)
    guard.record_failure(now=clock.t)
    guard.pause("manual", auto=True, now=clock.t)

    checkpoint = guard.output_clock_checkpoint()
    assert json.dumps(checkpoint)
    assert checkpoint["failure_count"] == 1
    assert checkpoint["auto_paused"] is True

    restored = make_guard(clock)
    restored.restore(checkpoint)
    snapshot = restored.snapshot(now=clock.t)
    assert snapshot["auto_paused"] is True
    assert snapshot["last_output_at"] == clock.t
    assert snapshot["failures"] == 1
    assert restored.allow(now=clock.t).reason == "auto_paused"

    clone = guard.fork()
    clone.resume(auto=True, now=clock.t)
    assert clone.allow(now=clock.t + 12).allowed
    assert guard.allow(now=clock.t).reason == "auto_paused"

    guard.restore_output_clock(checkpoint)
    assert guard.snapshot(now=clock.t)["last_output_at"] == clock.t
    assert guard.snapshot(now=clock.t)["auto_paused"] is True


def test_checkpoint_restore_preserves_failure_time_distribution() -> None:
    clock = FakeClock()
    guard = make_guard(clock, failure_threshold=3,
                       failure_window_seconds=10.0)
    guard.record_failure(now=1000.0)
    guard.record_failure(now=1009.0)

    checkpoint = guard.output_clock_checkpoint()
    assert checkpoint["failure_timestamps"] == [1000.0, 1009.0]

    restored = make_guard(clock, failure_threshold=3,
                          failure_window_seconds=10.0)
    restored.restore(checkpoint)

    # At 1012 the 1000 failure is outside the 10 s window, so exactly the
    # 1009 failure remains.
    assert restored.snapshot(now=1012.0)["failures"] == 1

    restored.record_failure(now=1012.0)
    restored.record_failure(now=1013.0)
    snapshot = restored.snapshot(now=1013.0)
    assert snapshot["auto_paused"] is True
    assert snapshot["failures"] == 3


def test_checkpoint_restore_keeps_auto_pause_and_filters_bad_stamps() -> None:
    clock = FakeClock()
    guard = make_guard(clock, failure_threshold=3,
                       failure_window_seconds=10.0)
    guard.record_failure(now=1000.0)
    guard.record_failure(now=1001.0)
    guard.record_failure(now=1002.0)
    assert guard.snapshot(now=1002.0)["auto_paused"] is True

    restored = make_guard(clock, failure_threshold=3,
                          failure_window_seconds=10.0)
    restored.restore(guard.output_clock_checkpoint())
    snapshot = restored.snapshot(now=1002.0)
    assert snapshot["auto_paused"] is True
    assert snapshot["failures"] == 3
    assert restored.allow(now=1002.0).reason == "auto_paused"

    legacy = {
        "failure_count": 2,
        "failure_window_started_at": 0.0,
        "auto_paused": False,
    }
    compat = make_guard(clock, failure_threshold=3,
                        failure_window_seconds=10.0)
    compat.restore(legacy)
    # Count-only checkpoints cannot reconstruct the distribution, but they
    # must not make the failures look older than they are.
    assert compat.snapshot(now=1012.0)["failures"] == 2

    dirty = {
        "failure_count": 4,
        "failure_timestamps": [1000.0, float("nan"), float("inf"), 1009.0],
        "failure_window_started_at": 1000.0,
        "auto_paused": False,
    }
    filtered = make_guard(clock, failure_threshold=3,
                          failure_window_seconds=10.0)
    filtered.restore(dirty)
    assert filtered.snapshot(now=1012.0)["failures"] == 1
    assert json.dumps(filtered.output_clock_checkpoint())


def test_reset_clears_everything() -> None:
    clock = FakeClock()
    guard = make_guard(clock, failure_threshold=2)
    guard.pause("manual", now=clock.t)
    guard.record_failure(now=clock.t)
    guard.record_failure(now=clock.t)
    guard.mark_output(terminal=True, now=clock.t)

    guard.reset()
    snapshot = guard.snapshot(now=clock.t)
    assert snapshot["manual_paused"] is False
    assert snapshot["auto_paused"] is False
    assert snapshot["failures"] == 0
    assert snapshot["last_output_at"] is None
    assert snapshot["last_critical_at"] is None
    assert snapshot["terminal_emitted"] is False
    assert guard.allow(now=clock.t).allowed
    assert guard.allow(terminal=True, now=clock.t).allowed


def test_breaker_release_probe_returns_unused_half_open_slot() -> None:
    clock = FakeClock()
    breaker = SafetyCircuitBreaker(
        SafetyConfig(failure_threshold=1, recovery_seconds=10.0,
                     half_open_max_probes=1),
        now_fn=clock,
    )
    breaker.record_failure(now=clock.t)
    assert breaker.state(now=clock.t) == "open"

    clock.t += 10
    assert breaker.allow(now=clock.t) is True   # probe slot reserved
    assert breaker.allow(now=clock.t) is False  # no second slot

    breaker.release_probe()
    assert breaker.allow(now=clock.t) is True   # slot returned

    # Success still closes the breaker and resets probes.
    breaker.record_success(now=clock.t)
    assert breaker.state(now=clock.t) == "closed"
    assert breaker.allow(now=clock.t) is True
