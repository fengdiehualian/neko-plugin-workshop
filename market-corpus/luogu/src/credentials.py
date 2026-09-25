"""Plugin-local encrypted storage for allow-listed Luogu login cookies.

Mirrors ``plugin/plugins/netease_music/credentials.py``: the cookie value is
Fernet-encrypted to a private file under ``data_path()`` with a separate key
file, written atomically and chmod 0600 on POSIX. Only allow-listed fields are
retained, and values are sanitised to reject whitespace / ``;`` / control
characters so they cannot be used to smuggle extra headers.

NOTE (calibrate): the allow-listed login fields below are a best guess against
Luogu's current session cookies. Confirm the exact field names (likely
``_uid`` / ``__client_id``) via the browser devtools before relying on a saved
login cookie long-term.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_COOKIE_FILE = "luogu_credentials.bin"
_KEY_FILE = "luogu_credentials.key"
_MAX_COOKIE_LENGTH = 4096
_MAX_COOKIE_INPUT_LENGTH = 8192
# Login-relevant Luogu cookies. Anonymous C3VK is intentionally excluded — it is
# obtained live by the client and expires in ~5 minutes.
_COOKIE_NAMES = {
    "_uid": "_uid",
    "__uid": "__uid",
    "__client_id": "__client_id",
    "_id": "_id",
}
_LOGIN_FIELDS = ("_uid", "__uid")
# Users often paste cookies separated by a fullwidth semicolon '；' (U+FF1B) from
# the browser devtools; split on both the ASCII ';' and '；'.
_COOKIE_SEP_RE = re.compile(r"[;；]")


class CredentialError(RuntimeError):
    """The plugin-local credential could not be safely read or written."""


def _normalize_cookie_value(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_COOKIE_LENGTH:
        return ""
    # Cookie values must be printable ASCII. Rejecting non-ASCII here prevents a
    # stray fullwidth '；' / CJK / emoji pasted into one field from corrupting the
    # stored cookie (which otherwise blows up httpx's ascii Cookie-header encode).
    if any(
        char.isspace() or char == ";" or not (32 <= ord(char) <= 126)
        for char in normalized
    ):
        return ""
    return normalized


def normalize_luogu_cookies(value: object) -> dict[str, str]:
    """Normalize a Cookie header/dict while retaining only Luogu login fields."""
    raw_cookies: dict[str, object] = {}
    if isinstance(value, Mapping):
        raw_cookies = {str(key): candidate for key, candidate in value.items() if isinstance(key, str)}
    elif isinstance(value, str):
        raw = value.strip()
        if not raw or len(raw) > _MAX_COOKIE_INPUT_LENGTH:
            return {}
        first_name, separator, _first_value = raw.partition("=")
        is_cookie_header = bool(_COOKIE_SEP_RE.search(raw)) or (
            bool(separator) and first_name.strip().lower() in _COOKIE_NAMES
        )
        if not is_cookie_header:
            raw_cookies["_uid"] = raw
        else:
            for item in _COOKIE_SEP_RE.split(raw):
                if "=" not in item:
                    continue
                key, candidate = item.strip().split("=", 1)
                raw_cookies[key.strip().lower()] = candidate.strip()
    else:
        return {}

    normalized: dict[str, str] = {}
    for key, candidate in raw_cookies.items():
        canonical = _COOKIE_NAMES.get(key.strip().lower())
        if canonical is None:
            continue
        cookie_value = _normalize_cookie_value(candidate)
        if cookie_value:
            normalized[canonical] = cookie_value
    if not any(login in normalized for login in _LOGIN_FIELDS):
        return {}
    return normalized


class CredentialStore:
    """Encrypt Luogu cookies inside this plugin's private data directory."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._cookie_path = self._data_dir / _COOKIE_FILE
        self._key_path = self._data_dir / _KEY_FILE
        self._lock = asyncio.Lock()

    async def configured(self) -> bool:
        return bool((await self.load()))

    async def load(self) -> dict[str, str]:
        async with self._lock:
            try:
                return await asyncio.to_thread(self._load_sync)
            except (OSError, InvalidToken, UnicodeError, ValueError, json.JSONDecodeError):
                return {}

    async def save(self, value: object) -> None:
        cookies = normalize_luogu_cookies(value)
        if not cookies:
            raise CredentialError("no valid Luogu login cookie")
        async with self._lock:
            try:
                await asyncio.to_thread(self._save_sync, cookies)
            except (OSError, ValueError) as exc:
                raise CredentialError("Luogu cookies could not be saved") from exc

    async def clear(self) -> None:
        async with self._lock:
            try:
                await asyncio.to_thread(self._clear_sync)
            except OSError as exc:
                raise CredentialError("Luogu cookies could not be cleared") from exc

    def _load_sync(self) -> dict[str, str]:
        if not self._cookie_path.is_file() or not self._key_path.is_file():
            return {}
        key = self._key_path.read_bytes()
        encrypted = self._cookie_path.read_bytes()
        payload = json.loads(Fernet(key).decrypt(encrypted).decode("utf-8"))
        if not isinstance(payload, dict):
            return {}
        return normalize_luogu_cookies(payload)

    def _save_sync(self, cookies: dict[str, str]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        key = (
            self._key_path.read_bytes()
            if self._key_path.is_file()
            else Fernet.generate_key()
        )
        try:
            fernet = Fernet(key)
        except ValueError:
            key = Fernet.generate_key()
            fernet = Fernet(key)
        encrypted = fernet.encrypt(json.dumps(cookies, ensure_ascii=False).encode("utf-8"))
        self._atomic_write(self._key_path, key)
        self._atomic_write(self._cookie_path, encrypted)

    def _clear_sync(self) -> None:
        for path in (self._cookie_path, self._key_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                path.chmod(0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


__all__ = ["CredentialError", "CredentialStore", "normalize_luogu_cookies"]
