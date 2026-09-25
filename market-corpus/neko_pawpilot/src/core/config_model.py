"""配置模型：从 data/config/main.json 读取的纯参数对象。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config" / "main.json"

DEFAULTS = {
    "enabled": True,
    "dry_run": True,
    "poll_interval_s": 1.0,
    "speeding_reset_s": 30.0,
    "speeding_escalate_kmh": 15.0,
    "event_cooldown_s": 10.0,
    "time_warn_min": 60,
    "low_fuel_percent": 15,
    "crash_damage_delta": 0.05,
    "hard_brake_force": 0.9,
    "hard_brake_speed_kmh": 60,
    "push_visibility": ["chat"],
    "push_enabled": True,
    # 全局限流 / 抢占冷却
    "global_rate_limit_s": 12.0,
    "critical_cooldown_s": 5.0,
    # 安全门
    "safety_window_s": 60.0,
    "safety_failure_limit": 5,
    "safety_auto_stop": True,
    # 播报偏好（类别开关 + 频率模式）
    "broadcast_frequency": "standard",
    "broadcast_categories": {
        "safety": True,
        "task": True,
        "trip": True,
        "lifecycle": True,
        "chatter": False,
    },
    # 遥测插件路径（相对游戏根目录）
    "telemetry_plugin_rel": "bin/win_x64/plugins/scs-telemetry.dll",
    # 遥测捆绑文件路径（相对插件根目录）
    "telemetry_bundle_rel": "data/telemetry/scs-telemetry.dll",
}


def load_main_config() -> Dict[str, Any]:
    """读取 data/config/main.json；缺失回退默认值。"""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return dict(DEFAULTS)


class PawpilotConfig:
    """插件运行参数。"""

    def __init__(self, raw: Dict[str, Any] = None) -> None:
        data = raw or load_main_config()
        self.enabled = bool(data.get("enabled", DEFAULTS["enabled"]))
        self.dry_run = bool(data.get("dry_run", DEFAULTS["dry_run"]))
        self.poll_interval_s = float(data.get("poll_interval_s", DEFAULTS["poll_interval_s"]))
        self.speeding_reset_s = float(data.get("speeding_reset_s", DEFAULTS["speeding_reset_s"]))
        self.speeding_escalate_kmh = float(data.get("speeding_escalate_kmh", DEFAULTS["speeding_escalate_kmh"]))
        self.event_cooldown_s = float(data.get("event_cooldown_s", DEFAULTS["event_cooldown_s"]))
        self.time_warn_min = int(data.get("time_warn_min", DEFAULTS["time_warn_min"]))
        self.low_fuel_percent = float(data.get("low_fuel_percent", DEFAULTS["low_fuel_percent"]))
        self.crash_damage_delta = float(data.get("crash_damage_delta", DEFAULTS["crash_damage_delta"]))
        self.hard_brake_force = float(data.get("hard_brake_force", DEFAULTS["hard_brake_force"]))
        self.hard_brake_speed_kmh = float(data.get("hard_brake_speed_kmh", DEFAULTS["hard_brake_speed_kmh"]))
        self.push_visibility = list(data.get("push_visibility", DEFAULTS["push_visibility"]))
        self.push_enabled = bool(data.get("push_enabled", DEFAULTS["push_enabled"]))
        self.global_rate_limit_s = float(data.get("global_rate_limit_s", DEFAULTS["global_rate_limit_s"]))
        self.critical_cooldown_s = float(data.get("critical_cooldown_s", DEFAULTS["critical_cooldown_s"]))
        self.safety_window_s = float(data.get("safety_window_s", DEFAULTS["safety_window_s"]))
        self.safety_failure_limit = int(data.get("safety_failure_limit", DEFAULTS["safety_failure_limit"]))
        self.safety_auto_stop = bool(data.get("safety_auto_stop", DEFAULTS["safety_auto_stop"]))
        self.broadcast_frequency = str(data.get("broadcast_frequency", DEFAULTS["broadcast_frequency"]))
        self.broadcast_categories = dict(data.get("broadcast_categories", DEFAULTS["broadcast_categories"]))
        self.telemetry_plugin_rel = str(data.get("telemetry_plugin_rel", DEFAULTS["telemetry_plugin_rel"]))
        self.telemetry_bundle_rel = str(data.get("telemetry_bundle_rel", DEFAULTS["telemetry_bundle_rel"]))

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}
