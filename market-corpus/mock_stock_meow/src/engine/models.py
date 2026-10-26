"""Pure data models for the market engine.

This module holds **data only** — no behaviour beyond two trivial derived
properties on :class:`Position`.  It must stay importable without any
N.E.K.O runtime and must not import any other engine module, which keeps the
dependency graph acyclic (see ``文档/P1-引擎层契约.md`` §0.2).

All models are plain data on purpose: a finished game can be serialised to
JSON (seed + trade log) and replayed later.

Educational notice
------------------
This module belongs to a **purely fictional** anti-fraud sandbox.  Stock ids
look like ``FAKE-xxx`` and never map to any real ticker or currency pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "HeatEvent",
    "HeatEventContext",
    "MarketState",
    "NewsItem",
    "Player",
    "Position",
    "SettlementReport",
    "Stock",
    "Trade",
]


class HeatEvent(Enum):
    """上头指数（heat）的事件类型。

    V1 规则见 ``文档/P1-引擎层契约.md`` §2.2。``CONCENTRATION`` 是状态分，
    每 tick 按当前集中度档位取值，其余为交易事件分。
    """

    LOSS_AVERAGING = "loss_averaging"
    CHASE_HIGH = "chase_high"
    LOSS_LEVERAGE = "loss_leverage"
    HIGH_FREQUENCY = "high_frequency"
    FAKE_NEWS_CHASE = "fake_news_chase"
    THUNDER_NEWS_AVERAGING = "thunder_news_averaging"
    CONCENTRATION = "concentration"
    LIVING_COST_AVERAGING = "living_cost_averaging"


@dataclass(frozen=True)
class HeatEventContext:
    """描述一次交易对 heat 的影响，由上层在成交时构造。"""

    is_loss_position: bool = False
    is_leverage: bool = False
    is_chase_high: bool = False
    high_frequency: bool = False
    after_fake_news: bool = False
    after_thunder_news: bool = False
    living_cost_averaging: bool = False
    concentration: float = 0.0


@dataclass
class Stock:
    """一只虚构标的。

    ``alive`` 是**状态镜像字段**：价格函数不返回它，退市由上层在拿到
    ``None`` 时把 ``alive`` 置为 ``False``（见契约 §3.3）。
    """

    id: str
    name: str
    price: float
    prev_price: float
    mu: float
    sigma: float
    alive: bool = True
    listed_day: int = 0
    delisted_day: int | None = None
    tag: str = "neutral"
    kline: list[tuple[int, float]] = field(default_factory=list)


@dataclass
class Position:
    """一个玩家在一只标的上的持仓。"""

    stock_id: str
    amount: float
    avg_cost: float
    today_bought: float = 0.0
    today_bought_day: int = 0
    opened_day: int = 0

    def sellable_amount(self, day: int) -> float:
        """当前交易日可卖出的数量（T+1 锁定今日买入部分）。"""
        if self.today_bought_day != day:
            return self.amount
        return max(0.0, self.amount - self.today_bought)

    def market_value(self, price: float) -> float:
        """按现价计算的市值。"""
        return self.amount * price

    def unrealized_pnl(self, price: float) -> float:
        """浮盈亏（未扣手续费）。"""
        return (price - self.avg_cost) * self.amount


@dataclass
class Trade:
    """一笔成交记录。"""

    id: int
    player_id: str
    stock_id: str
    side: str
    amount: float
    price: float
    fee: float
    tick: int
    day: int
    reason: str = "manual"


@dataclass
class Player:
    """一名玩家（人类玩家或猫娘共用同一个模型）。

    ``heat`` / ``heat_history`` 是**引擎内部字段**，局中不得出现在任何对外
    返回体，只在结算页以 ``SettlementReport.heat_curve`` 呈现。
    """

    id: str
    name: str
    cash: float
    mode: str
    day: int = 1
    alive: bool = True
    positions: dict[str, Position] = field(default_factory=dict)
    trade_count: int = 0
    heat: float = 0.0
    heat_history: list[float] = field(default_factory=list)
    tokens: int = 0
    strikes: int = 0
    loan_debt: float = 0.0
    is_catgirl: bool = False
    death_reason: str | None = None
    death_title: str | None = None
    pending_action: dict | None = None
    total_fee_paid: float = 0.0
    initial_cash: float = 0.0
    #: 连续未领取低保的天数（P4）。领取成功后归零；超过上限即判定 ``welfare_neglect``。
    welfare_missed_days: int = 0
    #: 最近一次借款发生在第几天（P4）。用于"每天最多借一次"。
    loan_taken_day: int = 0
    #: 最近一次领取低保发生在第几天（P4）。用于"同一发放周期内领取幂等"。
    welfare_claimed_day: int = 0
    #: 最近一次**分析**发生在第几天（P5）。用于"每天 N 次分析机会"的按天重置。
    analyses_day: int = 0
    #: 今天已经用掉的分析次数（P5）。
    analyses_used: int = 0

    def position_value(self, prices: dict[str, float]) -> float:
        """全部持仓的市值合计。"""
        return sum(
            position.market_value(prices.get(stock_id, 0.0))
            for stock_id, position in self.positions.items()
        )

    def total_asset(self, prices: dict[str, float]) -> float:
        """总资产 = 现钱 + 持仓市值。"""
        return self.cash + self.position_value(prices)


@dataclass
class MarketState:
    """全局市场状态。

    ``fee_pool`` 是全局手续费池：只累加、只显示与销毁，**不分配给任何人**。
    ``seed`` / ``reversal_days`` / ``news_today`` 属引擎内部，局中不外泄。
    """

    tick: int = 0
    day: int = 1
    phase: str = "honeymoon"
    seed: int = 0
    stocks: dict[str, Stock] = field(default_factory=dict)
    fee_pool: float = 0.0
    total_fees: float = 0.0
    news_today: list[NewsItem] = field(default_factory=list)
    reversal_days: set[int] = field(default_factory=set)

    def prices(self) -> dict[str, float]:
        """当前全部标的的现价表（退市标的取最后一次有效价）。"""
        return {stock_id: stock.price for stock_id, stock in self.stocks.items()}

    def live_stock_ids(self) -> list[str]:
        """仍在交易的标的 id。"""
        return [stock_id for stock_id, stock in self.stocks.items() if stock.alive]


@dataclass(frozen=True)
class NewsItem:
    """一条新闻。

    ``type`` 取值：``true`` / ``fake`` / ``vague`` / ``thunder`` / ``noise``。
    ``accuracy`` 仅对真新闻有意义，表示其内容的准确概率。

    ``direction``（P4 新增）是这条新闻的**多空方向**：``bullish`` / ``bearish`` /
    ``None``。噪音与模糊新闻没有方向。它是引擎**内部**字段：局中返回给玩家的
    只有中性分类 ``kind``，真假与方向都由引擎自己拿去算 heat 事件。
    """

    id: str
    day: int
    type: str
    stock_id: str | None
    content: str
    accuracy: float | None = None
    #: 方向："bullish" / "bearish" / None（噪音与模糊新闻没有方向）
    direction: str | None = None


@dataclass(frozen=True)
class SettlementReport:
    """一局结束的完整结算报告。

    这是 heat 曲线与种子**唯一**允许出现的地方。
    """

    player_id: str
    mode: str
    seed: int
    survivor: bool
    days_survived: int
    ticks_played: int
    trade_count: int
    initial_cash: float
    final_cash: float
    final_asset: float
    total_fee_paid: float
    fee_pool_total: float
    heat_curve: list[float]
    peak_day: int | None
    peak_heat: float
    death_reason: str | None
    death_title: str | None
    achievements: list[str]
    ending: str | None
    catgirl_result: dict | None
    news_log: list[NewsItem]
