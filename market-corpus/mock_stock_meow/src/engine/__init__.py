"""《拟股喵喵》收割模拟器 · 引擎层（第三层）。

这一层是**纯 Python**，不认识 N.E.K.O 的存在：只依赖标准库，不 import 任何
``plugin.*`` / ``utils.*``，可以脱离宿主独立用 pytest 测试。

教育目的声明
------------
本引擎服务一个**纯虚构、纯娱乐**的反诈教学沙盘。所有标的 id 形如
``FAKE-xxx``，不映射任何真实股票代码或外汇对；价格、费率、代币均为虚构数值，
无现金价值。本模块**不存在**充值、提现、兑换、入金、出金一类功能。

请勿把本模块的代码、参数或文案用于任何真实的金融工具、投资建议或营销场景。
"""

from __future__ import annotations

from .config import (
    STREAM_CATGIRL,
    STREAM_DELISTING,
    STREAM_HARVEST,
    STREAM_MARKET,
    STREAM_NEWS,
    STREAM_PAWNBROKER,
    AnalysisConfig,
    CatgirlConfig,
    ClassicConfig,
    DifficultyConfig,
    EngineConfig,
    FeeConfig,
    HeatConfig,
    InfiniteConfig,
    MarketConfig,
    NewsConfig,
    PawnbrokerConfig,
    default_engine_config,
)
from .errors import (
    EngineError,
    InsufficientCashError,
    InvalidAmountError,
    StockNotFoundError,
    TPlusOneError,
)
from .harvest import HarvestResult, execute_harvest, select_harvest_target
from .models import (
    HeatEvent,
    HeatEventContext,
    MarketState,
    NewsItem,
    Player,
    Position,
    SettlementReport,
    Stock,
    Trade,
)
from .settlement import (
    DailySettlement,
    WelfareClaim,
    apply_death,
    claim_welfare,
    daily_settlement,
    welfare_cycle,
    welfare_entitlement,
)

__all__ = [
    "STREAM_CATGIRL",
    "STREAM_DELISTING",
    "STREAM_HARVEST",
    "STREAM_MARKET",
    "STREAM_NEWS",
    "STREAM_PAWNBROKER",
    "AnalysisConfig",
    "CatgirlConfig",
    "ClassicConfig",
    "DailySettlement",
    "DifficultyConfig",
    "EngineConfig",
    "EngineError",
    "FeeConfig",
    "HarvestResult",
    "HeatConfig",
    "HeatEvent",
    "HeatEventContext",
    "InfiniteConfig",
    "InsufficientCashError",
    "InvalidAmountError",
    "MarketConfig",
    "MarketState",
    "NewsConfig",
    "NewsItem",
    "PawnbrokerConfig",
    "Player",
    "Position",
    "SettlementReport",
    "Stock",
    "StockNotFoundError",
    "TPlusOneError",
    "Trade",
    "WelfareClaim",
    "apply_death",
    "claim_welfare",
    "daily_settlement",
    "default_engine_config",
    "execute_harvest",
    "select_harvest_target",
    "welfare_cycle",
    "welfare_entitlement",
]
