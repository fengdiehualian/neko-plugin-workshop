"""模式层共享组件（P4）。

铁律
----
1. **只 import ``..engine``**（纯标准库引擎层）；绝不 import 任何 ``plugin.*``，
   绝不认识 N.E.K.O 的存在。
2. **不碰数据库、不做任何 IO**（不读文件、不写文件、不开连接）。落库与写回
   全部由第一层（插件层）负责。
3. 一切随游戏推进而变化的量都通过返回的 dataclass 交给插件层；本层**只改
   内存里的 ``Player`` / ``MarketState`` 对象**。

为什么要单独一层
----------------
P3 的 tick 流水线与日终结算全部写在插件层里，插件层越写越胖，而且"经典四档"
与"无限模式"两套流程混在一起。P4 把**模式规则**抽到这里：

* ``modes/classic.py``  —— 经典四档（短局、生活费缩放、30 个游戏日自动终局）
* ``modes/infinite.py`` —— 无限模式（低保、高利贷、三振阶梯、隐藏结局）
* ``modes/shared.py``   —— 公共底盘（日终结算、二道贩子、收割调度、新闻接入）

而**行情推进、heat 衰减、收割执行、落库**这些"所有模式都一样"的东西留在
插件层：它们是底盘，不是某个模式的规则。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..engine.config import (
    STREAM_HARVEST,
    STREAM_MARKET,
    DifficultyConfig,
    EngineConfig,
)
from ..engine.harvest import (
    TRIGGER_PEAK,
    TRIGGER_PHASE,
    HarvestResult,
    execute_harvest,
)
from ..engine.heat import is_at_peak, position_concentration
from ..engine.market import apply_jump
from ..engine.models import MarketState, NewsItem, Player, Position, Trade
from ..engine.news import generate_news, is_reversal_day
from ..engine.pawnbroker import forced_sell_price, urgency
from ..engine.rng import derive_stream_rng
from ..engine.settlement import DailySettlement, daily_settlement, execute_sell
from ..engine.state_machine import evaluate_transition, jump_severity

__all__ = [
    "DailyOutcome",
    "ModeContext",
    "PhaseDecision",
    "TickOutcome",
    "apply_phase_jump",
    "classic_initial_days",
    "classic_living_cost_range",
    "daily_living_cost",
    "ensure_day_news",
    "execute_scheduled_harvest",
    "force_distress_sell",
    "harvest_threshold",
    "next_living_cost",
    "prey_index",
    "run_daily_settlement",
    "schedule_harvest",
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ModeContext:
    """模式层的一次调用上下文（纯内存对象，不含任何 IO）。"""

    player: Player
    market: MarketState
    difficulty: DifficultyConfig
    engine_config: EngineConfig
    catgirl: Player | None = None
    #: 当前模式名（``classic_arcade`` / ``infinite`` ...）。
    mode: str = ""
    #: 当前真实时间戳（秒）。
    now: float = 0.0

    @property
    def ticks_per_day(self) -> int:
        return max(1, int(self.difficulty.ticks_per_day))


@dataclass(frozen=True)
class PhaseDecision:
    """阶段判定 + 收割调度。

    **模式层决定，插件层执行**：模式层只回答"该不该收割、用什么阈值"，插件层
    才真正调用 ``execute_harvest``（它需要落库）。
    """

    previous_phase: str
    next_phase: str
    #: 阶段切换触发的收割（``"phase"``）；``None`` 表示阶段切换不收割。
    harvest_trigger: str | None = None
    #: 峰值收割是否在本 tick 触发。
    peak_trigger: bool = False
    #: 本档的收割集中度门槛。
    harvest_threshold: float = 0.0
    #: 阶段切换时是否做"温和跳跃"（蜜月期 → 陷阱期那一记）。
    mild_jump: bool = False


@dataclass
class TickOutcome:
    """一个 tick 内模式层算出来的**全部**改动。

    插件层拿到它之后，只负责把这些改动落库 —— 不再自己算游戏规则。
    """

    #: 本 tick 产生的成交（按顺序）。
    trades: list[Trade] = field(default_factory=list)
    #: 本 tick 的收割结果。
    liquidations: list[HarvestResult] = field(default_factory=list)
    #: 本 tick 新生成的新闻。
    news: list[NewsItem] = field(default_factory=list)
    #: 本 tick 的二道贩子流水。
    pawnbroker: list[dict[str, Any]] = field(default_factory=list)
    #: 阶段判定与收割调度结果。
    phase_decision: PhaseDecision | None = None
    #: 日终结算结果（只在日终 tick 上有值）。
    daily: DailyOutcome | None = None
    #: 本局是否已终局。
    finished: bool = False
    #: 终局原因。
    finish_reason: str | None = None
    #: 猫娘日终决策结果（仅无限模式）。
    catgirl_daily: dict[str, Any] | None = None


@dataclass
class DailyOutcome:
    """一次日终结算的结果。"""

    #: 引擎层的日终结算结果。
    settlement: DailySettlement | None = None
    #: 日终结算产生的全部成交（二道贩子强制卖出 + 猫娘日终交易）。
    trades: list[Trade] = field(default_factory=list)
    #: 日终结算产生的二道贩子流水。
    pawnbroker: list[dict[str, Any]] = field(default_factory=list)
    #: 二道贩子强制卖出产生的成交（``trades`` 的子集，便于插件层单独处理）。
    distress_trades: list[Trade] = field(default_factory=list)
    #: 二道贩子提案的腿（供撤销窗口回滚）。
    distress_legs: list[dict[str, Any]] = field(default_factory=list)
    #: 撤销窗口截止时间戳（秒）。
    distress_window_until: float = 0.0
    #: 二道贩子提案（写进 ``player.pending_action``）。
    pending_action: dict[str, Any] | None = None
    #: 本次结算后是否已死。
    dead: bool = False
    #: 死因。
    death_reason: str | None = None
    #: 连续未领低保的天数。
    welfare_missed_days: int = 0
    #: 未偿还的高利贷。
    loan_debt: float = 0.0
    #: 本次是否领取了低保。
    welfare_claimed: bool = False
    #: 低保本次补领覆盖的天数。
    welfare_days_owed: int = 0
    #: 猫娘日终决策结果（仅无限模式）。
    catgirl_daily: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# 公共底盘
# ---------------------------------------------------------------------------


def classic_living_cost_range(
    difficulty: DifficultyConfig, engine_config: EngineConfig
) -> tuple[float, float]:
    """本档的生活费区间（难度可覆盖全局基准值）。

    覆盖值写在 ``DifficultyConfig.living_cost_min`` / ``living_cost_max``；
    没写覆盖的档位用 ``ClassicConfig`` 的全局值。
    """
    classic = engine_config.classic
    low = (
        classic.living_cost_min
        if difficulty.living_cost_min is None
        else float(difficulty.living_cost_min)
    )
    high = (
        classic.living_cost_max
        if difficulty.living_cost_max is None
        else float(difficulty.living_cost_max)
    )
    return float(low), float(high)


def classic_initial_days(
    difficulty: DifficultyConfig, engine_config: EngineConfig
) -> int:
    """本档的初始天数（难度可覆盖全局基准值）。"""
    if difficulty.initial_days is not None:
        return int(difficulty.initial_days)
    return int(engine_config.classic.initial_days)


def daily_living_cost(
    mode: str, day: int, seed: int, engine_config: EngineConfig
) -> float:
    """当日生活费。

    经典四档在**本档**的 ``[living_cost_min, living_cost_max]`` 内按日随机；
    无限模式固定 ``daily_living_cost``（150）。街机档没有生活费，返回 0。
    """
    if mode == "infinite":
        return engine_config.infinite.daily_living_cost
    difficulty = engine_config.difficulties.get(mode)
    if difficulty is None or not difficulty.has_living_cost:
        return 0.0
    low, high = classic_living_cost_range(difficulty, engine_config)
    rng = derive_stream_rng(seed, STREAM_MARKET, day, 999)
    return round(rng.uniform(low, high), 2)


def next_living_cost(difficulty: DifficultyConfig, engine_config: EngineConfig) -> float:
    """次日生活费；没有生活费的档位返回 0。"""
    if not difficulty.has_living_cost:
        return 0.0
    if difficulty.mode == "infinite":
        return engine_config.infinite.daily_living_cost
    _, high = classic_living_cost_range(difficulty, engine_config)
    return high


def harvest_threshold(
    difficulty: DifficultyConfig, engine_config: EngineConfig
) -> float:
    """本档的收割集中度门槛（难度可覆盖全局值）。"""
    override = difficulty.harvest_concentration_threshold
    if override is not None:
        return float(override)
    return float(engine_config.heat.harvest_concentration_threshold)


def prey_index(player: Player, market: MarketState) -> float:
    """个人猎物指数：你的收益率领先全市场越多，越像"猎物"。

    这是"地狱"档的收割触发条件。纯函数，不含随机数。
    """
    prices = market.prices()
    if not player.positions:
        return 0.0
    invested = sum(
        position.amount * position.avg_cost for position in player.positions.values()
    )
    if invested <= 0.0:
        return 0.0
    current = sum(
        position.market_value(prices.get(stock_id, 0.0))
        for stock_id, position in player.positions.items()
    )
    player_return = (current - invested) / invested
    returns = [
        (stock.price - stock.prev_price) / stock.prev_price
        for stock in market.stocks.values()
        if stock.alive and stock.prev_price
    ]
    market_return = sum(returns) / len(returns) if returns else 0.0
    return max(0.0, (player_return - market_return) * 100.0)


def schedule_harvest(ctx: ModeContext) -> PhaseDecision:
    """阶段判定 + 收割调度（P4："什么时候收割"的唯一决策点）。

    两个触发源共用一个执行函数（插件层的 ``execute_harvest``），每 tick 最多
    一次收割：阶段触发优先于峰值触发。
    """
    market = ctx.market
    player = ctx.player
    difficulty = ctx.difficulty
    engine_config = ctx.engine_config

    previous = market.phase
    next_phase = evaluate_transition(
        previous,
        difficulty=difficulty,
        heat_config=engine_config.heat,
        player=player,
        prey_index=prey_index(player, market),
    )
    threshold = harvest_threshold(difficulty, engine_config)
    changed = next_phase != previous

    # 峰值判定**不受阶段切换影响**：同一次 tick 里既切了阶段又到了峰值拐点时，
    # 先执行阶段收割，没收割成再走峰值收割（P3 的行为，保持不变）。
    at_peak = is_at_peak(
        player.heat_history,
        threshold=engine_config.heat.peak_threshold,
        window=engine_config.heat.peak_window,
    )

    return PhaseDecision(
        previous_phase=previous,
        next_phase=next_phase,
        harvest_trigger=(
            TRIGGER_PHASE if (changed and next_phase == "slaughter") else None
        ),
        peak_trigger=bool(at_peak),
        harvest_threshold=threshold,
        mild_jump=bool(changed and next_phase != "slaughter"),
    )


def apply_phase_jump(
    ctx: ModeContext, *, phase: str, rng
) -> None:
    """阶段切换时的温和跳跃（蜜月期 → 陷阱期）。"""
    player = ctx.player
    market = ctx.market
    config = ctx.engine_config.market
    prices = market.prices()
    target_id = None
    best = 0.0
    for stock_id, position in player.positions.items():
        value = position.market_value(prices.get(stock_id, 0.0))
        if value > best:
            best = value
            target_id = stock_id
    if target_id is None:
        return
    stock = market.stocks.get(target_id)
    if stock is None:
        return
    concentration = position_concentration(player.positions, prices)
    severity = jump_severity(concentration, phase=phase)
    price_after = apply_jump(stock.price, severity=severity, rng=rng)
    stock.prev_price = stock.price
    stock.price = price_after
    stock.kline.append((market.tick, price_after))
    if len(stock.kline) > config.kline_window:
        del stock.kline[: len(stock.kline) - config.kline_window]


# ---------------------------------------------------------------------------
# 收割执行（公共底盘）
# ---------------------------------------------------------------------------


def execute_scheduled_harvest(
    ctx: ModeContext, decision: PhaseDecision | None, *, tick: int
) -> HarvestResult | None:
    """按模式层的调度**执行**收割（公共底盘）。

    模式层只回答"该不该收割、用什么阈值"；真正的执行在这里。两个触发源共用
    一个执行函数，**每 tick 最多收割一次**（阶段触发优先于峰值触发）。

    插件层与 ``tests/simulate.py`` 共用这一份实现 —— 模拟脚本绝不自己抄一遍
    收割逻辑，免得统计出来的分布与真实游戏对不上。
    """
    if decision is None:
        return None
    market = ctx.market
    player = ctx.player
    engine_config = ctx.engine_config
    harvest_rng = derive_stream_rng(market.seed, STREAM_HARVEST, tick)
    harvested = False

    if decision.next_phase != decision.previous_phase:
        if decision.harvest_trigger == TRIGGER_PHASE:
            result = execute_harvest(
                player,
                market,
                trigger=TRIGGER_PHASE,
                phase=decision.next_phase,
                threshold=decision.harvest_threshold,
                rng=harvest_rng,
                config=engine_config.market,
            )
            if result is not None:
                harvested = True
                market.phase = decision.next_phase
                return result
        elif decision.mild_jump:
            # 蜜月期 → 陷阱期：一记温和的跌，开始震荡诱多（不是收割）
            apply_phase_jump(ctx, phase=decision.next_phase, rng=harvest_rng)
        market.phase = decision.next_phase

    if not harvested and decision.peak_trigger:
        result = execute_harvest(
            player,
            market,
            trigger=TRIGGER_PEAK,
            phase=market.phase,
            threshold=decision.harvest_threshold,
            rng=harvest_rng,
            config=engine_config.market,
        )
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# 新闻接入
# ---------------------------------------------------------------------------

def ensure_day_news(ctx: ModeContext, outcome: TickOutcome) -> list[NewsItem]:
    """确保当天新闻已生成（幂等），并返回当天新闻。

    顺序：

    1. **反转日登记**：经典模式用开局预计划的集合；无限模式不预计划（局长
       无上限），每天按 15%~20% 的概率判定一次。
    2. **生成新闻**：纯函数 ``generate_news(seed, day, ...)``，同一个
       ``(seed, day)`` 必然得到同一批新闻。

    反转日会把当天所有新闻的多空方向**整体翻转**。
    """
    market = ctx.market
    day = market.day
    current = market.news_today
    if current and current[0].day == day:
        return list(current)

    if is_reversal_day(
        market.seed,
        day,
        reversal_days=market.reversal_days,
        config=ctx.engine_config.news,
    ):
        market.reversal_days.add(day)

    items = generate_news(
        market.seed,
        day,
        phase=market.phase,
        symbols=list(market.stocks.keys()),
        reversal_days=market.reversal_days,
        config=ctx.engine_config.news,
    )
    market.news_today = list(items)
    outcome.news.extend(items)
    return items


# ---------------------------------------------------------------------------
# 日终结算与二道贩子
# ---------------------------------------------------------------------------


def run_daily_settlement(
    ctx: ModeContext, outcome: TickOutcome, *, mode: str
) -> DailyOutcome:
    """日终结算（公共底盘）。

    顺序固定：

    1. **还高利贷**（仅无限模式）：到期日从现金里扣，还不上 → ``loan_default``；
    2. **扣当日生活费**；
    3. **数低保欠款**（仅无限模式）：本发放周期内没领 → +1，领过 → 归零；
    4. **判定次日生活费能不能交上** → 三振阶梯；
    5. **二道贩子强制卖出**（交不起时）：默认执行 + 撤销窗口。

    **终局日例外（P4 收尾修正）**
    ------------------------------
    经典模式最后一个游戏日（``player.day >= total_game_days``）结算完就自动
    终局，**没有"次日"可交**。这时候判的是"**今天的账单有没有全额交上**"，
    二道贩子覆盖的也是"今天没交上的那部分"，而不是明天的账单。
    """
    player = ctx.player
    market = ctx.market
    engine_config = ctx.engine_config
    day = player.day
    next_day = day + 1

    living_cost = daily_living_cost(mode, day, market.seed, engine_config)
    next_cost = next_living_cost(ctx.difficulty, engine_config)
    is_infinite = mode == "infinite"
    strike_limit = engine_config.infinite.strike_limit if is_infinite else 3
    relief = engine_config.infinite.weekly_relief if is_infinite else 0.0
    final_day = (not is_infinite) and day >= int(engine_config.classic.total_game_days)

    settlement = daily_settlement(
        player,
        mode=mode,
        living_cost=living_cost,
        next_day_living_cost=next_cost,
        strike_limit=strike_limit,
        weekly_relief=relief,
        relief_interval_days=engine_config.infinite.relief_interval_days,
        welfare_enabled=is_infinite and relief > 0,
        welfare_claim_limit_days=engine_config.infinite.welfare_claim_limit_days,
        loan_enabled=is_infinite,
        final_day=final_day,
    )

    daily = DailyOutcome(
        settlement=settlement,
        dead=settlement.dead,
        death_reason=settlement.death_reason,
        welfare_missed_days=settlement.welfare_missed_days,
        loan_debt=player.loan_debt,
    )

    if settlement.needs_distress_sell:
        if final_day:
            # 终局日：二道贩子覆盖的是"今天没交上的那部分账单"
            bill_target = max(0.0, living_cost - settlement.living_cost_paid)
            urgency_base = living_cost
        else:
            bill_target = next_cost
            urgency_base = next_cost
        force_distress_sell(
            ctx,
            outcome,
            daily,
            bill_target=bill_target,
            urgency_base=urgency_base,
            mode=mode,
        )

    daily.trades = list(outcome.trades)
    daily.pawnbroker = list(outcome.pawnbroker)

    player.day = next_day
    return daily


def _sell_order_positions(
    player: Player, prices: dict[str, float], *, smallest_loss_first: bool
) -> list[Position]:
    """二道贩子的下刀顺序。

    * 第 1 次交不起（以及经典模式）：按**浮盈从高到低** —— 先割你最赚的。
    * 第 2 次交不起（无限模式）：按**浮亏从小到大** —— 先动你最不想卖的那只
      （浮亏最小的那只），卖完仍不够再继续往下卖。
    """
    positions = list(player.positions.values())
    if not smallest_loss_first:
        return sorted(
            positions,
            key=lambda item: (prices.get(item.stock_id, 0.0) - item.avg_cost)
            * item.amount,
            reverse=True,
        )

    def key(item: Position) -> tuple[int, float]:
        price = prices.get(item.stock_id, 0.0)
        floating_loss = (item.avg_cost - price) * item.amount
        if floating_loss > 0:
            # 浮亏的先卖，且浮亏**最小**的排最前
            return (0, floating_loss)
        # 盈利的排在后面，盈利最小的排最前
        return (1, -((price - item.avg_cost) * item.amount))

    return sorted(positions, key=key)


def force_distress_sell(
    ctx: ModeContext,
    outcome: TickOutcome,
    daily: DailyOutcome,
    *,
    bill_target: float,
    mode: str,
    urgency_base: float | None = None,
) -> None:
    """二道贩子强制卖出（P3：**默认执行 + 撤销窗口**）。

    覆盖到能交得起账单为止；卖光了也覆盖不了 = 真的交不起 → 斩杀
    （``liquidated``：被二道贩子按回收价收光，仍然交不起）。

    ``bill_target`` 是"**这一刀要还清的那张账单**"：普通日是次日生活费，
    终局日是今天没交上的那部分。``urgency_base`` 是紧急度的分母（默认等于
    ``bill_target``）—— 普通日是账单总额，终局日是当天的账单总额。
    """
    player = ctx.player
    market = ctx.market
    engine_config = ctx.engine_config
    if urgency_base is None:
        urgency_base = bill_target

    shortfall = bill_target - player.cash
    if shortfall <= 1e-9:
        player.pending_action = None
        return

    urgency_value = urgency(shortfall, urgency_base)
    prices = market.prices()
    legs: list[dict[str, Any]] = []
    trades: list[Trade] = []

    # 无限模式第二次交不起时，改为"先卖浮亏最小的那只"。
    smallest_loss_first = mode == "infinite" and player.strikes >= 2
    positions = _sell_order_positions(
        player, prices, smallest_loss_first=smallest_loss_first
    )

    for position in positions:
        if shortfall <= 1e-9:
            break
        stock = market.stocks.get(position.stock_id)
        if stock is None or position.amount <= 0:
            continue
        price = prices.get(position.stock_id, 0.0)
        if price <= 0:
            continue
        recovery = forced_sell_price(
            price,
            urgency_value,
            base_discount=engine_config.pawnbroker.base_discount,
            urgent_extra=engine_config.pawnbroker.urgent_extra,
        )
        if recovery <= 0:
            continue
        # 回收价成交还要扣手续费，所以要卖得比缺口"多一点点"：
        # 净到手 = amount × recovery × (1 − 卖出费率)。
        net_ratio = 1.0 - (
            engine_config.fees.sell_rate + engine_config.fees.stamp_tax_rate
        )
        if net_ratio <= 0:
            continue
        amount = min(position.amount, shortfall / (recovery * net_ratio))
        if amount <= 0:
            continue
        prev_position = {
            "amount": position.amount,
            "avg_cost": position.avg_cost,
            "today_bought": position.today_bought,
            "today_bought_day": position.today_bought_day,
            "opened_day": position.opened_day,
        }
        trade = execute_sell(
            player,
            stock,
            amount,
            config=engine_config.fees,
            day=player.day,
            tick=market.tick,
            trade_id=0,
            reason="pawnbroker_forced",
            price=recovery,
            t_plus_one=False,
        )
        market.fee_pool += trade.fee
        market.total_fees += trade.fee
        outcome.trades.append(trade)
        trades.append(trade)
        outcome.pawnbroker.append(
            {
                "player_id": player.id,
                "tick": market.tick,
                "day": player.day,
                "action": "forced_sell",
                "stock_id": position.stock_id,
                "price": recovery,
                "reason": "living_cost_shortfall",
            }
        )
        legs.append(
            {
                "stock_id": position.stock_id,
                "amount": trade.amount,
                "price": trade.price,
                "fee": trade.fee,
                "notional": trade.price * trade.amount,
                "trade_id": 0,
                "prev_position": prev_position,
            }
        )
        shortfall -= trade.amount * trade.price

    daily.distress_trades = trades
    daily.distress_legs = legs

    if player.cash >= bill_target - 1e-9:
        # 交上了：开一个撤销窗口，玩家可以在下一个 tick 之前反悔
        window = float(time.time() + max(1, ctx.difficulty.tick_interval_seconds))
        pending = {
            "kind": "pawnbroker_sell",
            "legs": legs,
            "window_until": window,
        }
        player.pending_action = pending
        daily.pending_action = pending
        daily.distress_window_until = window
    else:
        # 卖光了也交不起 → 真的交不起
        from ..engine.settlement import apply_death

        apply_death(player, "liquidated")
        player.pending_action = None
        daily.pending_action = None
        daily.dead = True
        daily.death_reason = "liquidated"
