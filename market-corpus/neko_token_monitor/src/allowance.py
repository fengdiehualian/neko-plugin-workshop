"""Typed allowance observations, deterministic selection and a private local audit.

Credential slots identify configurations, not verified provider accounts. Unknown
accounts are never merged, even if their displayed balances happen to match.
"""

import copy
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

SCHEMA = 1
METHODS = {
    "deepseek": ("provider_api", "官方余额接口"),
    "codex": ("local_service", "本机 Codex 额度服务"),
    "desktop_quota": ("desktop_panel", "本机积分面板"),
    "app_bridge": ("application_bridge", "Harness 本机桥接"),
    "aliyun": ("official_cli", "阿里云官方 CLI"),
    "openai": ("cost_budget", "预算减组织费用"),
    "anthropic": ("cost_budget", "预算减组织费用"),
    "manual": ("manual", "手动预算"),
    "manual_quota": ("manual", "手动额度"),
    "jev": ("reported_usage", "本机已上报用量"),
    "custom": ("custom_api", "自定义余额接口"),
}
STATUSES = {
    "current": "当前读数",
    "stale": "上次读数",
    "manual": "手动记录",
    "unknown": "尚无读数",
    "invalid": "读数无效",
    "expired": "窗口已过期",
    "conflict": "来源冲突",
}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        if isinstance(value, str) and (not value.strip() or len(value) > 100):
            return None
        result = Decimal(str(value))
        return (
            result
            if result.is_finite() and result.copy_abs() <= Decimal("1e18") and result.as_tuple().exponent >= -18
            else None
        )
    except (InvalidOperation, ValueError):
        return None


def stamp(value):
    n = number(value)
    return float(n) if n is not None and 0 < n < Decimal("1e12") else None


