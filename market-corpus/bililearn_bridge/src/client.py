"""HTTP client for the local BiliLearn web panel.

BiliLearn 3.1.6 guards every ``/api/*`` route behind a Flask session cookie:

1. a disclaimer must be acknowledged (``POST /api/disclaimer/confirm``),
2. the panel must have an account (auto-seeded as ``yaya`` on first start),
3. the session must be logged in (``POST /api/auth/login``).

Only ``/api/health``, ``/api/asr/status``, ``/deploy_status``, ``/assets/*``
and the ``/api/auth/*`` family are reachable before login. This client walks
that handshake once, keeps the cookie jar, and then speaks to any of the 300+
panel endpoints on the catgirl's behalf.

Standard library only (http.cookiejar / urllib / asyncio / threading).
"""

from __future__ import annotations

import asyncio
import http.cookiejar
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HEALTH_PATH = "/api/health"
DISCLAIMER_PATH = "/api/disclaimer/confirm"
LOGIN_PATH = "/api/auth/login"
SETUP_PATH = "/api/auth/setup"
AUTH_STATUS_PATH = "/api/auth/status"

# /api/health advertises this so we never mistake a foreign process on the
# port for the panel (utils/web_launcher.py: is_our_panel).
SERVICE_ID = "bilibili-learning-bot-web"
DISCLAIMER_ACCEPT = "我同意"

# The panel seeds this local-only account on first start
# (web_panel._ensure_default_panel_account). Only ever tried once, because
# repeated failures trip a 10-minute login rate limit.
SEEDED_USERNAME = "yaya"
SEEDED_PASSWORD = "yaya"

# Sentinel returned by the panel when no account exists yet; the only
# condition under which auto-setup may run (it would otherwise overwrite an
# existing password, since /api/auth/setup is not guarded by an "already
# configured" check).
_NOT_CONFIGURED_MARKER = "尚未设置"

_SAFE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


class BiliLearnClientError(ValueError):
    """Raised for local misuse, e.g. a malformed path or method."""


