"""Persistent world state: market + accounts, saved through PluginStore."""

from __future__ import annotations

import copy
import json
import time
from typing import Any, Dict, List, Optional

try:
    from .engine import TradeError, create_account, validate_account
    from .market import Market, bootstrap_market, resume_market
except ImportError:  # 平铺导入（本地测试、云端服务端）
    from engine import TradeError, create_account, validate_account  # type: ignore
    from market import Market, bootstrap_market, resume_market  # type: ignore

STATE_KEY = "moneta_fx.world.v1"
STATE_VERSION = 1
SPEED_CHOICES = (1, 5, 20, 100)


def _now_ms() -> int:
    return int(time.time() * 1000)


class World:
    """The shared fictional market and every account trading in it."""

    def __init__(self, initial_balance: float = 100000.0, seed: Optional[int] = None) -> None:
        self.market: Market = bootstrap_market(seed=seed if seed is not None else 735191)
        self.users: List[Dict[str, Any]] = []
        self.plans: List[Dict[str, Any]] = []
        self.audit: List[Dict[str, Any]] = []
        self.gaps: List[Dict[str, Any]] = []
        self.paused = False
        self.speed = 1
        self.revision = 1
        self.initial_balance = initial_balance
        self.funding: Dict[str, List[Dict[str, Any]]] = {"entries": []}
        self.users.append(self._new_user("developer", "开发者", "developer"))

    # ── users ───────────────────────────────────────────────────────────
    def _new_user(self, user_id: str, username: str, role: str) -> Dict[str, Any]:
        return {
            "id": user_id,
            "username": username,
            "role": role,
            "account": create_account("shared", self.market.time, self.initial_balance),
            "receipts": [],
            "activity": [],
            "createdAt": _now_ms(),
            "lastLoginAt": None,
            "lastSeenAt": None,
        }

    def register(self, username: str) -> Dict[str, Any]:

        if len(self.users) >= 1000:
            raise TradeError("当前测试服务器的账户数量已满")
        user = self._new_user(self._next_id(), username, "player")
        self.users.append(user)
        self.record(user, "register", f"创建普通账户 · 初始 {self.initial_balance:,.2f} USD 模拟资金")
        return user

    def _next_id(self) -> str:
        import uuid

        return str(uuid.uuid4())

    def find(self, user_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None
        return next((u for u in self.users if u["id"] == user_id), None)

    def player(self, user_id: str) -> Dict[str, Any]:

        user = self.find(user_id)
        if user is None or user["role"] != "player":
            raise TradeError("普通账户不存在", status=404)
        return user

    def developer(self) -> Optional[Dict[str, Any]]:
        return next((u for u in self.users if u["role"] == "developer"), None)

    # ── audit / activity ────────────────────────────────────────────────
    def record(self, user: Dict[str, Any], action: str, message: str, kind: str = "info", extra: Optional[Dict[str, Any]] = None) -> None:
        entry = {
            "id": (user["activity"][-1]["id"] + 1) if user["activity"] else 1,
            "time": self.market.time,
            "realTime": _now_ms(),
            "action": action,
            "message": message,
            "kind": kind,
        }
        if extra:
            entry.update(extra)
        user["activity"].append(entry)
        if len(user["activity"]) > 2000:
            del user["activity"][: len(user["activity"]) - 2000]

    def audit_log(self, actor: str, message: str) -> None:
        self.audit.append({"actor": actor, "time": self.market.time, "realTime": _now_ms(), "message": message})
        if len(self.audit) > 200:
            del self.audit[: len(self.audit) - 200]

    # ── persistence ─────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "market": self.market_state(),
            "paused": self.paused,
            "speed": self.speed,
            "plans": self.plans,
            "audit": self.audit,
            "gaps": self.gaps,
            "revision": self.revision,
            "initialBalance": self.initial_balance,
            "funding": self.funding,
            "users": self.users,
        }

    def net_funding(self, user_id: str) -> float:
        total = 0
        for entry in self.funding.get("entries", []):
            for change in entry.get("changes", []):
                if change.get("accountId") == user_id:
                    total += change.get("deltaCents", 0)
        return total / 100.0

    def market_state(self) -> Dict[str, Any]:
        market = self.market
        return {
            "seed": market.seed,
            "time": market.time,
            "values": market.values,
            "anchor": market.anchor,
            "baseline": market.baseline,
            "shock": market.shock,
            "volatility": market.volatility,
            "event": market.event,
            "bars": market.bars,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "World":

        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            raise TradeError("共享市场存档损坏，请检查备份", status=500)
        market_data = data.get("market")
        users = data.get("users")
        if not isinstance(market_data, dict) or not isinstance(users, list) or not isinstance(data.get("plans"), list):
            raise TradeError("共享市场存档损坏，请检查备份", status=500)
        if data.get("speed") not in SPEED_CHOICES or not isinstance(data.get("paused"), bool):
            raise TradeError("共享市场存档损坏，请检查备份", status=500)
        funding = data.get("funding")
        if funding is not None and not isinstance(funding, dict):
            raise TradeError("共享市场存档损坏，请检查备份", status=500)

        world = cls.__new__(cls)
        market = Market(seed=int(market_data.get("seed", 735191)), now_ms=int(market_data.get("time", _now_ms())))
        market.values = {k: float(v) for k, v in market_data.get("values", {}).items()}
        market.anchor = {k: float(v) for k, v in market_data.get("anchor", {}).items()}
        market.baseline = {k: float(v) for k, v in market_data.get("baseline", {}).items()}
        market.shock = {k: float(v) for k, v in market_data.get("shock", {}).items()}
        market.volatility = float(market_data.get("volatility", 1.0))
        market.event = str(market_data.get("event", "常态市场"))
        market.bars = {k: list(v) for k, v in market_data.get("bars", {}).items()}
        if market.values.get("USD") != 1 or not market.values.get("EUR"):
            raise TradeError("共享市场存档损坏，请检查备份", status=500)

        world.market = market
        world.paused = bool(data["paused"])
        world.speed = int(data["speed"])
        world.plans = list(data["plans"])
        world.audit = list(data.get("audit", []))
        world.gaps = list(data.get("gaps", []))
        world.revision = int(data.get("revision", 1))
        world.initial_balance = float(data.get("initialBalance", 100000.0))
        world.funding = {"entries": list((funding or {}).get("entries", []))}
        world.users = []
        for raw in users:
            if not isinstance(raw, dict) or raw.get("role") not in ("developer", "player"):
                raise TradeError("共享账户存档损坏", status=500)
            user = dict(raw)
            user["account"] = validate_account(copy.deepcopy(raw.get("account")), "shared")
            user.setdefault("receipts", [])
            user.setdefault("activity", [])
            user["activity"] = list(user["activity"])
            world.users.append(user)
        if not any(u["role"] == "developer" for u in world.users):
            world.users.append(world._new_user("developer", "开发者", "developer"))
        return world

    @classmethod
    def load(cls, raw: Any, initial_balance: float = 100000.0, seed: Optional[int] = None) -> "World":
        if not raw:
            return cls(initial_balance=initial_balance, seed=seed)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError) as error:

                raise TradeError("共享市场存档损坏，请检查备份", status=500) from error
        return cls.from_dict(raw)

    def resume(self, saved_at_ms: Optional[float]) -> Optional[Dict[str, Any]]:
        gap = resume_market(self.market, _now_ms(), saved_at_ms, self.users)
        if gap:
            self.gaps.append(gap)
            if len(self.gaps) > 50:
                del self.gaps[: len(self.gaps) - 50]
            self.revision += 1
            self.audit_log(
                "系统",
                f"停机补绘 · {gap['seconds'] / 3600:.2f} 小时 · 末端价格保持不变 · 未撮合或扣费",
            )
        return gap