def decimal_text(value):
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def normalize(state, profile, scope, *, now, page=False):
    """Pure conversion; emits only allowlisted facts, never raw config/errors."""
    cfg = state.get("config") or {}
    source = cfg.get("source", "")
    manual = source in ("manual", "manual_quota") or source == "desktop_quota" and cfg.get("quota_mode") == "manual"
    method, label = (
        ("browser_page", "DeepSeek 官方网页余额")
        if page
        else ("manual", "手动额度")
        if manual
        else METHODS.get(source, ("provider_api", "服务商余额接口"))
    )
    observed = stamp(cfg.get("recorded_at")) if manual else stamp(state.get("updated_at"))
    interval = number(cfg.get("interval"))
    ttl = 15 if page else float(interval * 2) if interval and 0 < interval <= 86400 else 600
    result = {
        "schema_version": SCHEMA,
        "profile": profile,
        "metric": "money_balance",
        "unit": cfg.get("currency") if cfg.get("currency") in ("CNY", "USD") else "CNY",
        "value": None,
        "total": None,
        "percent": None,
        "percent_basis": None,
        "observed_at": observed,
        "checked_at": stamp(state.get("checked_at")),
        "fresh_until": observed + ttl if observed and not manual else None,
        "expires_at": observed + ttl if observed and not manual else None,
        "method": method,
        "source_label": label,
        "adapter_revision": 1,
        "precision": "page_display" if page else "manual_entry" if manual else "source_reported",
        "account_scope": copy.deepcopy(scope),
        "status": "unknown",
        "windows": [],
        "warnings": [],
        "basis": "",
        "can_alert": False,
        "period_start": cfg.get("budget_start") if source in ("openai", "anthropic", "manual") else None,
    }
    value, total = None, None
    if source == "codex":
        result.update(metric="quota_remaining", unit="%")
        raw = state.get("codex_quota") or {}
        for index, w in enumerate(raw.get("windows", [])[:10]):
            if not isinstance(w, dict):
                continue
            n, reset = number(w.get("remaining")), stamp(w.get("resets_at"))
            if n is None or not 0 <= n <= 100:
                result["warnings"].append("invalid_window")
                continue
            minutes = w.get("minutes")
            result["windows"].append(
                {
                    "id": str(index),
                    "label": str(w.get("label") or "额度窗口")[:50],
                    "remaining": decimal_text(n),
                    "resets_at": reset,
                    "minutes": minutes if type(minutes) is int and minutes > 0 else None,
                }
            )
        active = [w for w in result["windows"] if w["resets_at"] and w["resets_at"] > now]
        if active:
            window = min(active, key=lambda w: Decimal(w["remaining"]))
            value, total = Decimal(window["remaining"]), Decimal(100)
            result["basis"] = "Codex " + window["label"] + "剩余"
            result["percent_basis"] = "provider_window"
            result["expires_at"] = min(result["expires_at"] or math.inf, min(w["resets_at"] for w in active))
        elif result["windows"]:
            result["status"] = "expired" if all(w["resets_at"] for w in result["windows"]) else "unknown"
            result["warnings"].append("window_time_unavailable" if result["status"] == "unknown" else "window_expired")
    elif source == "jev":
        usage = state.get("reported_usage") or {}
        inputs, outputs = number(usage.get("input_tokens")), number(usage.get("output_tokens"))
        if inputs is not None and outputs is not None and inputs >= 0 and outputs >= 0:
            value = inputs + outputs
        observed = stamp(usage.get("updated_at"))
        result.update(
            metric="usage_total",
            unit="token",
            observed_at=observed,
            expires_at=None,
            basis="仅本机已上报用量，不代表账户剩余",
        )
    elif source in ("manual_quota", "desktop_quota"):
        result.update(metric="quota_remaining", unit=cfg.get("quota_unit") if manual else "积分")
        raw_value = cfg.get("quota_remaining") if manual else (state.get("account_quota") or {}).get("remaining")
        value = number(raw_value)
        if value is not None and value < 0:
            value = None
        total = Decimal(100) if result["unit"] == "%" else number(cfg.get("quota_total"))
        result["basis"] = "手动记录，不自动扣减或重置" if manual else "本机面板显示积分"
        result["percent_basis"] = "manual_percent" if result["unit"] == "%" else "configured_total"
        if result["unit"] not in ("%", "token", "积分", "次"):
            value = None
        if raw_value is not None and value is None:
            result["status"] = "invalid"
    else:
        raw_value = (state.get("selected") or {}).get("balance")
        value = number(raw_value)
        budget = source in ("openai", "anthropic", "manual")
        result["metric"] = "budget_remaining" if budget else "money_balance"
        result["basis"] = (
            "自设预算减组织费用，并非官方余额"
            if source in ("openai", "anthropic")
            else "手动预算，不自动扣减"
            if manual
            else label
        )
        total = number(cfg.get("budget") if budget else cfg.get("balance_total"))
        result["percent_basis"] = "configured_budget" if budget else "configured_total"
        if source == "manual":
            value = number(cfg.get("budget"))
        if raw_value is not None and value is None:
            result["status"] = "invalid"
    if value is not None:
        result["value"] = decimal_text(value)
        if total is not None and total > 0 and result["metric"] != "usage_total":
            result["total"] = decimal_text(total)
            result["percent"] = float(max(Decimal(0), min(Decimal(100), value / total * 100)))
        result["status"] = "manual" if manual else "current"
        if result["metric"] == "usage_total" and (not observed or observed > now + 5):
            result["status"] = "stale"
            result["warnings"].append("usage_time_unknown")
        if not manual and result["metric"] != "usage_total" and (not observed or observed > now + 5):
            result.update(value=None, percent=None, status="invalid")
            result["warnings"].append("invalid_observation_time")
        elif (
            not manual
            and result["metric"] != "usage_total"
            and (state.get("error") or state.get("stale") or now >= result["expires_at"])
        ):
            result["status"] = "stale"
    if manual and not observed:
        result["warnings"].append("entry_time_unknown")
    if not scope.get("verified"):
        result["warnings"].append("account_identity_unverified")
    result["can_alert"] = (
        result["value"] is not None and result["status"] in ("current", "manual") and result["metric"] != "usage_total"
    )
    result["status_label"] = STATUSES[result["status"]]
    return result


