"""The shared market runtime: one process-wide world plus a per-user session.

Both front-ends drive this class:
  * the N.E.K.O. plugin binds a ``user_id`` per panel instance,
  * the standalone cloud server binds a ``user_id`` per browser session cookie.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

try:
    from . import auth as auth
    from .auth import (
        digest_password as digest_password,
    )
    from .auth import (
        validate_credentials as validate_credentials,
    )
    from .engine import (
        TradeError as TradeError,
    )
    from .engine import (
        cancel_order as cancel_order,
    )
    from .engine import (
        close_position as close_position,
    )
    from .engine import (
        create_account as create_account,
    )
    from .engine import (
        estimate_order as estimate_order,
    )
    from .engine import (
        log as log,
    )
    from .engine import (
        metrics as metrics,
    )
    from .engine import (
        modify_position as modify_position,
    )
    from .engine import (
        place_order as place_order,
    )
    from .engine import (
        process_account as process_account,
    )
    from .engine import (
        validate_account as validate_account,
    )
    from .market import (
        CURRENCIES as CURRENCIES,
    )
    from .market import (
        MAJORS as MAJORS,
    )
    from .market import (
        OFFLINE_SYMBOLS as OFFLINE_SYMBOLS,
    )
    from .market import (
        WATCHLIST as WATCHLIST,
    )
    from .market import (
        aggregate_bars as aggregate_bars,
    )
    from .market import (
        digits as digits,
    )
    from .market import (
        is_valid_symbol as is_valid_symbol,
    )
    from .market import (
        pip_size as pip_size,
    )
    from .market import (
        record_quote as record_quote,
    )
    from .market import (
        symbol_name as symbol_name,
    )
    from .store import SPEED_CHOICES as SPEED_CHOICES
    from .store import World as World
except ImportError:  # 平铺导入（本地测试、云端服务端）
    import auth as auth  # type: ignore
    from auth import (  # type: ignore
        digest_password as digest_password,
    )
    from auth import (
        validate_credentials as validate_credentials,
    )
    from engine import (  # type: ignore
        TradeError as TradeError,
    )
    from engine import (
        cancel_order as cancel_order,
    )
    from engine import (
        close_position as close_position,
    )
    from engine import (
        create_account as create_account,
    )
    from engine import (
        estimate_order as estimate_order,
    )
    from engine import (
        log as log,
    )
    from engine import (
        metrics as metrics,
    )
    from engine import (
        modify_position as modify_position,
    )
    from engine import (
        place_order as place_order,
    )
    from engine import (
        process_account as process_account,
    )
    from engine import (
        validate_account as validate_account,
    )
    from market import (  # type: ignore
        CURRENCIES as CURRENCIES,
    )
    from market import (
        MAJORS as MAJORS,
    )
    from market import (
        OFFLINE_SYMBOLS as OFFLINE_SYMBOLS,
    )
    from market import (
        WATCHLIST as WATCHLIST,
    )
    from market import (
        aggregate_bars as aggregate_bars,
    )
    from market import (
        digits as digits,
    )
    from market import (
        is_valid_symbol as is_valid_symbol,
    )
    from market import (
        pip_size as pip_size,
    )
    from market import (
        record_quote as record_quote,
    )
    from market import (
        symbol_name as symbol_name,
    )
    from store import SPEED_CHOICES as SPEED_CHOICES  # type: ignore
    from store import World as World

EVENT_TYPES = ("dollar", "euro", "yen", "storm")
MAX_POSITIONS_AND_PENDING = 200
MAX_QUOTES_PER_POLL = 12


class Session:
    """One authenticated viewer of the shared market.

    ``world`` 在云端模式下为 ``None``——插件的云端分支不经过本地 world，
    账户信息由服务端会话 cookie 决定。
    """

    def __init__(self, user_id: Optional[str], world: Optional[World]) -> None:
        self.user_id = user_id
        self.world = world
        self.selected = "EUR/USD"
        self.last_poll_ms = 0
        self.last_journal_len = 0
        self.pending_trade: Optional[Dict[str, Any]] = None
        self.trade_busy = False

    @property
    def user(self) -> Dict[str, Any]:
        if self.world is None:
            raise TradeError("云端模式下请通过服务端会话访问账户", status=401)
        user = self.world.find(self.user_id)
        if user is None:
            raise TradeError("请先登录虚构市场账户", status=401)
        return user

    @property
    def is_developer(self) -> bool:
        if self.world is None:
            return False
        user = self.world.find(self.user_id)
        return bool(user and user["role"] == "developer")

    def logout(self) -> None:
        self.user_id = None
        self.pending_trade = None
        if self.world is not None:
            self.world = self.world


class MarketRuntime:
    """Owns the world, the market clock and every trading mutation."""

    def __init__(self, initial_balance: float = 100000.0, seed: Optional[int] = None,
                 tick_interval_ms: int = 500, persist_interval_ms: int = 5000) -> None:
        self.initial_balance = initial_balance
        self.seed = seed
        self.tick_interval_ms = tick_interval_ms
        self.persist_interval_ms = persist_interval_ms
        self._lock = threading.RLock()
        self._world: Optional[World] = None
        self.sessions = auth.SessionStore()
        self.limiter = auth.LoginLimiter()
        self._last_tick_ms = 0
        self._last_persist_ms = 0
        self._dirty = True
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── lifecycle ───────────────────────────────────────────────────────
    @property
    def world(self) -> World:
        if self._world is None:
            self._world = World(initial_balance=self.initial_balance, seed=self.seed)
        return self._world

    def load(self, raw: Any, saved_at_ms: Optional[float] = None) -> None:
        world = World.load(raw, initial_balance=self.initial_balance, seed=self.seed)
        if saved_at_ms is not None:
            world.resume(saved_at_ms)
        self._world = world

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="moneta-market", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self.persist(force=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as error:  # pragma: no cover - defensive
                with self._lock:
                    self.world.paused = True
                print(f"[moneta_fx] 市场时钟异常：{error}")
            self._stop.wait(self.tick_interval_ms / 1000.0)

    # ── clock ───────────────────────────────────────────────────────────
    def tick(self) -> None:
        with self._lock:
            world = self.world
            current = auth.now_ms()
            seconds = min(2.0, max(0.0, (current - self._last_tick_ms) / 1000.0))
            self._last_tick_ms = current
            if seconds:
                self.advance(seconds * world.speed)
            if current - self._last_persist_ms > self.persist_interval_ms:
                self.persist()
            self.sessions.prune()
            self.limiter.prune()

    def advance(self, seconds: float) -> None:
        """Advance market time, run every trend plan, then settle all accounts."""
        world = self.world
        remaining = seconds
        while remaining > 1e-6:
            step = min(5.0, remaining)
            world.market.advance(step)
            for plan in list(world.plans):
                part = min(step, plan["remaining"])
                value = world.market.values[plan["currency"]]
                world.market.values[plan["currency"]] = value * (plan["target"] / value) ** (part / plan["remaining"])
                plan["remaining"] -= part
                if plan["remaining"] <= 1e-6:
                    world.market.values[plan["currency"]] = plan["target"]
                    world.market.anchor[plan["currency"]] = plan["target"]
            world.plans = [p for p in world.plans if p["remaining"] > 1e-6]
            self.settle()
            remaining -= step

    def settle(self) -> None:
        world = self.world
        quotes = self.all_quotes()
        for symbol, bars in world.market.bars.items():
            if quotes.get(symbol):

                record_quote(bars, quotes[symbol])
        for user in world.users:
            previous = user["account"]["journal"][-1] if user["account"]["journal"] else None
            process_account(user["account"], quotes, world.market.time)
            self._capture_journal(user, previous, "automatic")
        world.revision += 1
        self._dirty = True

    def _capture_journal(self, user: Dict[str, Any], previous: Optional[Dict[str, Any]], action: str) -> None:
        journal = user["account"]["journal"]
        start = (journal.index(previous) + 1) if previous in journal else 0
        for entry in journal[start:]:
            self.world.record(user, action, entry["message"], entry["kind"], {"time": entry["time"]})

    # ── quotes ──────────────────────────────────────────────────────────
    def all_quotes(self) -> Dict[str, Dict[str, Any]]:
        world = self.world
        return world.market.quotes(OFFLINE_SYMBOLS, 1.0)

    # ── persistence ─────────────────────────────────────────────────────
    def snapshot_state(self) -> Dict[str, Any]:
        import json

        return json.loads(json.dumps(self.world.to_dict(), ensure_ascii=False, default=str))

    def persist(self, force: bool = False) -> None:
        if not force and not self._dirty:
            return
        self._dirty = False
        self._last_persist_ms = auth.now_ms()

    # ── auth ────────────────────────────────────────────────────────────
    def login(self, username: str, password: str, address: str = "127.0.0.1") -> Dict[str, Any]:

        with self._lock:
            validate_credentials(username, password)
            normalized = username.lower()
            user = next((u for u in self.world.users if u["username"].lower() == normalized), None)
            if user is None or user["role"] != "player" or not auth.constant_time_equal(
                digest_password(password, user.get("salt", "")), user.get("passwordHash", "")
            ):
                self.limiter.denied(address)
                raise TradeError("用户名或密码不正确", status=401)
            self.limiter.clear(address)
            return self._start_session(user)

    def register(self, username: str, password: str, address: str = "127.0.0.1") -> Dict[str, Any]:

        with self._lock:
            validate_credentials(username, password)
            normalized = username.lower()
            if any(u["username"].lower() == normalized for u in self.world.users):
                raise TradeError("用户名已存在", status=409)
            user = self.world.register(username)
            salt = auth.new_id().replace("-", "")
            user["salt"] = salt
            user["passwordHash"] = digest_password(password, salt)
            self._dirty = True
            self.persist(force=True)
            self.limiter.clear(address)
            return self._start_session(user)

    def developer_login(self, key: str, developer_key: str) -> Dict[str, Any]:
        with self._lock:
            if not auth.constant_time_equal(key, developer_key):
                raise TradeError("开发者密钥不正确", status=403)
            user = self.world.developer()
            if user is None:
                raise TradeError("开发者账户不存在", status=404)
            return self._start_session(user)

    def _start_session(self, user: Dict[str, Any]) -> Dict[str, Any]:
        user["lastLoginAt"] = auth.now_ms()
        user["lastSeenAt"] = auth.now_ms()
        token = self.sessions.create(user["id"])
        self.world.record(user, "login", "登录共享账户")
        self._dirty = True
        self.persist(force=True)
        return {"token": token, "user": self.profile(user)}

    def logout(self, token: Optional[str]) -> None:
        user_id = self.sessions.user_id(token)
        user = self.world.find(user_id) if user_id else None
        if user:
            self.world.record(user, "logout", "退出共享账户")
            self._dirty = True
            self.persist(force=True)
        self.sessions.revoke(token)

    def logout_user(self, user_id: str) -> None:
        """Drop every session bound to ``user_id`` (plugin panels have no token)."""
        self.sessions.revoke_user(user_id)
        user = self.world.find(user_id)
        if user:
            self.world.record(user, "logout", "退出共享账户")
            self._dirty = True
            self.persist(force=True)

    def session_user(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        user_id = self.sessions.user_id(token)
        if not user_id:
            return None
        user = self.world.find(user_id)
        if user is None:
            return None
        user["lastSeenAt"] = auth.now_ms()
        self._dirty = True
        return user

    @staticmethod
    def profile(user: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not user:
            return None
        return {"id": user["id"], "username": user["username"], "role": user["role"]}

    # ── snapshot ────────────────────────────────────────────────────────
    def snapshot(self, user: Dict[str, Any], selected: str = "EUR/USD") -> Dict[str, Any]:
        with self._lock:
            if selected not in OFFLINE_SYMBOLS:
                raise TradeError("不支持的虚构货币对")
            quotes = self.all_quotes()
            bars = self.world.market.bars.setdefault(selected, [])
            if not bars:

                record_quote(bars, quotes[selected])
                self._dirty = True
            market = self.world.market
            return {
                "user": self.profile(user),
                "book": {
                    "version": 1,
                    "mode": "shared",
                    "account": user["account"],
                    "quotes": quotes,
                    "bars": {selected: bars},
                    "selected": selected,
                },
                "market": {
                    "time": market.time,
                    "paused": self.world.paused,
                    "speed": self.world.speed,
                    "event": market.event,
                    "revision": self.world.revision,
                    "gaps": self.world.gaps[-10:],
                },
                "baseline": market.baseline,
            }

    def admin_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            market = self.world.market
            return {
                "plans": self.world.plans,
                "audit": self.world.audit,
                "volatility": market.volatility,
                "paused": self.world.paused,
                "speed": self.world.speed,
                "time": market.time,
            }

    def account_summary(self, user: Dict[str, Any]) -> Dict[str, Any]:
        quotes = self.all_quotes()
        active_ids = self.sessions.active_user_ids()
        current = metrics(user["account"], quotes)
        funded = self.net_funding(user["id"])
        last_seen = user.get("lastSeenAt") or 0
        presence = "offline"
        if user["id"] in active_ids:
            presence = "online" if auth.now_ms() - last_seen < 15000 else "idle"
        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "createdAt": user.get("createdAt"),
            "lastLoginAt": user.get("lastLoginAt"),
            "lastSeenAt": user.get("lastSeenAt"),
            "presence": presence,
            "balance": user["account"]["balance"],
            "metrics": current,
            "positions": len(user["account"]["positions"]),
            "pending": len(user["account"]["pending"]),
            "trades": len(user["account"]["history"]),
            "netFunding": funded,
            "tradingPnl": current["equity"] - user["account"]["initialBalance"] - funded,
            "transferable": max(0.0, min(user["account"]["balance"], current["freeMargin"])),
        }

    def admin_accounts(self) -> Dict[str, Any]:
        with self._lock:
            players = [u for u in self.world.users if u["role"] == "player"]
            accounts = [self.account_summary(u) for u in players]
            totals = {
                "accounts": len(accounts),
                "online": sum(1 for a in accounts if a["presence"] == "online"),
                "balance": sum(a["balance"] for a in accounts),
                "equity": sum(a["metrics"]["equity"] for a in accounts),
                "positions": sum(a["positions"] for a in accounts),
                "netFunding": sum(a["netFunding"] for a in accounts),
            }
            return {"accounts": accounts, "totals": totals, "realTime": auth.now_ms(), "revision": self.world.revision}

    def admin_account_detail(self, user_id: str, limit: int = 100, before: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            user = self.world.player(user_id)
            activity = list(reversed(user["activity"]))
            if before is not None:
                activity = [e for e in activity if e["id"] < before]
            page = activity[:limit]
            return {
                "user": self.account_summary(user),
                "account": user["account"],
                "netFunding": self.net_funding(user["id"]),
                "activity": {
                    "entries": page,
                    "nextBefore": activity[limit]["id"] if len(activity) > limit else None,
                    "total": len(user["activity"]),
                },
                "realTime": auth.now_ms(),
                "revision": self.world.revision,
            }

    def net_funding(self, user_id: str) -> float:
        return self.world.net_funding(user_id)

    # ── funding ─────────────────────────────────────────────────────────
    def fund(self, actor: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:

        with self._lock:
            request_id = body.get("requestId")
            if not isinstance(request_id, str) or not auth.REQUEST_ID_PATTERN.match(request_id):
                raise TradeError("资金请求编号不正确")
            kind = body.get("kind")
            if kind not in ("credit", "debit", "transfer"):
                raise TradeError("资金操作类型不正确")
            amount_raw = body.get("amount")
            if not isinstance(amount_raw, (int, float, str)) or not auth.AMOUNT_PATTERN.match(str(amount_raw)):
                raise TradeError("金额须为正数，最多保留两位小数")
            amount_cents = round(float(amount_raw) * 100)
            if not 1 <= amount_cents <= 10000000000:
                raise TradeError("单笔金额范围为 0.01 至 100,000,000 USD")
            reason = body.get("reason")
            if not isinstance(reason, str) or not 2 <= len(reason.strip()) <= 200:
                raise TradeError("请填写 2–200 个字符的资金操作原因")
            reason = reason.strip()
            fingerprint = self._fingerprint({
                "kind": kind,
                "accountId": body.get("accountId"),
                "destinationId": body.get("destinationId") if kind == "transfer" else None,
                "amountCents": amount_cents,
                "reason": reason,
            })

            ledger = self.world.funding.setdefault("entries", [])
            previous = next((e for e in ledger if e.get("requestId") == request_id and e["actorId"] == actor["id"]), None)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise TradeError("同一资金编号不能用于不同指令", status=409)
                self.persist(force=True)
                return {"entry": self._public_funding(previous), "duplicate": True}

            source = self.world.player(body["accountId"])
            destination = self.world.player(body["destinationId"]) if kind == "transfer" else None
            if destination is not None and source["id"] == destination["id"]:
                raise TradeError("转出和收款账户不能相同")
            amount = amount_cents / 100
            quotes = self.all_quotes()
            if kind != "credit" and self.account_summary(source)["transferable"] < amount:
                raise TradeError("可调出资金不足，请降低金额或先释放保证金", status=409)

            changes = [{
                "accountId": source["id"],
                "username": source["username"],
                "deltaCents": amount_cents if kind == "credit" else -amount_cents,
            }]
            if destination:
                changes.append({"accountId": destination["id"], "username": destination["username"], "deltaCents": amount_cents})

            for change in changes:
                target = self.world.player(change["accountId"])
                change["before"] = target["account"]["balance"]
                change["after"] = change["before"] + change["deltaCents"] / 100
                if not -10000000000 < change["after"] < 10000000000:
                    raise TradeError("操作后余额超过测试账户的支持范围")

            backups = {u["id"]: (u["account"], len(u["activity"])) for u in self.world.users}
            audit_backup = list(self.world.audit)
            revision = self.world.revision
            ledger_length = len(ledger)

            entry = {
                "id": ledger_length + 1,
                "transactionId": auth.new_id(),
                "requestId": request_id,
                "fingerprint": fingerprint,
                "kind": kind,
                "amountCents": amount_cents,
                "reason": reason,
                "actorId": actor["id"],
                "actor": actor["username"],
                "time": self.world.market.time,
                "realTime": auth.now_ms(),
                "changes": changes,
            }
            try:
                label = {"credit": "入金", "debit": "扣款", "transfer": "转账"}[kind]
                for change in changes:
                    target = self.world.player(change["accountId"])
                    target["account"]["balance"] = change["after"]
                    message = f"管理员{label} · {'+' if change['deltaCents'] >= 0 else '-'}{amount:.2f} USD · {reason}"
                    log(target["account"], self.world.market.time, message)
                    target["account"]["equityCurve"].append({"time": self.world.market.time, "value": metrics(target["account"], quotes)["equity"]})
                    if len(target["account"]["equityCurve"]) > 1500:
                        del target["account"]["equityCurve"][:1]
                    self.world.record(target, "funding", message, "info", {"fundingId": entry["transactionId"], "deltaCents": change["deltaCents"], "actor": actor["username"]})
                ledger.append(entry)
                self.world.audit_log(actor["username"], f"{label} · {source['username']}" + (f" → {destination['username']}" if destination else "") + f" · {amount:.2f} USD · {reason}")
                self.world.revision += 1
                self._dirty = True
                self.persist(force=True)
            except Exception:
                for user_id, (account, activity_len) in backups.items():
                    target = self.world.find(user_id)
                    if target:
                        target["account"] = account
                        del target["activity"][activity_len:]
                del ledger[ledger_length:]
                self.world.audit[:] = audit_backup
                self.world.revision = revision
                raise
            return {"entry": self._public_funding(entry), "duplicate": False}

    def admin_ledger(self, account_id: Optional[str] = None, limit: int = 100, before: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            if account_id:
                self.world.player(account_id)
            entries = self.world.funding.get("entries", [])
            if account_id:
                entries = [e for e in entries if any(c["accountId"] == account_id for c in e["changes"])]
            ordered = list(reversed(entries))
            if before is not None:
                ordered = [e for e in ordered if e["id"] < before]
            page = ordered[:limit]
            return {
                "entries": [self._public_funding(e) for e in page],
                "nextBefore": ordered[limit]["id"] if len(ordered) > limit else None,
                "total": len(entries),
            }

    @staticmethod
    def _public_funding(entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": entry["id"],
            "transactionId": entry["transactionId"],
            "requestId": entry.get("requestId"),
            "kind": entry["kind"],
            "amountCents": entry["amountCents"],
            "reason": entry["reason"],
            "actorId": entry["actorId"],
            "actor": entry["actor"],
            "time": entry["time"],
            "realTime": entry["realTime"],
            "changes": entry["changes"],
        }

    # ── trading ─────────────────────────────────────────────────────────
    def trade(self, user: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if body.get("accountId") not in (None, user["id"]):
                raise TradeError("登录账户已变化，请重新确认交易", status=409)
            request_id = body.get("requestId")
            if not isinstance(request_id, str) or not auth.REQUEST_ID_PATTERN.match(request_id):
                raise TradeError("交易请求编号不正确")

            receipts = user.setdefault("receipts", [])
            receipt = next((r for r in receipts if r["id"] == request_id), None)
            fingerprint = self._fingerprint(body)
            if receipt:
                if receipt["fingerprint"] != fingerprint:
                    raise TradeError("同一交易编号不能用于不同指令", status=409)
                self.persist(force=True)
                return self.snapshot(user)

            backup = _clone(user["account"])
            previous = user["account"]["journal"][-1] if user["account"]["journal"] else None
            quotes = self.all_quotes()
            now = self.world.market.time
            try:
                action = body.get("action")
                if action == "order":
                    order = body.get("order") or {}
                    if order.get("symbol") not in OFFLINE_SYMBOLS or order.get("side") not in ("buy", "sell") or order.get("type") not in ("market", "limit", "stop"):
                        raise TradeError("订单参数不正确")
                    if len(user["account"]["positions"]) + len(user["account"]["pending"]) >= MAX_POSITIONS_AND_PENDING:
                        raise TradeError("每个账户最多同时保留 200 笔持仓和挂单")
                    place_order(user["account"], order, quotes, now)
                elif action == "close":
                    close_position(user["account"], body["id"], quotes, now, body.get("lots"))
                elif action == "modify":
                    modify_position(user["account"], body["id"], quotes, body.get("sl"), body.get("tp"))
                    log(user["account"], now, "修改保护单 · " + str(body.get("id")) + " · SL " + str(body.get("sl") or "未设置") + " · TP " + str(body.get("tp") or "未设置"))
                elif action == "cancel":
                    cancel_order(user["account"], body["id"], now)
                elif action == "closeAll":
                    for position in list(user["account"]["positions"]):
                        close_position(user["account"], position["id"], quotes, now)
                else:
                    raise TradeError("未知交易操作")
                process_account(user["account"], quotes, now)
            except TradeError as error:
                user["account"] = backup
                self.world.record(user, "rejected", f"操作被拒绝 · {str(body.get('action'))[:30]} · {error}", "warning")
                self.persist(force=True)
                raise

            messages = self._journal_since(user, previous)
            order = body.get("order") or {}
            details = (
                {"symbol": order.get("symbol"), "side": order.get("side"), "type": order.get("type"), "lots": order.get("lots"), "price": order.get("price"), "sl": order.get("sl"), "tp": order.get("tp")}
                if body.get("action") == "order"
                else {"positionId": body.get("id"), "lots": body.get("lots"), "sl": body.get("sl"), "tp": body.get("tp")}
            )
            self.world.record(user, str(body.get("action")), "；".join(messages) or f"交易操作 · {body.get('action')}", "trade", {"details": details})
            receipts.append({"id": request_id, "fingerprint": fingerprint})
            del receipts[:-200]
            self.world.revision += 1
            self._dirty = True
            self.persist(force=True)
            return self.snapshot(user)

    @staticmethod
    def _fingerprint(body: Dict[str, Any]) -> str:
        import json

        return json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)

    @staticmethod
    def _journal_since(user: Dict[str, Any], previous: Optional[Dict[str, Any]]) -> List[str]:
        journal = user["account"]["journal"]
        if previous is None:
            return [e["message"] for e in journal]
        try:
            start = journal.index(previous) + 1
        except ValueError:
            start = 0
        return [e["message"] for e in journal[start:]]

    # ── market control (developer) ──────────────────────────────────────
    def control(self, user: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            if user["role"] != "developer":
                raise TradeError("只有开发者可以操控行情", status=403)
            world = self.world
            kind = body.get("kind")
            message: str
            if kind == "clock":
                speed, paused = body.get("speed"), body.get("paused")
                if speed is not None and speed not in SPEED_CHOICES:
                    raise TradeError("时钟参数不正确")
                if paused is not None and not isinstance(paused, bool):
                    raise TradeError("时钟参数不正确")
                if speed is None and paused is None:
                    raise TradeError("时钟参数不正确")
                if speed is not None:
                    world.speed = speed
                if paused is not None:
                    world.paused = paused
                self._last_tick_ms = auth.now_ms()
                message = f"市场时钟 · {'暂停' if world.paused else '运行'} · {world.speed}×"
            elif kind == "price":
                symbol = body.get("symbol")
                price = body.get("price")
                if symbol not in OFFLINE_SYMBOLS or not isinstance(price, (int, float)) or price <= 0:
                    raise TradeError("价格或货币对不正确")
                base, term = symbol.split("/")
                current = world.market.values[base] / world.market.values[term]
                factor = price / current
                if not 0.5 <= factor <= 2:
                    raise TradeError("单次改价需在当前价格的 50% 至 200% 之间")
                currency = term if base == "USD" else base
                world.market.values[currency] = world.market.values[base] / price if base == "USD" else price * world.market.values[term]
                world.market.anchor[currency] = world.market.values[currency]
                world.plans = [p for p in world.plans if p["currency"] != currency]
                world.market.event = "开发者调整虚构行情"
                message = f"直接改价 · {symbol} {current:.6f} → {price}"
            elif kind == "trend":

                currency = body.get("currency")
                percent = body.get("percent")
                seconds = body.get("seconds")
                if currency not in CURRENCIES or currency == "USD":
                    raise TradeError("走势参数不正确：货币需为非 USD 的主要货币")
                if not isinstance(percent, (int, float)) or abs(percent) > 30:
                    raise TradeError("走势参数不正确：幅度 ±30%")
                if not isinstance(seconds, (int, float)) or not 5 <= seconds <= 86400:
                    raise TradeError("走势参数不正确：时长 5 秒至 24 小时")
                world.plans = [p for p in world.plans if p["currency"] != currency]
                world.plans.append({
                    "currency": currency,
                    "target": world.market.values[currency] * (1 + percent / 100),
                    "remaining": seconds,
                })
                world.market.event = "虚构市场情境运行中"
                message = f"编排走势 · {currency} {percent:+.1f}% / {seconds:.0f} 模拟秒"
            elif kind == "cancel-trends":
                world.plans = []
                world.market.event = "常态市场"
                message = "已取消所有未来走势编排"
            elif kind == "volatility":
                value = body.get("value")
                if not isinstance(value, (int, float)) or not 1 <= value <= 20:
                    raise TradeError("波动强度应为 1 至 20")
                world.market.volatility = value
                message = f"波动强度 → {value}×"
            elif kind == "event":
                event = body.get("event")
                if event not in EVENT_TYPES:
                    raise TradeError("未知虚构事件")
                world.market.shock_event(event)
                message = world.market.event
            else:
                raise TradeError("未知行情控制操作")

            world.audit_log(user["username"], message)
            self.settle()
            self._dirty = True
            self.persist(force=True)
            return self.snapshot(user)

    # ── static reference data ───────────────────────────────────────────
    @staticmethod
    def reference() -> Dict[str, Any]:

        return {
            "symbols": OFFLINE_SYMBOLS,
            "watchlist": WATCHLIST,
            "majors": MAJORS,
            "currencies": {code: meta["name"] for code, meta in CURRENCIES.items()},
            "count": len(OFFLINE_SYMBOLS),
        }


def _clone(value: Any) -> Any:
    import copy

    return copy.deepcopy(value)
