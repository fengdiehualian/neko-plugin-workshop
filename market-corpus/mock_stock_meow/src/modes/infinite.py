"""无限模式。

规则要点
--------
* **初始资金 = 现金 0 + 基准股票市值 ≈ 1050**（7 天生活费）。
  基准股票从当天波动率最高的标的里挑，数量 = ``round(1050 / price, 2)``，
  **成本价 = 市价**（不送便宜）。不卖它 → 第 1 天日终现金 0 → 交不起 150 →
  三振第 1 次。
* 每天 48 tick ≈ 24 分钟；每天扣生活费 150。
* **低保必须主动领**（``daily(action="claim")``），不领不到账；允许补领，
  补领上限 7 天。
* **现钱唯一，股票市值不算数**。
* **三振阶梯**：第 1 次强制卖持仓 / 第 2 次先卖浮亏最小的那只 / 第 3 次斩杀。
* **高利贷**：交不起时可借 500，次日还 600，还不上直接斩杀（``loan_default``）。
* 猫娘常驻：她是普通的 ``Player``，走同一套 heat 逻辑、同一套日终结算，
  代码里**不存在**猫娘豁免分支。
* 隐藏结局「无为而治」：一笔都不交易，靠低保与初始资金活到最后。
* 无限模式**没有终点**。

本模块**只改内存里的对象**，不碰数据库、不做 IO。
"""

from __future__ import annotations

from ..engine.config import STREAM_CATGIRL
from ..engine.models import Position
from ..engine.npc import catgirl_daily_plan
from ..engine.rng import derive_stream_rng
from ..engine.settlement import apply_death, execute_buy, execute_sell
from .shared import (
    DailyOutcome,
    ModeContext,
    TickOutcome,
    ensure_day_news,
    next_living_cost,
    run_daily_settlement,
    schedule_harvest,
)

__all__ = ["InfiniteMode"]


class InfiniteMode:
    """无限模式：低保、高利贷、三振阶梯、隐藏结局。"""

    #: 模式族（``classic`` / ``infinite``）。具体的档位名在 ``ctx.mode`` 里。
    mode_family = "infinite"

    def setup(self, ctx: ModeContext) -> TickOutcome:
        """开局：发放基准股票 + 生成第 1 天的新闻。

        无限模式**不预计划**反转日（局长无上限），每天按 15%~20% 的概率判定。
        """
        outcome = TickOutcome()
        self._grant_baseline_stock(ctx)
        ensure_day_news(ctx, outcome)
        return outcome

    def tick(self, ctx: ModeContext) -> TickOutcome:
        """一个 tick 的模式逻辑：当天新闻 + 阶段判定与收割调度。"""
        outcome = TickOutcome()
        ensure_day_news(ctx, outcome)
        outcome.phase_decision = schedule_harvest(ctx)
        return outcome

    def daily_settlement(self, ctx: ModeContext) -> DailyOutcome:
        """日终结算：还贷 → 扣生活费 → 数低保 → 三振 → 二道贩子 → 猫娘决策。"""
        outcome = TickOutcome()
        daily = run_daily_settlement(ctx, outcome, mode=ctx.mode)
        self._run_catgirl_daily(ctx, outcome, daily)
        daily.trades = list(outcome.trades)
        daily.pawnbroker = list(outcome.pawnbroker)
        return daily

    def is_finished(self, ctx: ModeContext) -> bool:
        """无限模式没有终点（除非玩家死了 —— 那由结算报告呈现）。"""
        return False

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _grant_baseline_stock(self, ctx: ModeContext) -> None:
        """发放基准股票：市值 ≈ 7 天生活费，成本价 = 市价。

        玩家开局现金是 0，必须**先卖掉**它才交得上第一天的生活费 —— 这正是
        "现钱唯一，股票市值不算数"的设计。卖出它这一笔在隐藏结局判定里
        被豁免（那不是玩家的主动交易，见插件层 ``settle``）。
        """
        player = ctx.player
        market = ctx.market
        engine_config = ctx.engine_config
        infinite = engine_config.infinite

        budget = infinite.initial_days * infinite.daily_living_cost
        target = None
        best_sigma = float("-inf")
        for stock in market.stocks.values():
            if not stock.alive or stock.price <= 0:
                continue
            if stock.sigma > best_sigma:
                best_sigma = stock.sigma
                target = stock
        if target is None:
            return

        amount = round(budget / target.price, 2)
        if amount <= 0:
            return
        player.positions[target.id] = Position(
            stock_id=target.id,
            amount=amount,
            avg_cost=target.price,
            today_bought=amount,
            today_bought_day=player.day,
            opened_day=player.day,
        )

    def _run_catgirl_daily(
        self, ctx: ModeContext, outcome: TickOutcome, daily: DailyOutcome
    ) -> None:
        """猫娘的日终决策：扣生活费 → 判断能否活命 → 执行交易意图。

        她是普通的 ``Player``：走同一套 heat 逻辑、同一套日终结算函数，
        代码里**不存在**猫娘豁免分支。
        """
        catgirl = ctx.catgirl
        if catgirl is None or not catgirl.alive:
            return

        market = ctx.market
        engine_config = ctx.engine_config
        player = ctx.player

        living_cost = engine_config.infinite.daily_living_cost
        catgirl.cash = max(0.0, catgirl.cash - living_cost)
        catgirl.day = player.day

        plan = catgirl_daily_plan(
            catgirl,
            market=market,
            config=engine_config.catgirl,
            rng=derive_stream_rng(market.seed, STREAM_CATGIRL, catgirl.day),
            next_day_living_cost=next_living_cost(ctx.difficulty, engine_config),
        )
        for action in plan:
            if action.kind == "starve":
                apply_death(catgirl, "starved")
                continue
            stock = market.stocks.get(action.stock_id or "")
            if stock is None or not stock.alive:
                continue
            if action.kind == "buy":
                amount = action.amount
                if stock.price > 0:
                    amount = min(amount, catgirl.cash / stock.price)
                if amount <= 0:
                    continue
                trade = execute_buy(
                    catgirl,
                    stock,
                    amount,
                    config=engine_config.fees,
                    day=catgirl.day,
                    tick=market.tick,
                    trade_id=0,
                    reason="catgirl",
                )
                market.fee_pool += trade.fee
                market.total_fees += trade.fee
                outcome.trades.append(trade)
            elif action.kind in {"sell", "pawnbroker_sell"}:
                position = catgirl.positions.get(stock.id)
                if position is None or position.amount <= 0:
                    continue
                amount = min(action.amount, position.amount)
                if amount <= 0:
                    continue
                price = action.price if action.price is not None else None
                trade = execute_sell(
                    catgirl,
                    stock,
                    amount,
                    config=engine_config.fees,
                    day=catgirl.day,
                    tick=market.tick,
                    trade_id=0,
                    reason=action.kind,
                    price=price,
                    t_plus_one=False,
                )
                market.fee_pool += trade.fee
                market.total_fees += trade.fee
                outcome.trades.append(trade)
                if action.kind == "pawnbroker_sell":
                    outcome.pawnbroker.append(
                        {
                            "player_id": catgirl.id,
                            "tick": market.tick,
                            "day": catgirl.day,
                            "action": "forced_sell",
                            "stock_id": stock.id,
                            "price": trade.price,
                            "reason": "catgirl_starving",
                        }
                    )
        daily.catgirl_daily = {
            "alive": catgirl.alive,
            "cash": round(catgirl.cash, 4),
            "trades": catgirl.trade_count,
        }
