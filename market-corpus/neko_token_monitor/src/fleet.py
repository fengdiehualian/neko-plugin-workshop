"""Independent provider profiles, migrated from the original single-source store."""

import copy
import sys
import threading
import time
from pathlib import Path

from .allowance import AllowanceLedger
from .core import Monitor
from .foreground import ForegroundWatcher
from .integrations import info as integration_info
from .topup import info as topup_info
from .visible_balance import saved_wallet

CATALOG = [
    ("codex", "Codex", "codex", "套餐额度 · 自动查询"),
    ("trae", "TRAE", "desktop_quota", "积分 · 打开应用自动读取 / 手动校正"),
    ("workbuddy", "WorkBuddy", "desktop_quota", "积分 · 本机用量页读取 / 手动校正"),
    ("deepseek", "DeepSeek", "deepseek", "官方账户余额"),
    ("deepseek_chat", "DeepSeek 网页聊天", "manual_quota", "网页账户额度 · 手动记录，不代用 API 余额"),
    ("deepseek_harness", "DeepSeek Harness", "app_bridge", "登录账户余额 · Harness 原生桥接 · 充值余额与赠金"),
    ("openai", "GPT / OpenAI API", "openai", "自定预算减组织费用"),
    ("anthropic", "Claude API", "anthropic", "自定预算减组织费用"),
    ("openrouter", "OpenRouter", "openrouter", "多模型共享余额 · Management Key"),
    ("siliconflow", "硅基流动", "siliconflow", "多模型共享余额 · API Key"),
    ("chatgpt", "ChatGPT", "manual_quota", "订阅额度 · 手动记录"),
    ("claude", "Claude / Claude Code", "manual_quota", "订阅额度 · 手动记录"),
    ("gemini", "Gemini", "manual_quota", "额度 · 手动记录"),
    ("kimi", "Kimi 会员 / Code", "manual_quota", "套餐额度 · 手动记录"),
    ("kimi_api_cn", "Kimi API 中国站", "moonshot_cn", "开放平台账户余额 · CNY · 自动查询"),
    ("kimi_api_global", "Kimi API 国际站", "moonshot_global", "开放平台账户余额 · USD · 自动查询"),
    ("qwen", "Qwen 聊天 / 百炼 Token", "manual_quota", "套餐 / 免费 Token · 手动记录"),
    ("qwen_api", "Qwen / 阿里云账户", "aliyun", "阿里云共享可用额度 · 官方 CLI 只读查询"),
    ("doubao", "豆包", "manual_quota", "额度 · 手动记录"),
    ("glm", "智谱 GLM", "manual_quota", "额度 · 手动记录"),
    ("jev", "JEV", "jev", "本机上报用量"),
    ("custom", "自定义服务商", "custom", "HTTPS 余额接口"),
    ("manual", "其他模型", "manual", "手动金额预算"),
]
PROFILES = {row[0]: row for row in CATALOG}


