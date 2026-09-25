from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name)
    return value if isinstance(value, Mapping) else {}


_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def normalize_settings_update(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the small, user-editable settings surface used by Hosted UI."""
    output: dict[str, Any] = {}
    bool_fields = {
        "enabled",
        "shadow_mode",
        "background_llm",
        "web_search",
        "bilibili",
        "openbiliclaw_enabled",
    }
    int_ranges = {
        "daily_limit": (0, 20),
        "min_interval_minutes": (0, 1440),
        "min_user_silence_minutes": (0, 1440),
        "max_idle_seconds": (0, 86400),
        "openbiliclaw_port": (1024, 65535),
        "openbiliclaw_backend_port": (1024, 65535),
    }
    for key in bool_fields:
        if key in raw:
            if not isinstance(raw[key], bool):
                raise ValueError(f"{key} must be a boolean")
            output[key] = raw[key]
    for key, (minimum, maximum) in int_ranges.items():
        if key in raw:
            if isinstance(raw[key], bool):
                raise ValueError(f"{key} must be an integer")
            value = int(raw[key])
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
            output[key] = value
    if "score_threshold" in raw:
        if isinstance(raw["score_threshold"], bool):
            raise ValueError("score_threshold must be a number")
        threshold = float(raw["score_threshold"])
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("score_threshold must be between 0 and 1")
        output["score_threshold"] = threshold
    for key in ("quiet_start", "quiet_end"):
        if key in raw:
            value = str(raw[key])
            if not _TIME_PATTERN.fullmatch(value):
                raise ValueError(f"{key} must use HH:MM in 24-hour time")
            output[key] = value
    if not output:
        raise ValueError("no valid settings supplied")
    return output


@dataclass(frozen=True, slots=True)
class RecommendationConfig:
    enabled: bool = False
    shadow_mode: bool = True
    memory_bucket: str = "default"
    daily_limit: int = 2
    min_interval_minutes: int = 240
    quiet_start: str = "23:00"
    quiet_end: str = "09:00"
    score_threshold: float = 0.72
    candidate_ttl_hours: int = 12
    reply_window_minutes: int = 10
    ignored_window_minutes: int = 30
    max_consecutive_ignored: int = 2
    max_idle_seconds: int = 900
    min_user_silence_minutes: int = 20
    web_search: bool = True
    bilibili: bool = False
    background_llm: bool = True
    openbiliclaw_enabled: bool = False
    openbiliclaw_host: str = "127.0.0.1"
    openbiliclaw_port: int = 8421
    openbiliclaw_backend_port: int = 8420

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "RecommendationConfig":
        root = raw if isinstance(raw, Mapping) else {}
        rec = _section(root, "recommendation")
        sources = _section(rec, "sources")
        compat = _section(root, "openbiliclaw")
        return cls(
            enabled=bool(rec.get("enabled", False)),
            shadow_mode=bool(rec.get("shadow_mode", True)),
            memory_bucket=str(rec.get("memory_bucket") or "default"),
            daily_limit=max(0, int(rec.get("daily_limit", 2))),
            min_interval_minutes=max(0, int(rec.get("min_interval_minutes", 240))),
            quiet_start=str(rec.get("quiet_start") or "23:00"),
            quiet_end=str(rec.get("quiet_end") or "09:00"),
            score_threshold=min(1.0, max(0.0, float(rec.get("score_threshold", 0.72)))),
            candidate_ttl_hours=max(1, int(rec.get("candidate_ttl_hours", 12))),
            reply_window_minutes=max(1, int(rec.get("reply_window_minutes", 10))),
            ignored_window_minutes=max(1, int(rec.get("ignored_window_minutes", 30))),
            max_consecutive_ignored=max(1, int(rec.get("max_consecutive_ignored", 2))),
            max_idle_seconds=max(0, int(rec.get("max_idle_seconds", 900))),
            min_user_silence_minutes=max(
                0, int(rec.get("min_user_silence_minutes", 20))
            ),
            web_search=bool(sources.get("web_search", True)),
            bilibili=bool(sources.get("bilibili", False)),
            background_llm=bool(rec.get("background_llm", True)),
            openbiliclaw_enabled=bool(compat.get("enabled", False)),
            # The compatibility ingress is deliberately loopback-only. This is
            # a browser-extension bridge, not a LAN API.
            openbiliclaw_host="127.0.0.1",
            openbiliclaw_port=min(65535, max(1024, int(compat.get("port", 8421)))),
            openbiliclaw_backend_port=min(
                65535, max(1024, int(compat.get("backend_port", 8420)))
            ),
        )
