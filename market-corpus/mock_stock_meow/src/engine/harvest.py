"""收割执行。

教育目的说明
------------
"养套杀"的最后一刀在这里落下。它对应真实世界里最常见的收割手法：
**等你把筹码全压在一只票上、并在最亢奋的时候，才真正开始出货**。

所以收割的力度与**集中度**直接挂钩（``jump_severity``）：越集中，跌得越狠；
没有集中度就谈不上收割。收割本身**只做价格跳跃**——不清仓、不强卖、不改
玩家的生死。强制卖出是二道贩子的活（见 ``engine/pawnbroker.py``），斩杀是
日终结算的活（见 ``engine/settlement.py``），三者互不越界。

本模块是**纯函数**：不认识数据库、不认识 N.E.K.O、不弹窗、不写日志。
所有标的 ID 形如 ``FAKE-xxx``，价格与幅度均为虚构，无现金价值。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .config import MarketConfig
from .heat import position_concentration
from .market import apply_jump
from .models import MarketState, Player
from .state_machine import jump_severity

__all__ = ["HarvestResult", "execute_harvest", "select_harvest_target"]

#: 收割触发源：阶段切换到收割期。
TRIGGER_PHASE = "phase"

#: 收割触发源：上头指数到达峰值拐点。
TRIGGER_PEAK = "peak"


@dataclass(frozen=True)
class HarvestResult:
    """一次收割的结果（纯数据）。

    ``heat_at_harvest`` 是**收割那一刻的上头指数**。它可以落库、可以在结算页
    展示，但**绝不能出现在局中的任何返回体里**——这是 P2 契约 §6 的硬底线。
    """

    stock_id: str
    severity: float
    concentration: float
    price_before: float
    price_after: float
    heat_at_harvest: float
    trigger: str
    phase: str


def select_harvest_target(
    player: Player,
    prices: dict[str, float],
) -> str | None:
    """选出收割目标：**持仓市值占比最高**的那一只。

    空仓返回 ``None``（没筹码的人收割不了）。
    """
    best_id: str | None = None
    best_value = 0.0
    for stock_id, position in player.positions.items():
        value = position.market_value(prices.get(stock_id, 0.0))
        if value > best_value:
            best_value = value
            best_id = stock_id
    return best_id


def execute_harvest(
    player: Player,
    market: MarketState,
    *,
    trigger: str,
    phase: str,
    threshold: float,
    rng: random.Random | None = None,
    config: MarketConfig | None = None,
) -> HarvestResult | None:
    """执行一次收割（原地修改 market 的价格与 K 线）。

    ``trigger`` 为 ``"peak"`` 时会先检查集中度是否达到 ``threshold``；
    ``trigger`` 为 ``"phase"`` 时不做这道检查（阶段切换本身就是收割信号）。

    没有持仓、目标标的不存在、或集中度不够时返回 ``None``（本次不收割）。
    """
    prices = market.prices()
    target_id = select_harvest_target(player, prices)
    if target_id is None:
        return None
    stock = market.stocks.get(target_id)
    if stock is None:
        return None

    concentration = position_concentration(player.positions, prices)
    if trigger == TRIGGER_PEAK and concentration < threshold:
        return None

    severity = jump_severity(concentration, phase=phase)
    price_before = stock.price
    price_after = apply_jump(price_before, severity=severity, rng=rng)

    stock.prev_price = stock.price
    stock.price = price_after
    stock.kline.append((market.tick, price_after))
    if config is not None and len(stock.kline) > config.kline_window:
        del stock.kline[: len(stock.kline) - config.kline_window]

    return HarvestResult(
        stock_id=target_id,
        severity=severity,
        concentration=concentration,
        price_before=price_before,
        price_after=price_after,
        heat_at_harvest=player.heat,
        trigger=trigger,
        phase=phase,
    )
