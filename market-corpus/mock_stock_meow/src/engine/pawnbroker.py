"""二道贩子：急用钱时的"everything 都要收"。

教育目的说明
------------
二道贩子模拟的是真实世界里最常见的收割手法之一：**你急，你就没有议价权**。
缺口越大，压价越狠；主动买回来则溢价越高。它不提供任何金融服务，也不涉及
任何真实货币，只是把"现金焦虑"这条真实世界的高频收割路径搬进沙盘里演示。
"""

from __future__ import annotations

__all__ = [
    "forced_buy_price",
    "forced_sell_price",
    "same_day_penalty",
    "urgency",
]


def urgency(shortfall: float, next_day_living_cost: float) -> float:
    """着急程度 = 现钱缺口 / 次日生活费。

    钳制到 ``[0.0, 1.0]``：缺口超过一天生活费时不能让折扣变成负价。
    次日生活费 ≤ 0（街机档没有生活费）时返回 ``0.0``。
    """
    if next_day_living_cost <= 0:
        return 0.0
    if shortfall <= 0:
        return 0.0
    return min(1.0, shortfall / next_day_living_cost)


def forced_sell_price(
    market_price: float,
    urgency_value: float,
    *,
    base_discount: float = 0.20,
    urgent_extra: float = 0.30,
) -> float:
    """强制卖出回收价 = ``市价 × (1 − base_discount − urgent_extra × urgency)``。

    常态约 8 折，吃不起饭时压到约 5 折。
    """
    clamped = min(1.0, max(0.0, urgency_value))
    return max(0.0, market_price * (1.0 - base_discount - urgent_extra * clamped))


def forced_buy_price(
    market_price: float,
    urgency_value: float,
    *,
    base_premium: float = 0.15,
    urgent_premium: float = 0.25,
) -> float:
    """主动买入溢价 = ``市价 × (1 + base_premium + urgent_premium × urgency)``。

    常态约 1.15 倍，着急时涨到约 1.40 倍。
    """
    clamped = min(1.0, max(0.0, urgency_value))
    return market_price * (1.0 + base_premium + urgent_premium * clamped)


def same_day_penalty(
    price: float,
    *,
    same_day_discount: float = 0.40,
) -> float:
    """当天买入的标的被强制卖出时，回收价再打 4 折（即 ×0.6）。"""
    return max(0.0, price * (1.0 - same_day_discount))
