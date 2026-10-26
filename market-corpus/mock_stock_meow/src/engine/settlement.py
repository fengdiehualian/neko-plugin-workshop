"""成交、结算与手续费。

教育目的说明
------------
这一层计算"每次交易让你损失多少"。手续费是纯虚构的游戏数值，**不接入任何
真实支付渠道**：本引擎中不存在充值、提现、兑换、入金、出金一类功能，连变
量名与占位符都没有。手续费池只累加、只显示与销毁，绝不分配给任何人。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import FeeConfig
from .errors import InsufficientCashError, InvalidAmountError, TPlusOneError
from .models import MarketState, Player, Position, SettlementReport, Stock, Trade

__all__ = [
    "DailySettlement",
    "WelfareClaim",
    "accrue_fee",
    "apply_death",
    "build_settlement_report",
    "buy_fee",
    "check_inaction",
    "claim_welfare",
    "daily_settlement",
    "execute_buy",
    "execute_sell",
    "sell_fee",
    "welfare_cycle",
    "welfare_entitlement",
]


def buy_fee(notional: float, config: FeeConfig) -> float:
    """买入佣金。"""
    return notional * config.buy_rate


def sell_fee(notional: float, config: FeeConfig) -> float:
    """卖出佣金 + 印花税（印花税单边收取，只在卖出方向）。"""
    return notional * (config.sell_rate + config.stamp_tax_rate)


def accrue_fee(market: MarketState, fee: float) -> float:
    """把手续费累加进全局手续费池。

    手续费池**只增不减、只显示与销毁**，不存在任何再分配路径。
    """
    market.fee_pool += fee
    market.total_fees += fee
    return market.fee_pool


def execute_buy(
    player: Player,
    stock: Stock,
    amount: float,
    *,
    config: FeeConfig,
    day: int,
    tick: int,
    trade_id: int,
    reason: str = "manual",
) -> Trade:
    """买入成交：扣现钱、加持仓（均价含买入手续费）、返回成交记录。"""
    if amount <= 0:
        raise InvalidAmountError(f"invalid buy amount: {amount}")
    price = stock.price
    notional = price * amount
    fee = buy_fee(notional, config)
    total_cost = notional + fee
    if total_cost > player.cash + 1e-9:
        raise InsufficientCashError(
            f"need {total_cost:.4f} cash, have {player.cash:.4f}"
        )

    player.cash -= total_cost
    position = player.positions.get(stock.id)
    if position is None:
        position = Position(
            stock_id=stock.id,
            amount=0.0,
            avg_cost=0.0,
            opened_day=day,
        )
        player.positions[stock.id] = position
    new_amount = position.amount + amount
    position.avg_cost = (position.avg_cost * position.amount + total_cost) / new_amount
    position.amount = new_amount
    position.today_bought += amount
    position.today_bought_day = day

    player.trade_count += 1
    player.total_fee_paid += fee
    return Trade(
        id=trade_id,
        player_id=player.id,
        stock_id=stock.id,
        side="buy",
        amount=amount,
        price=price,
        fee=fee,
        tick=tick,
        day=day,
        reason=reason,
    )


def execute_sell(
    player: Player,
    stock: Stock,
    amount: float,
    *,
    config: FeeConfig,
    day: int,
    tick: int,
    trade_id: int,
    reason: str = "manual",
    price: float | None = None,
    t_plus_one: bool = True,
) -> Trade:
    """卖出成交：加现钱、减持仓、返回成交记录。

    ``price`` 缺省为市价；二道贩子强制卖出时传入回收价，手续费也按回收价为
    基数计算（这是二道贩子的"额外抽成"）。
    """
    if amount <= 0:
        raise InvalidAmountError(f"invalid sell amount: {amount}")
    position = player.positions.get(stock.id)
    if position is None:
        raise InvalidAmountError(f"no position on {stock.id}")

    if t_plus_one:
        sellable = position.sellable_amount(day)
        if amount > sellable + 1e-9:
            raise TPlusOneError(
                f"{stock.id} sellable {sellable:.4f}, tried to sell {amount:.4f}"
            )
    elif amount > position.amount + 1e-9:
        raise InvalidAmountError(
            f"{stock.id} holds {position.amount:.4f}, tried to sell {amount:.4f}"
        )

    fill_price = stock.price if price is None else price
    notional = fill_price * amount
    fee = sell_fee(notional, config)
    player.cash += notional - fee

    position.amount -= amount
    if position.amount <= 1e-9:
        player.positions.pop(stock.id, None)

    player.trade_count += 1
    player.total_fee_paid += fee
    return Trade(
        id=trade_id,
        player_id=player.id,
        stock_id=stock.id,
        side="sell",
        amount=amount,
        price=fill_price,
        fee=fee,
        tick=tick,
        day=day,
        reason=reason,
    )


def check_inaction(
    player: Player, *, max_trades: int = 0, exempt_trades: int = 0
) -> bool:
    """隐藏结局"无为而治"判定：几乎没交易且存活到结局。

    ``exempt_trades`` 豁免"开局强制发放的基准股票被卖出"这一笔：无限模式开局
    现金为 0，玩家**必须**先卖掉基准股票才交得上第一天生活费，那不算他主动
    交易。除此之外的每一笔买卖都算数。
    """
    effective = player.trade_count - max(0, exempt_trades)
    return effective <= max_trades


def build_settlement_report(
    player: Player,
    *,
    market: MarketState,
    mode: str,
    days_survived: int,
    ticks_played: int,
    ticks_per_day: int = 12,
    achievements: list[str] | None = None,
    ending: str | None = None,
    catgirl_result: dict | None = None,
    news_log: list | None = None,
) -> SettlementReport:
    """组装结算报告。

    这是 heat 曲线与种子**唯一**允许出现的地方。固定的反诈文案不由引擎
    产出，由第一层（插件层）拼装，以保证它无法被配置关闭。
    """
    prices = market.prices()
    curve = list(player.heat_history)
    peak_heat = max(curve) if curve else 0.0
    peak_day: int | None = None
    if curve:
        peak_index = curve.index(peak_heat)
        peak_day = peak_index // max(1, ticks_per_day) + 1
    return SettlementReport(
        player_id=player.id,
        mode=mode,
        seed=market.seed,
        survivor=player.alive,
        days_survived=days_survived,
        ticks_played=ticks_played,
        trade_count=player.trade_count,
        initial_cash=player.initial_cash,
        final_cash=player.cash,
        final_asset=player.total_asset(prices),
        total_fee_paid=player.total_fee_paid,
        fee_pool_total=market.fee_pool,
        heat_curve=curve,
        peak_day=peak_day,
        peak_heat=peak_heat,
        death_reason=player.death_reason,
        death_title=player.death_title,
        achievements=list(achievements or []),
        ending=ending,
        catgirl_result=catgirl_result,
        news_log=list(news_log or []),
    )


# ---------------------------------------------------------------------------
# 日终结算与斩杀
# ---------------------------------------------------------------------------

#: 死因 → 反诈文案。固定文案由引擎产出，保证玩家一定能看到"为什么"。
DEATH_TITLES: dict[str, str] = {
    "starved": (
        "交不起第三天的账单。你以为你在投资，其实在给别人的现金流打工。"
    ),
    "liquidated": (
        "二道贩子按回收价收走了你的筹码。你越急，价越低——差价就是他们赚的钱。"
    ),
    "harvested": (
        "你在最上头的那一刻被精准收割。集中度越高的人，死得越惨。"
    ),
    "welfare_neglect": (
        "连续七天没去领低保。制度不养闲人 —— 但这条命是你自己的，不是低保的。"
    ),
    "loan_default": (
        "高利贷到期还不上。借五百还六百，差价就是他们全部的利润。"
    ),
}


@dataclass(frozen=True)
class DailySettlement:
    """一次日终结算的结果（纯数据）。"""

    day: int
    living_cost_paid: float
    cash_after: float
    can_pay_next_day: bool
    needs_distress_sell: bool
    #: 连续交不起的次数（P4 语义：从 1 开始，每交不起一次 +1）
    strikes: int
    #: 本次是第几次交不起（1 = 第一次，2 = 第二次，3 = 第三次＝斩杀）
    strike_level: int
    dead: bool
    death_reason: str | None
    #: 连续未领取低保的天数
    welfare_missed_days: int
    #: 本次结算偿还的高利贷本金
    loan_repaid: float
    #: 是否高利贷违约
    loan_default: bool


@dataclass(frozen=True)
class WelfareClaim:
    """一次低保领取的结果（纯数据）。"""

    granted: float
    #: 本次补领覆盖的天数
    days_owed: int
    #: 是否"本发放周期内已经领过了"
    already_claimed: bool


def welfare_cycle(day: int, interval_days: int) -> int:
    """第 ``day`` 天属于第几个低保发放周期（从 1 开始）。

    周期 N 覆盖第 ``[(N-1)*interval+1, N*interval]`` 天，每个周期一份低保。
    """
    if interval_days <= 0:
        return 1
    return max(1, (day - 1) // interval_days + 1)


def welfare_entitlement(
    player: Player, *, day: int, interval_days: int, claim_limit_days: int
) -> int:
    """当前**可领取**的低保周期数（受补领上限约束）。

    * 同一周期内重复领取 → 0（幂等）。
    * 跨过多个周期 → 只补 ``claim_limit_days`` 天对应的周期数，更早的作废。
    """
    last_cycle = (
        welfare_cycle(player.welfare_claimed_day, interval_days)
        if player.welfare_claimed_day
        else 0
    )
    current_cycle = welfare_cycle(day, interval_days)
    if current_cycle <= last_cycle:
        return 0
    max_cycles = max(1, claim_limit_days // max(1, interval_days))
    return min(current_cycle - last_cycle, max_cycles)


def claim_welfare(
    player: Player,
    *,
    day: int,
    weekly_relief: float,
    interval_days: int = 7,
    claim_limit_days: int = 7,
) -> WelfareClaim:
    """主动领取低保（纯函数：改 ``player``，返回到账金额）。

    低保**必须主动领**：不调用这个函数就一分钱没有。允许补领，但补领上限是
    ``claim_limit_days`` 天，更早的欠款作废。领取成功后 ``welfare_missed_days``
    归零、``welfare_claimed_day`` 记为当天。
    """
    cycles = welfare_entitlement(
        player,
        day=day,
        interval_days=interval_days,
        claim_limit_days=claim_limit_days,
    )
    if cycles <= 0:
        return WelfareClaim(granted=0.0, days_owed=0, already_claimed=True)

    granted = weekly_relief * cycles
    player.cash += granted
    player.welfare_claimed_day = day
    player.welfare_missed_days = 0
    return WelfareClaim(
        granted=granted,
        days_owed=cycles * max(1, interval_days),
        already_claimed=False,
    )


def apply_death(player: Player, reason: str, title: str | None = None) -> None:
    """标记玩家死亡（``alive=False`` + 死因 + 死因标题）。"""
    player.alive = False
    player.death_reason = reason
    player.death_title = title or DEATH_TITLES.get(reason, "")


def daily_settlement(
    player: Player,
    *,
    mode: str,
    living_cost: float,
    next_day_living_cost: float,
    strike_limit: int = 3,
    weekly_relief: float = 0.0,
    relief_interval_days: int = 7,
    welfare_enabled: bool = False,
    welfare_claim_limit_days: int = 7,
    loan_enabled: bool = False,
    final_day: bool = False,
) -> DailySettlement:
    """日终结算（纯函数：只改 ``player`` 的现金、三振计数与低保计数）。

    顺序固定（P4）：

    1. **还高利贷**（仅无限模式）：到期日（``player.day > loan_taken_day``）
       从现金里扣；还不上 → ``loan_default``。
    2. **扣当日生活费**（没有生活费的档位跳过）。
    3. **数低保欠款**（仅无限模式）：本发放周期内没领过 → 欠款 +1；领过 → 归零。
    4. **判定能不能交得起次日生活费** → 三振阶梯。

    交不起时的分叉（P4 起 ``strikes`` 数的是"连续交不起的次数"）：

    * ``strikes`` 加一后 ``>= strike_limit`` → **斩杀**（三振用尽），``starved``；
    * 否则 → ``needs_distress_sell=True``，由上层执行二道贩子强制卖出。

    死因优先级：**loan_default > welfare_neglect > starved**。

    **终局日（``final_day=True``）例外**
    --------------------------------
    经典模式第 30 个游戏日结算完就自动终局，**根本不存在"第 31 天"**。
    这时候判定的是"**今天的账单有没有全额交上**"（``paid >= living_cost``），
    而不是"能不能预支明天的账单" —— 否则会出现"现金刚好够 30 天生活费，
    却死在第 30 天"的荒谬局面（P4 收尾实测到的缺陷）。

    **二道贩子那一刀由上层执行**（它需要行情价与成交函数），本函数只负责
    判定与结算。
    """
    cash = player.cash
    day = player.day

    # ① 高利贷还款（到期才扣，借 500 还 600 的 100 差价是对方的全部利润）
    loan_repaid = 0.0
    loan_default = False
    if (
        loan_enabled
        and player.loan_debt > 0
        and player.loan_taken_day
        and day > player.loan_taken_day
    ):
        due = player.loan_debt
        if cash >= due:
            cash -= due
            loan_repaid = due
            player.loan_debt = 0.0
        else:
            loan_default = True

    # ② 扣当日生活费
    paid = 0.0
    if living_cost > 0:
        paid = min(cash, living_cost)
        cash -= paid

    # ③ 低保欠款计数（本周期没领 → +1；领过 → 归零）
    welfare_missed = player.welfare_missed_days
    if welfare_enabled and weekly_relief > 0:
        cycle = welfare_cycle(day, relief_interval_days)
        last_cycle = (
            welfare_cycle(player.welfare_claimed_day, relief_interval_days)
            if player.welfare_claimed_day
            else 0
        )
        if last_cycle < cycle:
            welfare_missed += 1
        else:
            welfare_missed = 0
    player.welfare_missed_days = welfare_missed

    # ④ 次日生活费 → 三振阶梯
    #
    # **终局日例外**：第 30 天结算完就自动终局，没有"第 31 天"可交。这时要
    # 判定的是"今天的账单有没有全额交上"，而不是"能不能预支明天的账单"。
    if final_day:
        can_pay = living_cost <= 0 or paid >= living_cost - 1e-9
    else:
        can_pay = next_day_living_cost <= 0 or cash >= next_day_living_cost
    strikes = player.strikes
    strike_level = 0
    dead = False
    reason: str | None = None
    needs_distress = False

    if not can_pay:
        strikes += 1
        strike_level = strikes
        if strikes >= strike_limit:
            dead = True
            reason = "starved"
        else:
            needs_distress = True
    player.strikes = strikes

    # ⑤ 低保斩杀线：连续未领**超过**上限 → 无为而治的底线被击穿
    if welfare_enabled and welfare_missed > welfare_claim_limit_days:
        dead = True
        reason = "welfare_neglect"

    # ⑥ 死因优先级
    if loan_default:
        dead = True
        reason = "loan_default"

    player.cash = cash
    if dead:
        apply_death(player, reason or "starved")

    return DailySettlement(
        day=day,
        living_cost_paid=paid,
        cash_after=cash,
        can_pay_next_day=can_pay,
        needs_distress_sell=needs_distress,
        strikes=strikes,
        strike_level=strike_level,
        dead=dead,
        death_reason=reason,
        welfare_missed_days=welfare_missed,
        loan_repaid=loan_repaid,
        loan_default=loan_default,
    )
