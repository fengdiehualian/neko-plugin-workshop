"""P4 · 50 局模拟（**不是** pytest，用 ``python tests/simulate.py`` 跑）。

两个口径
--------
* **挂机口径（必须做）**：纯挂机 —— 不交易，只按规则领低保。测的是
  "**能不能活**"，不是"会不会玩"。
* **模拟玩家口径（补充做）**：一个简单策略 —— 每天随机买入 1~2 只、
  持有 2~3 天卖出、看到看涨新闻就追、浮亏时 50% 概率补仓。测的是
  "**真实收割触发分布**"：玩家会不会太早上头。

局限声明（必须一起看）
----------------------
挂机口径**测不出**"会不会太早上头"—— 挂机玩家根本不交易，永远不会被收割。
所以阈值判断（``peak_threshold`` 要不要从 40 提到 45~50）只能看**补充
模拟**的口径。

用法::

    python tests/simulate.py                 # 50 局 × 2 口径
    python tests/simulate.py --games 10      # 快速跑
    python tests/simulate.py --strategy trader
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

# 让脚本能 import 插件包（与宿主加载方式一致：plugins 是命名空间包）。
# ⚠ 这一段必须写成**表达式语句**（不能是赋值、也不能是 for/if）：ruff 的 E402
#   （import 不在文件顶部）是按「非 import 语句」划分 import 边界的，
#   裸表达式调用不算边界 —— 这样下面这些 import 才仍然算「在文件顶部」。
#   CI 的 ruff 带 ``--ignore-noqa``，任何 noqa 注释都救不了 E402，只能靠写法本身过关。
#   插入顺序与老写法一致：``plugins/`` 的父目录在前，插件自身目录在后。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 插件自身目录
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parents[1]))  # plugins/ 的父目录

from engine.config import default_engine_config
from engine.heat import position_concentration, update_heat
from engine.market import advance_market, create_market
from engine.models import MarketState, Player
from engine.rng import derive_rng
from engine.settlement import claim_welfare, execute_buy, execute_sell
from plugins.mock_stock_meow.modes import get_mode
from plugins.mock_stock_meow.modes.shared import (
    ModeContext,
    classic_initial_days,
    classic_living_cost_range,
    daily_living_cost,
    execute_scheduled_harvest,
)

__all__ = ["simulate_game", "run_batch", "report"]

#: 模拟的天数上限（超过就按"还活着"收尾）。
MAX_DAYS = 60


# ---------------------------------------------------------------------------
# 一局模拟
# ---------------------------------------------------------------------------


def _initial_cash(mode_name: str, seed: int, config) -> float:
    """与插件层一致的初始现金（按**本档**的生活费 × 初始天数）。"""
    if mode_name == "infinite":
        return 0.0
    difficulty = config.difficulties[mode_name]
    low, high = classic_living_cost_range(difficulty, config)
    days = classic_initial_days(difficulty, config)
    base = derive_rng(seed, 999).uniform(low, high)
    return round(base * days, 2)


def simulate_game(
    seed: int,
    mode_name: str,
    *,
    strategy: str = "hangman",
    max_days: int = MAX_DAYS,
    config=None,
) -> dict:
    """跑完一局，返回统计项。

    走的是**引擎层 + 模式层**（不碰数据库），与真实游戏同一套规则：
    收割执行复用 ``execute_scheduled_harvest``，日终结算复用模式层。
    """
    if config is None:
        config = default_engine_config()
    difficulty = config.difficulties[mode_name]
    mode = get_mode(mode_name)
    ticks_per_day = max(1, int(difficulty.ticks_per_day))
    max_ticks = ticks_per_day * max_days

    market = MarketState(seed=seed, tick=0, day=1, phase="honeymoon")
    market.stocks = create_market(
        seed,
        count=difficulty.initial_symbols,
        config=config.market,
        difficulty=difficulty,
    )

    initial_cash = _initial_cash(mode_name, seed, config)
    player = Player(
        id="p_sim",
        name="玩家",
        cash=initial_cash,
        mode=mode_name,
        day=1,
        alive=True,
        initial_cash=initial_cash,
    )
    catgirl = None
    if mode_name == "infinite":
        infinite = config.infinite
        catgirl = Player(
            id="c_sim",
            name="猫娘",
            cash=infinite.initial_days * infinite.daily_living_cost,
            mode=mode_name,
            day=1,
            alive=True,
            initial_cash=infinite.initial_days * infinite.daily_living_cost,
        )

    ctx = ModeContext(
        player=player,
        market=market,
        difficulty=difficulty,
        engine_config=config,
        catgirl=catgirl,
        mode=mode_name,
    )
    mode.setup(ctx)
    baseline_stock_id = next(iter(player.positions), None)

    stats = {
        "mode": mode_name,
        "strategy": strategy,
        "seed": seed,
        "first_harvest_tick": None,
        "harvest_count": 0,
        "death_tick": None,
        "death_reason": None,
        "days_survived": 0,
        "end_reason": None,
        "trade_count": 0,
    }

    rng = derive_rng(seed, 424242)
    hold_until: dict[str, int] = {}

    while True:
        tick = market.tick

        # ── 行情推进 + heat 衰减（与插件层完全一致）────────────
        advance_market(
            market, seed=seed, ticks_per_day=ticks_per_day, config=config.market
        )
        market.day = market.tick // ticks_per_day + 1
        prices = market.prices()
        player.heat = update_heat(
            player.heat,
            events=[],
            concentration=position_concentration(player.positions, prices),
            config=config.heat,
        )
        player.heat_history.append(player.heat)

        # ── 模式层：当天新闻 + 阶段判定与收割调度 ──────────────
        outcome = mode.tick(ctx)
        harvest = execute_scheduled_harvest(ctx, outcome.phase_decision, tick=tick)
        if harvest is not None:
            stats["harvest_count"] += 1
            if stats["first_harvest_tick"] is None:
                stats["first_harvest_tick"] = market.tick

        # ── 日终结算（模式层）─────────────────────────────────
        if market.tick % ticks_per_day == 0:
            daily = mode.daily_settlement(ctx)
            if daily.dead:
                stats["death_tick"] = market.tick
                stats["death_reason"] = player.death_reason
                stats["end_reason"] = "killed"

        # ── 策略动作 ──────────────────────────────────────────
        if player.alive:
            if mode_name == "infinite":
                _infinite_maintenance(ctx, config, baseline_stock_id)
            if strategy in {"trader", "patient"}:
                _trader_actions(
                    ctx, rng, hold_until, config, patient=(strategy == "patient")
                )

        if not player.alive:
            break
        if mode.is_finished(ctx):
            stats["end_reason"] = "finished"
            break
        if market.tick >= max_ticks:
            stats["end_reason"] = "timeout"
            break

    stats["days_survived"] = market.tick // ticks_per_day
    stats["trade_count"] = player.trade_count
    return stats


def _infinite_maintenance(ctx: ModeContext, config, baseline_stock_id: str | None) -> None:
    """无限模式：卖掉基准股票 + 每 7 天主动领一次低保。

    卖基准股票是**被迫的开局动作**（玩家现金为 0，不卖就活不过第 1 天），
    也是隐藏结局判定里被豁免的那一笔。卖完它之后，挂机玩家一笔都不再交易。
    """
    player = ctx.player
    market = ctx.market
    infinite = config.infinite

    # 开局：把**基准股票**换成现钱（隐藏结局豁免的那一笔）
    if baseline_stock_id:
        position = player.positions.get(baseline_stock_id)
        stock = market.stocks.get(baseline_stock_id)
        if position is not None and stock is not None and position.amount > 0:
            try:
                execute_sell(
                    player,
                    stock,
                    position.amount,
                    config=config.fees,
                    day=player.day,
                    tick=market.tick,
                    trade_id=0,
                    reason="baseline_disposal",
                    t_plus_one=False,
                )
            except Exception:  # noqa: BLE001 - 模拟脚本不该因单笔失败而中断
                pass

    # 每周领一次低保
    cycle = ((player.day - 1) // infinite.relief_interval_days) + 1
    last_cycle = (
        ((player.welfare_claimed_day - 1) // infinite.relief_interval_days) + 1
        if player.welfare_claimed_day
        else 0
    )
    if cycle > last_cycle:
        claim_welfare(
            player,
            day=player.day,
            weekly_relief=infinite.weekly_relief,
            interval_days=infinite.relief_interval_days,
            claim_limit_days=infinite.welfare_claim_limit_days,
        )


def _trader_actions(
    ctx: ModeContext, rng, hold_until: dict[str, int], config, *, patient: bool = False
) -> None:
    """模拟玩家策略。

    **``patient=False``（契约指定的"模拟玩家"）**：每天随机买 1~2 只、持 2~3 天
    卖、见看涨新闻就追、浮亏 50% 概率补仓。

    **``patient=True`（诊断用对照）**：同样的买卖，但**拿得久**（5~12 天）、
    赚 5% 才卖、不追新闻、不补仓。用来回答一个问题：经典模式的 0% 生存率
    是**数值**的问题还是**策略**的问题 —— 短持有被手续费与滑点吃掉，
    而经典模式 30 天的设计要的是"拿住"。

    两种策略都**永远留 3 天生活费**：现实里活得久的人都知道要留后路，
    一个不留后路的模拟玩家只会测出"破产得早"。
    """
    player = ctx.player
    market = ctx.market
    day = player.day
    living_cost = daily_living_cost(ctx.mode, day, market.seed, config)
    reserve = living_cost * 3.0

    # 到期卖出
    for stock_id, until in list(hold_until.items()):
        if day < until:
            continue
        hold_until.pop(stock_id, None)
        position = player.positions.get(stock_id)
        stock = market.stocks.get(stock_id)
        if position is None or stock is None or position.amount <= 0:
            continue
        try:
            execute_sell(
                player,
                stock,
                position.amount,
                config=config.fees,
                day=day,
                tick=market.tick,
                trade_id=0,
                reason="manual",
                t_plus_one=False,
            )
        except Exception:  # noqa: BLE001 - 模拟脚本不该因单笔失败而中断
            continue

    # 每天买 1~2 只
    buys = 1 if patient else 1 + (1 if rng.random() < 0.5 else 0)
    candidates = [item for item in market.stocks.values() if item.alive]
    if not candidates:
        return
    for _ in range(buys):
        stock = candidates[int(rng.random() * len(candidates)) % len(candidates)]
        if stock.price <= 0 or player.cash <= 0:
            continue
        spendable = player.cash - reserve
        if spendable <= 0:
            continue
        amount = (spendable * 0.3 if patient else spendable * 0.25) / stock.price
        if amount <= 0:
            continue
        if not patient:
            # 见看涨新闻就追：额外加一点仓位
            chased = any(
                item.stock_id == stock.id and item.direction == "bullish"
                for item in market.news_today
            )
            if chased:
                amount *= 1.5
            # 浮亏时 50% 概率补仓
            position = player.positions.get(stock.id)
            if position is not None and stock.price < position.avg_cost:
                if rng.random() < 0.5:
                    amount *= 1.5
        amount = min(amount, player.cash / stock.price)
        if amount <= 0:
            continue
        try:
            execute_buy(
                player,
                stock,
                amount,
                config=config.fees,
                day=day,
                tick=market.tick,
                trade_id=0,
                reason="manual",
            )
        except Exception:  # noqa: BLE001
            continue
        if patient:
            hold_until[stock.id] = day + 5 + (7 if rng.random() < 0.5 else 0)
        else:
            hold_until[stock.id] = day + 2 + (1 if rng.random() < 0.5 else 0)


# ---------------------------------------------------------------------------
# 批量与报告
# ---------------------------------------------------------------------------


def _stable_offset(name: str) -> int:
    """稳定的模式偏移量（**不能用** ``hash()``：字符串哈希每进程随机）。"""
    return sum(ord(char) for char in name) % 1000


def run_batch(
    *,
    games: int = 50,
    strategy: str = "hangman",
    modes: tuple[str, ...] = (
        "classic_arcade",
        "classic_beginner",
        "classic_advanced",
        "classic_hell",
        "infinite",
    ),
    seed_base: int = 20261023,
) -> list[dict]:
    """跑 ``games`` 局（各模式均匀覆盖）。"""
    results: list[dict] = []
    per_mode = max(1, games // len(modes))
    for mode_name in modes:
        for index in range(per_mode):
            seed = seed_base + index * 7919 + _stable_offset(mode_name)
            results.append(
                simulate_game(seed, mode_name, strategy=strategy)
            )
    return results


def report(results: list[dict], *, title: str) -> str:
    """把统计结果整理成人能读的表。"""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"{title}（共 {len(results)} 局）")
    lines.append("=" * 78)

    by_mode: dict[str, list[dict]] = {}
    for item in results:
        by_mode.setdefault(item["mode"], []).append(item)

    for mode_name, items in by_mode.items():
        deaths = Counter(
            item["death_reason"] for item in items if item["death_reason"]
        )
        ends = Counter(item["end_reason"] for item in items)
        days = [item["days_survived"] for item in items]
        harvests = [item["harvest_count"] for item in items]
        first = [
            item["first_harvest_tick"]
            for item in items
            if item["first_harvest_tick"] is not None
        ]
        lines.append("")
        lines.append(f"── {mode_name}（{len(items)} 局）────────────────────────────")
        lines.append(f"  终局原因：{dict(ends)}")
        lines.append(f"  死因分布：{dict(deaths) or '（无死亡）'}")
        lines.append(
            f"  存活天数：均值 {statistics.mean(days):.1f} / "
            f"最小 {min(days)} / 最大 {max(days)}"
        )
        lines.append(
            f"  收割次数：均值 {statistics.mean(harvests):.2f} / 最大 {max(harvests)}"
        )
        if first:
            lines.append(
                f"  首次收割 tick：均值 {statistics.mean(first):.1f} / "
                f"最小 {min(first)} / 最大 {max(first)}"
            )
            early = sum(1 for tick in first if tick <= 15)
            lines.append(f"  第 15 tick 前被收割：{early}/{len(first)} 局")

    # 阈值判据（只看模拟玩家口径）
    firsts = [
        item["first_harvest_tick"]
        for item in results
        if item["first_harvest_tick"] is not None
    ]
    lines.append("")
    lines.append("── 阈值判据（§5.4）────────────────────────────────────")
    if not firsts:
        lines.append("  没有任何一局触发收割：peak_threshold=40 明显偏高。")
    else:
        early = sum(1 for tick in firsts if tick <= 15)
        ratio = early / len(firsts)
        lines.append(f"  被收割局数：{len(firsts)}/{len(results)}")
        lines.append(f"  其中第 15 tick 前：{early}（{ratio:.0%}）")
        if ratio >= 0.8:
            lines.append("  判据：≥40 局在第 15 tick 前被收割 → peak_threshold 提到 45~50。")
        else:
            lines.append("  判据：未达到 ≥40/50 的门槛 → peak_threshold 保持 40。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="拟股喵喵 · 批量模拟")
    parser.add_argument("--games", type=int, default=50, help="每种口径的局数")
    parser.add_argument(
        "--strategy",
        choices=("hangman", "trader", "patient"),
        default="hangman",
        help="挂机口径（默认）/ 模拟玩家口径 / 耐心持有口径（诊断对照）",
    )
    parser.add_argument(
        "--both", action="store_true", help="两种口径都跑（挂机 + 模拟玩家）"
    )
    parser.add_argument(
        "--modes",
        default="",
        help="逗号分隔的档位过滤（默认五个都跑）",
    )
    args = parser.parse_args(argv)

    modes = tuple(
        part.strip() for part in args.modes.split(",") if part.strip()
    ) or (
        "classic_arcade",
        "classic_beginner",
        "classic_advanced",
        "classic_hell",
        "infinite",
    )

    if args.both:
        for strategy in ("hangman", "trader"):
            results = run_batch(games=args.games, strategy=strategy, modes=modes)
            print(report(results, title=f"口径：{strategy}"))
            print()
        return 0

    results = run_batch(games=args.games, strategy=args.strategy, modes=modes)
    print(report(results, title=f"口径：{args.strategy}"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
