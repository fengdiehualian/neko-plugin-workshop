"""Validated settings for the two selfie generation protocols."""
import math
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class SelfieError(Exception):
    """Stable, non-sensitive error code; never include provider response bodies."""


@dataclass(frozen=True)
class Settings:
    provider: str
    endpoint: str
    model: str
    api_key: str = field(repr=False)
    reference_image: str = field(repr=False)
    timeout: float = 120.0
    cooldown: float = 60.0
    size: str = "2K"
    target_lanlan: str = ""
    locale: str = "en"

    @classmethod
    def from_config(cls, config):
        if not isinstance(config, dict):
            raise SelfieError("config")
        provider = config.get("provider", "volcengine")
        if provider not in ("volcengine", "chat"):
            raise SelfieError("config")
        base = config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
        model = config.get("model", "")
        key = config.get("api_key", "")
        reference = config.get("reference_image", "")
        if not all(isinstance(value, str) and value.strip() for value in (base, model, key, reference)):
            raise SelfieError("config")
        base, model, key, reference = (value.strip() for value in (base, model, key, reference))
        try:
            parsed = urlsplit(base)
            parsed.port
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment
                    or any(c.isspace() or ord(c) < 32 for c in base)):
                raise ValueError()
            timeout = float(config.get("timeout_seconds", 120))
            cooldown = float(config.get("cooldown_seconds", 60))
            if not (math.isfinite(timeout) and 5 <= timeout <= 240
                    and math.isfinite(cooldown) and 0 <= cooldown <= 3600):
                raise ValueError()
        except (ValueError, TypeError):
            raise SelfieError("config") from None
        if len(model) > 200 or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise SelfieError("config")
        path = base.rstrip("/")
        suffix = "/images/generations" if provider == "volcengine" else "/chat/completions"
        if provider == "chat" and path.endswith("/v1/chat"):
            endpoint = path
        else:
            endpoint = path if path.endswith(suffix) else path + suffix
        size = config.get("size", "2K")
        target = config.get("target_lanlan", "")
        locale = config.get("locale", "en")
        if not all(isinstance(v, str) for v in (size, target, locale)) or not size or len(size) > 40:
            raise SelfieError("config")
        return cls(provider, endpoint, model, key, reference, timeout, cooldown, size, target, locale)
