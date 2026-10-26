"""阶段状态机：HONEYMOON → TRAP → SLAUGHTER。

教育目的说明
------------
三个阶段就是"养套杀"的三个步骤：蜜月期让你小赢，陷阱期震荡诱多，收割期精准
落下。这一层只负责**决定阶段与阶段参数**，不做任何成交，也不直接操作玩家
数据。
"""

from __future__ import annotations

from .config import DifficultyConfig, HeatConfig
from .heat import is_at_peak
from .models import Player

__all__ = [
    "PHASE_HONEYMOON",
    "PHASE_SLAUGHTER",
    "PHASE_TRAP",
    "evaluate_transition",
    "jump_severity",
    "phase_profile",
]

PHASE_HONEYMOON = "honeymoon"
PHASE_TRAP = "trap"
PHASE_SLAUGHTER = "slaughter"

_TRIGGER_FIXED_TRADES = "fixed_trades"
_TRIGGER_HEAT_PEAK = "heat_peak"
_TRIGGER_PREY_INDEX = "prey_index"


def phase_profile(phase: str, difficulty: DifficultyConfig) -> tuple[float, float]:
    """返回该阶段的 ``(mu, sigma)``。"""
    if phase == PHASE_HONEYMOON:
        return (difficulty.honeymoon_mu, difficulty.honeymoon_sigma)
    if phase == PHASE_TRAP:
        return (difficulty.trap_mu, difficulty.trap_sigma)
    return (difficulty.slaughter_mu, difficulty.slaughter_sigma)


def evaluate_transition(
    current_phase: str,
    *,
    difficulty: DifficultyConfig,
    heat_config: HeatConfig,
    player: Player,
    prey_index: float = 0.0,
) -> str:
    """根据当前阶段与收割触发条件，返回下一个阶段。

    ``slaughter`` 是终态。收割触发方式由 ``difficulty.harvest_trigger`` 决定：

    * ``fixed_trades``（街机档）：累计成交笔数达标；
    * ``heat_peak``（核心档）：heat 出现峰值；
    * ``prey_index``（地狱档）：由上层算出的个人猎物指数越线。
    """
    if current_phase == PHASE_SLAUGHTER:
        return PHASE_SLAUGHTER

    trigger = difficulty.harvest_trigger

    if current_phase == PHASE_HONEYMOON:
        if trigger == _TRIGGER_FIXED_TRADES:
            limit = difficulty.fixed_harvest_trades or 0
            if player.trade_count >= limit:
                return PHASE_TRAP
            return PHASE_HONEYMOON
        if trigger == _TRIGGER_PREY_INDEX:
            if prey_index >= difficulty.prey_index_threshold:
                return PHASE_TRAP
            return PHASE_HONEYMOON
        if is_at_peak(
            player.heat_history,
            threshold=heat_config.peak_threshold,
            window=heat_config.peak_window,
        ):
            return PHASE_TRAP
        return PHASE_HONEYMOON

    # current_phase == PHASE_TRAP
    if trigger == _TRIGGER_FIXED_TRADES:
        limit = (difficulty.fixed_harvest_trades or 0) * 2
        if player.trade_count >= limit:
            return PHASE_SLAUGHTER
        return PHASE_TRAP
    if trigger == _TRIGGER_PREY_INDEX:
        if prey_index >= difficulty.prey_index_threshold:
            return PHASE_SLAUGHTER
        return PHASE_TRAP
    if is_at_peak(
        player.heat_history,
        threshold=heat_config.peak_threshold,
        window=heat_config.peak_window,
    ):
        return PHASE_SLAUGHTER
    return PHASE_TRAP


def jump_severity(concentration: float, *, phase: str = PHASE_SLAUGHTER) -> float:
    """收割跳跃的幅度：与集中度挂钩，越集中跌得越狠。

    集中度 0 → 20%，集中度 1 → 80%。
    """
    clamped = min(1.0, max(0.0, concentration))
    severity = 0.20 + 0.60 * clamped
    if phase != PHASE_SLAUGHTER:
        severity *= 0.5
    return min(1.0, severity)
