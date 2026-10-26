"""会话重建与引擎配置装配。

对应 P2 契约 §5（种子）、§9（与 P1 引擎层的对接方式）。

三条硬规则
----------
1. **不持有长生命周期的引擎实例**：每个入口都从 ``self.db`` + ``self.state``
   重建，宿主随时重启插件进程也不会丢状态。
2. **``self.db`` 是权威，``self.state`` 是缓存**：两者不一致时以数据库为准
   （用 seed 重放覆盖快照）。
3. **价格不落库**：价格是 ``(seed, tick)`` 的纯函数，重启后重放即可精确还原。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from typing import Any

from ..engine.config import (
    AnalysisConfig,
    ClassicConfig,
    DifficultyConfig,
    EngineConfig,
    FeeConfig,
    HeatConfig,
    InfiniteConfig,
    MarketConfig,
    NewsConfig,
    PawnbrokerConfig,
)
from ..engine.market import replay_market
from ..engine.models import MarketState, NewsItem, Player, Position, Stock
from .repo import Repo

__all__ = [
    "MarketSnapshot",
    "Session",
    "build_engine_config",
    "load_session",
    "load_snapshot",
    "save_snapshot",
]

_SECTION_CONFIG: dict[str, type] = {
    "heat": HeatConfig,
    "market": MarketConfig,
    "fees": FeeConfig,
    "pawnbroker": PawnbrokerConfig,
    "news": NewsConfig,
    "analysis": AnalysisConfig,
    "classic": ClassicConfig,
    "infinite": InfiniteConfig,
}


def build_engine_config(raw: dict[str, Any] | None) -> EngineConfig:
    """把宿主配置（``config.example.toml`` 的各段）装配成 :class:`EngineConfig`。

    只接受 dataclass 已声明的字段名；配置里多余的键会被忽略，缺省的键用引擎
    默认值补齐。**猫娘策略参数刻意不在这里**——它只存在于 ``CatgirlConfig``。
    """
    raw = raw if isinstance(raw, dict) else {}
    kwargs: dict[str, Any] = {}
    for section, config_cls in _SECTION_CONFIG.items():
        values = raw.get(section)
        values = values if isinstance(values, dict) else {}
        allowed = {item.name for item in fields(config_cls)}
        kwargs[section] = config_cls(
            **{key: value for key, value in values.items() if key in allowed}
        )
    return EngineConfig(**kwargs)


@dataclass
class MarketSnapshot:
    """短生命周期市场快照，存 ``self.state``。

    ``__freezable__`` 是 SDK 约定的持久化字段清单：只有列在里面的属性会被
    写进状态文件。全部是 JSON 安全的标量与列表。
    """

    __freezable__ = (
        "tick",
        "day",
        "phase",
        "seed",
        "fee_pool",
        "player_id",
        "stocks",
        "reversal_days",
    )

    tick: int = 0
    day: int = 1
    phase: str = "honeymoon"
    seed: int = 0
    fee_pool: float = 0.0
    player_id: str = ""
    stocks: list = field(default_factory=list)
    #: 反转日集合（P4）。JSON 安全的整型列表，落库为 JSON 文本。
    reversal_days: list = field(default_factory=list)


@dataclass
class Session:
    """一次入口调用所需要的全部状态。"""

    player: Player
    market: MarketState
    player_id: str
    mode: str
    difficulty: DifficultyConfig
    #: 猫娘（仅无限模式常驻）。``None`` 表示本局没有猫娘。
    catgirl: Player | None = None
    #: 入场身份（P5.5 · 改动 2）。**纯叙事字段** —— 不参与任何判定，
    #: 只在 ``join`` 的返回体、``status`` 与**破产结算页**上出现。
    #:
    #: 它**刻意不放**在 ``engine.models.Player`` 上：引擎不认识它，
    #: 引擎也**不需要**认识它（P5.5 总约束：``engine/`` 一行不动）。
    identity: str = "quick"
    #: 最近一次「跳过本日」发生在第几天（P5.5 改动 6）。每天限跳一次。
    skip_day_used: int = 0
    #: 是否已解锁「无为而治」称号（P5.5 改动 6）。
    inaction_unlocked: int = 0


async def load_snapshot(state: Any) -> MarketSnapshot | None:
    """从 ``self.state`` 读取市场快照；没有或读取失败时返回 ``None``。"""
    snapshot = MarketSnapshot()
    result = await state.load(snapshot)
    if result.is_err():
        return None
    if not result.unwrap():
        return None
    return snapshot


async def save_snapshot(state: Any, market: MarketState, player_id: str) -> None:
    """把市场快照写进 ``self.state``（缓存，数据库才是权威）。"""
    snapshot = MarketSnapshot(
        tick=market.tick,
        day=market.day,
        phase=market.phase,
        seed=market.seed,
        fee_pool=market.fee_pool,
        player_id=player_id,
        reversal_days=sorted(int(day) for day in market.reversal_days),
        stocks=[
            {
                "id": stock.id,
                "name": stock.name,
                "price": stock.price,
                "prev_price": stock.prev_price,
                "mu": stock.mu,
                "sigma": stock.sigma,
                "alive": stock.alive,
                "tag": stock.tag,
                "listed_day": stock.listed_day,
                "delisted_day": stock.delisted_day,
                "kline": [list(point) for point in stock.kline],
            }
            for stock in market.stocks.values()
        ],
    )
    await state.save(snapshot)


def _market_from_snapshot(snapshot: MarketSnapshot) -> MarketState:
    """由快照还原市场对象。"""
    stocks: dict[str, Stock] = {}
    for item in snapshot.stocks:
        stocks[item["id"]] = Stock(
            id=item["id"],
            name=item["name"],
            price=item["price"],
            prev_price=item["prev_price"],
            mu=item["mu"],
            sigma=item["sigma"],
            alive=bool(item["alive"]),
            listed_day=int(item.get("listed_day", 0)),
            delisted_day=item.get("delisted_day"),
            tag=item.get("tag", "neutral"),
            kline=[(int(point[0]), float(point[1])) for point in item.get("kline", [])],
        )
    return MarketState(
        tick=snapshot.tick,
        day=snapshot.day,
        phase=snapshot.phase,
        seed=snapshot.seed,
        stocks=stocks,
        fee_pool=snapshot.fee_pool,
        reversal_days={int(day) for day in (snapshot.reversal_days or [])},
    )


def _parse_reversal_days(raw: Any) -> set[int]:
    """把 ``market_state.reversal_days`` 的 JSON 文本还原成整数集合。"""
    if isinstance(raw, (set, frozenset, list, tuple)):
        return {int(day) for day in raw}
    if not raw:
        return set()
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return set()
    if not isinstance(value, list):
        return set()
    return {int(day) for day in value if isinstance(day, (int, float))}


def _parse_pending_action(raw: Any) -> dict | None:
    """把 ``players.pending_action`` 的 JSON 文本还原成字典。"""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _player_from_row(row: Any) -> Player:
    """由 ``players`` 表的一行还原 :class:`Player`。"""
    keys = row.keys()
    return Player(
        id=row["id"],
        name=row["name"],
        cash=float(row["cash"]),
        mode=row["mode"],
        day=int(row["day"]),
        alive=bool(row["alive"]),
        trade_count=int(row["trade_count"]),
        heat=float(row["heat"]),
        tokens=int(row["tokens"]),
        strikes=int(row["strikes"]),
        loan_debt=float(row["loan_debt"]),
        is_catgirl=bool(row["is_catgirl"]) if "is_catgirl" in keys else False,
        death_reason=row["death_reason"],
        death_title=row["death_title"],
        pending_action=_parse_pending_action(row["pending_action"])
        if "pending_action" in keys
        else None,
        total_fee_paid=float(row["total_fee_paid"]),
        initial_cash=float(row["initial_cash"]),
        # ── P4：低保与高利贷的三列 ──────────────────────────────────
        welfare_missed_days=int(row["welfare_missed_days"])
        if "welfare_missed_days" in keys
        else 0,
        loan_taken_day=int(row["loan_taken_day"]) if "loan_taken_day" in keys else 0,
        welfare_claimed_day=int(row["welfare_claimed_day"])
        if "welfare_claimed_day" in keys
        else 0,
        # ── P5：分析次数的两列 ──────────────────────────────────────────
        analyses_day=int(row["analyses_day"]) if "analyses_day" in keys else 0,
        analyses_used=int(row["analyses_used"]) if "analyses_used" in keys else 0,
    )


async def _load_player_details(repo: Repo, player: Player) -> None:
    """补齐一个玩家的持仓与 heat 曲线。"""
    async with repo.reader() as session:
        cursor = await session.execute(
            "SELECT * FROM positions WHERE player_id = ?", (player.id,)
        )
        for row in cursor.fetchall():
            player.positions[row["stock_id"]] = Position(
                stock_id=row["stock_id"],
                amount=float(row["amount"]),
                avg_cost=float(row["avg_cost"]),
                today_bought=float(row["today_bought"]),
                today_bought_day=int(row["today_bought_day"]),
                opened_day=int(row["opened_day"]),
            )
        cursor = await session.execute(
            "SELECT tick, heat FROM heat_history WHERE player_id = ? ORDER BY tick ASC",
            (player.id,),
        )
        for row in cursor.fetchall():
            player.heat_history.append(float(row["heat"]))
    if player.heat_history:
        player.heat = player.heat_history[-1]


async def _load_today_news(repo: Repo, market: MarketState) -> None:
    """把当天已落库的新闻装回 ``market.news_today``。

    新闻是 ``(seed, day, phase)`` 的纯函数，但**阶段会变**：插件重启后重新生成
    可能与落库的那一批对不上。所以新闻以**数据库**为准，重启后直接读回来，
    保证同一天看到的永远是同一批新闻。
    """
    async with repo.reader() as session:
        cursor = await session.execute(
            "SELECT day, news_id, news_type, stock_id, content, accuracy, direction "
            "FROM news_log WHERE day = ? ORDER BY news_id ASC",
            (market.day,),
        )
        rows = cursor.fetchall()
    keys = rows[0].keys() if rows else ()
    market.news_today = [
        NewsItem(
            id=row["news_id"],
            day=int(row["day"]),
            type=row["news_type"],
            stock_id=row["stock_id"],
            content=row["content"],
            accuracy=row["accuracy"],
            direction=row["direction"] if "direction" in keys else None,
        )
        for row in rows
    ]


async def load_session(
    repo: Repo,
    state: Any,
    *,
    engine_config: EngineConfig,
) -> Session | None:
    """从数据库 + 状态缓存重建当前会话；没有进行中的对局时返回 ``None``。"""
    market_row = await repo.fetch_one("SELECT * FROM market_state WHERE id = 1")
    if market_row is None:
        return None
    player_id = market_row["player_id"] or ""
    if not player_id:
        return None

    player_row = await repo.fetch_one(
        "SELECT * FROM players WHERE id = ?", (player_id,)
    )
    if player_row is None:
        return None

    mode = player_row["mode"]
    difficulty = engine_config.difficulties.get(mode)
    if difficulty is None:
        difficulty = engine_config.difficulties["classic_arcade"]

    player = _player_from_row(player_row)
    await _load_player_details(repo, player)

    # 猫娘只在无限模式常驻；找不到行就当这一局没有猫娘。
    catgirl: Player | None = None
    catgirl_row = await repo.fetch_one(
        "SELECT * FROM players WHERE is_catgirl = 1 AND mode = ? LIMIT 1", (mode,)
    )
    if catgirl_row is not None and catgirl_row["id"] != player_id:
        catgirl = _player_from_row(catgirl_row)
        await _load_player_details(repo, catgirl)

    market: MarketState | None = None
    snapshot = await load_snapshot(state)
    if (
        snapshot is not None
        and snapshot.stocks
        and snapshot.seed == market_row["seed"]
        and snapshot.tick == market_row["tick"]
    ):
        market = _market_from_snapshot(snapshot)
    if market is None:
        market = replay_market(
            int(market_row["seed"]),
            until_tick=int(market_row["tick"]),
            count=difficulty.initial_symbols,
            config=engine_config.market,
            difficulty=difficulty,
            ticks_per_day=difficulty.ticks_per_day,
        )
    market.phase = market_row["phase"]
    market.fee_pool = float(market_row["fee_pool"])
    market.total_fees = float(market_row["total_fees"])
    # 反转日集合以**数据库**为准（数据库是权威，快照只是缓存）。
    market.reversal_days = _parse_reversal_days(
        market_row["reversal_days"] if "reversal_days" in market_row.keys() else None
    )
    # 当天新闻以数据库为准（保证重启前后是同一批新闻）。
    await _load_today_news(repo, market)

    return Session(
        player=player,
        market=market,
        player_id=player_id,
        mode=mode,
        difficulty=difficulty,
        catgirl=catgirl,
        identity=(
            str(player_row["identity"])
            if "identity" in player_row.keys() and player_row["identity"]
            else "quick"
        ),
        skip_day_used=(
            int(player_row["skip_day_used"])
            if "skip_day_used" in player_row.keys()
            and player_row["skip_day_used"] is not None
            else 0
        ),
        inaction_unlocked=(
            int(player_row["inaction_unlocked"])
            if "inaction_unlocked" in player_row.keys()
            and player_row["inaction_unlocked"] is not None
            else 0
        ),
    )
