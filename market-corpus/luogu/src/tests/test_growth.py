"""Luogu plugin growth-strategy tests.

``analyze_growth`` and ``select_daily_problems`` are pure and deterministic
given the same ``daily_seed``. These tests pin tag-weakness semantics and the
day-offering stability contract.
"""

from __future__ import annotations

from datetime import date

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


def test_growth_suggestion_renders_tags_through_injected_label() -> None:
    """Production tags are numeric ids; prose must show names, not "42"."""
    report = g.analyze_growth(
        [_sub("P1", "WA", ("42",), attempt=4)],
        weak_attempt_threshold=3,
        tag_label=lambda tag_id: {"42": "线段树"}.get(tag_id, tag_id),
    )
    assert "线段树" in report.suggestion
    assert "42" not in report.suggestion
    # The structured report keeps the caller's tag values for pool matching.
    assert report.weak_tags[0].tag == "42"


def test_relative_weakness_compares_each_tag_with_the_users_own_rate() -> None:
    # Overall 4/7; 图论 is 0/3 while 动态规划 is a perfect 4/4.
    subs = [
        _sub("P1", "AC", ("动态规划",)),
        _sub("P2", "AC", ("动态规划",)),
        _sub("P3", "AC", ("动态规划",)),
        _sub("P4", "AC", ("动态规划",)),
        _sub("P5", "WA", ("图论",)),
        _sub("P6", "WA", ("图论",)),
        _sub("P7", "WA", ("图论",)),
    ]
    report = g.analyze_growth(subs, weak_attempt_threshold=3)
    relative = {stat.tag: stat for stat in report.relative_weak_tags}
    assert "图论" in relative
    assert "动态规划" not in relative
    assert relative["图论"].relative_gap == pytest.approx(4 / 7, abs=0.01)


def test_tag_stats_rate_records_not_weighted_attempts() -> None:
    """Rates must be comparable with the overall rate (one count per record)."""
    subs = [_sub("P1", "WA", ("图论",), attempt=10), _sub("P2", "AC", ("图论",))]
    report = g.analyze_growth(subs, weak_attempt_threshold=1)
    stat = {item.tag: item for item in report.tag_stats}["图论"]
    assert stat.attempts == 2  # not 11
    assert stat.ac_rate == pytest.approx(0.5)
    # The attempt-weighted counter still decides absolute weakness: solved once.
    assert report.weak_tags == ()


def test_difficulty_stats_skip_unrated_rows() -> None:
    subs = [
        _sub("P1", "AC", ("a",)),
        SubmissionRecord(pid="P2", title="P2", status="Wrong Answer", difficulty=7, tags=("a",), solved=False),
        SubmissionRecord(pid="P3", title="P3", status="Wrong Answer", difficulty=0, tags=("a",), solved=False),
    ]
    report = g.analyze_growth(subs)
    bands = {stat.difficulty: stat for stat in report.difficulty_stats}
    assert 3 in bands
    assert 7 in bands
    assert 0 not in bands  # 暂无评定 is not a difficulty band
    assert bands[7].ac_rate == pytest.approx(0.0)
    assert bands[3].ac_rate == pytest.approx(1.0)


def _pool(*pids: str) -> list[ProblemMeta]:
    return [ProblemMeta(pid=pid, title=pid, difficulty=3, tags=("a",)) for pid in pids]


def test_selection_reports_the_window_it_actually_used() -> None:
    report = g.analyze_growth([_sub("P1", "AC", ("a",))])
    selection = g.select_daily_with_suppression(
        report,
        _pool("P1", "P2"),
        difficulty_range=(2, 4),
        count=1,
        daily_seed="x",
        today=date(2026, 9, 27),
    )
    assert selection.difficulty_window == (2, 4)


def _pool_with(difficulties: dict[str, int]) -> list[ProblemMeta]:
    return [
        ProblemMeta(pid=pid, title=pid, difficulty=difficulty, tags=("a",))
        for pid, difficulty in difficulties.items()
    ]


def test_fresh_problem_in_a_wider_band_beats_repeating_yesterdays_set() -> None:
    """Freshness outranks band precision: cooldown is the outer loop."""
    report = g.analyze_growth([_sub("P1", "AC", ("a",))])
    selection = g.select_daily_with_windows(
        report,
        _pool_with({"P1": 3, "P2": 3, "P3": 3, "P4": 5, "P5": 5}),
        windows=[(3, 3), (3, 5)],
        count=2,
        daily_seed="x",
        history={"P1": "2026-09-26", "P2": "2026-09-26", "P3": "2026-09-26"},
        today=date(2026, 9, 27),
    )
    assert {p.pid for p in selection.problems} == {"P4", "P5"}
    assert selection.difficulty_window == (3, 5)
    assert selection.cooldown_days == 14  # the cooldown was never given up
    assert selection.relaxed is True  # ...but the widening is reported honestly


