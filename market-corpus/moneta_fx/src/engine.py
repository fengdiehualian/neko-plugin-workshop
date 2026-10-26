"""FX trading engine: accounts, orders, margin, protection, liquidation.

Ported from MONETA FX ``public/js/engine.js``. All money is fictional.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    from .market import digits, is_valid_symbol, pip_size
except ImportError:  # 平铺导入（本地测试、云端服务端）
    from market import digits, is_valid_symbol, pip_size  # type: ignore

DAY_MS = 86400000
ROLLOVER_OFFSET_MS = 22 * 3600000
CONTRACT_SIZE = 100000

DEFAULT_SETTINGS: Dict[str, float] = {
    "leverage": 100,
    "spreadMultiplier": 1.0,
    "slippage": 0.3,
    "commission": 3.0,
    "swapLong": -6.0,
    "swapShort": 1.5,
}

LEVERAGE_CHOICES = (10, 20, 30, 50, 100, 200, 500)
JOURNAL_LIMIT = 1000
EQUITY_CURVE_LIMIT = 1500
EQUITY_SAMPLE_MS = 60000
MARGIN_CALL_LEVEL = 50.0


class TradeError(Exception):
    """User-facing trading failure, mirroring the JS ``fail`` messages."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _uid() -> str:
    return str(uuid.uuid4())


def rollover_slot(time_ms: int) -> int:
    return int(math.floor((time_ms - ROLLOVER_OFFSET_MS) / DAY_MS))


def create_account(mode: str = "shared", time_ms: Optional[int] = None, balance: float = 100000.0) -> Dict[str, Any]:
    now = int(time_ms if time_ms is not None else time.time() * 1000)
    label = {"offline": "离线沙盒", "shared": "共享虚构市场"}.get(mode, "在线模拟")
    return {
        "version": 1,
        "mode": mode,
        "initialBalance": balance,
        "balance": balance,
        "positions": [],
        "pending": [],
        "history": [],
        "journal": [{
            "time": now,
            "message": f"{label}账户已创建 · {balance:,.2f} USD 模拟资金",
            "kind": "info",
        }],
        "settings": dict(DEFAULT_SETTINGS),
        "lastRollover": rollover_slot(now),
        "equityCurve": [{"time": now, "value": balance}],
    }


def log(account: Dict[str, Any], time_ms: int, message: str, kind: str = "info") -> None:
    account["journal"].append({"time": int(time_ms), "message": message, "kind": kind})
    if len(account["journal"]) > JOURNAL_LIMIT:
        del account["journal"][: len(account["journal"]) - JOURNAL_LIMIT]


def check_quote(quote: Optional[Dict[str, Any]]) -> None:
    if not quote:
        raise TradeError("尚无可用报价")
    mid = quote.get("mid")
    bid = quote.get("bid")
    ask = quote.get("ask")
    if not _finite(mid) or not _finite(bid) or not _finite(ask) or bid <= 0 or ask < bid:
        raise TradeError("尚无可用报价")


def currency_to_usd(currency: str, quotes: Dict[str, Dict[str, Any]]) -> float:
    """BFS over available quotes to price any currency in USD."""
    if currency == "USD":
        return 1.0
    queue: List[Tuple[str, float]] = [(currency, 1.0)]
    visited = {currency}
    while queue:
        current, factor = queue.pop(0)
        for quote in quotes.values():
            mid = quote.get("mid")
            if not _finite(mid) or mid <= 0:
                continue
            base, term = quote["symbol"].split("/")
            if base == current:
                nxt = term
            elif term == current:
                nxt = base
            else:
                continue
            if nxt in visited:
                continue
            step = factor * (mid if base == current else 1.0 / mid)
            if nxt == "USD":
                return step
            visited.add(nxt)
            queue.append((nxt, step))
    raise TradeError(f"缺少 {currency} → USD 换算报价，无法成交")


def position_pnl(position: Dict[str, Any], quotes: Dict[str, Dict[str, Any]]) -> float:
    quote = quotes.get(position["symbol"])
    check_quote(quote)
    price = quote["bid"] if position["side"] == "buy" else quote["ask"]
    term = position["symbol"].split("/")[1]
    direction = 1 if position["side"] == "buy" else -1
    return (price - position["entry"]) * direction * position["lots"] * CONTRACT_SIZE * currency_to_usd(term, quotes)


