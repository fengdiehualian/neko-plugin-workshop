"""周期计算核心（cycle.py）的本地单测。

运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forever_companion.core.cycle import (  # noqa: E402
    TideConfigError,
    build_body_whisper,
    build_status_payload,
    compute_phase_state,
    derive_cycle_params,
    parse_anchor_date,
    randomized_default_anchor,
    resolve_today,
    validate_cycle_params,
)


def _state(**overrides):
    params = {
        "today": date(2026, 8, 23),
        "anchor": date(2026, 8, 1),
        "cycle_length": 28,
        "period_length": 5,
        "ovulation_day": 14,
        "ovulation_window": 3,
        "advance_days": 0,
    }
    params.update(overrides)
    return compute_phase_state(**params)


def test_parse_anchor_date() -> None:
    assert parse_anchor_date("2026-08-01") == date(2026, 8, 1)
    try:
        parse_anchor_date("2026/08/01")
    except TideConfigError:
        pass
    else:
        raise AssertionError("expected TideConfigError")


def test_day1_is_menstrual() -> None:
    state = _state(today=date(2026, 8, 1))
    assert state.phase == "menstrual"
    assert state.cycle_day == 1


def test_day5_last_menstrual_day() -> None:
    state = _state(today=date(2026, 8, 5))
    assert state.phase == "menstrual"
    assert state.cycle_day == 5


def test_day7_follicular() -> None:
    state = _state(today=date(2026, 8, 7))
    assert state.phase == "follicular"


def test_ovulation_window_days_11_to_17() -> None:
    for day in (11, 14, 17):
        state = _state(today=date(2026, 8, day))
        assert state.phase == "ovulatory", f"day {day}"


def test_day20_luteal() -> None:
    state = _state(today=date(2026, 8, 20))
    assert state.phase == "luteal"


def test_cycle_wraps_back_to_menstrual() -> None:
    # 第 29 天 = 下一轮第 1 天
    state = _state(today=date(2026, 8, 29))
    assert state.phase == "menstrual"
    assert state.cycle_day == 1


def test_advance_days_shifts_body_clock() -> None:
    normal = _state(today=date(2026, 8, 7))
    advanced = _state(today=date(2026, 8, 7), advance_days=7)
    assert normal.phase == "follicular"
    assert advanced.cycle_day == normal.cycle_day + 7
    assert advanced.phase == "ovulatory"


def test_anchor_in_future_returns_before_start() -> None:
    state = _state(today=date(2026, 7, 30), anchor=date(2026, 8, 1))
    assert state.phase == "before_start"


def test_invalid_params_rejected() -> None:
    try:
        _state(cycle_length=5)
    except TideConfigError:
        pass
    else:
        raise AssertionError("cycle_length too short should fail")
    try:
        _state(period_length=28)
    except TideConfigError:
        pass
    else:
        raise AssertionError("period equal to cycle should fail")
    try:
        _state(ovulation_window=10)
    except TideConfigError:
        pass
    else:
        raise AssertionError("ovulation window overlapping period should fail")


def test_resolve_today_uses_timezone() -> None:
    utc_now = datetime(2026, 8, 23, 17, 0, tzinfo=timezone.utc)  # 上海已是 24 日凌晨 1 点
    assert resolve_today("Asia/Shanghai", now=utc_now) == date(2026, 8, 24)
    assert resolve_today("UTC", now=utc_now) == date(2026, 8, 23)


PHASES = {
    "menstrual": {
        "prompt": "容易疲倦，情绪敏感。",
        "time_morning": "早晨腹部不适明显。",
        "time_afternoon": "午后容易犯困。",
        "time_night": "深夜情绪低落。",
    },
}


def test_whisper_contains_phase_and_forbidden_words() -> None:
    state = _state(today=date(2026, 8, 2))  # 潮汐期第 2 天
    text = build_body_whisper(
        PHASES,
        state,
        timezone_name="Asia/Shanghai",
        forbidden_words=["月经", "激素"],
        now=datetime(2026, 8, 2, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert "潮汐期" in text
    assert "第 2 天" in text
    assert "午后容易犯困" in text
    assert "月经、激素" in text


def test_whisper_empty_before_start_and_skips_no_change_lines() -> None:
    before = _state(today=date(2026, 7, 30), anchor=date(2026, 8, 1))
    assert build_body_whisper(PHASES, before, timezone_name="Asia/Shanghai") == ""
    luteal_state = _state(today=date(2026, 8, 20))
    text = build_body_whisper({}, luteal_state, timezone_name="Asia/Shanghai")
    assert "平稳期" in text


def test_status_payload_shape() -> None:
    payload = build_status_payload(_state(), enabled=True)
    assert payload["phase"] == "luteal"
    assert payload["enabled"] is True
    assert payload["tone"] == "default"


# ---------- 自动时区与潮汐日历 ----------

def test_resolve_today_auto_uses_system_local() -> None:
    from datetime import date as _date
    assert resolve_today("auto") == _date.today()
    assert resolve_today("") == _date.today()


def test_time_bucket_auto_no_crash() -> None:
    from forever_companion.core.cycle import _time_bucket
    bucket = _time_bucket("auto")
    assert bucket in ("morning", "afternoon", "night")


def test_month_calendar_marks_tide_days() -> None:
    from forever_companion.core.cycle import build_month_calendar
    months = build_month_calendar(
        today=date(2026, 8, 23),
        anchor=date(2026, 8, 1),
        cycle_length=28,
        period_length=5,
        ovulation_day=14,
        ovulation_window=3,
    )
    assert len(months) == 3  # 上月/本月/下月
    cur = months[1]
    assert cur["year"] == 2026 and cur["month"] == 8
    cells = [c for c in cur["cells"] if c["in_month"]]
    assert len(cells) == 31
    # 8/1-8/5 为潮汐日；8/29 起下一轮（跨月），措辞不出现"月经"
    tide_days = [c["day"] for c in cells if c["is_tide"]]
    assert tide_days == [1, 2, 3, 4, 5, 29, 30, 31]
    assert all(c["phase_label"] == "潮汐日" for c in cells if c["is_tide"])
    assert "月经" not in str(cur["cells"])
    # 今天高亮
    today_cell = next(c for c in cells if c["day"] == 23)
    assert today_cell["is_today"] is True
    assert today_cell["phase"] == "luteal"
    # 下月预测：8/29 起的潮汐期跨到 9/1-9/2；再下一轮 9/26 起
    nxt = months[2]
    nxt_tide = [c["day"] for c in nxt["cells"] if c["in_month"] and c["is_tide"]]
    assert nxt_tide == [1, 2, 26, 27, 28, 29, 30]
    # 周一补位：2026-08-01 是周六，前面补 5 个空位
    assert cur["cells"][0]["in_month"] is False
    assert len([c for c in cur["cells"] if not c["in_month"]]) == 5


# ---------- 自动演算（derive_cycle_params） ----------

def test_derive_standard_cycle() -> None:
    # 28 天周期：活跃日 = 28 − 14，潮汐期 5 天，活跃窗口 ±3 天
    assert derive_cycle_params(28) == {
        "cycle_length": 28,
        "period_length": 5,
        "ovulation_day": 14,
        "ovulation_window": 3,
    }


def test_derive_short_and_long_cycles() -> None:
    # 短周期：活跃日夹取到潮汐期后至少留 1 天回升期，窗口随之收窄
    assert derive_cycle_params(10) == {
        "cycle_length": 10,
        "period_length": 5,
        "ovulation_day": 7,
        "ovulation_window": 1,
    }
    # 长周期：活跃日 = 90 − 14，窗口保持 ±3
    assert derive_cycle_params(90) == {
        "cycle_length": 90,
        "period_length": 5,
        "ovulation_day": 76,
        "ovulation_window": 3,
    }


def test_derive_manual_override_kept_and_clamped() -> None:
    # 合法手动值原样保留
    custom = derive_cycle_params(30, period_length=6, ovulation_day=16, ovulation_window=2)
    assert custom == {
        "cycle_length": 30,
        "period_length": 6,
        "ovulation_day": 16,
        "ovulation_window": 2,
    }
    # 越界手动值夹回合法区间，且组合仍然自洽
    clamped = derive_cycle_params(28, period_length=99, ovulation_day=1, ovulation_window=99)
    assert clamped["period_length"] <= 14
    assert clamped["ovulation_day"] >= clamped["period_length"] + 2
    assert clamped["ovulation_window"] >= 1


def test_derive_always_passes_validation() -> None:
    # 任意合法周期长度的自动演算结果都必须通过整体校验
    for length in range(10, 91):
        params = derive_cycle_params(length)
        validate_cycle_params(
            anchor=date(2026, 8, 1),
            cycle_length=params["cycle_length"],
            period_length=params["period_length"],
            ovulation_day=params["ovulation_day"],
            ovulation_window=params["ovulation_window"],
        )


def test_before_start_displayed_as_luteal_label() -> None:
    """锚点前的日子：数据层 phase=before_start（注入短路），展示层标签=平稳期。"""
    from forever_companion.core.cycle import _PHASE_LABELS_ZH, CALENDAR_PHASE_LABELS_ZH, build_status_payload

    state = _state(today=date(2026, 7, 30), anchor=date(2026, 8, 1))
    assert state.phase == "before_start"
    assert _PHASE_LABELS_ZH["before_start"] == "平稳期"
    assert CALENDAR_PHASE_LABELS_ZH["before_start"] == "平稳期"
    payload = build_status_payload(state, enabled=True)
    assert payload["phase"] == "before_start"
    assert payload["phase_label"] == "平稳期"





def test_randomized_default_anchor_lands_in_luteal() -> None:

    """随机默认锚点：今天必须落在平稳期（luteal）内，且不贴周期末。"""

    import random as random_mod



    today = date(2026, 8, 31)

    params = {"cycle_length": 28, "period_length": 5, "ovulation_day": 14, "ovulation_window": 3}

    for seed in range(50):

        rng = random_mod.Random(seed)

        anchor = randomized_default_anchor(today=today, **params, rng=rng)

        state = compute_phase_state(today=today, anchor=anchor, **params)

        assert state.phase == "luteal", f"seed={seed} phase={state.phase}"

        # 尾量：今天距下次潮汐至少 3 天（装完前几天不会立刻进潮汐期）

        assert state.days_until_next_period >= 3, f"seed={seed} tail={state.days_until_next_period}"





def test_randomized_default_anchor_varies() -> None:

    """不同随机序列产生不同锚点（不是每次都同一天）。"""

    import random as random_mod



    today = date(2026, 8, 31)

    params = {"cycle_length": 28, "period_length": 5, "ovulation_day": 14, "ovulation_window": 3}

    anchors = {

        randomized_default_anchor(today=today, **params, rng=random_mod.Random(seed))

        for seed in range(50)

    }

    assert len(anchors) > 1, "all seeds produced the same anchor"





def test_randomized_default_anchor_extreme_params() -> None:

    """极端周期参数下不崩溃且返回合法日期（平稳区间容不下尾量时的兜底）。"""

    import random as random_mod



    today = date(2026, 8, 31)

    # 平稳期极短：周期 10 天、活跃日 7、窗口 ±2 -> 平稳期只有第 10 天（容不下 3 天尾量）

    params = {"cycle_length": 10, "period_length": 2, "ovulation_day": 7, "ovulation_window": 2}


    for seed in range(20):

        anchor = randomized_default_anchor(today=today, **params, rng=random_mod.Random(seed))

        state = compute_phase_state(today=today, anchor=anchor, **params)

        assert state.phase in ("luteal", "follicular", "ovulatory"), f"seed={seed} phase={state.phase}"

        assert state.phase != "menstrual", "extreme params should never land on tide days"





def test_randomized_default_anchor_cycles_backwards() -> None:

    """锚点落在过去：周期循环反推，日历能给出"过去"的阶段（自然语义）。"""

    import random as random_mod



    today = date(2026, 8, 31)

    params = {"cycle_length": 28, "period_length": 5, "ovulation_day": 14, "ovulation_window": 3}

    anchor = randomized_default_anchor(today=today, **params, rng=random_mod.Random(0))

    assert anchor < today, "anchor should be in the past (cycle is circular)"

    state = compute_phase_state(today=today, anchor=anchor, **params)

    assert state.cycle_day >= 14 + 3 + 1  # 活跃期后第一天起

    assert state.cycle_day <= 28 - 3  # 预留尾量

