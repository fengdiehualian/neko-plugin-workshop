"""P4 收尾：**终局日差一天**的修正。

背景
----
经典模式第 30 个游戏日结算完就自动终局，**根本不存在"第 31 天"**。但修正
之前，每次日终结算都会判定"能不能交得起**次日**生活费"—— 于是第 30 天
结算时，玩家会被要求为不存在的第 31 天预支生活费。

实测后果：挂机口径下，初始资金刚好等于 30 天生活费的玩家**破产在第 30 天**，
四档经典模式生存率全线 0%。

修正
----
``daily_settlement(..., final_day=True)``：终局日改判"**今天的账单有没有
全额交上**"（``paid >= living_cost``），而不是"能不能预支明天的账单"。
二道贩子在终局日覆盖的也是"今天没交上的那部分"，而不是明天的账单。

守护线
------
1. **只有终局日变**：普通日的"次日预支"判定必须原样保留（无限模式永远
   不算终局日）。
2. **终局日没交上今天的账单 → 照样交不起**：不能因为没有"明天"就免检。
"""

from __future__ import annotations

from engine.config import default_engine_config
from engine.models import MarketState, Player
from engine.settlement import daily_settlement
from plugins.mock_stock_meow.modes.shared import ModeContext, TickOutcome


def _ctx(tier: str, *, day: int, cash: float) -> ModeContext:
    config = default_engine_config()
    return ModeContext(
        player=Player(id="p", name="玩家", cash=cash, mode=tier, day=day),
        market=MarketState(seed=1),
        difficulty=config.difficulties[tier],
        engine_config=config,
        mode=tier,
    )


# ---------------------------------------------------------------- 引擎层


def test_final_day_judges_todays_bill_not_tomorrows() -> None:
    """终局日：现金刚好够今天的账单 → 不交不起，不三振。"""
    player = Player(id="p1", name="玩家", cash=10.0, mode="classic_beginner")
    result = daily_settlement(
        player,
        mode="classic_beginner",
        living_cost=10.0,
        next_day_living_cost=10.0,
        strike_limit=3,
        final_day=True,
    )
    assert result.living_cost_paid == 10.0
    assert result.can_pay_next_day is True
    assert result.strikes == 0
    assert result.dead is False
    assert player.alive is True


def test_normal_day_still_requires_tomorrows_bill() -> None:
    """普通日不变：现金交完今天就不剩了 → 交不起明天 → 三振阶梯照走。"""
    player = Player(id="p1", name="玩家", cash=10.0, mode="classic_beginner")
    result = daily_settlement(
        player,
        mode="classic_beginner",
        living_cost=10.0,
        next_day_living_cost=10.0,
        strike_limit=3,
        final_day=False,
    )
    assert result.can_pay_next_day is False
    assert result.strike_level == 1
    assert result.needs_distress_sell is True
    assert result.dead is False


def test_final_day_still_catches_an_unpaid_bill() -> None:
    """终局日不是免检：今天的账单没全额交上 → 照样算交不起。"""
    player = Player(id="p1", name="玩家", cash=4.0, mode="classic_beginner")
    result = daily_settlement(
        player,
        mode="classic_beginner",
        living_cost=10.0,
        next_day_living_cost=10.0,
        strike_limit=3,
        final_day=True,
    )
    assert result.living_cost_paid == 4.0
    assert result.can_pay_next_day is False
    assert result.strike_level == 1
    assert result.needs_distress_sell is True


def test_final_day_without_living_cost_never_fails() -> None:
    """没有生活费的档位（街机）在终局日也交不起不了。"""
    player = Player(id="p1", name="玩家", cash=0.0, mode="classic_arcade")
    result = daily_settlement(
        player,
        mode="classic_arcade",
        living_cost=0.0,
        next_day_living_cost=0.0,
        strike_limit=3,
        final_day=True,
    )
    assert result.can_pay_next_day is True
    assert result.dead is False


# ---------------------------------------------------------------- 模式层


def test_classic_final_day_survives_with_exactly_one_day_of_bill() -> None:
    """回归：经典模式第 30 天，现金刚好够当天账单 → 活到终局。

    修正之前，这一局会在这里被"预支第 31 天"判定为交不起，然后被二道贩子
    收光（``liquidated``）。
    """
    from plugins.mock_stock_meow.modes.shared import run_daily_settlement

    ctx = _ctx("classic_beginner", day=30, cash=10.0)
    outcome = TickOutcome()
    daily = run_daily_settlement(ctx, outcome, mode="classic_beginner")

    assert daily.dead is False
    assert daily.death_reason is None
    assert ctx.player.alive is True
    assert ctx.player.strikes == 0
    # 账单照扣
    assert ctx.player.cash == 0.0


def test_classic_day_before_final_keeps_the_next_day_check() -> None:
    """第 29 天不是终局日：依然要预支第 30 天的账单。"""
    from plugins.mock_stock_meow.modes.shared import run_daily_settlement

    ctx = _ctx("classic_beginner", day=29, cash=10.0)
    outcome = TickOutcome()
    daily = run_daily_settlement(ctx, outcome, mode="classic_beginner")

    assert daily.settlement.strike_level == 1
    assert ctx.player.strikes == 1


def test_infinite_mode_is_never_a_final_day() -> None:
    """无限模式没有终点 → 永远走"预支次日"的判定。"""
    from plugins.mock_stock_meow.modes.shared import run_daily_settlement

    ctx = _ctx("infinite", day=9999, cash=150.0)
    outcome = TickOutcome()
    daily = run_daily_settlement(ctx, outcome, mode="infinite")

    # 现金正好交完当天 → 交不起明天 → 三振第 1 次
    assert daily.settlement.strike_level == 1
    assert ctx.player.strikes == 1