class BiliLearnClient:
    """Cookie-aware JSON client for one BiliLearn panel instance."""

    def __init__(
        self,
        *,
        port: int,
        host: str = "127.0.0.1",
        username: str = "",
        password: str = "",
        auto_setup: bool = True,
        state_dir: Path | None = None,
        timeout: float = 20.0,
        logger: Any = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.username = (username or "").strip()
        self.password = password or ""
        self.auto_setup = bool(auto_setup)
        self.state_dir = Path(state_dir) if state_dir else None
        self.timeout = float(timeout)
        self.logger = logger

        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._lock = threading.Lock()
        self._authenticated = False
        self._cred_source = ""
        self._last_auth_error = ""
        self._auth_block_until = 0.0

    # ------------------------------------------------------------------ #
    # URLs
    # ------------------------------------------------------------------ #
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:  # noqa: BLE001 - logging must never break a call
            pass

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #
    def _sync_request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict:
        method = method.upper()
        if method not in _SAFE_METHODS:
            raise BiliLearnClientError(f"不支持的 HTTP 方法: {method}")
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise BiliLearnClientError(f"非法接口路径: {path!r}")

        url = f"{self.base_url}{path}"
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean, doseq=True)

        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                payload = json.loads(raw) if raw else {}
                return {"ok": True, "status": response.status, "data": payload}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return {"ok": False, "status": exc.code, "data": payload}
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return {"ok": False, "status": 0, "data": {"error": str(exc)}}

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict:
        # One lock guards the shared cookie jar across worker threads.
        with self._lock:
            return self._sync_request(method, path, body=body, query=query, timeout=timeout)

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict:
        return await asyncio.to_thread(self._request, method, path, body=body, query=query, timeout=timeout)

    # ------------------------------------------------------------------ #
    # Readiness
    # ------------------------------------------------------------------ #
    def _is_our_panel(self, payload: Any) -> bool:
        return isinstance(payload, dict) and payload.get("ok") is True and payload.get("service") == SERVICE_ID

    async def health(self) -> bool:
        """True when the panel (not some other process) answers on the port."""
        try:
            res = await self.request("GET", HEALTH_PATH, timeout=3.0)
        except BiliLearnClientError:
            return False
        return bool(res.get("ok")) and self._is_our_panel(res.get("data"))

    # ------------------------------------------------------------------ #
    # Credentials
    # ------------------------------------------------------------------ #
    def _cred_file(self) -> Path | None:
        if self.state_dir is None:
            return None
        return Path(self.state_dir) / "panel_credentials.json"

    def _load_saved_credentials(self) -> tuple[str, str] | None:
        path = self._cred_file()
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        user = str(payload.get("username") or "").strip()
        pwd = str(payload.get("password") or "")
        if user and pwd:
            return user, pwd
        return None

    def _save_credentials(self, username: str, password: str) -> None:
        path = self._cred_file()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"username": username, "password": password}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.chmod(path, 0o600)
        except OSError as exc:
            self._log("warning", "保存面板凭据失败: %s", exc)

    def _candidate_credentials(self) -> list[tuple[str, str, str]]:
        """Ordered (username, password, source) attempts. Never brute-forces."""
        attempts: list[tuple[str, str, str]] = []
        if self.username and self.password:
            attempts.append((self.username, self.password, "config"))
        saved = self._load_saved_credentials()
        if saved and saved not in {(u, p) for u, p, _ in attempts}:
            attempts.append((saved[0], saved[1], "saved"))
        if not attempts:
            attempts.append((SEEDED_USERNAME, SEEDED_PASSWORD, "seeded-default"))
        return attempts

    # ------------------------------------------------------------------ #
    # Auth handshake
    # ------------------------------------------------------------------ #
    async def ensure_auth(self, *, force: bool = False) -> dict:
        """Walk disclaimer + login so later calls carry a valid session.

        Returns ``{"authenticated": bool, "mode": str, "message": str}``.
        Safe to call before every entry point: it short-circuits on a live
        session and backs off after a hard failure to avoid the panel's
        login rate limit.
        """
        if not force and self._authenticated:
            res = await self.request("GET", AUTH_STATUS_PATH, timeout=5.0)
            if res.get("ok") and bool((res.get("data") or {}).get("authenticated")):
                return {"authenticated": True, "mode": "cached", "message": "已登录"}
            self._authenticated = False

        if not force and time.monotonic() < self._auth_block_until:
            return {"authenticated": False, "mode": "cooldown", "message": self._last_auth_error}

        # 1) Disclaimer (idempotent, allowed pre-auth).
        await self.request("POST", DISCLAIMER_PATH, body={"agree": DISCLAIMER_ACCEPT}, timeout=5.0)

        # 2) Maybe already logged in (session cookie survived a restart).
        res = await self.request("GET", AUTH_STATUS_PATH, timeout=5.0)
        if res.get("ok") and bool((res.get("data") or {}).get("authenticated")):
            self._authenticated = True
            self._cred_source = "session"
            return {"authenticated": True, "mode": "session", "message": "会话有效"}

        # 3) Log in with the best available credentials.
        last_message = "面板需要登录"
        for username, password, source in self._candidate_credentials():
            res = await self.request(
                "POST", LOGIN_PATH, body={"username": username, "password": password}, timeout=10.0
            )
            payload = res.get("data") or {}
            if res.get("ok") and payload.get("ok"):
                self._authenticated = True
                self._cred_source = source
                return {"authenticated": True, "mode": source, "message": "登录成功"}

            last_message = str(payload.get("message") or payload.get("error") or "登录失败")
            if res.get("status") == 429:
                self._remember_failure(f"登录被限流：{last_message}", cooldown=600.0)
                return {"authenticated": False, "mode": "rate-limited", "message": self._last_auth_error}

            # Panel has no account yet: this is the ONLY safe moment to create
            # one, because /api/auth/setup rewrites existing credentials.
            if self.auto_setup and _NOT_CONFIGURED_MARKER in last_message:
                created = await self._auto_setup()
                if created.get("authenticated"):
                    return created
                last_message = created.get("message", last_message)
                break

        self._remember_failure(
            f"面板登录失败（{last_message}）。请在插件配置中填写 panel_username / panel_password。",
            cooldown=120.0,
        )
        return {"authenticated": False, "mode": "failed", "message": self._last_auth_error}

    def _remember_failure(self, message: str, *, cooldown: float) -> None:
        self._last_auth_error = message
        self._authenticated = False
        self._auth_block_until = time.monotonic() + cooldown
        self._log("warning", "%s", message)

    async def _auto_setup(self) -> dict:
        """Create a panel account when none exists, then persist it privately."""
        username = self.username or SEEDED_USERNAME
        password = self.password or secrets.token_urlsafe(18)
        res = await self.request(
            "POST", SETUP_PATH, body={"username": username, "password": password}, timeout=15.0
        )
        payload = res.get("data") or {}
        if res.get("ok") and payload.get("ok"):
            self._authenticated = True
            self._cred_source = "auto-setup"
            self.username, self.password = username, password
            self._save_credentials(username, password)
            self._log("info", "已为 BiliLearn 面板创建账号 %s", username)
            return {
                "authenticated": True,
                "mode": "auto-setup",
                "message": "面板尚未配置，已自动创建账号（凭据保存在插件数据目录）",
            }
        message = str(payload.get("message") or payload.get("error") or "自动创建账号失败")
        return {"authenticated": False, "mode": "setup-failed", "message": message}

    # ------------------------------------------------------------------ #
    # API surface
    # ------------------------------------------------------------------ #
    async def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
        reauth: bool = True,
    ) -> dict:
        """Call any panel endpoint, transparently (re)authenticating once."""
        if reauth:
            auth = await self.ensure_auth()
            if not auth.get("authenticated"):
                return {"ok": False, "status": 401, "data": {"message": auth.get("message", "未登录")}}

        res = await self.request(method, path, body=body, query=query, timeout=timeout)

        # A live session can expire (panel restart / secret rotation): retry once.
        if (
            reauth
            and not res.get("ok")
            and res.get("status") == 401
            and bool((res.get("data") or {}).get("auth_required"))
        ):
            self._authenticated = False
            auth = await self.ensure_auth(force=True)
            if auth.get("authenticated"):
                res = await self.request(method, path, body=body, query=query, timeout=timeout)
        return res

    async def get(self, path: str, **kwargs: Any) -> dict:
        return await self.call("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> dict:
        return await self.call("POST", path, **kwargs)
