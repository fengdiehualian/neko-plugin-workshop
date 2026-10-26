"""HTTP helpers shared by every adapter.

Deliberately does *not* police destinations: this plugin runs on the user's
own machine and its sources are the user's own choice, including loopback and
LAN addresses. The guards here are about not hanging or blowing up memory.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5


class FetchError(Exception):
    """A source-level failure carrying a stable, log-safe error code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


class HttpClient:
    """Thin wrapper over one shared ``aiohttp.ClientSession``."""

    def __init__(self, session: aiohttp.ClientSession, proxy: str = "") -> None:
        self._session = session
        self._proxy = (proxy or "").strip() or None

    async def request(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        body: Any = None,
        timeout: float = 6.0,
        max_bytes: int = MAX_BYTES,
    ) -> tuple[int, bytes, dict[str, str]]:
        if not url or not url.lower().startswith(("http://", "https://")):
            raise FetchError("CONFIG_INVALID", "url must be http(s)")

        client_timeout = aiohttp.ClientTimeout(
            total=max(1.0, float(timeout)), connect=min(5.0, max(1.0, float(timeout)))
        )
        request_kwargs: dict[str, Any] = {
            "headers": headers or None,
            "params": params or None,
            "timeout": client_timeout,
            "max_redirects": MAX_REDIRECTS,
            "allow_redirects": True,
            "proxy": self._proxy,
        }
        if body is not None:
            if isinstance(body, (dict, list)):
                request_kwargs["json"] = body
            else:
                request_kwargs["data"] = str(body)

        try:
            async with self._session.request(method.upper(), url, **request_kwargs) as response:
                status = response.status
                if status >= 400:
                    raise FetchError(f"HTTP_{status}")
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    chunks.extend(chunk)
                    if len(chunks) > max_bytes:
                        raise FetchError("RESPONSE_TOO_LARGE")
                return status, bytes(chunks), dict(response.headers)
        except asyncio.TimeoutError as exc:
            raise FetchError("TIMEOUT") from exc
        except aiohttp.TooManyRedirects as exc:
            raise FetchError("TOO_MANY_REDIRECTS") from exc
        except aiohttp.ClientError as exc:
            raise FetchError("NETWORK_ERROR", type(exc).__name__) from exc

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 6.0,
    ) -> Any:
        _, payload, _ = await self.request(
            url, headers=headers, params=params, timeout=timeout
        )
        try:
            return json.loads(decode_bytes(payload))
        except (TypeError, ValueError) as exc:
            raise FetchError("PARSE_ERROR", "response is not valid JSON") from exc

    async def get_bytes(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 6.0,
    ) -> bytes:
        _, payload, _ = await self.request(url, headers=headers, timeout=timeout)
        return payload


def decode_bytes(payload: bytes, headers: dict[str, str] | None = None) -> str:
    charset = ""
    if headers:
        content_type = ""
        for key, value in headers.items():
            if key.lower() == "content-type":
                content_type = value
                break
        for part in content_type.split(";")[1:]:
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip('"')
    for encoding in filter(None, (charset, "utf-8", "gb18030")):
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def parse_proxy_server(raw: str) -> str:
    """Normalize a WinINET ``ProxyServer`` value into an aiohttp proxy URL.

    Accepts ``127.0.0.1:7890``, ``http://127.0.0.1:7890`` and the
    ``http=host:port;https=host:port`` form.
    """

    text = (raw or "").strip()
    if not text:
        return ""
    if "=" in text:
        mapping: dict[str, str] = {}
        for part in text.split(";"):
            if "=" not in part:
                continue
            scheme, value = part.split("=", 1)
            mapping[scheme.strip().lower()] = value.strip()
        text = mapping.get("https") or mapping.get("http") or ""
        if not text:
            return ""
    if not text.lower().startswith(("http://", "https://")):
        text = "http://" + text
    return text


def detect_system_proxy() -> str:
    """Best-effort Windows system proxy lookup; empty string means direct."""

    import os

    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return ""
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
            return parse_proxy_server(str(server))
    except (OSError, ValueError):
        return ""


def resolve_proxy(setting: str) -> str:
    """``auto`` detects the system proxy, ``none`` forces direct."""

    value = (setting or "").strip()
    lowered = value.lower()
    if lowered in ("", "auto"):
        return detect_system_proxy()
    if lowered in ("none", "off", "direct", "false", "0"):
        return ""
    return parse_proxy_server(value)


__all__ = [
    "FetchError",
    "HttpClient",
    "MAX_BYTES",
    "decode_bytes",
    "detect_system_proxy",
    "parse_proxy_server",
    "resolve_proxy",
]
