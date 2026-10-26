"""Luogu plugin ability-estimate tests.

The estimator is pure arithmetic, so these pin the behaviour that matters:
recency decay, attempt penalty, robust reduction, outlier damping, the AC-rate
correction that needs a minimum signal, and a recalibration step that a single
good day cannot blow past.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from plugin.plugins.luogu import _ability as a
from plugin.plugins.luogu._models import SubmissionRecord

pytestmark = pytest.mark.plugin_unit

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _ac(difficulty: int, *, days_ago: float = 1.0, attempts: int = 1, pid: str = "") -> SubmissionRecord:
    stamp = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return SubmissionRecord(
        pid=pid or f"P{int(difficulty)}x{int(days_ago)}",
        title="t",
        status="Accepted",
        difficulty=difficulty,
        solved=True,
        attempt_count=attempts,
        submitted_at=stamp,
    )


def test_recency_weight_halves_at_one_half_life() -> None:
    assert a.recency_weight(0) == pytest.approx(1.0)
    assert a.recency_weight(a.HALF_LIFE_DAYS) == pytest.approx(0.5)
    assert a.recency_weight(a.HALF_LIFE_DAYS * 2) == pytest.approx(0.25)
    assert a.recency_weight(-5) == pytest.approx(1.0)  # clock skew is not negative evidence
    assert a.recency_weight(10, half_life_days=0) == pytest.approx(1.0)


def test_attempt_factor_decays_towards_a_floor() -> None:
    assert a.attempt_factor(1) == pytest.approx(1.0)
    assert a.attempt_factor(2) < a.attempt_factor(1)
    assert a.attempt_factor(50) == pytest.approx(a.MIN_ATTEMPT_FACTOR)


def test_difficulty_valid_rejects_the_unrated_band() -> None:
    assert a.difficulty_valid(1)
    assert a.difficulty_valid(8)
    assert not a.difficulty_valid(0)  # 暂无评定 carries no evidence at all
    assert not a.difficulty_valid(9)


def test_weighted_median_and_mean() -> None:
    assert a.weighted_median([]) is None
    assert a.weighted_mean([]) is None
    assert a.weighted_median([(3.0, 1.0)]) == pytest.approx(3.0)
    assert a.weighted_mean([(2.0, 1.0), (4.0, 3.0)]) == pytest.approx(3.5)
    # An even weight split lands between the two middle votes.
    assert a.weighted_median([(1.0, 1.0), (5.0, 1.0)]) == pytest.approx(3.0)
    # Heavier weight on one side pulls the median onto that vote.
    assert a.weighted_median([(1.0, 1.0), (5.0, 3.0)]) == pytest.approx(5.0)
    assert a.weighted_median([(1.0, 0.0), (5.0, 0.0)]) is None


def test_reduce_evidence_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        a.reduce_evidence([])


def test_damp_outliers_only_touches_votes_far_above_the_centre() -> None:
    damped = a.damp_outliers([(3.0, 1.0), (8.0, 1.0), (2.0, 1.0)], 3.0)
    assert damped == [(3.0, 1.0), (8.0, 0.5), (2.0, 1.0)]


def test_performance_adjustment_needs_signal_and_is_capped() -> None:
    assert a.performance_adjustment(3, 0) == 0.0  # too few submissions to judge
    assert a.performance_adjustment(10, 5) == pytest.approx(0.0)
    assert a.performance_adjustment(10, 10) == pytest.approx(a.ADJUST_CAP)
    assert a.performance_adjustment(10, 0) == pytest.approx(-a.ADJUST_CAP)
    assert a.performance_adjustment(100, 60) == pytest.approx(0.15)


def test_recalibrate_is_bounded_and_scaled_by_evidence() -> None:
    moved = a.recalibrate(2.0, 6.0, a.CONFIDENT_SAMPLES)
    assert 2.0 < moved < 6.0  # part-way, never all the way
    tiny = a.recalibrate(2.0, 6.0, 1.0)
    assert tiny - 2.0 < 0.1  # one lucky problem barely moves it
    assert a.recalibrate(1.0, 8.0, 1000.0) <= 1.0 + a.RECALIBRATION_CAP


def test_collect_evidence_weights_recent_first_try_solves_most() -> None:
    records = [_ac(5, days_ago=1), _ac(5, days_ago=40, attempts=6)]
    evidence = a.collect_evidence(records, now=NOW, window_days=60)
    assert len(evidence) == 2
    assert evidence[0][1] > evidence[1][1]


def test_estimate_ability_of_steady_first_try_solves() -> None:
    records = [_ac(4, days_ago=day) for day in range(0, 20, 2)]
    estimate = a.estimate_ability(records, now=NOW)
    # The evidence sits exactly at the difficulty the user keeps solving...
    assert estimate.base == pytest.approx(4.0, abs=0.2)
    # ...and a perfect AC rate adds the capped "you are under-challenged" bonus.
    assert estimate.adjustment == pytest.approx(a.ADJUST_CAP)
    assert estimate.level == pytest.approx(4.0 + a.ADJUST_CAP, abs=0.1)
    # Recency decay means 10 solves three weeks apart are well under 10 "fresh"
    # samples, so full confidence needs considerably more history than this.
    assert 0.3 < estimate.confidence < 1.0
    assert estimate.recalibrated is False
    assert estimate.ac_count == len(records)


def test_estimate_ability_damps_a_lone_hard_solve() -> None:
    easy = [_ac(2, days_ago=day) for day in range(1, 9)]
    estimate = a.estimate_ability(easy + [_ac(8, days_ago=1)], now=NOW)
    assert estimate.base < 3.0  # one 黑题 does not define the level


def test_attempts_shift_the_centre_towards_the_cleaner_solves() -> None:
    records = [_ac(3, days_ago=day) for day in (1, 2, 3)] + [_ac(6, days_ago=1, attempts=12)]
    estimate = a.estimate_ability(records, now=NOW)
    assert estimate.base < 4.0


def test_low_ac_rate_lowers_the_estimate_once_there_is_enough_signal() -> None:
    solved = [_ac(5, days_ago=day) for day in range(1, 6)]
    failed = [
        SubmissionRecord(
            pid=f"F{day}",
            title="f",
            status="Wrong Answer",
            difficulty=5,
            solved=False,
            submitted_at=(NOW - timedelta(days=day)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for day in range(1, 11)
    ]
    estimate = a.estimate_ability(solved + failed, now=NOW)
    assert estimate.adjustment < 0
    assert estimate.level < estimate.base


def test_estimate_ability_requires_rated_solves() -> None:
    records = [
        SubmissionRecord(pid="P1", status="Accepted", difficulty=0, solved=True, submitted_at="2026-09-27T00:00:00Z"),
        SubmissionRecord(pid="P2", status="Wrong Answer", difficulty=3, solved=False, submitted_at="2026-09-27T00:00:00Z"),
    ]
    estimate = a.estimate_ability(records, now=NOW)
    assert estimate.level == pytest.approx(a.DEFAULT_LEVEL)
    assert estimate.confidence == 0.0
    assert estimate.ac_count == 1
    assert estimate.ac_rate == pytest.approx(0.5)
    assert "没有" in estimate.reason


def test_estimate_ability_ignores_submissions_outside_the_window() -> None:
    estimate = a.estimate_ability([_ac(8, days_ago=200), _ac(3, days_ago=1)], now=NOW, window_days=60)
    assert estimate.level == pytest.approx(3.0, abs=0.3)
    assert estimate.ac_count == 1  # the stale solve is not even counted


def test_missing_timestamps_count_as_neither_fresh_nor_stale() -> None:
    records = [
        _ac(4, days_ago=1),
        SubmissionRecord(pid="P9", status="Accepted", difficulty=4, solved=True, submitted_at=""),
    ]
    estimate = a.estimate_ability(records, now=NOW, window_days=60)
    assert estimate.ac_count == 2
    assert estimate.level == pytest.approx(4.0, abs=0.3)


def test_recalibration_only_moves_partway_from_the_stored_level() -> None:
    records = [_ac(7, days_ago=day) for day in range(1, 15)]
    cold = a.estimate_ability(records, now=NOW)
    warm = a.estimate_ability(records, now=NOW, previous=2.0)
    assert warm.recalibrated is True
    assert warm.previous_level == pytest.approx(2.0)
    assert 2.0 < warm.level < cold.level
    assert warm.confidence == cold.confidence
