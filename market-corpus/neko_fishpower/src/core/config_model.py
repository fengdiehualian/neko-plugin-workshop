"""配置模型：从 plugin.toml [neko_fishpower] 段读取的纯参数对象。"""

from __future__ import annotations

from typing import Any, Dict

DEFAULTS = {
    "enabled": True,
    "dry_run": True,
    "telemetry_host": "127.0.0.1",
    "telemetry_port": 9877,
    "event_cooldown_s": 10.0,
    "global_rate_limit_s": 12.0,
    "critical_cooldown_s": 5.0,
    "safety_window_s": 60.0,
    "broadcast_frequency": "standard",
    "broadcast_categories": {
        "caught": True,
        "casino": True,
        "grill": True,
        "boss": True,
        "journal": True,
        "lifecycle": True,
        "chatter": False,
    },
}


class FishpowerConfig:
    """插件运行参数。"""

    def __init__(self, raw: Dict[str, Any] = None) -> None:
        data = raw or {}
        self.enabled = bool(data.get("enabled", DEFAULTS["enabled"]))
        self.dry_run = bool(data.get("dry_run", DEFAULTS["dry_run"]))
        self.telemetry_host = str(data.get("telemetry_host", DEFAULTS["telemetry_host"]))
        self.telemetry_port = int(data.get("telemetry_port", DEFAULTS["telemetry_port"]))
        self.event_cooldown_s = float(data.get("event_cooldown_s", DEFAULTS["event_cooldown_s"]))
        self.global_rate_limit_s = float(data.get("global_rate_limit_s", DEFAULTS["global_rate_limit_s"]))
        self.critical_cooldown_s = float(data.get("critical_cooldown_s", DEFAULTS["critical_cooldown_s"]))
        self.safety_window_s = float(data.get("safety_window_s", DEFAULTS["safety_window_s"]))
        self.broadcast_frequency = str(data.get("broadcast_frequency", DEFAULTS["broadcast_frequency"]))
        self.broadcast_categories = dict(data.get("broadcast_categories", DEFAULTS["broadcast_categories"]))

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}
