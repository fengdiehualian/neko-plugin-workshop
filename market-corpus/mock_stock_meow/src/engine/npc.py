"""猫娘策略（NPC）。

教育目的说明
------------
猫娘是**普通的 Player 实例**，走同一套 heat 逻辑、同一个 settle 函数；本模块
**不存在**任何 ``if player.is_catgirl`` 式的豁免分支。她的存在是为了演示
"不冒险的人反而活得久"：收益率接近 0%、交易频率比玩家低、现金保留比例比玩
家高，但依然可能因为浮亏撞上账单日而饿死——这正是本沙盘想传达的另一半。

本模块只产出**意图**（要买什么/卖什么），实际成交由上层调用与人类玩家完全
相同的买卖函数完成。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import CatgirlConfig
from .models import MarketState, Player, Stock
from .pawnbroker import forced_sell_price, urgency

__all__ = ["NpcAction", "catgirl_daily_plan"]


@dataclass(frozen=True)
class NpcAction:
    """猫娘的一个行动意图。"""

    kind: str
    stock_id: str | None = None
    amount: float = 0.0
    price: float | None = None


def _is_recent_high(stock: Stock, price: float, *, ratio: float) -> bool:
    """当前价是否贴近近期高点（猫娘不追涨）。"""
    if not stock.kline:
        return False
    highest = max(value for _, value in stock.kline)
    return price >= highest * ratio


def _positions_by_smallest_loss(
    player: Player,
    prices: dict[str, float],
) -> list:
    """按浮亏从少到多排序（先卖最不心疼的那只）。"""
    def loss(position) -> float:
        price = prices.get(position.stock_id, 0.0)
        return (price - position.avg_cost) * position.amount

    return sorted(player.positions.values(), key=loss, reverse=True)


def catgirl_daily_plan(
    player: Player,
    *,
    market: MarketState,
    config: CatgirlConfig,
    rng: random.Random,
    next_day_living_cost: float,
) -> list[NpcAction]:
    """猫娘每日决策（纯函数：不修改任何状态，只产出意图）。

    规则：

    1. 现金够未来 ``buffer_days`` 天生活费 → 小额、低频、不追涨；
    2. 现金不够 → 先卖浮亏最少的持仓；
    3. 仍不够 → 走二道贩子强制卖出（按回收价）；
    4. 二道贩子也救不了 → 买不起小鱼干，饿死。
    """
    prices = market.prices()
    need = next_day_living_cost * config.buffer_days
    actions: list[NpcAction] = []

    if player.cash >= need:
        held = len(player.positions)
        if held < config.max_positions:
            candidates = [
                stock_id
                for stock_id in market.live_stock_ids()
                if stock_id not in player.positions
            ]
            rng.shuffle(candidates)
            for stock_id in candidates:
                if len(actions) >= config.max_positions - held:
                    break
                if rng.random() > config.trade_prob_per_tick:
                    continue
                stock = market.stocks[stock_id]
                price = prices.get(stock_id, 0.0)
                if price <= 0:
                    continue
                if _is_recent_high(stock, price, ratio=config.avoid_high_ratio):
                    continue
                budget = player.cash * config.trade_cash_ratio
                amount = budget / price
                if amount <= 0:
                    continue
                actions.append(NpcAction(kind="buy", stock_id=stock_id, amount=amount))
        return actions

    # 现金不足：先卖浮亏最少的持仓
    remaining = need - player.cash
    already_sold: set[str] = set()
    for position in _positions_by_smallest_loss(player, prices):
        if remaining <= 1e-9:
            break
        price = prices.get(position.stock_id, 0.0)
        if price <= 0:
            continue
        sellable = position.sellable_amount(player.day)
        if sellable <= 0:
            continue
        amount = min(sellable, remaining / price)
        if amount <= 0:
            continue
        actions.append(NpcAction(kind="sell", stock_id=position.stock_id, amount=amount))
        already_sold.add(position.stock_id)
        remaining -= amount * price

    # 仍不足：走二道贩子（只处理还没卖过的持仓）
    if remaining > 1e-9:
        urgency_value = urgency(remaining, next_day_living_cost)
        for position in list(player.positions.values()):
            if remaining <= 1e-9:
                break
            if position.stock_id in already_sold:
                continue
            price = prices.get(position.stock_id, 0.0)
            if price <= 0 or position.amount <= 0:
                continue
            recovery = forced_sell_price(price, urgency_value)
            actions.append(
                NpcAction(
                    kind="pawnbroker_sell",
                    stock_id=position.stock_id,
                    amount=position.amount,
                    price=recovery,
                )
            )
            remaining -= position.amount * recovery

    # 二道贩子也救不了 → 饿死
    if remaining > 1e-9:
        actions.append(NpcAction(kind="starve"))
    return actions
