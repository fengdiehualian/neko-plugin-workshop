"""模式层（P4）：第二层。

这一层是**模式规则**的所在地：经典四档与无限模式各自是一套流程，公共底盘
（日终结算、二道贩子、收割调度、新闻接入）在 ``modes/shared.py``。

铁律
----
* 只 import ``..engine``（纯标准库引擎层），**绝不** import 任何 ``plugin.*``；
* 绝不碰数据库、绝不做 IO；
* 插件层通过 :func:`get_mode` 拿到模式对象，然后调用
  ``setup`` / ``tick`` / ``daily_settlement`` / ``is_finished`` 四个方法，
  再把返回的 dataclass 落库。

模式清单
--------
=============  ======================================================
``classic_*``  经典四档（街机 / 入门 / 进阶 / 地狱）：30 个游戏日自动终局
``infinite``   无限模式：低保、高利贷、三振阶梯、隐藏结局
=============  ======================================================
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .classic import ClassicMode
from .infinite import InfiniteMode
from .shared import (
    DailyOutcome,
    ModeContext,
    PhaseDecision,
    TickOutcome,
    daily_living_cost,
    ensure_day_news,
    force_distress_sell,
    harvest_threshold,
    next_living_cost,
    prey_index,
    run_daily_settlement,
    schedule_harvest,
)

__all__ = [
    "MODES",
    "ClassicMode",
    "DailyOutcome",
    "InfiniteMode",
    "ModeContext",
    "ModeProtocol",
    "PhaseDecision",
    "TickOutcome",
    "daily_living_cost",
    "ensure_day_news",
    "force_distress_sell",
    "get_mode",
    "harvest_threshold",
    "next_living_cost",
    "prey_index",
    "run_daily_settlement",
    "schedule_harvest",
]


@runtime_checkable
class ModeProtocol(Protocol):
    """一个模式必须提供的四个方法。"""

    #: 模式族（``classic`` / ``infinite``）。
    mode_family: str

    def setup(self, ctx: ModeContext) -> TickOutcome:
        """开局：发放初始资源、登记反转日、生成第 1 天的新闻。"""

    def tick(self, ctx: ModeContext) -> TickOutcome:
        """一个 tick 的模式逻辑（新闻 + 阶段判定与收割调度）。"""

    def daily_settlement(self, ctx: ModeContext) -> DailyOutcome:
        """日终结算。"""

    def is_finished(self, ctx: ModeContext) -> bool:
        """本局是否已终局。"""


#: 模式注册表：档位名 → 模式对象。
#:
#: 经典四档共用一个 :class:`ClassicMode` 实例 —— 四档的差异全在
#: ``DifficultyConfig`` 里，不在模式代码里。
MODES: dict[str, ModeProtocol] = {
    "classic_arcade": ClassicMode(),
    "classic_beginner": ClassicMode(),
    "classic_advanced": ClassicMode(),
    "classic_hell": ClassicMode(),
    "infinite": InfiniteMode(),
}


def get_mode(name: str) -> ModeProtocol | None:
    """按档位名取模式对象；未知档位返回 ``None``。"""
    return MODES.get(name)
