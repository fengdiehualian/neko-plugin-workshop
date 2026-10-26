"""Heatmap calendar boundaries and relative color scales without a running host."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def heat_view():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend calendar checks")
    script = r"""
      import { stripTypeScriptTypes } from "node:module";
      import { readFileSync } from "node:fs";
      const root = process.argv[1];
      const utilsSource = stripTypeScriptTypes(readFileSync(root + "/ui/utils.ts", "utf8"));
      const utils = await import("data:text/javascript;base64," + Buffer.from(utilsSource).toString("base64"));
      const moment = readFileSync(root + "/ui/moment.tsx", "utf8");
      const calendar = moment.slice(moment.indexOf("function parseISODate("), moment.indexOf("// 月报导航"));
      const weekModule = stripTypeScriptTypes(calendar) + "\nexport { buildWeekColumns };";
      const { buildWeekColumns } = await import("data:text/javascript;base64," + Buffer.from(weekModule).toString("base64"));
      const records = [0, -3, NaN, Infinity, ...Array.from({ length: 40 }, (_, i) => i + 1), 100000];
      const original = [...records];
      const thresholds = utils.heatThresholds(records);
      console.log(JSON.stringify({
        thresholds,
        levels: records.map(n => utils.heatLevel(n, thresholds)),
        unchanged: records.every((n, i) => Object.is(n, original[i])),
        tied: [0, 12, 12, 12, 12].map(n => utils.heatLevel(n, utils.heatThresholds([12, 12, 12, 12]))),
        empty: utils.heatThresholds([0, -1, NaN]),
        early: buildWeekColumns("2026-01-01", "2026-01-02"),
        leap: buildWeekColumns("2024-01-01", "2024-12-31"),
        year: buildWeekColumns("2025-01-01", "2025-12-31"),
        boundary: buildWeekColumns("2026-10-30", "2026-11-03"),
        reversed: buildWeekColumns("2026-07-07", "2026-07-06"),
      }));
    """
    result = subprocess.run(
        [node, "--input-type=module", "-e", script, str(ROOT)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_relative_scale_keeps_typical_days_visible_and_equal_counts_equal(heat_view):
    assert heat_view["thresholds"] == [11, 21, 31]
    assert set(heat_view["levels"][4:-1]) == {1, 2, 3, 4}
    assert heat_view["levels"][-1] == 4
    assert heat_view["unchanged"] is True
    assert heat_view["tied"] == [0, 1, 1, 1, 1]
    assert heat_view["empty"] == [9, 29, 79]


def test_first_week_never_fabricates_previous_year_or_pre_relationship_days(heat_view):
    assert heat_view["early"][0]["cells"] == ["", "", "", "2026-01-01", "2026-01-02", "", ""]
    assert heat_view["early"][0]["monthNo"] == 1
    assert heat_view["boundary"][0]["cells"][:4] == ["", "", "", ""]
    assert [week["monthNo"] for week in heat_view["boundary"]] == [10, 11]
    assert heat_view["reversed"] == []


@pytest.mark.parametrize(("key", "year", "count"), [("leap", 2024, 366), ("year", 2025, 365)])
def test_full_year_contains_every_day_exactly_once(heat_view, key, year, count):
    weeks = heat_view[key]
    dates = [day for week in weeks for day in week["cells"] if day]
    assert len(weeks) == 53
    assert len(dates) == len(set(dates)) == count
    assert dates[0] == f"{year}-01-01"
    assert dates[-1] == f"{year}-12-31"
    assert [week["monthNo"] for week in weeks if week["monthNo"]] == list(range(1, 13))
