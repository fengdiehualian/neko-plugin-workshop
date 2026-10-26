"""经典模式（四档：街机 / 入门 / 进阶 / 地狱）。

规则要点
--------
* 每局 **30 个游戏日**（``ClassicConfig.total_game_days``），每天 12 tick。
* 第 30 个游戏日结束时**自动终局并自动结算**；之后玩家再调 ``settle`` 会拿到
  ``no_active_game``，不重复结算。
* **没有低保、没有高利贷** —— 那是无限模式的东西。
* 生活费按短局缩放：每天 15~20，初始 7 天总额 ≈ 105~140。
* 街机档**没有生活费**，只有现钱概念。
* 反转日：开局由种子预计划 1~3 天；非计划日另有 15%~20% 的概率成为反转日。

本模块**只改内存里的对象**，不碰数据库、不做 IO。
"""

from __future__ import annotations

from ..engine.news import plan_reversal_days
from .shared import (
    DailyOutcome,
    ModeContext,
    TickOutcome,
    ensure_day_news,
    run_daily_settlement,
    schedule_harvest,
)

__all__ = ["ClassicMode"]


class ClassicMode:
    """经典模式（四档共用一套流程，档位差异全在 ``DifficultyConfig`` 里）。"""

    #: 模式族（``classic`` / ``infinite``）。具体的档位名在 ``ctx.mode`` 里。
    mode_family = "classic"

    def setup(self, ctx: ModeContext) -> TickOutcome:
        """开局：预计划反转日 + 生成第 1 天的新闻。"""
        outcome = TickOutcome()
        market = ctx.market
        engine_config = ctx.engine_config
        news_config = engine_config.news
        market.reversal_days = set(
            plan_reversal_days(
                market.seed,
                days=engine_config.classic.total_game_days,
                min_days=news_config.reversal_days_min,
                max_days=news_config.reversal_days_max,
            )
        )
        ensure_day_news(ctx, outcome)
        return outcome

    def tick(self, ctx: ModeContext) -> TickOutcome:
        """一个 tick 的模式逻辑：当天新闻 + 阶段判定与收割调度。"""
        outcome = TickOutcome()
        ensure_day_news(ctx, outcome)
        outcome.phase_decision = schedule_harvest(ctx)
        return outcome

    def daily_settlement(self, ctx: ModeContext) -> DailyOutcome:
        """日终结算：扣生活费 → 三振 → 二道贩子。"""
        outcome = TickOutcome()
        daily = run_daily_settlement(ctx, outcome, mode=ctx.mode)
        outcome.daily = daily
        return daily

    def is_finished(self, ctx: ModeContext) -> bool:
        """第 30 个游戏日结束后自动终局。"""
        total_days = ctx.engine_config.classic.total_game_days
        return ctx.player.day > total_days