def test_repeats_happen_only_when_no_window_can_fill_the_set() -> None:
    report = g.analyze_growth([_sub("P1", "AC", ("a",))])
    selection = g.select_daily_with_windows(
        report,
        _pool_with({"P1": 3, "P2": 3}),
        windows=[(3, 3), (3, 5)],
        count=2,
        daily_seed="x",
        history={"P1": "2026-09-26", "P2": "2026-09-26"},
        today=date(2026, 9, 27),
    )
    assert {p.pid for p in selection.problems} == {"P1", "P2"}
    assert selection.cooldown_days == 0  # relaxed as the last resort
    assert selection.relaxed is True


def test_suppressed_pids_window_and_same_day_rule() -> None:
    today = date(2026, 9, 27)
    assert g.suppressed_pids({"P1": "2026-09-27"}, today, cooldown_days=14) == frozenset()
    assert g.suppressed_pids({"P1": "2026-09-26"}, today, cooldown_days=14) == frozenset({"P1"})
    assert g.suppressed_pids({"P1": "2026-09-10"}, today, cooldown_days=14) == frozenset()
    assert g.suppressed_pids({"P1": "2026-09-26"}, today, cooldown_days=0) == frozenset()
    assert g.suppressed_pids({}, today, cooldown_days=14, review_pids=frozenset({"P9"})) == frozenset({"P9"})


def test_cooldown_ladder_uses_the_strictest_tier_that_fits() -> None:
    report = g.analyze_growth([_sub("P1", "AC", ("a",))])
    selection = g.select_daily_with_suppression(
        report,
        _pool("P1", "P2", "P3", "P4", "P5"),
        difficulty_range=(1, 8),
        count=2,
        daily_seed="x",
        history={"P1": "2026-09-26", "P2": "2026-09-26"},
        today=date(2026, 9, 27),
        review_pids=frozenset({"P3"}),
    )
    assert selection.tier_index == 0
    assert selection.cooldown_days == 14
    assert selection.relaxed is False
    assert {p.pid for p in selection.problems} == {"P4", "P5"}


def test_cooldown_ladder_relaxes_rather_than_returning_an_empty_set() -> None:
    report = g.analyze_growth([_sub("P1", "AC", ("a",))])
    selection = g.select_daily_with_suppression(
        report,
        _pool("P1", "P2", "P3", "P4", "P5"),
        difficulty_range=(1, 8),
        count=4,
        daily_seed="x",
        history={"P1": "2026-09-26", "P2": "2026-09-26"},
        today=date(2026, 9, 27),
        review_pids=frozenset({"P3"}),
    )
    assert selection.relaxed is True
    assert selection.cooldown_days == 0  # the cooldown was given up, not the set
    assert len(selection.problems) == 4
    assert "P3" not in {p.pid for p in selection.problems}  # review still excluded


def test_review_exclusion_is_dropped_only_as_the_last_resort() -> None:
    report = g.analyze_growth([_sub("P1", "AC", ("a",))])
    selection = g.select_daily_with_suppression(
        report,
        _pool("P1", "P2", "P3"),
        difficulty_range=(1, 8),
        count=3,
        daily_seed="x",
        history={},
        today=date(2026, 9, 27),
        review_pids=frozenset({"P1", "P2", "P3"}),
    )
    assert selection.tier_index == len(g.COOLDOWN_LADDER) - 1
    assert selection.exclude_review is False
    assert len(selection.problems) == 3


def test_record_daily_picks_stamps_today_and_prunes_old_entries() -> None:
    today = date(2026, 9, 27)
    updated = g.record_daily_picks(
        {"OLD": "2026-01-01", "RECENT": "2026-09-20", "BROKEN": "nonsense"},
        _pool("NEW"),
        today,
        keep_days=120,
    )
    assert updated["NEW"] == "2026-09-27"
    assert "OLD" not in updated
    assert updated["RECENT"] == "2026-09-20"
    assert updated["BROKEN"] == "nonsense"  # unparsable dates are kept, not guessed at


def test_difficulty_band_follows_the_ability_level() -> None:
    # Asymmetric: practice reaches further up than down.
    assert g.difficulty_band(3.2) == (2, 5)
    assert g.difficulty_band(1.0) == (1, 3)  # clamped at the bottom of the scale
    assert g.difficulty_band(8.0) == (7, 8)  # and at the top
    assert g.difficulty_band(4.0, below=2, above=2) == (2, 6)
    assert g.difficulty_band(None) == (1, 3)  # garbage in, low band out


def test_history_round_trips_and_tolerates_junk() -> None:
    history = {"P1": "2026-09-27"}
    assert g.load_history(g.dump_history(history)) == history
    assert g.load_history(None) == {}
    assert g.load_history({"v": 99, "items": history}) == {}
    assert g.load_history({"v": g.HISTORY_VERSION, "items": "nope"}) == {}