def choose(candidates, *, now, preferred_scope=None):
    """Pure policy: keep configured account, never average values across sources."""
    rows = copy.deepcopy(candidates)
    if not rows:
        raise ValueError("需要至少一个额度来源")
    for row in rows:
        if row["windows"] and row["status"] != "invalid":
            active = [w for w in row["windows"] if w["resets_at"] and w["resets_at"] > now]
            if not active:
                row.update(
                    status="expired" if all(w["resets_at"] for w in row["windows"]) else "unknown",
                    value=None,
                    percent=None,
                    can_alert=False,
                )
            else:
                window = min(active, key=lambda w: Decimal(w["remaining"]))
                row.update(value=window["remaining"], percent=float(window["remaining"]))
                row["basis"] = "Codex " + window["label"] + "剩余"
                row["expires_at"] = min(row["fresh_until"] or math.inf, min(w["resets_at"] for w in active))
        if row["status"] == "current" and row.get("fresh_until") and now >= row["fresh_until"]:
            row.update(status="stale", can_alert=False)
    for row in rows:
        for window in row["windows"]:
            window["status"] = (
                "active"
                if window["resets_at"] and window["resets_at"] > now
                else "expired"
                if window["resets_at"]
                else "unknown"
            )
    selected = next((r for r in rows if r["account_scope"]["id"] == preferred_scope), None) or rows[0]
    selected["selection_rule"] = "configured_scope" if preferred_scope else "profile_source_order"
    selected["alternatives"] = []
    for other in rows:
        if other is selected:
            continue
        same = (
            selected["account_scope"].get("verified")
            and other["account_scope"].get("verified")
            and selected["account_scope"]["id"] == other["account_scope"]["id"]
        )
        comparable = (
            same
            and all(selected[k] == other[k] for k in ("metric", "unit", "period_start", "precision"))
            and selected.get("total") == other.get("total")
        )
        if selected["windows"] or other["windows"]:

            def signature(r):
                return sorted((str(w["minutes"]), w["resets_at"] or 0) for w in r["windows"])

            comparable = comparable and signature(selected) == signature(other)
        close = (
            selected.get("observed_at")
            and other.get("observed_at")
            and abs(selected["observed_at"] - other["observed_at"]) <= 15
        )
        conflict = (
            comparable
            and close
            and selected["status"] == other["status"] == "current"
            and selected["value"] != other["value"]
        )
        if conflict:
            selected.update(status="conflict", can_alert=False)
            selected["warnings"].append("comparable_source_conflict")
        selected["alternatives"].append(
            {
                "method": other["method"],
                "status": other["status"],
                "decision": "conflict" if conflict else "separate_account_scope" if not same else "not_selected",
            }
        )
    selected["status_label"] = STATUSES[selected["status"]]
    return selected


