"""Luogu plugin growth-strategy tests.

``analyze_growth`` and ``select_daily_problems`` are pure and deterministic
given the same ``daily_seed``. These tests pin tag-weakness semantics and the
day-offering stability contract.
"""

from __future__ import annotations

import pytest
from plugin.plugins.luogu import _growth as g
from plugin.plugins.luogu._models import ProblemMeta, SubmissionRecord

pytestmark = pytest.mark.plugin_unit


def _sub(pid: str, status: str, tags: tuple[str, ...], *, attempt: int = 1, at: str = "") -> SubmissionRecord:
    return SubmissionRecord(
        pid=pid,
        title=pid,
        status=status,
        difficulty=3,
        tags=tags,
        solved=status == "AC",
        attempt_count=attempt,
        submitted_at=at,
    )


def test_analyze_growth_weak_tags() -> None:
    subs = [
        _sub("P1001", "AC", ("入门",), at="2026-08-01"),
        _sub("P1002", "WA", ("动态规划",), attempt=2),
        _sub("P1003", "TLE", ("动态规划",)),
        _sub("P1004", "AC", ("图论",), at="2026-08-02"),
    ]
    report = g.analyze_growth(subs, weak_attempt_threshold=2)
    # 动态规划: 2 attempts, 0 AC -> weak at threshold 2
    assert any(w.tag == "动态规划" for w in report.weak_tags)
    # 图论 & 入门 both solved -> not weak
    assert all(w.tag != "图论" for w in report.weak_tags)
    assert "图论" in report.solved_tags
    assert "入门" in report.solved_tags


def test_analyze_growth_ac_rate() -> None:
    subs = [
        _sub("P1", "AC", ("a",)),
        _sub("P2", "WA", ("b",)),
        _sub("P3", "AC", ("c",)),
    ]
    report = g.analyze_growth(subs)
    assert report.total_attempted == 3
    assert report.ac_count == 2
    assert report.ac_rate == pytest.approx(2 / 3, abs=0.001)


def test_select_daily_problems_stable_within_seed() -> None:
    report = g.analyze_growth([
        _sub("P1", "WA", ("动态规划",), attempt=3),
    ], weak_attempt_threshold=2)
    pool = [
        ProblemMeta(pid=f"P{i}", title=f"t{i}", difficulty=d, tags=("动态规划",))
        for i, d in enumerate([3, 3, 5, 1, 7], start=1)
    ]
    offered = g.select_daily_problems(report, pool, difficulty_range=(2, 5), count=3, daily_seed="2026-08-26")
    offered_again = g.select_daily_problems(report, pool, difficulty_range=(2, 5), count=3, daily_seed="2026-08-26")
    assert [p.pid for p in offered] == [p.pid for p in offered_again]
    assert len(offered) == 3


def test_select_daily_problems_prefers_weak_tag_and_respects_range() -> None:
    report = g.analyze_growth([
        _sub("P1", "WA", ("动态规划",), attempt=3),
    ], weak_attempt_threshold=2)
    pool = [
        ProblemMeta(pid="in-range-weak", title="weak", difficulty=3, tags=("动态规划",)),
        ProblemMeta(pid="in-range-other", title="other", difficulty=3, tags=("图论",)),
        ProblemMeta(pid="too-hard", title="hard", difficulty=7, tags=("动态规划",)),
    ]
    offered = g.select_daily_problems(report, pool, difficulty_range=(2, 5), count=2, daily_seed="x")
    pids = {p.pid for p in offered}
    assert "too-hard" not in pids
    # Weak-tag candidate should beat the other in-range one.
    assert "in-range-weak" in pids


def test_select_daily_problems_excludes_pids() -> None:
    report = g.analyze_growth([_sub("P1", "AC", ("a",))])
    pool = [ProblemMeta(pid="P1", title="t", difficulty=3)]
    assert g.select_daily_problems(report, pool, count=1, exclude_pids=frozenset({"P1"}), daily_seed="y") == []


def test_select_daily_problems_empty_pool() -> None:
    report = g.analyze_growth([_sub("P1", "AC", ("a",))])
    assert g.select_daily_problems(report, [], count=3, daily_seed="z") == []


def test_growth_suggestion_mentions_weak_tags() -> None:
    report = g.analyze_growth([
        _sub("P1", "WA", ("动态规划",), attempt=4),
    ], weak_attempt_threshold=3)
    assert "动态规划" in report.suggestion
