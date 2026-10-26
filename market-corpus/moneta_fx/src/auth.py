"""Accounts, sessions and credentials shared by the plugin and the cloud server."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
import uuid
from typing import Any, Dict, Optional

try:
    from .engine import TradeError
except ImportError:  # 平铺导入（本地测试、云端服务端）
    from engine import TradeError  # type: ignore

SESSION_COOKIE = "moneta_session"
SESSION_TTL_MS = 86400000
USERNAME_PATTERN = re.compile(r"^[\w一-鿿-]{2,24}$", re.UNICODE)
REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
AMOUNT_PATTERN = re.compile(r"^\d+(?:\.\d{1,2})?$")

LOGIN_ATTEMPT_LIMIT = 15
LOGIN_ATTEMPT_WINDOW_MS = 600000


def now_ms() -> int:
    return int(time.time() * 1000)


def digest_password(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"), n=1 << 14, r=8, p=1, dklen=64).hex()


def constant_time_equal(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if len(left.encode("utf-8")) != len(right.encode("utf-8")):
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def new_id() -> str:
    return str(uuid.uuid4())


class SessionStore:
    """In-memory login sessions keyed by a random cookie token."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def create(self, user_id: str, ttl_ms: int = SESSION_TTL_MS) -> str:
        token = secrets.token_hex(32)
        self._sessions[token] = {"userId": user_id, "expires": now_ms() + ttl_ms}
        return token

    def revoke(self, token: Optional[str]) -> None:
        if token:
            self._sessions.pop(token, None)

    def user_id(self, token: Optional[str]) -> Optional[str]:
        session = self._sessions.get(token or "")
        if not session or session["expires"] < now_ms():
            return None
        return session["userId"]

    def revoke_user(self, user_id: str) -> None:
        for token in [t for t, s in self._sessions.items() if s["userId"] == user_id]:
            self._sessions.pop(token, None)

    def active_user_ids(self) -> set:
        current = now_ms()
        return {s["userId"] for s in self._sessions.values() if s["expires"] > current}

    def prune(self) -> None:
        current = now_ms()
        for token in [t for t, s in self._sessions.items() if s["expires"] < current]:
            self._sessions.pop(token, None)


def cookie_header(token: str, secure: bool) -> str:
    parts = [
        f"{SESSION_COOKIE}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Strict",
        f"Max-Age={SESSION_TTL_MS // 1000}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header() -> str:
    return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


def read_cookie(header: Optional[str], name: str = SESSION_COOKIE) -> Optional[str]:
    if not header:
        return None
    for chunk in header.split(";"):
        key, _, value = chunk.strip().partition("=")
        if key == name:
            return value or None
    return None


class LoginLimiter:
    """Per-address throttle on credential endpoints."""

    def __init__(self) -> None:
        self._attempts: Dict[str, Dict[str, Any]] = {}

    def check(self, address: str) -> None:
        attempt = self._attempts.get(address)
        if attempt and attempt["until"] > now_ms() and attempt["count"] >= LOGIN_ATTEMPT_LIMIT:

            raise TradeError("登录尝试过多，请 10 分钟后再试", status=429)

    def denied(self, address: str) -> None:
        attempt = self._attempts.setdefault(address, {"count": 0, "until": now_ms() + LOGIN_ATTEMPT_WINDOW_MS})
        attempt["count"] += 1
        attempt["until"] = now_ms() + LOGIN_ATTEMPT_WINDOW_MS

    def clear(self, address: str) -> None:
        self._attempts.pop(address, None)

    def prune(self) -> None:
        current = now_ms()
        for address in [a for a, attempt in self._attempts.items() if attempt["until"] < current]:
            self._attempts.pop(address, None)


def validate_credentials(username: Any, password: Any) -> None:

    if not isinstance(username, str) or not USERNAME_PATTERN.match(username):

        raise TradeError("用户名需为 2–24 个中英文、数字或下划线")
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise TradeError("密码需为 8–128 个字符")
