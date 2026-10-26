"""Luogu plugin heatmap tests (grid shape, intensity ranking, streaks).

The heatmap is what turns the daily AC counts into something a person can read, so
these pin the three things that are easy to get subtly wrong: the grid must be a
rectangle aligned to Mondays, intensity must be ranked against the user's own
spread (absolute buckets flatten a "1–3 problems a day" history), and a streak
must not survive a missed day.
"""

from __future__ import annotations

from datetime import date

import pytest
from plugin.plugins.luogu import _trend as t

pytestmark = pytest.mark.plugin_unit

# 2026-09-27 is a Sunday; 2026-09-21 is the Monday of that week.
TODAY = date(2026, 9, 27)
MONDAY = date(2026, 9, 21)


def test_heatmap_levels_rank_against_the_users_own_spread() -> None:
    counts = {
        "2026-09-23": 0,
        "2026-09-24": 1,
        "2026-09-25": 2,
        "2026-09-26": 3,
        "2026-09-27": 4,
    }
    levels = t.heatmap_levels(counts)
    assert levels["2026-09-23"] == 0  # an empty day is never shaded
    assert levels["2026-09-24"] == 1
    assert levels["2026-09-27"] == 4
    assert levels["2026-09-25"] < levels["2026-09-26"] < levels["2026-09-27"]


def test_heatmap_levels_of_a_flat_history_are_flat() -> None:
    counts = {"2026-09-26": 2, "2026-09-27": 2}
    assert set(t.heatmap_levels(counts).values()) == {4}


def test_heatmap_levels_of_nothing_are_empty() -> None:
    assert t.heatmap_levels({}) == {}
    assert t.heatmap_levels({"2026-09-27": 0}) == {"2026-09-27": 0}


def test_heatmap_grid_is_rectangular_and_monday_aligned() -> None:
    grid = t.heatmap_grid({TODAY.isoformat(): 2}, today=TODAY, days=14)
    assert len(grid) == 2
    assert all(len(column) == 7 for column in grid)
    assert grid[0][0]["date"] == "2026-09-14"
    assert date.fromisoformat(grid[0][0]["date"]).weekday() == 0
    assert grid[-1][-1]["date"] == TODAY.isoformat()
    assert grid[-1][-1]["count"] == 2
    assert grid[-1][-1]["future"] is False
    assert all(cell["in_range"] for column in grid for cell in column)


def test_heatmap_grid_marks_days_outside_the_window() -> None:
    grid = t.heatmap_grid({}, today=TODAY, days=10)
    assert len(grid) == 2
    # The window opens on a Friday, so the first four cells predate it.
    assert grid[0][0]["date"] == "2026-09-14"
    assert grid[0][0]["in_range"] is False
    assert sum(1 for column in grid for cell in column if cell["in_range"]) == 10


def test_heatmap_grid_marks_days_after_today_as_future() -> None:
    grid = t.heatmap_grid({MONDAY.isoformat(): 1}, today=MONDAY, days=7)
    # A seven-day window ending on a Monday opens on the previous Tuesday, so the
    # first column is partial and the second is today plus six future cells.
    assert len(grid) == 2
    assert grid[0][0]["in_range"] is False
    assert all(cell["in_range"] for cell in grid[0][1:])
    today_cell = grid[1][0]
    assert today_cell["date"] == MONDAY.isoformat()
    assert today_cell["future"] is False
    assert today_cell["count"] == 1
    assert today_cell["level"] == 4  # the only active day ranks top
    assert [cell["future"] for cell in grid[1]] == [False] + [True] * 6


def test_heatmap_grid_of_no_days_is_empty() -> None:
    assert t.heatmap_grid({}, today=TODAY, days=0) == []


def test_heatmap_grid_uses_supplied_levels() -> None:
    counts = {TODAY.isoformat(): 5}
    grid = t.heatmap_grid(counts, today=TODAY, days=7, levels={TODAY.isoformat(): 2})
    assert grid[-1][-1]["level"] == 2


def test_streak_days_counts_back_from_today() -> None:
    counts = {
        "2026-09-27": 1,
        "2026-09-26": 2,
        "2026-09-25": 1,  # current streak: 3
        "2026-09-20": 1,
        "2026-09-19": 1,
        "2026-09-18": 1,
        "2026-09-17": 1,  # longest streak: 4
    }
    assert t.streak_days(counts, TODAY) == (3, 4)


def test_streak_does_not_survive_a_missed_day() -> None:
    counts = {"2026-09-26": 3, "2026-09-25": 1}
    assert t.streak_days(counts, TODAY) == (0, 2)


def test_streak_ignores_empty_days_and_unparsable_dates() -> None:
    assert t.streak_days({}, TODAY) == (0, 0)
    assert t.streak_days({TODAY.isoformat(): 0}, TODAY) == (0, 0)
    assert t.streak_days({"nonsense": 5}, TODAY) == (0, 0)
