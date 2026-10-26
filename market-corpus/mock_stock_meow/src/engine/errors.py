"""Engine level errors.

These are plain exceptions raised when an invariant would be violated (not
enough cash, T+1 lock, bad amount, unknown symbol).  The first layer maps
them onto the host's ``Err(SdkError(...))`` result type.
"""

from __future__ import annotations

__all__ = [
    "EngineError",
    "InsufficientCashError",
    "InvalidAmountError",
    "StockNotFoundError",
    "TPlusOneError",
]


class EngineError(Exception):
    """Base class for engine errors, carrying a stable machine-readable code."""

    code = "engine_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


class InsufficientCashError(EngineError):
    """现钱不足。"""

    code = "insufficient_cash"


class InvalidAmountError(EngineError):
    """数量不合法。"""

    code = "invalid_amount"


class StockNotFoundError(EngineError):
    """找不到该标的。"""

    code = "stock_not_found"


class TPlusOneError(EngineError):
    """T+1 限制：今日买入的标的明日才能卖出。"""

    code = "t_plus_one_locked"
