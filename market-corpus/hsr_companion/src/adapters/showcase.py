"""Bounded, explicit-consent public showcase reads. No game/client access."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

PROVIDER = "enka.network"
PROVIDER_URL = "https://enka.network"
ENDPOINT = "https://enka.network/api/hsr/uid/{}"
MAX_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 12
MIN_REFRESH_SECONDS = 60


class ShowcaseError(ValueError):
    def __init__(self, code: str, retry_seconds: int = 0, *, http_status: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_seconds = retry_seconds
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None


def validate_uid(value: object) -> str:
    # ASCII digits only; never accept a URL/path, credentials or arbitrary host.
    uid = str(value).strip()
    if not re.fullmatch(r"[125][0-9]{8}", uid):
        raise ShowcaseError("invalid_cn_uid")
    return uid


@dataclass(frozen=True)
class ShowcaseResponse:
    payload: dict
    ttl: int


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not disclose the UID to another provider implicitly.


def request_proxies(*, use_system_proxy: bool = False) -> dict[str, str]:
    """Keep inherited policy by default; registry fallback requires explicit opt-in.

    On Windows, urllib's getproxies() treats a lone NO_PROXY as a complete
    environment configuration and otherwise ignores the existing registry proxy.
    Only this opener is changed; do not mutate environment or global handlers.
    Explicit HTTP(S)/ALL_PROXY (even empty) and NO_PROXY=* remain authoritative.
    """
    proxies = urllib.request.getproxies()
    registry = getattr(urllib.request, "getproxies_registry", None)
    explicit = any(key.lower() in {"http_proxy", "https_proxy", "all_proxy"} for key in os.environ)
    if (
        use_system_proxy is True
        and callable(registry)
        and not explicit
        and proxies.get("no", "").strip() != "*"
        and not any(key != "no" for key in proxies)
    ):
        proxies = {**registry(), **proxies}
    return proxies


def fetch_showcase(uid: str, *, use_system_proxy: bool = False) -> ShowcaseResponse:
    """Called in a worker thread, only after an explicit panel action."""
    uid = validate_uid(uid)
    request = urllib.request.Request(
        ENDPOINT.format(uid),
        headers={
            "User-Agent": "N.E.K.O-hsr-companion/0.9.6 (+https://github.com/WhiteFrostune/n.e.k.o_plugin_hsr_companion)",
            "Accept": "application/json",
        },
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(request_proxies(use_system_proxy=use_system_proxy)), _NoRedirect()
    )
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ShowcaseError("response_too_large")
        payload = json.loads(body)
    except urllib.error.HTTPError as exc:
        codes = {
            400: "invalid_cn_uid",
            404: "player_not_found",
            424: "maintenance",
            429: "rate_limited",
            503: "maintenance",
        }
        retry = str(exc.headers.get("Retry-After", "60"))
        retry = max(60, min(int(retry), 86400)) if retry.isascii() and retry.isdigit() else 60
        raise ShowcaseError(codes.get(exc.code, "provider_unavailable"), retry, http_status=exc.code) from None
    except (OSError, TimeoutError, urllib.error.URLError):
        raise ShowcaseError("network_unavailable", 60) from None
    except ShowcaseError:
        raise
    except (ValueError, UnicodeError):
        raise ShowcaseError("unsupported_response", 60) from None
    if not isinstance(payload, dict):
        raise ShowcaseError("unsupported_response", 60)
    ttl = payload.get("ttl", MIN_REFRESH_SECONDS)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 0 <= ttl <= 86400:
        raise ShowcaseError("unsupported_response", 60)
    return ShowcaseResponse(payload, max(MIN_REFRESH_SECONDS, ttl))
