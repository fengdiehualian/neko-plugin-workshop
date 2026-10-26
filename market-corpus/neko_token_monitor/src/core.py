"""Official balance reads only. No chat requests, no guessed token limits."""

import datetime as dt
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .codex_quota import read_quota
from .desktop_quota import NavigationBusy
from .desktop_quota import read_quota as read_desktop_quota
from .harness_bridge import read_balance as read_harness_balance
from .providers import query, validate_endpoint
from .usage import add_usage


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def query_balance(key):
    request = urllib.request.Request(
        "https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + key}
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=12) as response:
            return json.loads(response.read(65537))
    except urllib.error.HTTPError as exc:
        raise ValueError(
            {401: "密钥无效，请重新填写", 403: "账户无权查询余额", 429: "查询过于频繁，请稍后再试"}.get(
                exc.code, "余额接口暂不可用"
            )
        ) from None
    except Exception:
        raise ValueError("未能连接 DeepSeek，保留上次结果") from None


def parse_balance(payload):
    if type(payload.get("is_available")) is not bool:
        raise ValueError("余额接口返回了无法识别的数据")
    rows = []
    try:
        for item in payload["balance_infos"]:
            currency = item["currency"]
            if isinstance(item["total_balance"], bool):
                raise ValueError()
            amount = Decimal(item["total_balance"])
            if currency not in ("CNY", "USD") or not amount.is_finite():
                raise ValueError()
            if any(row["currency"] == currency for row in rows):
                raise ValueError()
            rows.append({"currency": currency, "balance": str(amount)})
        if not rows:
            raise ValueError()
    except (KeyError, TypeError, InvalidOperation, ValueError):
        raise ValueError("余额接口返回了无法识别的数据") from None
    return rows


