"""All tunable numbers for the market engine.

Rule R4 of the contract: **every** magic number lives here.  Other engine
modules may only reference these constants; they never hard-code a rate, a
threshold or a window.

The dataclasses below map 1:1 onto the user-editable sections of
``config.example.toml`` (see contract §8.3).  ``DifficultyConfig`` and
``CatgirlConfig`` are deliberately **not** exposed to the user: the catgirl
strategy is fixed in code so that "the catgirl and the human player share one
heat model and one settle function" cannot be quietly changed by a config
edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .models import HeatEvent

__all__ = [
    "AnalysisConfig",
    "CatgirlConfig",
    "ClassicConfig",
    "DifficultyConfig",
    "EngineConfig",
    "FeeConfig",
    "HeatConfig",
    "InfiniteConfig",
    "MarketConfig",
    "NewsConfig",
    "PawnbrokerConfig",
    "STREAM_CATGIRL",
    "STREAM_DELISTING",
    "STREAM_HARVEST",
    "STREAM_MARKET",
    "STREAM_NEWS",
    "STREAM_PAWNBROKER",
    "default_engine_config",
]


# ---------------------------------------------------------------------------
# 六条互相独立的随机流（P3 契约 §15 / 决策 ⑥）
# ---------------------------------------------------------------------------
#
# 每条流都由 ``derive_rng(seed, <流 ID>, *上下文)`` 派生。流 ID 是**整数常量**，
# 不能传字符串——``derive_rng`` 只接受 int。
#
# 独立性的含义：改动其中一条流的 ID 或用法，**不会**改变另外五条流抽到的数字。
# 单测 ``tests/test_rng_streams.py`` 逐条守住这条不变量。

#: 行情流：tick 序列的价格推进（``advance_market``）。
STREAM_MARKET = 100_001

#: 猫娘流：猫娘每日决策（``catgirl_daily_plan``）。
STREAM_CATGIRL = 100_002

#: 新闻流：新闻生成与反转日判定（``generate_news`` / ``pick_reversal_days``）。
STREAM_NEWS = 100_003

#: 二道贩子流：二道贩子定价的抖动（强制卖出价）。
STREAM_PAWNBROKER = 100_004

#: 收割流：收割跳跃的 severity 抖动（``apply_jump``）。
STREAM_HARVEST = 100_005

#: 退市流：退市缓冲区里"是否被击穿退市"的判定。
STREAM_DELISTING = 100_006



@dataclass(frozen=True)
class HeatConfig:
    """上头指数参数。"""

    decay: float = 0.85
    peak_threshold: float = 40.0
    peak_window: int = 5
    concentration_50: float = 10.0
    concentration_70: float = 25.0
    concentration_90: float = 40.0
    concentration_weight: float = 1.0
    chase_window: int = 10
    chase_ratio: float = 0.95
    high_freq_window: int = 20
    high_freq_ratio: float = 1.5
    thunder_window: int = 3
    #: 收割触发所需的最低集中度（0~1）。难度档位可覆盖。
    harvest_concentration_threshold: float = 0.5
    event_scores: Mapping[HeatEvent, float] = field(
        default_factory=lambda: MappingProxyType(
            {
                HeatEvent.LOSS_AVERAGING: 15.0,
                HeatEvent.CHASE_HIGH: 10.0,
                HeatEvent.LOSS_LEVERAGE: 30.0,
                HeatEvent.HIGH_FREQUENCY: 5.0,
                HeatEvent.FAKE_NEWS_CHASE: 10.0,
                HeatEvent.THUNDER_NEWS_AVERAGING: 20.0,
                HeatEvent.LIVING_COST_AVERAGING: 20.0,
            }
        )
    )


@dataclass(frozen=True)
class MarketConfig:
    """市场引擎参数。"""

    classic_ticks_per_day: int = 12
    infinite_ticks_per_day: int = 48
    initial_symbols: int = 20
    sigma_growth_per_day: float = 0.01
    sigma_max: float = 0.40
    delisting_floor: float = 0.01
    fee_pool_reveal: bool = True
    sigma_stable_max: float = 0.10
    sigma_explosive_min: float = 0.20
    kline_window: int = 60
    #: 一次心跳最多补几个 tick（防止离线/休眠后一口气补几十个）。
    max_catchup_ticks: int = 3
    #: 单个 tick 的耗时告警阈值（秒）。
    tick_warn_seconds: float = 5.0
    #: 单个 tick 的耗时跳过阈值（秒），超过则放弃本次 tick。
    tick_skip_seconds: float = 15.0
    #: 心跳自身的耗时告警阈值（毫秒）。
    heartbeat_warn_ms: float = 100.0
    #: 退市缓冲区：价格不高于「地板 × 该倍数」的标的进入缓冲区。
    delisting_buffer_ratio: float = 3.0
    #: 缓冲区内标的每 tick 被击穿退市的概率（用退市流抽样）。
    delisting_prob_per_tick: float = 0.05


@dataclass(frozen=True)
class FeeConfig:
    """手续费率。印花税只在卖出方向收取（单边）。"""

    buy_rate: float = 0.003
    sell_rate: float = 0.005
    stamp_tax_rate: float = 0.001


@dataclass(frozen=True)
class PawnbrokerConfig:
    """二道贩子定价参数。"""

    base_discount: float = 0.20
    urgent_extra: float = 0.30
    base_premium: float = 0.15
    urgent_premium: float = 0.25
    same_day_discount: float = 0.40


@dataclass(frozen=True)
class NewsConfig:
    """新闻系统参数。

    ``truth_ratio_honeymoon`` 是**真新闻内容的准确率**；
    ``fake_ratio_trap`` 是**TRAP 阶段新闻中假新闻的占比**。两者语义不同。
    """

    truth_ratio_honeymoon: float = 0.70
    true_ratio_honeymoon: float = 0.60
    fake_ratio_trap: float = 0.50
    thunder_ratio_slaughter: float = 0.40
    reversal_prob_min: float = 0.15
    reversal_prob_max: float = 0.20
    reversal_magnitude_min: float = 0.20
    reversal_magnitude_max: float = 0.40
    reversal_days_min: int = 1
    reversal_days_max: int = 3
    noise_ratio: float = 0.20
    news_per_day: int = 3
    #: 新闻影响 heat 的时间窗（tick）。经典模式 1/4 天、无限模式 1/16 天。
    news_window_ticks: int = 3


@dataclass(frozen=True)
class AnalysisConfig:
    """分析按钮的消耗方式。"""

    cost_mode: str = "token"
    token_cost: int = 5
    tick_cost: int = 1


@dataclass(frozen=True)
class ClassicConfig:
    """经典模式参数（四档共用，按短局缩放）。

    这是**基准值**：各档位的实际生活费与初始天数写在 ``DifficultyConfig`` 里
    （``living_cost_min`` / ``living_cost_max`` / ``initial_days``），没写覆盖的
    档位才用这里的全局值。

    ``total_game_days``（P4 新增）：经典模式总游戏日。第 30 个游戏日结束时
    自动终局并自动结算。经典模式**没有低保、没有高利贷**。
    """

    ticks_per_day: int = 12
    living_cost_min: float = 10.0
    living_cost_max: float = 10.0
    initial_days: int = 17
    #: 经典模式总游戏日（P4）。超过即自动终局。
    total_game_days: int = 30


@dataclass(frozen=True)
class InfiniteConfig:
    """无限模式参数。

    低保是**主动领取**的（``daily(action="claim")``），不领不到账；允许补领，
    补领上限 ``welfare_claim_limit_days`` 天。高利贷每天限借
    ``loan_take_per_day`` 次。
    """

    initial_days: int = 7
    daily_living_cost: float = 150.0
    weekly_relief: float = 1050.0
    loan_amount: float = 500.0
    loan_repay: float = 600.0
    strike_limit: int = 3
    #: 低保发放间隔（天）。一个周期 = 一份低保（``weekly_relief``）。
    relief_interval_days: int = 7
    #: 无限模式的 tick 间隔（秒）。
    tick_interval_seconds: int = 30
    #: 低保补领上限（天）。超过上限的欠款作废。
    welfare_claim_limit_days: int = 7
    #: 每天最多可借款次数。
    loan_take_per_day: int = 1


@dataclass(frozen=True)
class DifficultyConfig:
    """一档难度的完整规则。"""

    mode: str
    has_living_cost: bool = True
    t_plus_one: bool = False
    show_fee: bool = True
    leverage: float = 1.0
    limit_up_down: bool = False
    slippage: float = 0.0
    teacher_calls: bool = False
    harvest_trigger: str = "heat_peak"
    fixed_harvest_trades: int | None = None
    prey_index_threshold: float = 60.0
    initial_symbols: int = 20
    ticks_per_day: int = 12
    #: 本档的 tick 间隔（秒）。经典四档 8 秒，无限模式 30 秒。
    tick_interval_seconds: int = 8
    #: 本档的收割集中度门槛；``None`` 表示沿用 ``HeatConfig`` 的全局值。
    harvest_concentration_threshold: float | None = None
    #: 本档的生活费下限（覆盖 ``ClassicConfig`` 的全局值；``None`` = 沿用全局）。
    living_cost_min: float | None = None
    #: 本档的生活费上限（覆盖 ``ClassicConfig`` 的全局值；``None`` = 沿用全局）。
    living_cost_max: float | None = None
    #: 本档的初始天数（覆盖 ``ClassicConfig`` 的全局值；``None`` = 沿用全局）。
    initial_days: int | None = None
    honeymoon_mu: float = 0.01
    honeymoon_sigma: float = 0.05
    trap_mu: float = 0.0
    trap_sigma: float = 0.15
    slaughter_mu: float = -0.02
    slaughter_sigma: float = 0.30


@dataclass(frozen=True)
class CatgirlConfig:
    """猫娘策略参数（**刻意不暴露给用户配置**）。

    写死在代码里，是为了让"猫娘与人类玩家走同一套 heat 逻辑、同一个 settle
    函数，代码里不存在猫娘豁免分支"这一教学前提无法被配置悄悄改掉。
    """

    buffer_days: int = 3
    max_positions: int = 2
    trade_cash_ratio: float = 0.15
    trade_prob_per_tick: float = 0.20
    avoid_high_ratio: float = 0.95
    sell_order: str = "smallest_loss"


def _default_difficulties() -> Mapping[str, DifficultyConfig]:
    """五档难度的默认规则（经典四档 + 无限模式）。

    tick 密度一律取自 :class:`MarketConfig` 的同名默认值，避免两处常量漂移。
    """
    market_defaults = MarketConfig()
    classic_ticks = market_defaults.classic_ticks_per_day
    difficulties = {
        # 街机档：没有生活费，只有现钱。初始交易本金沿用旧口径（7 天 ×
        # 15~20），**数值不变**；这两个覆盖值只用来算本金，不会被扣。
        "classic_arcade": DifficultyConfig(
            mode="classic_arcade",
            has_living_cost=False,
            t_plus_one=False,
            show_fee=False,
            leverage=1.0,
            harvest_trigger="fixed_trades",
            fixed_harvest_trades=30,
            ticks_per_day=classic_ticks,
            living_cost_min=15.0,
            living_cost_max=20.0,
            initial_days=7,
        ),
        # 入门档：P4 收尾调整 —— 生活费 10/天、初始 17 天（≈170）。
        # **初始天数是生存率的唯一旋钮**：初始资金 = 生活费 × 天数，所以 30 天
        # 总生活费的"需翻倍数"恒等于 30 ÷ 天数，与每天多少钱无关。
        # 17 天 → 需翻 1.8 倍 → 实测生存率 22%（400 局模拟玩家口径）。
        "classic_beginner": DifficultyConfig(
            mode="classic_beginner",
            has_living_cost=True,
            t_plus_one=False,
            show_fee=True,
            leverage=1.0,
            harvest_trigger="heat_peak",
            ticks_per_day=classic_ticks,
            living_cost_min=10.0,
            living_cost_max=10.0,
            initial_days=17,
        ),
        # 进阶档：与入门档同一套经济曲线（T+1 与涨跌停是它真正的难度来源）。
        "classic_advanced": DifficultyConfig(
            mode="classic_advanced",
            has_living_cost=True,
            t_plus_one=True,
            show_fee=True,
            leverage=1.0,
            limit_up_down=True,
            slippage=0.01,
            harvest_trigger="heat_peak",
            ticks_per_day=classic_ticks,
            living_cost_min=10.0,
            living_cost_max=10.0,
            initial_days=17,
        ),
        # 地狱档：P4 收尾调整 —— 生活费 12/天、初始 14 天（≈168）。
        # 同样 1.8 倍的需翻倍数，但地狱档有 10 倍杠杆 + prey_index 收割触发，
        # 实测生存率 8%（400 局）—— 保持"真正的地狱"定位：这里是那 10%
        # 幸运玩家的试炼场。
        "classic_hell": DifficultyConfig(
            mode="classic_hell",
            has_living_cost=True,
            t_plus_one=True,
            show_fee=True,
            leverage=10.0,
            limit_up_down=True,
            slippage=0.02,
            teacher_calls=True,
            harvest_trigger="prey_index",
            ticks_per_day=classic_ticks,
            living_cost_min=12.0,
            living_cost_max=12.0,
            initial_days=14,
        ),
        # 无限模式：P2 只提供难度参数（生活费 / 初始资金 / tick 密度），
        # 猫娘与每日账单结算由 P4 的模式层实现。
        "infinite": DifficultyConfig(
            mode="infinite",
            has_living_cost=True,
            t_plus_one=False,
            show_fee=True,
            leverage=1.0,
            harvest_trigger="heat_peak",
            ticks_per_day=market_defaults.infinite_ticks_per_day,
            tick_interval_seconds=InfiniteConfig().tick_interval_seconds,
        ),
    }
    return MappingProxyType(difficulties)


@dataclass(frozen=True)
class EngineConfig:
    """引擎的全部配置。"""

    heat: HeatConfig = field(default_factory=HeatConfig)
    market: MarketConfig = field(default_factory=MarketConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    pawnbroker: PawnbrokerConfig = field(default_factory=PawnbrokerConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    classic: ClassicConfig = field(default_factory=ClassicConfig)
    infinite: InfiniteConfig = field(default_factory=InfiniteConfig)
    difficulties: Mapping[str, DifficultyConfig] = field(
        default_factory=_default_difficulties
    )
    catgirl: CatgirlConfig = field(default_factory=CatgirlConfig)


def default_engine_config() -> EngineConfig:
    """返回全部默认值的引擎配置（纯函数、无副作用）。"""
    return EngineConfig()
