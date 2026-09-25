"""潮汐时刻 —— 周期计算与状态注入文本构建。

纯函数模块：给定配置与当前时间，输出阶段判定与提示词。
不持有状态、不做 IO，方便单测。

四阶段划分（内部键 → 面向她的称呼）：
- menstrual  潮汐期: 第 1 ~ period_length 天
- ovulatory  活跃期: 活跃日前后各 ovulation_window 天
- follicular 回升期: 潮汐期结束 ~ 活跃期开始
- luteal     平稳期: 活跃期结束 ~ 周期末
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

PHASE_ORDER = ("menstrual", "follicular", "ovulatory", "luteal")

# 阶段的展示顺序按周期日排列；判定优先级：潮汐期 > 活跃期 > 其余按区间
_PHASE_TONE = {
    "menstrual": "danger",
    "follicular": "success",
    "ovulatory": "info",
    "luteal": "default",
}


class TideConfigError(ValueError):
    """周期参数不合法。"""


@dataclass(frozen=True, slots=True)
class PhaseState:
    """某一天所处的周期阶段快照。"""

    phase: str
    cycle_day: int  # 周期第几天（1 起）
    days_until_next_period: int  # 距下次潮汐首日
    day_ratio: float  # 在整个周期中的进度 0~1


def parse_anchor_date(raw: str) -> date:
    text = str(raw or "").strip()
    if not text:
        raise TideConfigError("anchor_date is empty")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise TideConfigError(f"anchor_date must be YYYY-MM-DD, got {text!r}") from exc


def validate_cycle_params(
    *,
    anchor: date,
    cycle_length: int,
    period_length: int,
    ovulation_day: int,
    ovulation_window: int,
) -> None:
    if cycle_length < 10 or cycle_length > 90:
        raise TideConfigError(f"cycle_length must be in [10, 90], got {cycle_length}")
    if period_length < 1 or period_length >= cycle_length:
        raise TideConfigError(f"period_length must be in [1, {cycle_length - 1}], got {period_length}")
    if ovulation_day < 2 or ovulation_day > cycle_length:
        raise TideConfigError(f"ovulation_day must be in [2, {cycle_length}], got {ovulation_day}")
    lo = ovulation_day - ovulation_window
    hi = ovulation_day + ovulation_window
    if lo <= period_length or hi > cycle_length:
        raise TideConfigError(
            "ovulation window overlaps menstrual phase or exceeds the cycle "
            f"(window {lo}..{hi}, period ends at {period_length})"
        )


def derive_cycle_params(
    cycle_length: int,
    *,
    period_length: int = 0,
    ovulation_day: int = 0,
    ovulation_window: int = 0,
) -> dict[str, int]:
    """由周期长度自动演算潮汐期长度、活跃日与活跃窗口。

    基于经典固定黄体期模型：黄体期长度相对恒定（约 14 天），
    因此活跃日 ≈ cycle_length − 14；潮汐期默认 5 天，活跃窗口默认 ±3 天。

    三个可选参数传 0（或负数）表示自动演算，传正数表示保留手动值
    （仍会夹取到合法区间）。返回的组合保证通过 validate_cycle_params。
    """
    cycle_length = max(10, min(90, int(cycle_length)))

    # 潮汐期：手动值优先，否则默认 5 天；
    # 上界留出至少 3 天给回升期与活跃日（活跃日 ≥ 潮汐期 + 2，窗口 ≥ 1）
    period = int(period_length) if int(period_length) > 0 else 5
    period = max(1, min(14, cycle_length - 3, period))

    # 活跃日：手动值优先，否则 周期长度 − 14；
    # 夹取保证潮汐期后至少留 1 天回升期、活跃日后至少留 1 天平稳期
    day = int(ovulation_day) if int(ovulation_day) > 0 else cycle_length - 14
    day = max(period + 2, min(cycle_length - 1, day))

    # 活跃窗口：手动值优先，否则 ±3 天；夹取保证不压潮汐期、不超出周期
    window = int(ovulation_window) if int(ovulation_window) > 0 else 3
    window = max(1, min(5, window, day - period - 1, cycle_length - day))

    return {
        "cycle_length": cycle_length,
        "period_length": period,
        "ovulation_day": day,
        "ovulation_window": window,
    }


def _resolve_tz(timezone_name: str | None):
    """时区解析：空/"auto" 用系统本地时区，否则按 IANA 名称。"""
    name = str(timezone_name or "").strip()
    if not name or name == "auto":
        return None  # None = 系统本地
    return ZoneInfo(name)


def resolve_today(timezone_name: str, now: datetime | None = None) -> date:
    tz = _resolve_tz(timezone_name)
    if tz is None:
        current = datetime.now().astimezone()  # 本地时区
    else:
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
    return current.date()


def randomized_default_anchor(
    *,
    today: date,
    cycle_length: int,
    period_length: int,
    ovulation_day: int,
    ovulation_window: int,
    rng: random.Random | None = None,
) -> date:
    """生成随机化的默认锚点：让"今天"落在平稳期（luteal）内的一段随机位置。

    旧默认锚点=今天导致两个问题：每次安装都从潮汐日第一天开始（不真实），
    且所有用户的第一天都是同一阶段。这里反推锚点使今天的周期日落在
    [活跃期结束+1, 周期末-最少剩余] 的平稳区间里随机取一点：

    - 锚点 = 今天 − (随机周期日 − 1)，即把今天放回本轮的平稳期某天
    - 平稳区起点 = ovulation_day + ovulation_window + 1（活跃期后第一天）
    - 预留 min_tail_days=3 天尾量：今天不能太贴近周期末（否则装完两三天
      就进潮汐期，与"前几天平稳"的初衷相悖），也不能贴活跃期末（刚结束
      活跃就装插件同样显刻意）
    - 兜底：区间异常（周期参数极端）时回落到"距下次潮汐 period_length+2 天"
      的安全偏移，保证返回值合法

    注意锚点可能落在过去（today − 数十天）--周期是循环的，日历会据此
    反推出"过去"的阶段，这正是"她早就有自己的节律、只是今天才开始被
    观测"的自然语义。
    """
    rng = rng or random
    cycle_length = max(10, min(90, int(cycle_length)))
    period_length = max(1, min(cycle_length - 1, int(period_length)))
    ovulation_day = max(2, min(cycle_length, int(ovulation_day)))
    ovulation_window = max(1, min(5, int(ovulation_window)))

    luteal_start = ovulation_day + ovulation_window + 1
    min_tail_days = 3
    luteal_end = cycle_length - min_tail_days  # 今天可取的最大周期日
    if luteal_start > luteal_end:
        # 极端参数（平稳期太短容不下尾量）：退化为距下次潮汐留出缓冲的取值
        luteal_start = max(period_length + 1, luteal_end)
    if luteal_start > luteal_end:
        # 仍不合法（如周期长度紧贴活跃窗口）：锚到上一轮末尾，保证今天距
        # 下次潮汐至少 min_tail_days 天
        cycle_day = max(1, cycle_length - min_tail_days)
    else:
        cycle_day = rng.randint(luteal_start, luteal_end)
    return today - timedelta(days=cycle_day - 1)


def compute_phase_state(
    *,
    today: date,
    anchor: date,
    cycle_length: int,
    period_length: int,
    ovulation_day: int,
    ovulation_window: int,
    advance_days: int = 0,
) -> PhaseState:
    """计算 today 所处阶段。advance_days 模拟"身体时钟比日历走得快"。"""
    validate_cycle_params(
        anchor=anchor,
        cycle_length=cycle_length,
        period_length=period_length,
        ovulation_day=ovulation_day,
        ovulation_window=ovulation_window,
    )
    effective = today + timedelta(days=max(0, int(advance_days)))
    delta_days = (effective - anchor).days

    # 锚点在未来的情况：还没开始第一轮，视为未进入周期
    if delta_days < 0:
        return PhaseState(phase="before_start", cycle_day=0, days_until_next_period=-delta_days, day_ratio=0.0)

    cycle_day = (delta_days % cycle_length) + 1

    period_end = period_length
    ovu_lo = max(period_end + 1, ovulation_day - ovulation_window)
    ovu_hi = min(cycle_length, ovulation_day + ovulation_window)

    if cycle_day <= period_end:
        phase = "menstrual"
    elif ovu_lo <= cycle_day <= ovu_hi:
        phase = "ovulatory"
    elif cycle_day < ovu_lo:
        phase = "follicular"
    else:
        phase = "luteal"

    days_until_next_period = cycle_length - cycle_day + 1
    return PhaseState(
        phase=phase,
        cycle_day=cycle_day,
        days_until_next_period=days_until_next_period,
        day_ratio=round(cycle_day / cycle_length, 4),
    )


def _time_bucket(timezone_name: str, now: datetime | None = None) -> str:
    """morning / afternoon / night 三段微调选择。"""
    tz = _resolve_tz(timezone_name)
    if tz is None:
        current = datetime.now().astimezone()
    else:
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
    hour = current.hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 19:
        return "afternoon"
    return "night"


# 日历用的委婉显示名（不出现"月经"字样，以"潮汐"代称）
CALENDAR_PHASE_LABELS_ZH = {
    "menstrual": "潮汐日",
    "follicular": "回升期",
    "ovulatory": "活跃期",
    "luteal": "平稳期",
    # 锚点前的日子：展示层按"平稳期"呈现（插件对安装前的身体状态一无所知，
    # 平稳期=无额外感受的默认人格，比"未开始"更贴切）；phase 数据层
    # 仍是 before_start，注入门控/资格判定不受影响
    "before_start": "平稳期",
}

# 注入模型与面板展示用的阶段名（潮汐意象，与日历委婉名同一套措辞）
_PHASE_LABELS_ZH = {
    "menstrual": "潮汐期",
    "follicular": "回升期",
    "ovulatory": "活跃期",
    "luteal": "平稳期",
    # 展示层与日历同一语义：锚点前按平稳期呈现（见 CALENDAR_PHASE_LABELS_ZH 注释）；
    # 注入链路对 before_start 短路（build_body_whisper 返回空串），此映射不进模型上下文
    "before_start": "平稳期",
}

_TIME_LABELS_ZH = {"morning": "早晨", "afternoon": "午后", "night": "深夜"}

_FORBID_NOTE = (
    "注意：绝不能向对方说出或暗示以上词汇，也不能用分析口吻解释自己的状态"
    "（例如\"因为激素变化所以烦躁\"）。只允许像普通人一样自然地表达，"
    "比如\"今天有点累\"\"最近状态不错\"。"
)


def build_body_whisper(
    phases_config: dict[str, Any],
    state: PhaseState,
    *,
    timezone_name: str,
    forbidden_words: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    """构建注入给模型的身体状态轻语（基础规则 + 当前阶段感受）。

    phases_config 形如::

        {"menstrual": {"prompt": ..., "time_morning": ..., ...}, ...}

    未开始周期时返回空串（调用方跳过注入）。
    """
    if state.phase == "before_start":
        return ""

    phase_cfg = dict(phases_config.get(state.phase) or {})
    parts: list[str] = []

    bucket = _time_bucket(timezone_name, now)
    time_text = str(phase_cfg.get(f"time_{bucket}") or "").strip()
    main_text = str(phase_cfg.get("prompt") or "").strip()
    # 阶段×情绪耦合：阶段不只影响身体感受，也影响情绪倾向（可配置关闭）
    mood_note = str(phase_cfg.get("mood_note") or "").strip()

    label = _PHASE_LABELS_ZH.get(state.phase, state.phase)
    time_label = _TIME_LABELS_ZH.get(bucket, bucket)
    header = (
        f"[身体感知系统] 现在是{label}第 {state.cycle_day} 天（{time_label}）。"
        f"你今天真实的身体感受是——{main_text or '一切正常。'}"
    )
    parts.append(header)
    if time_text and time_text != "无变化。正常。":
        parts.append(time_text)
    if mood_note:
        parts.append(mood_note)

    words = [w for w in (forbidden_words or []) if str(w).strip()]
    if words:
        parts.append("以下词汇禁止出现在你的任何回复中：" + "、".join(words) + "。" + _FORBID_NOTE)

    return "\n".join(part for part in parts if part)


def build_status_payload(state: PhaseState, *, enabled: bool) -> dict[str, Any]:
    """给入口/面板用的结构化状态。"""
    tone = _PHASE_TONE.get(state.phase, "default")
    return {
        "enabled": enabled,
        "phase": state.phase,
        "phase_label": _PHASE_LABELS_ZH.get(state.phase, state.phase),
        "tone": tone,
        "cycle_day": state.cycle_day,
        "days_until_next_period": state.days_until_next_period,
        "day_ratio": state.day_ratio,
    }


def build_month_calendar(
    *,
    today: date,
    anchor: date,
    cycle_length: int,
    period_length: int,
    ovulation_day: int,
    ovulation_window: int,
    advance_days: int = 0,
    months_back: int = 1,
    months_forward: int = 1,
) -> list[dict]:
    """构建逐月日历数据：每天标注阶段/是否潮汐日/周期第几天。

    返回 [months_back .. 当前月 .. months_forward] 的列表，每月含
    cells（对齐周一为首、含前后补位）。今天之后的日期是"预测"，
    今天及之前是"推算的过去"（同一套规则反推）。
    """
    months: list[dict] = []
    year, month = today.year, today.month
    for offset in range(-months_back, months_forward + 1):
        m_year = year + (month - 1 + offset) // 12
        m_month = (month - 1 + offset) % 12 + 1
        first_day = date(m_year, m_month, 1)
        if m_month == 12:
            next_first = date(m_year + 1, 1, 1)
        else:
            next_first = date(m_year, m_month + 1, 1)
        days_in_month = (next_first - first_day).days

        cells: list[dict] = []
        # 周一为一周之首，补前面的空位
        leading = first_day.weekday()
        for _ in range(leading):
            cells.append({"day": 0, "in_month": False})

        for d in range(1, days_in_month + 1):
            day_date = date(m_year, m_month, d)
            state = compute_phase_state(
                today=day_date,
                anchor=anchor,
                cycle_length=cycle_length,
                period_length=period_length,
                ovulation_day=ovulation_day,
                ovulation_window=ovulation_window,
                advance_days=advance_days,
            )
            cells.append({
                "day": d,
                "in_month": True,
                "phase": state.phase,
                "phase_label": CALENDAR_PHASE_LABELS_ZH.get(state.phase, state.phase),
                "is_tide": state.phase == "menstrual",
                "cycle_day": state.cycle_day,
                "is_today": day_date == today,
                "is_future": day_date > today,
            })
        months.append({
            "year": m_year,
            "month": m_month,
            "label": f"{m_year}年{m_month}月",
            "cells": cells,
        })
    return months
