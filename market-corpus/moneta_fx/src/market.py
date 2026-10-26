"""Fictional FX market model.

Ported from MONETA FX ``public/js/market.js``. Everything here is invented
data: no real quotes, no real history. Deterministic given the seed so a
shared market reproduces the same path for every account.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, List, Optional

PIP_SIZE_JPY = 0.01
PIP_SIZE_STANDARD = 0.0001

CURRENCIES: Dict[str, Dict[str, Any]] = {
    "USD": {"name": "美元", "value": 1.0},
    "EUR": {"name": "欧元", "value": 1.085},
    "GBP": {"name": "英镑", "value": 1.275},
    "JPY": {"name": "日元", "value": 1 / 151.2},
    "CHF": {"name": "瑞郎", "value": 1.12},
    "AUD": {"name": "澳元", "value": 0.665},
    "CAD": {"name": "加元", "value": 0.73},
    "NZD": {"name": "纽元", "value": 0.615},
    "CNY": {"name": "人民币", "value": 0.139},
    "HKD": {"name": "港元", "value": 0.128},
    "SGD": {"name": "新加坡元", "value": 0.745},
    "NOK": {"name": "挪威克朗", "value": 0.093},
    "SEK": {"name": "瑞典克朗", "value": 0.095},
    "DKK": {"name": "丹麦克朗", "value": 0.146},
    "ZAR": {"name": "南非兰特", "value": 0.054},
    "TRY": {"name": "土耳其里拉", "value": 0.03},
    "MXN": {"name": "墨西哥比索", "value": 0.057},
    "PLN": {"name": "波兰兹罗提", "value": 0.25},
    "HUF": {"name": "匈牙利福林", "value": 0.0028},
    "CZK": {"name": "捷克克朗", "value": 0.043},
    "INR": {"name": "印度卢比", "value": 0.012},
    "KRW": {"name": "韩元", "value": 0.00074},
    "BRL": {"name": "巴西雷亚尔", "value": 0.198},
    "THB": {"name": "泰铢", "value": 0.027},
    "AED": {"name": "阿联酋迪拉姆", "value": 0.272},
    "SAR": {"name": "沙特里亚尔", "value": 0.267},
}

MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "USD/CAD", "NZD/USD"]

WATCHLIST = MAJORS + [
    "EUR/JPY", "GBP/JPY", "EUR/GBP", "EUR/CHF", "AUD/JPY", "GBP/CHF",
    "AUD/NZD", "EUR/AUD", "CAD/JPY", "USD/CNY", "USD/HKD", "USD/SGD",
    "USD/MXN", "USD/ZAR", "USD/TRY",
]

NON_EXOTIC = {"EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "HKD", "AED", "SAR"}

INTERVAL_MS = 60000
BAR_LIMIT = 2000

_EVENT_TYPES = ("dollar", "euro", "yen", "storm")


def offline_symbols() -> List[str]:
    """Every currency pair the fictional market can quote."""
    codes = list(CURRENCIES)
    pairs: List[str] = []
    for index, base in enumerate(codes):
        for term in codes[index + 1:]:
            pairs.append(f"{base}/{term}")
    return sorted(set(WATCHLIST) | set(pairs))


OFFLINE_SYMBOLS = offline_symbols()
OFFLINE_SYMBOL_SET = set(OFFLINE_SYMBOLS)


def symbol_name(symbol: str) -> str:
    return " / ".join(CURRENCIES.get(code, {}).get("name", code) for code in symbol.split("/"))


def is_valid_symbol(symbol: Any) -> bool:
    if not isinstance(symbol, str) or symbol.count("/") != 1:
        return False
    base, term = symbol.split("/")
    return len(base) == 3 and len(term) == 3 and base.isupper() and term.isupper()


def pip_size(symbol: str) -> float:
    return PIP_SIZE_JPY if symbol.endswith("/JPY") else PIP_SIZE_STANDARD


def digits(symbol: str) -> int:
    return 3 if symbol.endswith("/JPY") else 5


class Market:
    """Deterministic invented market state shared by every account."""

    def __init__(self, seed: int = 735191, now_ms: Optional[int] = None) -> None:
        self.seed = int(seed) & 0xFFFFFFFF or 1
        self.time = int(now_ms if now_ms is not None else time.time() * 1000)
        self.values: Dict[str, float] = {code: float(meta["value"]) for code, meta in CURRENCIES.items()}
        self.anchor: Dict[str, float] = dict(self.values)
        self.baseline: Dict[str, float] = dict(self.values)
        self.shock: Dict[str, float] = {}
        self.volatility = 1.0
        self.bars: Dict[str, List[Dict[str, Any]]] = {}
        self.event = "常态市场"

    # ── deterministic RNG ────────────────────────────────────────────────
    def _random(self) -> float:
        value = self.seed & 0xFFFFFFFF
        value ^= (value << 13) & 0xFFFFFFFF
        value ^= value >> 17
        value ^= (value << 5) & 0xFFFFFFFF
        self.seed = value & 0xFFFFFFFF
        return (self.seed + 1) / 4294967297.0

    def _gaussian(self) -> float:
        u = max(self._random(), 1e-10)
        v = self._random()
        return math.sqrt(-2.0 * math.log(u)) * math.cos(2.0 * math.pi * v)

    # ── evolution ────────────────────────────────────────────────────────
    def advance(self, seconds: float) -> None:
        if seconds <= 0:
            return
        root = math.sqrt(seconds)
        global_drift = self._gaussian() * 0.000008 * root * self.volatility
        for code in list(self.values):
            if code == "USD":
                continue
            exotic = code not in NON_EXOTIC
            noise = self._gaussian() * (0.000022 if exotic else 0.000014) * root * self.volatility
            reversion = math.log(self.anchor[code] / self.values[code]) * seconds / 200000.0
            shock = self.shock.get(code, 0.0)
            self.values[code] *= math.exp(noise + global_drift + reversion + shock * seconds)
            if abs(shock) > 1e-8:
                self.shock[code] = shock * math.exp(-seconds / 240.0)
        self.time += int(seconds * 1000)
        self.volatility = 1.0 + (self.volatility - 1.0) * math.exp(-seconds / 1800.0)

    def quotes(self, symbols: Optional[Iterable[str]] = None, spread_multiplier: float = 1.0) -> Dict[str, Dict[str, Any]]:
        wanted = list(symbols) if symbols is not None else list(WATCHLIST)
        result: Dict[str, Dict[str, Any]] = {}
        for symbol in wanted:
            base, term = symbol.split("/")
            base_value = self.values.get(base)
            term_value = self.values.get(term)
            if not base_value or not term_value:
                continue
            mid = base_value / term_value
            if symbol in MAJORS:
                normal = 1.2
            elif mid > 100 and term != "JPY":
                normal = 80.0
            elif symbol in WATCHLIST:
                normal = 2.4
            else:
                normal = 6.0
            half = min(mid * 0.004, pip_size(symbol) * normal * spread_multiplier * math.sqrt(self.volatility) / 2.0)
            result[symbol] = {
                "symbol": symbol,
                "mid": mid,
                "bid": mid - half,
                "ask": mid + half,
                "time": self.time,
                "marketOpen": True,
            }
        return result

    def shock_event(self, scenario: str) -> None:
        if scenario not in _EVENT_TYPES:
            raise ValueError("未知虚构事件")
        if scenario == "storm":
            self.volatility = 7.0
        else:
            self.volatility = 3.0
        if scenario == "dollar":
            for code in list(self.values):
                if code != "USD":
                    self.shock[code] = -0.000005
            self.event = "虚构事件 · 美元突然走强"
        elif scenario == "euro":
            self.shock["EUR"] = 0.000011
            self.event = "虚构事件 · 欧元利好"
        elif scenario == "yen":
            self.shock["JPY"] = 0.000014
            self.event = "虚构事件 · 日元干预冲击"
        else:
            self.event = "虚构事件 · 流动性风暴"
            for code in list(self.values):
                if code != "USD":
                    self.values[code] *= math.exp(self._gaussian() * 0.0025)


def record_quote(bars: List[Dict[str, Any]], quote: Dict[str, Any], interval_ms: int = INTERVAL_MS) -> None:
    """Fold one quote into one-minute bars, mirroring ``recordQuote``."""
    if not quote:
        return
    slot = int(quote["time"] // interval_ms) * interval_ms
    last = bars[-1] if bars else None
    if last is not None and last["time"] == slot:
        last["close"] = quote["mid"]
        last["high"] = max(last["high"], quote["mid"])
        last["low"] = min(last["low"], quote["mid"])
    elif last is None or slot > last["time"]:
        bars.append({"time": slot, "open": quote["mid"], "high": quote["mid"], "low": quote["mid"], "close": quote["mid"]})
    if len(bars) > BAR_LIMIT:
        del bars[: len(bars) - BAR_LIMIT]


def bootstrap_market(seed: int = 735191, now_ms: Optional[int] = None) -> Market:
    """Warm up six simulated hours so charts are never empty on first load."""
    market = Market(seed=seed, now_ms=(now_ms - 360 * INTERVAL_MS) if now_ms else None)
    for _ in range(360):
        for _ in range(4):
            market.advance(15)
            for quote in market.quotes().values():
                record_quote(market.bars.setdefault(quote["symbol"], []), quote)
    return market


def aggregate_bars(bars: List[Dict[str, Any]], minutes: int) -> List[Dict[str, Any]]:
    if minutes <= 1:
        return list(bars)
    interval = minutes * INTERVAL_MS
    result: List[Dict[str, Any]] = []
    for bar in bars:
        slot = int(bar["time"] // interval) * interval
        last = result[-1] if result else None
        if last is not None and last["time"] == slot:
            last["high"] = max(last["high"], bar["high"])
            last["low"] = min(last["low"], bar["low"])
            last["close"] = bar["close"]
            if bar.get("synthetic"):
                last["synthetic"] = True
        else:
            result.append(dict(bar, time=slot))
    return result


def resume_market(market: Market, now_ms: int, saved_at_ms: Optional[float], users: List[Any]) -> Optional[Dict[str, Any]]:
    """Backfill chart-only history after downtime; never trades or charges."""
    if not isinstance(saved_at_ms, (int, float)) or not isinstance(now_ms, int):
        return None
    elapsed = now_ms - saved_at_ms
    if elapsed < 1000:
        return None

    start = market.time
    end = start + int(elapsed)
    retained_from = max(start, end - (BAR_LIMIT - 2) * INTERVAL_MS)
    symbols = list(market.bars)
    seed = (market.seed ^ int(start) ^ int(end)) & 0xFFFFFFFF or 1

    def random() -> float:
        nonlocal seed
        value = seed
        value ^= (value << 13) & 0xFFFFFFFF
        value ^= value >> 17
        value ^= (value << 5) & 0xFFFFFFFF
        seed = value & 0xFFFFFFFF
        return (seed + 1) / 4294967297.0

    parameters = {
        code: {
            "phase": random() * math.pi * 2,
            "frequency": 8 + random() * min(180, elapsed / 60000),
            "amplitude": min(0.025, 0.00002 * math.sqrt(elapsed / 1000))
            * math.sqrt(market.volatility)
            * (0 if code == "USD" else 1),
        }
        for code in market.values
    }

    def bridge(when: int) -> Dict[str, float]:
        progress = (when - start) / elapsed
        envelope = math.sin(math.pi * progress)
        bridged: Dict[str, float] = {}
        for code, value in market.values.items():
            param = parameters[code]
            offset = (
                param["amplitude"]
                * envelope
                * (math.sin(progress * param["frequency"] + param["phase"])
                   + 0.35 * math.sin(progress * param["frequency"] * 2.7 + param["phase"] * 0.6))
            )
            bridged[code] = value if when in (start, end) else value * math.exp(offset)
        return bridged

    def sample(when: int) -> None:
        bridged = bridge(when)
        for symbol in symbols:
            base, term = symbol.split("/")
            mid = bridged[base] / bridged[term]
            quote = {"symbol": symbol, "mid": mid, "bid": mid, "ask": mid, "time": when, "marketOpen": True}
            record_quote(market.bars[symbol], quote)
            market.bars[symbol][-1]["synthetic"] = True

    sample(retained_from)
    when = int(retained_from // 15000) * 15000 + 15000
    while when < end:
        sample(when)
        when += 15000
    sample(end)
    market.time = end

    rollover = int((end - 22 * 3600000) // 86400000)
    for user in users:
        account = getattr(user, "account", None)
        if account is not None:
            account["lastRollover"] = max(account.get("lastRollover", 0), rollover)

    return {
        "from": start,
        "to": end,
        "realFrom": int(saved_at_ms),
        "realTo": int(now_ms),
        "retainedFrom": retained_from,
        "seconds": elapsed / 1000,
    }