class Monitor:
    def __init__(self, directory, fetch=query_balance, announce=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.fetch, self.announce = fetch, announce
        self.stop = threading.Event()
        self.thread = None
        self.last_attempt = 0
        self.last_alert = 0
        self.trae_watcher = None
        self.allowance_ledger = None
        self.allowance_profile = None
        self.config = {
            "recorded_at": None,
            "source": "deepseek",
            "model": "",
            "quota_remaining": None,
            "quota_total": None,
            "quota_unit": "次",
            "quota_reset": "",
            "desktop_app": "trae",
            "quota_mode": "auto",
            "aliyun_profile": "",
            "budget": 0,
            "balance_total": None,
            "budget_start": dt.date.today().replace(day=1).isoformat(),
            "endpoint": "",
            "value_path": "balance",
            "api_key": "",
            "price_per_million": None,
            "currency": "CNY",
            "low_balance": 2.0,
            "interval": 300,
            "alerts": True,
        }
        self.config.update(self.read("config.json", {}))
        self.state = self.read("state.json", {})

    def read(self, name, default):
        try:
            value = json.loads((self.directory / name).read_text())
            return value if isinstance(value, dict) else default
        except (OSError, ValueError):
            return default

    def write(self, name, value):
        path = self.directory / name
        temp = path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
        os.chmod(temp, 0o600)
        temp.replace(path)

    def configure(self, values):
        with self.lock:
            cfg = dict(self.config)
            source = values.get("source", cfg["source"])
            if source not in (
                "deepseek",
                "openai",
                "anthropic",
                "custom",
                "manual",
                "jev",
                "codex",
                "openrouter",
                "siliconflow",
                "manual_quota",
                "desktop_quota",
                "moonshot_cn",
                "moonshot_global",
                "aliyun",
                "app_bridge",
            ):
                raise ValueError("查询方式无效")
            # Never reuse a previous provider's secret at a new destination.
            if source != cfg["source"] or values.get("endpoint", cfg["endpoint"]) != cfg["endpoint"]:
                cfg["api_key"] = ""
            cfg["source"] = source
            if source == "app_bridge":
                cfg["api_key"] = ""
            for field in ("model", "endpoint", "value_path", "budget_start", "aliyun_profile"):
                if field in values:
                    if not isinstance(values[field], str) or len(values[field]) > 500:
                        raise ValueError("设置格式无效")
                    cfg[field] = values[field].strip()
            if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in cfg["aliyun_profile"]) or len(cfg["aliyun_profile"]) > 80:
                raise ValueError("阿里云 CLI 配置名无效")
            for field, choices in (("desktop_app", ("trae", "workbuddy")), ("quota_mode", ("auto", "manual"))):
                if field in values:
                    if values[field] not in choices:
                        raise ValueError("本机额度读取设置无效")
                    cfg[field] = values[field]
            for field in ("quota_remaining", "quota_total", "balance_total"):
                if field in values:
                    raw = values[field]
                    if isinstance(raw, bool):
                        raise ValueError("额度数字无效")
                    number = None if raw in (None, "") else float(raw)
                    if number is not None and (not math.isfinite(number) or not 0 <= number <= 1e12):
                        raise ValueError("额度数字无效")
                    cfg[field] = number
            if "quota_unit" in values:
                if values["quota_unit"] not in ("次", "token", "%", "积分"):
                    raise ValueError("额度单位无效")
                cfg["quota_unit"] = values["quota_unit"]
            if "quota_reset" in values:
                value = values["quota_reset"]
                if not isinstance(value, str) or len(value) > 80:
                    raise ValueError("重置时间无效")
                cfg["quota_reset"] = value
            remaining, total = cfg["quota_remaining"], cfg["quota_total"]
            if not (source == "desktop_quota" and cfg["quota_mode"] == "auto") and remaining is not None and total is not None and remaining > total:
                raise ValueError("剩余额度不能超过总额度")
            if cfg["quota_unit"] == "%" and remaining is not None and remaining > 100:
                raise ValueError("百分比不能超过 100")
            dt.date.fromisoformat(cfg["budget_start"])
            if source == "custom":
                validate_endpoint(cfg["endpoint"])
                if not cfg["value_path"] or any(
                    not part or not all(c.isalnum() or c == "_" for c in part) for part in cfg["value_path"].split(".")
                ):
                    raise ValueError("余额字段路径无效")
            if source != "app_bridge" and "api_key" in values and values["api_key"]:
                key = values["api_key"].strip()
                if not 8 <= len(key) <= 512 or not key.isascii() or any(c.isspace() for c in key):
                    raise ValueError("API Key 格式不正确")
                cfg["api_key"] = key
            for field, minimum, maximum in [
                ("budget", 0, 1000000000),
                ("price_per_million", 0.000001, 1000000),
                ("low_balance", 0, 1000000),
                ("interval", 60, 86400),
            ]:
                if field in values:
                    if field == "price_per_million" and values[field] in (None, ""):
                        cfg[field] = None
                        continue
                    number = float(values[field])
                    if not math.isfinite(number) or not minimum <= number <= maximum:
                        raise ValueError("价格、阈值或刷新间隔超出允许范围")
                    cfg[field] = number
            if "currency" in values:
                if values["currency"] not in ("CNY", "USD"):
                    raise ValueError("请选择 CNY 或 USD")
                cfg["currency"] = values["currency"]
            if "alerts" in values:
                if type(values["alerts"]) is not bool:
                    raise ValueError("提醒设置无效")
                cfg["alerts"] = values["alerts"]
            if source in ("openai", "anthropic", "openrouter", "moonshot_global"):
                cfg["currency"] = "USD"
            if source in ("siliconflow", "moonshot_cn"):
                cfg["currency"] = "CNY"
            changed_key = any(
                cfg[k] != self.config[k]
                for k in (
                    "source",
                    "endpoint",
                    "value_path",
                    "api_key",
                    "currency",
                    "budget",
                    "budget_start",
                    "quota_remaining",
                    "quota_total",
                    "quota_unit",
                    "quota_reset",
                    "desktop_app",
                    "quota_mode",
                    "aliyun_profile",
                )
            )
            manual = source in ("manual", "manual_quota") or source == "desktop_quota" and cfg["quota_mode"] == "manual"
            entry_fields = ('budget', 'budget_start') if source == 'manual' else ('quota_remaining', 'quota_total', 'quota_unit', 'quota_reset')
            if manual and (changed_key or any(field in values for field in entry_fields)):
                cfg["recorded_at"] = time.time()
            self.write("config.json", cfg)
            self.config = cfg
            if changed_key:
                self.state = {}
                self.last_attempt = self.last_alert = 0
                self.write("state.json", {})
        return self.snapshot()

    def record_usage(self, item):
        with self.lock:
            data = add_usage(self.read("usage-jev.json", {}), item)
            self.write("usage-jev.json", data)
            return {k: v for k, v in data.items() if k != "seen"}

    def snapshot(self):
        with self.lock:
            cfg = {k: v for k, v in self.config.items() if k != "api_key"}
            row = next((r for r in self.state.get("balances", []) if r["currency"] == cfg["currency"]), None)
            updated = self.state.get("updated_at")
            if cfg['source'] in ('manual', 'manual_quota') or cfg['source'] == 'desktop_quota' and cfg['quota_mode'] == 'manual':
                updated = cfg.get('recorded_at')
            error = self.state.get("error")
            current = bool(updated and not error and time.time() - updated <= cfg["interval"] * 2)
            estimate = None
            if row and cfg["price_per_million"] and current:
                estimate = int(
                    max(Decimal(row["balance"]), Decimal(0)) * 1000000 / Decimal(str(cfg["price_per_million"]))
                )
            usage = {k: v for k, v in self.read("usage-jev.json", {}).items() if k != "seen"}
            result = {
                "checked_at": self.state.get("checked_at"),
                "codex_quota": self.state.get("codex_quota"),
                "account_quota": self.state.get("account_quota"),
                "reported_usage": usage,
                "config": cfg,
                "basis": self.state.get("basis", "官方账户余额"),
                "configured": self.config["source"] in ("manual", "jev", "codex", "manual_quota", "desktop_quota", "aliyun", "app_bridge")
                or bool(self.config["api_key"]),
                "balances": self.state.get("balances", []),
                "wallet_breakdown": self.state.get("wallet_breakdown"),
                "selected": row,
                "updated_at": updated,
                "error": error,
                "stale": not current,
                "is_available": self.state.get("is_available") if current else None,
                "estimated_tokens": estimate,
                "estimate_only": True,
                "observed_net_change": self.state.get("daily", {}).get(cfg["currency"])
                if self.state.get("day") == dt.date.today().isoformat()
                else None,
            }
            if self.allowance_ledger is not None:
                result["allowance"] = self.allowance_ledger.observe(result, self.allowance_profile, self.config)
            return result

    def refresh(self, *, app_open=False):
        # Serialize refreshes and key changes; never send a stale key's results to a new account.
        with self.lock:
            if self.config["source"] == "codex":
                if self.last_attempt and time.monotonic() - self.last_attempt < 60:
                    return self.snapshot()
                self.last_attempt = time.monotonic()
                self.state["checked_at"] = time.time()
                try:
                    quota = read_quota()
                    self.state = {
                        "checked_at": time.time(),
                        "codex_quota": quota,
                        "updated_at": quota.get('observed_at', time.time()),
                        "error": None,
                        "basis": "Codex 套餐额度",
                    }
                    self.write("state.json", self.state)
                except Exception as exc:
                    self.state["error"] = str(exc) if isinstance(exc, ValueError) else "Codex 额度查询失败"
                    try:
                        self.write("state.json", self.state)
                    except OSError:
                        pass
                return self.snapshot()
            if self.config["source"] == "desktop_quota" and self.config["quota_mode"] == "auto":
                if not app_open and self.last_attempt and time.monotonic() - self.last_attempt < 60:
                    return self.snapshot()
                previous_attempt = self.last_attempt
                self.last_attempt = time.monotonic()
                try:
                    quota = read_desktop_quota(self.config["desktop_app"])
                    self.state = {"account_quota": quota, "checked_at": time.time(), "updated_at": quota.get("observed_at", time.time()), "error": None, "basis": quota["basis"]}
                except NavigationBusy:
                    self.last_attempt = previous_attempt
                    return self.snapshot()
                except Exception as exc:
                    self.state["checked_at"] = time.time()
                    self.state["error"] = str(exc) if isinstance(exc, ValueError) else "本机额度读取失败"
                self.write("state.json", self.state)
                return self.snapshot()
            if self.config["source"] == "manual_quota" or self.config["source"] == "desktop_quota" and self.config["quota_mode"] == "manual":
                # Manual values retain their entry time; polling must not make them look fresh.
                if not self.state.get("updated_at") and self.config.get("recorded_at") and self.config["quota_remaining"] is not None:
                    self.state = {"updated_at": self.config["recorded_at"], "basis": "手动记录 · 不自动扣减或重置"}
                    self.write("state.json", self.state)
                return self.snapshot()
            if self.config["source"] == "jev":
                return self.snapshot()
            if not self.config["api_key"] and self.config["source"] not in ("manual", "aliyun", "app_bridge"):
                return self.snapshot()
            if self.last_attempt and time.monotonic() - self.last_attempt < 60:
                return self.snapshot()
            self.last_attempt = time.monotonic()
            self.state["checked_at"] = time.time()
            try:
                payload = (
                    read_harness_balance() if self.config["source"] == "app_bridge" else
                    self.fetch(self.config["api_key"]) if self.config["source"] == "deepseek" else query(self.config)
                )
                rows = parse_balance(payload)
                day = dt.date.today().isoformat()
                baseline = self.state.get("baseline", {}) if self.state.get("day") == day else {}
                daily = {}
                for row in rows:
                    currency = row["currency"]
                    baseline.setdefault(currency, row["balance"])
                    daily[currency] = str(Decimal(baseline[currency]) - Decimal(row["balance"]))
                self.state = {
                    "checked_at": time.time(),
                    "balances": rows,
                    "is_available": payload["is_available"],
                    "updated_at": self.config.get("recorded_at") if self.config["source"] == "manual" else payload.get("observed_at", time.time()),
                    "wallet_breakdown": payload.get("wallet_breakdown"),
                    "error": None,
                    "day": day,
                    "basis": payload.get("basis", "官方账户余额"),
                    "baseline": baseline,
                    "daily": daily,
                }
                self.write("state.json", self.state)
            except Exception as exc:
                self.state["error"] = str(exc) if isinstance(exc, ValueError) else "查询或保存失败，请稍后再试"
                try:
                    self.write("state.json", self.state)
                except OSError:
                    pass
                return self.snapshot()
            snapshot = self.snapshot()
            row = snapshot["selected"]
            low = not payload["is_available"] or (
                row and Decimal(row["balance"]) <= Decimal(str(self.config["low_balance"]))
            )
            if (
                low
                and self.config["alerts"]
                and self.announce
                and (not self.last_alert or time.monotonic() - self.last_alert >= 3600)
            ):
                text = (
                    f"碳基生物，猫粮余额只剩 {row['balance']} {row['currency']} 了喵～"
                    if row
                    else "碳基生物，猫粮余额不足了喵～"
                )
                try:
                    if self.announce(text):
                        self.last_alert = time.monotonic()
                except Exception:
                    pass
            return snapshot

    def start(self):
        if (self.config['source'] == 'desktop_quota' and self.config['desktop_app'] == 'trae'
                and self.config['quota_mode'] == 'auto' and self.trae_watcher is None):
            from .trae_watcher import TraeWatcher

            def opened():
                with self.lock:
                    if (not self.stop.is_set() and self.config['source'] == 'desktop_quota'
                            and self.config['desktop_app'] == 'trae' and self.config['quota_mode'] == 'auto'):
                        self.write('trae-open.json', {'at': time.time(), 'event': self.trae_watcher.last_native_event})
                        self.refresh(app_open=True)

            self.trae_watcher = TraeWatcher(opened)
            self.trae_watcher.start()
        if self.thread and self.thread.is_alive():
            return

        def worker():
            while not self.stop.is_set():
                self.refresh()
                self.stop.wait(self.config["interval"])

        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.trae_watcher:
            self.trae_watcher.close()
        if self.thread:
            self.thread.join(14)