class AllowanceLedger:
    """Bounded atomic audit; a corrupt file is preserved and never overwritten."""

    def __init__(self, directory, clock=time.time):
        self.path = Path(directory) / "allowance-ledger.json"
        self.clock, self.lock = clock, threading.RLock()
        self.error = None
        self.blocked = False
        self.data = {"version": SCHEMA, "salt": secrets.token_hex(32), "observations": [], "decisions": []}
        if self.path.exists():
            try:
                if self.path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError()
                value = json.loads(self.path.read_text())
                if (
                    value.get("version") != SCHEMA
                    or not isinstance(value.get("salt"), str)
                    or len(value["salt"]) != 64
                    or not isinstance(value.get("observations"), list)
                    or not isinstance(value.get("decisions"), list)
                    or len(value["observations"]) > 1000
                    or len(value["decisions"]) > 1000
                ):
                    raise ValueError()
                for name, required in [
                    ("observations", {"id", "profile", "account_scope", "metric", "unit", "value", "observed_at"}),
                    ("decisions", {"profile", "account_scope", "status", "at"}),
                ]:
                    for row in value[name]:
                        if (
                            not isinstance(row, dict)
                            or not required <= row.keys()
                            or not isinstance(row.get("account_scope"), dict)
                            or not isinstance(row["account_scope"].get("id"), str)
                        ):
                            raise ValueError()
                self.data = value
            except (OSError, ValueError, TypeError, AttributeError):
                self.error = "读数记录文件不可用，原文件已保留"
                self.blocked = True

    def scope(self, profile, config, page=False):
        method = "browser_page" if page else config.get("source", "")
        credential = "" if page else config.get("api_key", "")
        private = [
            profile,
            method,
            credential,
            "" if page else config.get("endpoint", ""),
            "" if page else config.get("aliyun_profile", ""),
            "" if page else config.get("quota_mode", ""),
        ]
        opaque = hmac.new(self.data["salt"].encode(), json.dumps(private).encode(), hashlib.sha256).hexdigest()[:20]
        context = [
            config.get(k)
            for k in (
                "source",
                "value_path",
                "currency",
                "budget",
                "budget_start",
                "balance_total",
                "quota_mode",
                "quota_unit",
                "quota_total",
                "desktop_app",
            )
        ]
        context_id = hmac.new(self.data["salt"].encode(), json.dumps(context).encode(), hashlib.sha256).hexdigest()[:20]
        kind = (
            "credential_slot"
            if credential
            else "browser_login"
            if page
            else "manual_profile"
            if method in ("manual", "manual_quota")
            or method == "desktop_quota"
            and config.get("quota_mode") == "manual"
            else "local_login"
        )
        return {
            "id": opaque,
            "configuration_id": context_id,
            "kind": kind,
            "verified": False,
            "label": {
                "credential_slot": "已配置密钥档案",
                "browser_login": "浏览器登录会话",
                "manual_profile": "手动额度档案",
                "local_login": "本机登录会话",
            }[kind]
            + "（未核验账户身份）",
        }

    def observe(self, state, profile, config):
        with self.lock:
            reading = normalize(state, profile, self.scope(profile, config), now=self.clock())
            if reading["status"] == "invalid":
                previous = next(
                    (
                        r
                        for r in reversed(self.data["observations"])
                        if r["profile"] == profile
                        and r["account_scope"] == reading["account_scope"]
                        and r["metric"] == reading["metric"]
                        and r["unit"] == reading["unit"]
                    ),
                    None,
                )
                if previous:
                    reading.update({k: copy.deepcopy(v) for k, v in previous.items() if k != "id"})
                    reading.update(status="stale", can_alert=False)
                    reading["warnings"].append("invalid_reading_kept_previous")
            reading = choose([reading], now=self.clock())
            reading["stage"] = "observation"
            return self._commit(reading)

    def select(self, primary, page_state=None, prefer_primary=False):
        with self.lock:
            candidates = [primary]
            if page_state:
                candidates.append(
                    normalize(
                        page_state,
                        primary["profile"],
                        self.scope(primary["profile"], {}, page=True),
                        now=self.clock(),
                        page=True,
                    )
                )
            if page_state and not prefer_primary:
                candidates.reverse()
            reading = choose(
                candidates, now=self.clock(), preferred_scope=primary["account_scope"]["id"] if prefer_primary else None
            )
            reading["stage"] = "selection"
            return self._commit(reading)

    def _commit(self, reading):
        now = self.clock()
        clean = copy.deepcopy(reading)
        # Check attempts and decision reasons have separate records from value observations.
        fact = {
            k: clean[k]
            for k in (
                "schema_version",
                "profile",
                "metric",
                "unit",
                "value",
                "total",
                "percent",
                "percent_basis",
                "observed_at",
                "expires_at",
                "fresh_until",
                "method",
                "period_start",
                "source_label",
                "adapter_revision",
                "precision",
                "account_scope",
                "windows",
                "basis",
            )
        }
        identifier = hashlib.sha256(json.dumps(fact, sort_keys=True).encode()).hexdigest()[:24]
        changed = False
        if fact["value"] is not None and not any(r["id"] == identifier for r in self.data["observations"]):
            self.data["observations"].append(dict(fact, id=identifier))
            self.data["observations"] = self.data["observations"][-1000:]
            changed = True
        decision = {
            k: clean.get(k)
            for k in (
                "profile",
                "status",
                "method",
                "checked_at",
                "account_scope",
                "warnings",
                "alternatives",
                "stage",
                "selection_rule",
            )
        }
        decision["observation_id"] = identifier if fact["value"] is not None else None
        previous = next(
            (
                r
                for r in reversed(self.data["decisions"])
                if all(r.get(k) == decision[k] for k in ("profile", "method", "account_scope", "stage"))
            ),
            None,
        )
        if previous is None or any(previous.get(k) != v for k, v in decision.items()):
            self.data["decisions"].append(dict(decision, at=now))
            self.data["decisions"] = self.data["decisions"][-1000:]
            changed = True
        if (changed or self.error) and not self.blocked:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temp = self.path.with_suffix(".tmp")
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as stream:
                    json.dump(self.data, stream, ensure_ascii=False, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.path)
                self.error = None
            except OSError:
                self.error = "读数记录暂未写入磁盘"
        clean["observation_id"] = identifier if fact["value"] is not None else None
        clean["audit_saved"] = self.error is None
        clean["audit_error"] = self.error
        return clean

    def history(self, reading):
        with self.lock:
            scope = reading["account_scope"]["id"]

            def matches(r):
                return r.get("profile") == reading["profile"] and r.get("account_scope", {}).get("id") == scope

            return {
                "schema_version": SCHEMA,
                "reading": copy.deepcopy(reading),
                "observations": copy.deepcopy([r for r in self.data["observations"] if matches(r)][-30:][::-1]),
                "decisions": copy.deepcopy([r for r in self.data["decisions"] if matches(r)][-30:][::-1]),
                "error": self.error,
            }
