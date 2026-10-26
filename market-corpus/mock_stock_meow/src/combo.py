"""P16.0 · 连击系统（插件层 · **纯表现层**）。

街机档（``classic_arcade``）的「爽感」改造：连续买中上涨的股票时，
面板上弹一个**连击徽章**，盈利数字也跟着跳起来。

三条铁律
--------
1. **零数值改动** —— 连击只写 ``ComboTracker`` 这几个纯展示字段；
   引擎的 heat / 现金 / 持仓 / 费率**一个字节都不碰**。
2. **零引擎改动** —— 判定用的是引擎既有的 ``buy_fee`` / ``sell_fee``
   （**只读**），引擎文件本身零改动 ⇒ 500 局基线逐字节全等。
3. **只有街机档有** —— 连击是**新手村的糖**；后面两档不给，
   否则后期收割的压迫感会被这块糖抵消掉。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .engine.config import FeeConfig
from .engine.settlement import buy_fee, sell_fee

__all__ = [
    "COMBO_MODES",
    "ComboTracker",
    "judge_entry",
]

#: 哪些档位享受「连击」表现层（街机 = 新手村）。
COMBO_MODES: tuple[str, ...] = ("classic_arcade",)


def judge_entry(
    *,
    entry_price: float,
    amount: float,
    current_price: float,
    fees: FeeConfig,
) -> bool:
    """判定一笔买入**按现价卖出**能不能净赚钱。

    口径与引擎完全一致：买入佣金 + 卖出佣金 + 印花税全算进去，
    只有净赚才算「命中」—— 否则玩家会觉得自己被手续费坑了。
    """
    if entry_price <= 0.0 or amount <= 0.0 or current_price <= 0.0:
        return False
    cost = entry_price * amount
    exit_value = current_price * amount
    total_fee = buy_fee(cost, fees) + sell_fee(exit_value, fees)
    return exit_value - total_fee > cost


@dataclass
class ComboTracker:
    """连击状态（**纯展示**）。

    ``pending`` 是「买完还没判定」的买入记录；下一次价格推进之后
    逐笔判定：命中就 ``streak += 1``，没命中就清零。

    ``best`` 是本局最长连击 —— 结算页可以拿它做彩蛋。
    """

    streak: int = 0
    best: int = 0
    pending: list[tuple[str, float, float]] = field(default_factory=list)

    def reset(self) -> None:
        """新的一局 ⇒ 连击从头开始。"""
        self.streak = 0
        self.best = 0
        self.pending.clear()

    def add_entry(self, stock_id: str, entry_price: float, amount: float) -> None:
        """记一笔「刚买进」（等下一次价格推进后判定）。"""
        self.pending.append((str(stock_id), float(entry_price), float(amount)))

    def resolve(
        self,
        prices: Mapping[str, float],
        holdings: Mapping[str, float],
        fees: FeeConfig,
    ) -> int:
        """结算上一批买入，返回新的连击数。

        ``holdings`` 是当前还攥着的标的（``{stock_id: amount}``）——
        已经卖掉的那些**不算命中也不算清零**（玩家根本没押注它）。
        """
        if not self.pending:
            return self.streak
        for stock_id, entry_price, amount in self.pending:
            if stock_id not in holdings:
                continue
            won = judge_entry(
                entry_price=entry_price,
                amount=amount,
                current_price=float(prices.get(stock_id, 0.0)),
                fees=fees,
            )
            if won:
                self.streak += 1
                if self.streak > self.best:
                    self.best = self.streak
            else:
                self.streak = 0
        self.pending.clear()
        return self.streak
