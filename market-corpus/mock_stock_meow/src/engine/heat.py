"""上头指数（heat）。

教育目的说明
------------
heat 是本沙盘的**核心教学变量**：它把"你在什么时候开始相信市场"变成一条
可计算的曲线。所谓"养套杀"，正是先让你小赢（蜜月期），等 heat 越过峰值
再精准收割。这套逻辑只存在于纯虚构的游戏里，不得用于任何真实的金融工具、
投资建议或营销场景。

机制要点
--------
* heat 是**实时曲线**，不是累计总分：每 tick 衰减一次（``decay``）。
* 公式：``H_new = H_old × decay + Σ(事件分) + 集中度状态分``。
* 局中 heat **完全隐藏**，只在结算页以 ``SettlementReport.heat_curve`` 出现。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from .config import HeatConfig
from .models import HeatEvent, HeatEventContext, Position

__all__ = [
    "apply_heat_events",
    "classify_concentration",
    "concentration_score",
    "heat_events_for_trade",
    "is_at_peak",
    "position_concentration",
    "update_heat",
]


def is_at_peak(
    heat_history: Sequence[float],
    *,
    threshold: float,
    window: int = 5,
) -> bool:
    """判断最近一个已完成的 tick 是否构成上头峰值（纯函数）。

    ``heat_history`` 是按时间升序的 heat 值序列，``heat_history[-1]`` 是本 tick
    的值 ``H_new``，``heat_history[-2]`` 是上一个 tick 的值 ``H_old``。

    三个条件必须**同时**成立：

    C1 超过阈值   ``H_old > threshold``（严格大于）
    C2 近窗口最高 ``H_old >= max(最近 window 个值，含 H_old 自身)``（允许并列）
    C3 开始回落   ``H_new < H_old``

    序列长度不足 2 时返回 ``False``。
    """
    if len(heat_history) < 2:
        return False
    old = heat_history[-2]
    new = heat_history[-1]
    if old <= threshold:
        return False
    window_slice = heat_history[-(window + 1) : -1]
    if window_slice and old < max(window_slice):
        return False
    return new < old


def classify_concentration(concentration: float) -> str:
    """集中度分档：``none`` / ``low`` / ``mid`` / ``high``。"""
    if concentration >= 0.90:
        return "high"
    if concentration >= 0.70:
        return "mid"
    if concentration >= 0.50:
        return "low"
    return "none"


def concentration_score(concentration: float, config: HeatConfig) -> float:
    """按集中度档位取状态分；低于 50% 返回 0。"""
    if concentration >= 0.90:
        return config.concentration_90
    if concentration >= 0.70:
        return config.concentration_70
    if concentration >= 0.50:
        return config.concentration_50
    return 0.0


def position_concentration(
    positions: Mapping[str, Position],
    prices: Mapping[str, float],
) -> float:
    """单一标的市值占总持仓市值的比例（0~1）。无持仓时返回 0。"""
    total = 0.0
    largest = 0.0
    for stock_id, position in positions.items():
        value = position.market_value(prices.get(stock_id, 0.0))
        total += value
        if value > largest:
            largest = value
    if total <= 0.0:
        return 0.0
    return largest / total


def heat_events_for_trade(
    context: HeatEventContext,
    config: HeatConfig,
) -> list[HeatEvent]:
    """由一次交易的上下文推导它触发的 heat 事件列表。

    这是"哪些行为让你上头"的唯一判定入口；分值一律从 ``config`` 取。
    """
    events: list[HeatEvent] = []
    if context.is_loss_position:
        events.append(HeatEvent.LOSS_AVERAGING)
    if context.is_chase_high:
        events.append(HeatEvent.CHASE_HIGH)
    if context.is_loss_position and context.is_leverage:
        events.append(HeatEvent.LOSS_LEVERAGE)
    if context.high_frequency:
        events.append(HeatEvent.HIGH_FREQUENCY)
    if context.after_fake_news:
        events.append(HeatEvent.FAKE_NEWS_CHASE)
    if context.after_thunder_news:
        events.append(HeatEvent.THUNDER_NEWS_AVERAGING)
    if context.living_cost_averaging:
        events.append(HeatEvent.LIVING_COST_AVERAGING)
    return events


def update_heat(
    heat: float,
    *,
    events: Sequence[HeatEvent],
    concentration: float,
    config: HeatConfig,
) -> float:
    """一次 tick 的 heat 更新（纯函数）。

    ``H_new = H_old × decay + Σ(事件分) + 集中度状态分 × concentration_weight``
    """
    total = concentration_score(concentration, config) * config.concentration_weight
    for event in events:
        total += config.event_scores.get(event, 0.0)
    return max(0.0, heat * config.decay + total)


def apply_heat_events(
    heat: float,
    events: Sequence[HeatEvent],
    config: HeatConfig,
) -> float:
    """叠加交易事件分（**不衰减**，纯函数）。

    衰减只发生在 tick 边界（:func:`update_heat`）；交易事件在两次 tick 之间
    发生时只加分，否则同一个 tick 内的多次交易会被重复衰减。
    """
    total = 0.0
    for event in events:
        total += config.event_scores.get(event, 0.0)
    return max(0.0, heat + total)
