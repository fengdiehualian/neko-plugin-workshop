"""Luogu HTTP client with JS-shell (C3VK) warm-up.

Luogu guards its pages with a small JS validator: a bare ``GET /`` returns a
``<script>`` shell that sets ``C3VK=<hex>`` (``window[document].cookie=...``).
A request that carries ``C3VK`` then gets the real page plus an ``__client_id``
cookie. This client reproduces that handshake and keeps a shared ``httpx`` pool
across event-loop boundaries (mirrors ``web_search``'s ``_get_client``).

Cookies are accumulated in a plain ``dict`` and re-injected whenever the pool is
rebuilt, so session state survives the host's separate ``asyncio.run`` calls for
startup / command-loop / shutdown. Login cookies are supplied at construction;
the anonymous ``C3VK`` is always obtained live (it expires in ~5 minutes).
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx

_LUOGU_HOME = "https://www.luogu.com.cn/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 N.E.K.O-Luogu/0.1"
)
_C3VK_RE = re.compile(r"C3VK=([0-9a-f]+)")
_CHALLENGE_RE = re.compile(r'window\[[^\]]*\]\.cookie="C3VK=')


class LuoguBlockedError(RuntimeError):
    """The anti-bot shell (or an HTTP 403) blocked the request."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        if retry_after_seconds is not None and not isinstance(retry_after_seconds, (int, float)):
            retry_after_seconds = None
        self.retry_after_seconds = (
            float(retry_after_seconds) if retry_after_seconds is not None else None
        )


def _headers(referer: bool = True) -> dict[str, str]:
    headers = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    }
    if referer:
        headers["Referer"] = _LUOGU_HOME
    return headers


def _extract_c3vk(html: str) -> str:
    match = _C3VK_RE.search(html)
    return match.group(1) if match else ""


def _is_challenge(html: str, status_code: int) -> bool:
    return status_code == 403 or bool(_CHALLENGE_RE.search(html))


def _absorb_cookies(target: dict[str, str], jar: httpx.Cookies, *, protected: set[str] = frozenset()) -> None:
    """Absorb fresh cookies, but never overwrite ``protected`` names.

    Luogu's anonymous C3VK shell round-trip issues ``_uid=0`` and a new
    ``__client_id``; those must not clobber the login cookies supplied at
    construction (the login session is what authenticates /record/list).
    """
    for cookie in jar.jar:
        if cookie.name and cookie.value and cookie.name not in protected:
            target[cookie.name] = cookie.value


class LuoguClient:
    """Shared session for all Luogu fetches."""

    def __init__(
        self,
        *,
        cookies: dict[str, str] | None = None,
        timeout: float = 15.0,
        min_interval: float = 1.0,
        cookie_ttl_seconds: float = 240.0,
    ) -> None:
        self._timeout = timeout
        self._min_interval = min_interval
        self._cookie_ttl = cookie_ttl_seconds
        self._cookies: dict[str, str] = dict(cookies or {})
        self._login_cookies: dict[str, str] = dict(cookies or {})
        self._c3vk = ""
        self._warmed_at = 0.0
        self._last_request = 0.0
        self._sem = asyncio.Semaphore(1)
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    def _get_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._client_loop is not loop:
            self._client = httpx.AsyncClient(
                cookies=dict(self._cookies),
                follow_redirects=True,
                timeout=self._timeout,
            )
            self._client_loop = loop
        return self._client

    def _restore_login_cookies(self, client: httpx.AsyncClient) -> None:
        """Re-assert the login cookies so the anonymous C3VK handshake can't
        clobber them. Luogu's shell round-trip issues ``_uid=0`` plus a fresh
        anonymous ``__client_id``; keeping the login ``_uid``/``__client_id`` is
        what makes login-required endpoints (e.g. /record/list) authenticated."""
        for name, value in self._login_cookies.items():
            self._cookies[name] = value
            try:
                client.cookies.set(name, value, domain=".luogu.com.cn", path="/")
            except Exception:
                pass

    async def warmup(self) -> str:
        """Perform the C3VK handshake. Returns the C3VK value (may be "")."""
        client = self._get_client()
        try:
            resp = await client.get(_LUOGU_HOME, headers=_headers(referer=False))
        except httpx.HTTPError as exc:
            raise LuoguBlockedError(f"warmup failed: {type(exc).__name__}") from exc
        self._restore_login_cookies(client)
        c3vk = _extract_c3vk(resp.text)
        if c3vk:
            client.cookies.set("C3VK", c3vk, domain=".luogu.com.cn", path="/")
            self._cookies["C3VK"] = c3vk
            try:
                redo = await client.get(_LUOGU_HOME, headers=_headers(referer=True))
                self._restore_login_cookies(client)
                _absorb_cookies(self._cookies, client.cookies, protected=set(self._login_cookies))
                _ = redo
            except httpx.HTTPError:
                pass  # the request above either sets __client_id or is redundant
        self._c3vk = c3vk
        self._warmed_at = time.monotonic()
        return c3vk

    async def _throttle(self) -> None:
        now = time.monotonic()
        delta = now - self._last_request
        if self._last_request and delta < self._min_interval:
            await asyncio.sleep(self._min_interval - delta)
        self._last_request = time.monotonic()

    async def get(self, path: str, params: dict[str, object] | None = None) -> httpx.Response:
        """Fetch ``path``, warming up (or re-warming) as needed."""
        async with self._sem:
            loop = asyncio.get_running_loop()
            stale = (
                not self._c3vk
                or (time.monotonic() - self._warmed_at) > self._cookie_ttl
                or self._client_loop is not loop
            )
            if stale:
                await self.warmup()

            for attempt in range(2):
                await self._throttle()
                client = self._get_client()
                try:
                    resp = await client.get(
                        f"{_LUOGU_HOME}{path.lstrip('/')}",
                        params=params,
                        headers=_headers(referer=True),
                    )
                except httpx.HTTPError as exc:
                    raise LuoguBlockedError(f"request failed: {type(exc).__name__}") from exc
                _absorb_cookies(self._cookies, client.cookies, protected=set(self._login_cookies))
                self._restore_login_cookies(client)

                if not _is_challenge(resp.text, resp.status_code):
                    return resp
                if attempt == 0:
                    await self.warmup()
                    continue
                raise LuoguBlockedError(
                    "Luogu anti-bot challenge persisted after re-warm-up",
                    retry_after_seconds=30.0,
                )

        raise LuoguBlockedError("request exhausted")  # pragma: no cover

    async def aclose(self) -> None:
        client, self._client = self._client, None
        self._client_loop = None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception:
                pass


__all__ = ["LuoguBlockedError", "LuoguClient"]
