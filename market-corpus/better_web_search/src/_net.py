"""Minimal stdlib HTTP layer with an explicit proxy policy.

This plugin is distributed as a standalone package, so it deliberately avoids
third-party HTTP clients. Requests are synchronous and run on worker threads via
``asyncio.to_thread``; the plugin's event loop is never blocked.

Proxy policy matters because search engines that are unreachable directly (for
example DuckDuckGo from mainland China) become reachable through a local proxy,
while engines that are reachable directly (Baidu, Bing) must not be pushed
through a proxy that would slow them down or leak requests.
"""

from __future__ import annotations

import gzip
import io
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass, field
from typing import Any, Mapping

DEFAULT_TIMEOUT = 12.0
MAX_RESPONSE_BYTES = 6_000_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

POLICY_AUTO = "auto"
POLICY_NONE = "none"
POLICY_SYSTEM = "system"


class NetworkError(RuntimeError):
    """Transport-level failure: DNS, refused connection, timeout, TLS."""


class HttpStatusCodeError(RuntimeError):
    """The server answered with a non-2xx status."""

    def __init__(self, message: str, status: int, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after_seconds = retry_after


class ResponseTooLargeError(RuntimeError):
    """The peer exceeded the configured byte budget."""


@dataclass
class Response:
    status: int
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str:
        want = name.casefold()
        for key, value in self.headers.items():
            if key.casefold() == want:
                return value
        return ""

    @property
    def content_type(self) -> str:
        return self.header("content-type").split(";")[0].strip().casefold()


def _system_proxies() -> dict[str, str]:
    try:
        found = urllib.request.getproxies()
    except Exception:
        return {}
    out: dict[str, str] = {}
    for scheme in ("http", "https"):
        value = str(found.get(scheme) or found.get(f"{scheme}_proxy") or "").strip()
        if value:
            out[scheme] = value if "://" in value else f"http://{value}"
    if not out:
        pac = str(found.get("pac") or found.get("autoconfig") or "").strip()
        if pac:
            out["pac_url"] = pac
    return out


_lock = threading.Lock()
_opener_cache: dict[str, urllib.request.OpenerDirector] = {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _build_opener(proxy: str | None, *, follow: bool,
                  cookie_jar: Any = None) -> urllib.request.OpenerDirector:
    handlers: list[Any] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        # Empty mapping disables both environment proxies and macOS/Windows
        # system proxy discovery, which is what policy "none" must mean.
        handlers.append(urllib.request.ProxyHandler({}))
    if not follow:
        handlers.append(_NoRedirect())
    handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    return urllib.request.build_opener(*handlers)


def _opener_for(proxy: str | None, follow: bool, cookie_jar: Any = None) -> urllib.request.OpenerDirector:
    # ``follow`` is already a bool: spelling the cache-key suffix out keeps the
    # key byte-identical to the old ``int(follow)`` form without a converting
    # call that can never fail here but reads as one that could.
    flag = "1" if follow else "0"
    if cookie_jar is None:
        key = f"{'-' if proxy is None else proxy}|{flag}"
    else:
        key = f"{'-' if proxy is None else proxy}|{flag}|jar{id(cookie_jar)}"
    with _lock:
        cached = _opener_cache.get(key)
        if cached is None:
            cached = _build_opener(proxy, follow=follow, cookie_jar=cookie_jar)
            _opener_cache[key] = cached
        return cached


def system_proxy_present() -> bool:
    """True when the OS or environment advertises an outbound proxy."""
    return bool(_system_proxies())


def resolve_proxy(policy: str, proxy_url: str = "") -> str | None:
    """Turn a configured policy into the proxy URL to use, if any."""
    explicit = (proxy_url or "").strip()
    normalized = (policy or POLICY_AUTO).strip().casefold()
    if normalized == POLICY_NONE and not explicit:
        return None
    if explicit and not explicit.lower().startswith("system"):
        return explicit if "://" in explicit else f"http://{explicit}"
    if normalized in {POLICY_NONE, ""}:
        return None
    proxies = _system_proxies()
    return proxies.get("https") or proxies.get("http")


def _decode_body(raw: bytes, encoding: str) -> bytes:
    if not raw:
        return raw
    encoding = (encoding or "").strip().casefold()
    try:
        if encoding in {"gzip", "x-gzip"}:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        if encoding == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception as error:  # a corrupt body must surface as a parse failure
        raise NetworkError(f"响应解压失败: {type(error).__name__}") from error
    return raw


def _retry_after(headers: Mapping[str, str]) -> float | None:
    import email.utils

    raw = ""
    for key, value in headers.items():
        if key.casefold() == "retry-after":
            raw = str(value).strip()
            break
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    import time

    target = parsed.timestamp()
    return max(0.0, target - time.time())


def request(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    data: bytes | None = None,
    json_body: Any = None,
    headers: Mapping[str, str] | None = None,
    policy: str = POLICY_AUTO,
    proxy_url: str = "",
    timeout: float = DEFAULT_TIMEOUT,
    follow_redirects: bool = True,
    max_bytes: int = MAX_RESPONSE_BYTES,
    cookie_jar: Any = None,
) -> Response:
    """Perform one HTTP request and return a decoded-enough :class:`Response`.

    Raises :class:`NetworkError` for transport failures and
    :class:`HttpStatusCodeError` for non-2xx answers (including 3xx when
    ``follow_redirects`` is disabled).
    """
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise NetworkError(f"不支持的协议: {parsed.scheme or '空'}")

    full = url
    if params:
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        for key, value in params.items():
            if value is None:
                continue
            query[str(key)] = str(value)
        rebuilt = list(parsed)
        rebuilt[3] = urllib.parse.urlencode(query, doseq=True)
        full = urllib.parse.urlunsplit(rebuilt)

    sent = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    sent.update({str(k): str(v) for k, v in (headers or {}).items()})
    body = data
    if json_body is not None:
        import json as _json

        body = _json.dumps(json_body).encode("utf-8")
        sent.setdefault("Content-Type", "application/json")

    proxy = resolve_proxy(policy, proxy_url)
    req = urllib.request.Request(full, data=body, method=method.upper())
    for key, value in sent.items():
        req.add_header(key, value)

    opener = _opener_for(proxy, follow_redirects, cookie_jar)
    try:
        with opener.open(req, timeout=max(1.0, float(timeout))) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw_headers = {k: v for k, v in resp.headers.items()}
            payload = resp.read(max_bytes + 1)
    # pi-lens-ignore: boolean-in-except
    except urllib.error.HTTPError as error:
        header_map = {k: v for k, v in (error.headers or {}).items()}
        status = int(error.code)
        if 300 <= status < 400 and not follow_redirects:
            raise HttpStatusCodeError(f"重定向到 {header_map.get('Location', '')}", status,
                                      _retry_after(header_map)) from error
        raise HttpStatusCodeError(
            f"HTTP {status}", status, _retry_after(header_map)
        ) from error
    except (socket.timeout, TimeoutError) as error:
        raise NetworkError("请求超时") from error
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise NetworkError("请求超时") from error
        raise NetworkError(f"网络不可达: {reason}") from error
    except (ssl.SSLError, OSError) as error:
        raise NetworkError(f"网络错误: {type(error).__name__}") from error

    if len(payload) > max_bytes:
        raise ResponseTooLargeError("响应体超过上限")

    content = raw_headers.get("Content-Encoding") or raw_headers.get("content-encoding") or ""
    return Response(
        status=status,
        url=full,
        headers=raw_headers,
        body=_decode_body(payload, content),
    )


def get(url: str, **kwargs: Any) -> Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> Response:
    return request("POST", url, **kwargs)
