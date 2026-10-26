"""价格引擎。

教育目的说明
------------
这里生成的所有价格都是**虚构随机数**，不接入任何真实行情源，也不对应任何
真实股票或外汇对。价格曲线的唯一目的，是让玩家亲身体验"先让你小赢、再在
你上头时精准收割"的节奏。

退市约定（契约 §3.3）
--------------------
``next_price`` 返回 ``float | None``，``None`` 表示本 tick 退市。``Stock.alive``
只是状态镜像字段，由上层在拿到 ``None`` 时置为 ``False``。
"""

from __future__ import annotations

import random

from .config import STREAM_DELISTING, STREAM_MARKET, DifficultyConfig, MarketConfig
from .models import MarketState, Stock
from .rng import derive_stream_rng

__all__ = [
    "advance_market",
    "apply_jump",
    "classify_sigma",
    "create_market",
    "grow_sigma",
    "next_price",
    "replay_market",
]

_FICTIONAL_NAME_PARTS = (
    "环轨",
    "深蓝",
    "玄铁",
    "云栖",
    "长风",
    "琥珀",
    "苍梧",
    "寒星",
    "流萤",
    "烬羽",
    "白鹭",
    "青岩",
)

_FICTIONAL_NAME_SUFFIXES = (
    "科技",
    "物流",
    "重工",
    "光电",
    "材料",
    "能源",
    "食饮",
    "医药",
    "传媒",
    "农机",
)


def next_price(
    price: float,
    *,
    mu: float,
    sigma: float,
    rng: random.Random,
    floor: float = 0.0,
) -> float | None:
    """下一个 tick 的价格（纯函数）。

    公式：``price × (1 + mu + sigma × gauss(0, 1))``。

    返回 ``None`` 表示该标的本 tick 退市（价格触及 ``floor`` 地板）。
    """
    if price <= floor:
        return None
    value = price * (1.0 + mu + sigma * rng.gauss(0.0, 1.0))
    if value <= floor:
        return None
    return value


def apply_jump(
    price: float,
    *,
    severity: float,
    rng: random.Random | None = None,
) -> float:
    """向下跳跃：``price × (1 - severity)``。

    传入 ``rng`` 时，severity 会被 ±20% 抖动，让收割幅度不至于完全可预测；
    抖动使用派生 RNG，因此依旧可复现。
    """
    if rng is not None:
        severity = severity * (0.8 + 0.4 * rng.random())
    severity = min(1.0, max(0.0, severity))
    return max(0.0, price * (1.0 - severity))


def classify_sigma(
    sigma: float,
    *,
    stable_max: float = 0.10,
    explosive_min: float = 0.20,
) -> str:
    """按波动率给标的打标签：``stable`` / ``neutral`` / ``explosive``。"""
    if sigma <= stable_max:
        return "stable"
    if sigma >= explosive_min:
        return "explosive"
    return "neutral"


def grow_sigma(sigma_base: float, day: int, config: MarketConfig) -> float:
    """越往后越混沌：sigma 随天数递增，并以 ``sigma_max`` 封顶。"""
    grown = sigma_base + config.sigma_growth_per_day * max(0, day)
    return min(config.sigma_max, grown)


def create_market(
    seed: int,
    *,
    count: int,
    config: MarketConfig,
    difficulty: DifficultyConfig,
) -> dict[str, Stock]:
    """按种子生成初始标的集合。

    标的 id 形如 ``FAKE-003``，**不映射任何真实股票代码或外汇对**；名字由
    虚构词根组合而成。
    """
    rng = derive_stream_rng(seed, STREAM_MARKET, 1)
    total = max(1, count)
    stocks: dict[str, Stock] = {}
    for index in range(total):
        stock_id = f"FAKE-{index + 1:03d}"
        name = (
            f"{rng.choice(_FICTIONAL_NAME_PARTS)}"
            f"{rng.choice(_FICTIONAL_NAME_SUFFIXES)}"
            f"{index + 1}号"
        )
        price = round(rng.uniform(5.0, 100.0), 4)
        mu = rng.uniform(-0.005, 0.015)
        grown_sigma = grow_sigma(rng.uniform(0.05, 0.20), 0, config)
        stocks[stock_id] = Stock(
            id=stock_id,
            name=name,
            price=price,
            prev_price=price,
            mu=mu,
            sigma=grown_sigma,
            alive=True,
            listed_day=1,
            delisted_day=None,
            tag=classify_sigma(
                grown_sigma,
                stable_max=config.sigma_stable_max,
                explosive_min=config.sigma_explosive_min,
            ),
            kline=[],
        )
    return stocks


def advance_market(
    market: MarketState,
    *,
    seed: int,
    ticks_per_day: int,
    config: MarketConfig,
) -> None:
    """推进一个 tick 的行情（原地修改 market）。

    这是**唯一**的行情推进实现：实时推进与"按 seed 重放恢复"共用同一个函数，
    因此重放结果与实时结果必然逐位一致——这正是"价格不落库"能成立的前提。

    随机源派生键为 ``(seed, 行情流, day, tick, stock_index)``；``stock_index``
    是标的在 ``market.stocks`` 中的位置（插入序），退市不会改变其它标的的下标。

    退市判定走**独立的退市流**（``STREAM_DELISTING``）：价格已经贴近地板的标的
    进入"退市缓冲区"，每个 tick 用退市流抽一次签，抽中就被击穿退市。
    这样行情流与退市流互不干扰——改一条不会污染另一条。
    """
    tick = market.tick
    day = tick // max(1, ticks_per_day) + 1
    floor = config.delisting_floor
    buffer_price = floor * config.delisting_buffer_ratio
    for index, stock in enumerate(market.stocks.values()):
        if not stock.alive:
            continue
        rng = derive_stream_rng(seed, STREAM_MARKET, day, tick, index)
        value = next_price(
            stock.price,
            mu=stock.mu,
            sigma=stock.sigma,
            rng=rng,
            floor=floor,
        )
        if value is None:
            stock.alive = False
            stock.delisted_day = day
            continue
        if value <= buffer_price:
            delist_rng = derive_stream_rng(seed, STREAM_DELISTING, day, tick, index)
            if delist_rng.random() < config.delisting_prob_per_tick:
                stock.alive = False
                stock.delisted_day = day
                continue
        stock.prev_price = stock.price
        stock.price = value
        stock.kline.append((tick, value))
        if len(stock.kline) > config.kline_window:
            del stock.kline[: len(stock.kline) - config.kline_window]
    market.tick = tick + 1
    market.day = day


def replay_market(
    seed: int,
    *,
    until_tick: int,
    count: int,
    config: MarketConfig,
    difficulty: DifficultyConfig,
    ticks_per_day: int,
) -> MarketState:
    """从 ``seed`` 重放行情到 ``until_tick``（确定性恢复市场状态）。

    价格是 ``(seed, tick)`` 的纯函数，所以重启后只要用同一个 seed 重放，
    就能精确还原价格、prev_price、kline 与退市状态，无需把价格落库。
    """
    market = MarketState(
        seed=seed,
        tick=0,
        day=1,
        phase="honeymoon",
        stocks=create_market(seed, count=count, config=config, difficulty=difficulty),
    )
    for _ in range(max(0, until_tick)):
        advance_market(
            market,
            seed=seed,
            ticks_per_day=ticks_per_day,
            config=config,
        )
    return market