class Fleet:
    def __init__(self, directory, announce=None):
        self.directory = Path(directory)
        self.allowances = AllowanceLedger(self.directory)
        self.storage = Monitor(directory)
        self.lock = threading.RLock()
        self.monitors = {}
        self.snapshots = {}
        self.running = False
        self.watcher = None
        self.announce = announce
        self.page_wallets = {key: wallet for key, value in self.storage.read("visible-balances.json", {}).items()
                             if key == "deepseek" and (wallet := saved_wallet(value))}
        self.page_wallet_saved_at = 0
        meta = self.storage.read("profiles.json", {})
        if not meta:
            selected = self.storage.config["source"]
            if selected not in PROFILES:
                selected = "deepseek"
            child = self._monitor(selected)
            child.config = dict(self.storage.config)
            child.state = dict(self.storage.state)
            child.write("config.json", child.config)
            child.write("state.json", child.state)
            meta = {"selected": selected, "enabled": [selected]}
            self.storage.write("profiles.json", meta)
        self.fixed_profile = meta.get("fixed_profile", meta.get("selected", "deepseek"))
        if self.fixed_profile not in PROFILES:
            self.fixed_profile = "deepseek"
        self.display_mode = meta.get("display_mode", "auto")
        if self.display_mode not in ("auto", "fixed"):
            self.display_mode = "auto"
        self.bubble_visible = meta.get("bubble_visible") is not False
        self.selected = self.fixed_profile
        self.foreground_profile = None
        self.foreground_wallet_visible = False
        self.foreground_status = "detecting" if sys.platform == "darwin" else "unsupported"
        self.enabled = {key for key in meta.get("enabled", []) if key in PROFILES}
        for key in self.enabled:
            self._monitor(key)

    def _monitor(self, key):
        if key not in PROFILES:
            raise ValueError("未知模型来源")
        if key not in self.monitors:
            monitor = Monitor(self.directory / "profiles" / key, announce=self.announce)
            if not (monitor.directory / "config.json").exists():
                monitor.config["source"] = PROFILES[key][2]
                monitor.config["model"] = PROFILES[key][1]
                if key in ("trae", "workbuddy"):
                    monitor.config.update(desktop_app=key, quota_unit="积分")
            monitor.allowance_ledger = self.allowances
            monitor.allowance_profile = key
            self.monitors[key] = monitor
            self.snapshots[key] = monitor.snapshot()
        return self.monitors[key]

    def _persist(self):
        self.storage.write("profiles.json", {"selected": self.fixed_profile, "fixed_profile": self.fixed_profile,
                                             "display_mode": self.display_mode, "bubble_visible": self.bubble_visible,
                                             "enabled": sorted(self.enabled)})

    def _snapshot(self, key):
        monitor = self._monitor(key)
        # A network read owns Monitor.lock. UI routing must remain responsive
        # while another platform is querying, and must never reuse its amounts.
        if monitor.lock.acquire(blocking=False):
            try:
                self.snapshots[key] = monitor.snapshot()
            finally:
                monitor.lock.release()
            state = copy.deepcopy(self.snapshots[key])
        else:
            state = dict(copy.deepcopy(self.snapshots[key]), refreshing=True)
        # Browser-login wallets are a fallback, not a replacement for an API
        # key that may belong to a different account.
        wallet = self.page_wallets.get(key)
        if key == "jev":
            state["reported_usage"] = self.storage.snapshot()["reported_usage"]
            state["allowance"] = self.allowances.observe(state, key, state["config"])
        primary = state["allowance"]
        page_state = None
        prefer_primary = primary["account_scope"]["kind"] == "credential_slot"
        if wallet:
            page_state = copy.deepcopy(state)
        if wallet and not prefer_primary:
            current = (self.foreground_wallet_visible and self.foreground_profile == key and self.foreground_status == "page"
                       and time.time() - wallet["observed_at"] <= 15)
            if state["config"]["currency"] != wallet["currency"]:
                state["config"].update(currency=wallet["currency"], balance_total=None)
            row = {"currency": wallet["currency"], "balance": wallet["balance"]}
            state.update(configured=True, selected=row, balances=[row], updated_at=wallet["observed_at"],
                         error=None, stale=not current, is_available=True if current else None,
                         estimated_tokens=None, page_observation=True,
                         basis="DeepSeek 网页余额 · 充值与赠送合计 · 页面显示精度",
                         wallet_breakdown={"CNY": {"normal": wallet["normal"], "bonus": wallet["bonus"]}})
        if wallet:
            page_state.update(config=copy.deepcopy(state["config"]), selected={"currency": wallet["currency"], "balance": wallet["balance"]},
                              updated_at=wallet["observed_at"], checked_at=wallet["observed_at"],
                              stale=not (self.foreground_wallet_visible and self.foreground_profile == key and self.foreground_status == "page"
                                         and 0 <= time.time() - wallet["observed_at"] <= 15), error=None)
        if page_state and page_state["config"]["currency"] != wallet["currency"]:
            page_state["config"].update(currency=wallet["currency"], balance_total=None)
        state["allowance"] = self.allowances.select(primary, page_state, prefer_primary=prefer_primary)
        return state

    def allowance_history(self, profile=None):
        with self.lock:
            key = profile or self.selected
            if key not in PROFILES:
                raise ValueError("未知模型来源")
            reading = self._snapshot(key)["allowance"]
            return self.allowances.history(reading)

    def tracking(self):
        with self.lock:
            fixed = self.display_mode == "fixed"
            following = self.foreground_profile is not None
            status = "fixed" if fixed else "following" if following else self.foreground_status
            name = PROFILES[self.selected][1]
            hint = ("固定监测 · " + name) if fixed else ("自动跟随前台 · " + name) if following else {
                "permission": "自动跟随 · 浏览器页面识别需要辅助功能权限，暂保留上次平台",
                "unsupported": "当前系统尚未支持前台识别，请选择固定监测",
                "detecting": "正在识别前台平台，暂保留上次展示",
                "unavailable": "前台识别暂不可用，保留上次平台",
            }.get(status, "前台不是已识别平台，暂保留 " + name)
            return {"mode": self.display_mode, "fixed_profile": self.fixed_profile,
                    "profile": self.selected, "foreground_profile": self.foreground_profile,
                    "status": status, "hint": hint, "supported": sys.platform == "darwin",
                    "bubble_visible": self.bubble_visible}

    def _activate(self, key):
        monitor = self._monitor(key)
        # These existing readers need no copied credentials. Other products
        # remain visibly unconfigured until the user connects their own source.
        if key in ("codex", "trae", "workbuddy", "deepseek_harness") and key not in self.enabled:
            self.enabled.add(key)
            self._persist()
        if self.running and key in self.enabled:
            monitor.start()

    def follow_foreground(self, context):
        with self.lock:
            if context.get("status") == "ignored":
                return
            key = context.get("profile")
            self.foreground_profile = key if key in PROFILES else None
            self.foreground_status = context.get("status", "unrecognized")
            wallet = saved_wallet(context.get("page_wallet")) if key == "deepseek" else None
            self.foreground_wallet_visible = wallet is not None
            if wallet:
                previous = self.page_wallets.get(key, {})
                self.page_wallets[key] = wallet
                if (previous.get("balance") != wallet["balance"] or previous.get("normal") != wallet["normal"]
                        or time.monotonic() - self.page_wallet_saved_at > 15):
                    self.storage.write("visible-balances.json", self.page_wallets)
                    self.page_wallet_saved_at = time.monotonic()
            if self.display_mode == "auto" and self.foreground_profile:
                self.selected = self.foreground_profile
                self._activate(self.selected)

    def configure_tracking(self, values):
        if (not values or set(values) - {"mode", "profile", "bubble_visible"}
                or ("mode" in values and values["mode"] not in ("auto", "fixed"))
                or ("mode" not in values and set(values) != {"bubble_visible"})
                or ("bubble_visible" in values and type(values["bubble_visible"]) is not bool)):
            raise ValueError("请选择自动跟随或固定监测")
        with self.lock:
            key = values.get("profile", self.fixed_profile)
            if key not in PROFILES:
                raise ValueError("未知模型来源")
            if "mode" in values:
                self.display_mode = values["mode"]
                if self.display_mode == "fixed":
                    self.fixed_profile = key
                    self.selected = key
                elif self.foreground_profile:
                    self.selected = self.foreground_profile
                self._activate(self.selected)
            if "bubble_visible" in values:
                self.bubble_visible = values["bubble_visible"]
            self._persist()
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            state = self._snapshot(self.selected)
            state["reported_usage"] = self.storage.snapshot()["reported_usage"]
            state["profile"] = self.selected
            state["topup"] = topup_info(self.selected)
            state["tracking"] = self.tracking()
            state["profile_enabled"] = self.selected in self.enabled or bool(state.get("page_observation"))
            state["profiles"] = []
            for key, name, source, hint in CATALOG:
                snapshot = self._snapshot(key)
                state["profiles"].append(
                    {
                        "id": key,
                        "name": name,
                        "source": source,
                        "hint": hint,
                        "topup": topup_info(key),
                        "integration": integration_info(key),
                        "enabled": key in self.enabled or bool(snapshot.get("page_observation")),
                        "snapshot": snapshot,
                    }
                )
            return state

    def configure(self, values):
        with self.lock:
            key = values.get("profile", values.get("source", self.selected))
            monitor = self._monitor(key)
            values = dict(values, source=PROFILES[key][2])
            if key in ("trae", "workbuddy"):
                values["desktop_app"] = key
            monitor.configure(values)
            self.enabled.add(key)
            if self.display_mode == "fixed" or self.foreground_profile is None:
                self.selected = key
                self.fixed_profile = key
            self._persist()
            if self.running:
                monitor.start()
        return self.snapshot()

    def select(self, key):
        with self.lock:
            if key not in self.enabled:
                raise ValueError("请先配置此来源")
        return self.configure_tracking({"mode": "fixed", "profile": key})

    def refresh(self, profile=None):
        with self.lock:
            monitor = self._monitor(profile or self.selected)
        monitor.refresh()
        return self.snapshot()

    def record_usage(self, item):
        return self.storage.record_usage(item)

    def start(self):
        with self.lock:
            self.running = True
            for key in self.enabled:
                self._monitor(key).start()
            if self.watcher is None:
                self.watcher = ForegroundWatcher(self.follow_foreground)
                self.watcher.start()

    def close(self):
        if self.watcher:
            self.watcher.close()
        with self.lock:
            self.running = False
            monitors = list(self.monitors.values())
        for monitor in monitors:
            monitor.close()