def metrics(account: Dict[str, Any], quotes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    unrealized = 0.0
    complete = True
    for position in account["positions"]:
        try:
            unrealized += position_pnl(position, quotes)
        except TradeError:
            unrealized += position.get("lastPnl", 0.0)
            complete = False
    margin = sum(p.get("margin", 0.0) for p in account["positions"])
    equity = account["balance"] + unrealized
    # 空仓时保证金水平为无穷大；JSON 不支持 Infinity，用 None 表示（前端显示为 "—"）。
    margin_level = (equity / margin * 100.0) if margin else None
    return {
        "unrealized": unrealized,
        "equity": equity,
        "margin": margin,
        "freeMargin": equity - margin,
        "marginLevel": margin_level,
        "complete": complete,
    }


def _validate_lots(lots: Any) -> None:
    if not _finite(lots) or lots < 0.01 or lots > 1000 or abs(lots * 100 - round(lots * 100)) > 1e-6:
        raise TradeError("手数必须是 0.01 至 1000 之间的 0.01 倍数")


def _validate_protection(side: str, entry: float, sl: Any, tp: Any) -> None:
    if sl is not None:
        if not _finite(sl) or sl <= 0 or (sl >= entry if side == "buy" else sl <= entry):
            raise TradeError("止损价格方向不正确：多单止损低于买价，空单止损高于卖价")
    if tp is not None:
        if not _finite(tp) or tp <= 0 or (tp <= entry if side == "buy" else tp >= entry):
            raise TradeError("止盈价格方向不正确：多单止盈高于买价，空单止盈低于卖价")


def _execution_price(side: str, quote: Dict[str, Any], slippage: float) -> float:
    jitter = slippage * random.random() * pip_size(quote["symbol"])
    return quote["ask"] + jitter if side == "buy" else quote["bid"] - jitter


def estimate_order(account: Dict[str, Any], order: Dict[str, Any], quotes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    quote = quotes.get(order["symbol"])
    check_quote(quote)
    base, term = order["symbol"].split("/")
    conversion = currency_to_usd(term, quotes)
    margin = order["lots"] * CONTRACT_SIZE * currency_to_usd(base, quotes) / account["settings"]["leverage"]
    spread_cost = (quote["ask"] - quote["bid"]) * order["lots"] * CONTRACT_SIZE * conversion
    price = (quote["ask"] if order["side"] == "buy" else quote["bid"]) if order["type"] == "market" else order.get("price", quote["mid"])
    return {
        "margin": margin,
        "spreadCost": spread_cost,
        "commission": order["lots"] * account["settings"]["commission"],
        "risk": abs(price - order["sl"]) * order["lots"] * CONTRACT_SIZE * conversion if order.get("sl") else None,
        "reward": abs(order["tp"] - price) * order["lots"] * CONTRACT_SIZE * conversion if order.get("tp") else None,
    }


def place_order(account: Dict[str, Any], order: Dict[str, Any], quotes: Dict[str, Dict[str, Any]], now_ms: int) -> Dict[str, Any]:
    _validate_lots(order.get("lots"))
    if not is_valid_symbol(order.get("symbol")) or order.get("side") not in ("buy", "sell") or order.get("type") not in ("market", "limit", "stop"):
        raise TradeError("订单参数无效")
    quote = quotes.get(order["symbol"])
    check_quote(quote)
    base, term = order["symbol"].split("/")
    currency_to_usd(base, quotes)
    currency_to_usd(term, quotes)

    if order["type"] != "market":
        price = order.get("price")
        if not _finite(price) or price <= 0:
            raise TradeError("请输入有效的挂单价格")
        current = quote["ask"] if order["side"] == "buy" else quote["bid"]
        below = order["side"] == "buy" if order["type"] == "limit" else order["side"] == "sell"
        if (below and price >= current) or (not below and price <= current):
            raise TradeError("挂单价格方向不正确，请检查限价/突破类型")
        _validate_protection(order["side"], price, order.get("sl"), order.get("tp"))
        pending = {
            "id": _uid(),
            "symbol": order["symbol"],
            "side": order["side"],
            "type": order["type"],
            "lots": order["lots"],
            "price": price,
            "sl": order.get("sl"),
            "tp": order.get("tp"),
            "createdAt": int(now_ms),
        }
        account["pending"].append(pending)
        log(account, now_ms, f"挂单 {'买入' if order['side'] == 'buy' else '卖出'} {order['symbol']} {order['lots']:.2f} 手 @ {price:.{digits(order['symbol'])}f}", "trade")
        return pending

    return _open_position(account, order, quotes, now_ms)


def _open_position(account: Dict[str, Any], order: Dict[str, Any], quotes: Dict[str, Dict[str, Any]], now_ms: int, limit: Optional[float] = None) -> Dict[str, Any]:
    quote = quotes[order["symbol"]]
    check_quote(quote)
    base, term = order["symbol"].split("/")
    base_usd = currency_to_usd(base, quotes)
    term_usd = currency_to_usd(term, quotes)

    entry = _execution_price(order["side"], quote, account["settings"]["slippage"])
    if limit is not None:
        entry = min(entry, limit) if order["side"] == "buy" else max(entry, limit)
    if order["type"] == "market":
        _validate_protection(order["side"], entry, order.get("sl"), order.get("tp"))

    margin = order["lots"] * CONTRACT_SIZE * base_usd / account["settings"]["leverage"]
    commission = order["lots"] * account["settings"]["commission"]
    mark = quote["bid"] if order["side"] == "buy" else quote["ask"]
    cost = abs(entry - mark) * order["lots"] * CONTRACT_SIZE * term_usd
    current = metrics(account, quotes)
    if not current["complete"] or current["freeMargin"] + 1e-6 < margin + commission + cost:
        raise TradeError("可用保证金不足，降低手数或平掉部分持仓")

    position = {
        "id": _uid(),
        "symbol": order["symbol"],
        "side": order["side"],
        "lots": order["lots"],
        "entry": entry,
        "margin": margin,
        "openedAt": int(now_ms),
        "sl": order.get("sl"),
        "tp": order.get("tp"),
        "swap": 0.0,
        "openCommission": commission,
        "lastPnl": -cost,
    }
    account["balance"] -= commission
    account["positions"].append(position)
    log(account, now_ms, f"{'买入' if order['side'] == 'buy' else '卖出'} {order['symbol']} {order['lots']:.2f} 手，成交 {entry:.{digits(order['symbol'])}f}", "trade")
    return position


def close_position(account: Dict[str, Any], position_id: str, quotes: Dict[str, Dict[str, Any]], now_ms: int, lots: Any = None, reason: str = "手动平仓") -> Dict[str, Any]:
    position = next((p for p in account["positions"] if p["id"] == position_id), None)
    if position is None:
        raise TradeError("持仓不存在")
    amount = position["lots"] if lots is None else lots
    _validate_lots(amount)
    if amount > position["lots"] + 1e-6:
        raise TradeError("平仓手数超过持仓手数")

    quote = quotes.get(position["symbol"])
    check_quote(quote)
    term = position["symbol"].split("/")[1]
    conversion = currency_to_usd(term, quotes)
    exit_price = _execution_price("sell" if position["side"] == "buy" else "buy", quote, account["settings"]["slippage"])
    fraction = amount / position["lots"]
    close_commission = amount * account["settings"]["commission"]
    direction = 1 if position["side"] == "buy" else -1
    pnl = (exit_price - position["entry"]) * direction * amount * CONTRACT_SIZE * conversion

    closed = {
        **position,
        "id": _uid(),
        "lots": amount,
        "margin": position["margin"] * fraction,
        "swap": position["swap"] * fraction,
        "openCommission": position["openCommission"] * fraction,
        "exit": exit_price,
        "closedAt": int(now_ms),
        "pnl": pnl,
        "closeCommission": close_commission,
        "netPnl": pnl + position["swap"] * fraction - position["openCommission"] * fraction - close_commission,
        "reason": reason,
    }
    account["balance"] += pnl - close_commission
    account["history"].append(closed)

    if amount >= position["lots"] - 1e-6:
        account["positions"] = [p for p in account["positions"] if p["id"] != position_id]
    else:
        position["lots"] = round((position["lots"] - amount) * 100) / 100
        for key in ("margin", "swap", "openCommission", "lastPnl"):
            position[key] *= 1 - fraction

    log(account, now_ms, f"{reason} · {position['symbol']} {amount:.2f} 手 · 净盈亏 {closed['netPnl']:+.2f} USD", "warning" if reason == "强制平仓" else "trade")
    return closed


def modify_position(account: Dict[str, Any], position_id: str, quotes: Dict[str, Dict[str, Any]], sl: Any = None, tp: Any = None) -> None:
    position = next((p for p in account["positions"] if p["id"] == position_id), None)
    if position is None:
        raise TradeError("持仓不存在")
    check_quote(quotes.get(position["symbol"]))
    reference = quotes[position["symbol"]]["bid"] if position["side"] == "buy" else quotes[position["symbol"]]["ask"]
    _validate_protection(position["side"], reference, sl, tp)
    position["sl"] = sl
    position["tp"] = tp


def cancel_order(account: Dict[str, Any], order_id: str, now_ms: int) -> None:
    order = next((p for p in account["pending"] if p["id"] == order_id), None)
    if order is None:
        raise TradeError("挂单不存在")
    account["pending"] = [p for p in account["pending"] if p["id"] != order_id]
    log(account, now_ms, f"已撤销 {order['symbol']} 挂单")


def _process_protection(account: Dict[str, Any], position: Dict[str, Any], quotes: Dict[str, Dict[str, Any]], now_ms: int) -> None:
    quote = quotes.get(position["symbol"])
    if not quote:
        return
    try:
        check_quote(quote)
    except TradeError:
        return
    mark = quote["bid"] if position["side"] == "buy" else quote["ask"]
    if position.get("sl") is not None and (mark <= position["sl"] if position["side"] == "buy" else mark >= position["sl"]):
        close_position(account, position["id"], quotes, now_ms, None, "止损")
    elif position.get("tp") is not None and (mark >= position["tp"] if position["side"] == "buy" else mark <= position["tp"]):
        close_position(account, position["id"], quotes, now_ms, None, "止盈")


def process_account(account: Dict[str, Any], quotes: Dict[str, Dict[str, Any]], now_ms: int) -> None:
    """One full simulation step: marks, swaps, protection, pending triggers, liquidation."""
    for position in account["positions"]:
        try:
            position["lastPnl"] = position_pnl(position, quotes)
        except TradeError:
            pass

    slot = rollover_slot(now_ms)
    for day in range(account.get("lastRollover", slot) + 1, slot + 1):
        at = day * DAY_MS + ROLLOVER_OFFSET_MS
        weekday = time.gmtime(at / 1000).tm_wday  # Monday=0 … Sunday=6
        if weekday in (5, 6):
            continue
        for position in account["positions"]:
            if position["openedAt"] >= at:
                continue
            charge = (account["settings"]["swapLong"] if position["side"] == "buy" else account["settings"]["swapShort"]) * position["lots"] * (3 if weekday == 2 else 1)
            position["swap"] += charge
            account["balance"] += charge
            log(account, at, f"模拟隔夜结算 · {position['symbol']} {charge:+.2f} USD{' · 周三三倍' if weekday == 2 else ''}")
    account["lastRollover"] = max(slot, account.get("lastRollover", 0))

    for position in list(account["positions"]):
        _process_protection(account, position, quotes, now_ms)

    for pending in list(account["pending"]):
        quote = quotes.get(pending["symbol"])
        if not quote:
            continue
        try:
            check_quote(quote)
        except TradeError:
            continue
        mark = quote["ask"] if pending["side"] == "buy" else quote["bid"]
        price = pending["price"]
        if pending["type"] == "limit":
            trigger = mark <= price if pending["side"] == "buy" else mark >= price
        else:
            trigger = mark >= price if pending["side"] == "buy" else mark <= price
        if not trigger:
            continue
        try:
            position = _open_position(account, pending, quotes, now_ms, price if pending["type"] == "limit" else None)
            _process_protection(account, position, quotes, now_ms)
        except TradeError as error:
            log(account, now_ms, f"挂单取消 · {pending['symbol']} · {error}", "warning")
        account["pending"] = [p for p in account["pending"] if p["id"] != pending["id"]]

    # 有持仓就一定有保证金，marginLevel 不会是 None；这里显式判空以防存档异常。
    while account["positions"]:
        level = metrics(account, quotes)["marginLevel"]
        if level is None or level >= MARGIN_CALL_LEVEL:
            break
        ordered = sorted(account["positions"], key=lambda p: p.get("lastPnl", 0.0))
        closed_any = False
        for position in ordered:
            try:
                close_position(account, position["id"], quotes, now_ms, None, "强制平仓")
                closed_any = True
                break
            except TradeError:
                continue
        if not closed_any:
            break

    last = account["equityCurve"][-1] if account["equityCurve"] else None
    if last is None or now_ms - last["time"] >= EQUITY_SAMPLE_MS:
        account["equityCurve"].append({"time": int(now_ms), "value": metrics(account, quotes)["equity"]})
        if len(account["equityCurve"]) > EQUITY_CURVE_LIMIT:
            del account["equityCurve"][: len(account["equityCurve"]) - EQUITY_CURVE_LIMIT]


def validate_settings(settings: Any) -> bool:
    if not isinstance(settings, dict):
        return False
    leverage = settings.get("leverage")
    if leverage not in LEVERAGE_CHOICES:
        return False
    checks = (
        ("spreadMultiplier", 0.5, 10.0),
        ("slippage", 0.0, 20.0),
        ("commission", 0.0, 100.0),
    )
    for key, low, high in checks:
        value = settings.get(key)
        if not _finite(value) or value < low or value > high:
            return False
    for key in ("swapLong", "swapShort"):
        value = settings.get(key)
        if not _finite(value) or abs(value) > 100:
            return False
    return True


def validate_account(account: Any, mode: str) -> Dict[str, Any]:
    """Structural validation of a persisted account, mirroring ``validateBook``."""
    if not isinstance(account, dict):
        raise TradeError("存档账户或模式无效")
    if account.get("version") != 1 or account.get("mode") != mode:
        raise TradeError("存档账户或模式无效")
    for key in ("balance", "initialBalance"):
        if not _finite(account.get(key)):
            raise TradeError("存档账户或模式无效")
    if account["initialBalance"] <= 0:
        raise TradeError("存档账户或模式无效")
    if not validate_settings(account.get("settings")):
        raise TradeError("存档账户或模式无效")
    if not isinstance(account.get("lastRollover"), int) or abs(account["lastRollover"]) > 100000:
        raise TradeError("存档账户或模式无效")

    for key in ("positions", "pending", "history", "journal", "equityCurve"):
        if not isinstance(account.get(key), list) or len(account[key]) > 20000:
            raise TradeError("存档记录数量或结构不正确")

    for position in account["positions"] + account["history"]:
        if not isinstance(position, dict) or not isinstance(position.get("id"), str):
            raise TradeError("存档持仓参数不正确")
        if not is_valid_symbol(position.get("symbol")) or position.get("side") not in ("buy", "sell"):
            raise TradeError("存档持仓参数不正确")
        lots = position.get("lots")
        if not _finite(lots) or lots < 0.01 or lots > 1000 or abs(lots * 100 - round(lots * 100)) > 1e-6:
            raise TradeError("存档持仓参数不正确")
        for key in ("entry", "margin", "openedAt", "swap", "openCommission", "lastPnl"):
            if not _finite(position.get(key)) or (key in ("entry", "margin") and position[key] <= 0):
                raise TradeError("存档持仓参数不正确")
        for key in ("sl", "tp"):
            value = position.get(key)
            if value is not None and (not _finite(value) or value <= 0):
                raise TradeError("存档持仓参数不正确")

    for trade in account["history"]:
        for key in ("exit", "closedAt", "pnl", "netPnl", "closeCommission"):
            if not _finite(trade.get(key)) or (key == "exit" and trade[key] <= 0):
                raise TradeError("存档历史交易不正确")
        if not isinstance(trade.get("reason"), str):
            raise TradeError("存档历史交易不正确")

    for pending in account["pending"]:
        if not isinstance(pending, dict) or not isinstance(pending.get("id"), str):
            raise TradeError("存档挂单参数不正确")
        if not is_valid_symbol(pending.get("symbol")) or pending.get("side") not in ("buy", "sell") or pending.get("type") not in ("limit", "stop"):
            raise TradeError("存档挂单参数不正确")
        if not _finite(pending.get("lots")) or not 0.01 <= pending["lots"] <= 1000:
            raise TradeError("存档挂单参数不正确")
        for key in ("price", "createdAt"):
            if not _finite(pending.get(key)) or (key == "price" and pending[key] <= 0):
                raise TradeError("存档挂单参数不正确")
        for key in ("sl", "tp"):
            value = pending.get(key)
            if value is not None and (not _finite(value) or value <= 0):
                raise TradeError("存档挂单参数不正确")

    for entry in account["journal"]:
        if not isinstance(entry, dict) or not _finite(entry.get("time")) or not isinstance(entry.get("message"), str):
            raise TradeError("存档日志或净值不正确")
        if entry.get("kind") not in ("info", "trade", "warning"):
            raise TradeError("存档日志或净值不正确")

    for point in account["equityCurve"]:
        if not isinstance(point, dict) or not _finite(point.get("time")) or not _finite(point.get("value")):
            raise TradeError("存档日志或净值不正确")

    return account


def serialize_account(account: Dict[str, Any], indent: Optional[int] = None) -> str:
    import json

    return json.dumps(account, ensure_ascii=False, indent=indent, allow_nan=False)
