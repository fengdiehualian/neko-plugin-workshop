"""平台连接管理：凭据存储 + 登录 + 读取。

凭据只落在插件自己的 ``data/credentials.json``，用管理员密码做轻量混淆
（防误读，不是加密强度），且 ``data/`` 已被 .gitignore 排除，绝不进 git。
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Optional

from ._crawl import Crawler
from ._platforms import (
    PLATFORM_BY_ID,
    PlatformAdapter,
    PlatformSpec,
    list_platforms,
)


def _obfuscate(raw: str, key: str) -> str:
    seed = (key or "neko").encode("utf-8")
    data = raw.encode("utf-8")
    mixed = bytes(byte ^ seed[index % len(seed)] for index, byte in enumerate(data))
    return base64.b64encode(mixed).decode("ascii")


def _deobfuscate(payload: str, key: str) -> str:
    seed = (key or "neko").encode("utf-8")
    try:
        data = base64.b64decode(payload.encode("ascii"))
    except Exception:
        return ""
    mixed = bytes(byte ^ seed[index % len(seed)] for index, byte in enumerate(data))
    return mixed.decode("utf-8", errors="replace")


class CredentialStore:
    """账号凭据的本地存储。"""

    def __init__(self, data_dir: Path, key: str = "") -> None:
        self.path = Path(data_dir) / "credentials.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = key or "neko"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(_deobfuscate(raw, self._key) or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        payload = _obfuscate(json.dumps(data, ensure_ascii=False), self._key)
        self.path.write_text(payload, encoding="utf-8")

    def list_accounts(self) -> list[dict[str, Any]]:
        data = self._load()
        rows = []
        for platform_id, item in data.items():
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "platform": platform_id,
                    "username": item.get("username", ""),
                    "updated": item.get("updated", 0),
                    "connected": bool(item.get("connected")),
                }
            )
        return rows

    def get(self, platform_id: str) -> dict[str, Any]:
        item = self._load().get(platform_id)
        return item if isinstance(item, dict) else {}

    def put(self, platform_id: str, username: str, password: str, connected: bool = False) -> None:
        data = self._load()
        data[platform_id] = {
            "username": username,
            "password": password,
            "connected": connected,
            "updated": int(time.time()),
        }
        self._save(data)

    def mark_connected(self, platform_id: str, connected: bool) -> None:
        data = self._load()
        item = data.get(platform_id)
        if isinstance(item, dict):
            item["connected"] = bool(connected)
            item["updated"] = int(time.time())
            self._save(data)

    def delete(self, platform_id: str) -> None:
        data = self._load()
        data.pop(platform_id, None)
        self._save(data)


class ConnectorManager:
    """把「平台适配器 + 凭据 + 抓取器」串起来。"""

    def __init__(self, crawler: Crawler, data_dir: Path, admin_key: str = "") -> None:
        self.crawler = crawler
        self.store = CredentialStore(data_dir, admin_key)
        self.sessions: dict[str, dict[str, Any]] = {}

    def available(self) -> list[dict[str, Any]]:
        accounts = {row["platform"]: row for row in self.store.list_accounts()}
        rows = []
        for spec in list_platforms():
            account = accounts.get(spec["id"], {})
            rows.append(
                {
                    **spec,
                    "username": account.get("username", ""),
                    "connected": bool(account.get("connected")) or bool(self.sessions.get(spec["id"], {}).get("ok")),
                    "updated": account.get("updated", 0),
                }
            )
        return rows

    def _spec_for(self, platform_id: str, base_url: str = "") -> Optional[PlatformSpec]:
        spec = PLATFORM_BY_ID.get(platform_id)
        if spec is None:
            return None
        if platform_id == "jwc" and base_url:
            base = base_url.rstrip("/")
            spec = PlatformSpec(
                id=spec.id,
                name=spec.name,
                kind=spec.kind,
                home=base,
                login_url=f"{base}/login",
                username_field=spec.username_field,
                password_field=spec.password_field,
                captcha_field=spec.captcha_field,
                fail_texts=spec.fail_texts,
                read_paths=(f"{base}/xscjcx.aspx", f"{base}/xskb.aspx"),
                notes=spec.notes,
            )
        return spec

    def connect(
        self,
        platform_id: str,
        username: str,
        password: str,
        captcha: str = "",
        base_url: str = "",
        remember: bool = True,
    ) -> dict[str, Any]:
        spec = self._spec_for(platform_id, base_url)
        if spec is None:
            return {"ok": False, "status": "unknown_platform", "message": f"未知平台：{platform_id}"}
        if not spec.home and not base_url:
            return {"ok": False, "status": "need_base_url", "message": "请先在面板填写该教务系统的首页地址"}
        if remember and username and password:
            self.store.put(platform_id, username, password, connected=False)
        adapter = PlatformAdapter(spec, self.crawler)
        outcome = adapter.login(username, password, captcha)
        self.sessions[platform_id] = outcome
        if outcome.get("ok"):
            self.store.mark_connected(platform_id, True)
        return outcome

    def disconnect(self, platform_id: str) -> dict[str, Any]:
        self.sessions.pop(platform_id, None)
        self.store.delete(platform_id)
        self.crawler.clear_cookies()
        return {"ok": True, "status": "ok", "message": f"已断开 {platform_id} 并清除本机 Cookie"}

    def fetch_raw(self, platform_id: str, base_url: str = "", limit: int = 5000) -> dict[str, Any]:
        """抓取该平台登录后可见的页面原文，交给上层用大模型结构化。"""
        spec = self._spec_for(platform_id, base_url)
        if spec is None:
            return {"ok": False, "status": "unknown_platform", "pages": []}
        if not self.sessions.get(platform_id, {}).get("ok") and spec.login_url:
            return {
                "ok": False,
                "status": "not_connected",
                "message": f"{spec.name} 尚未登录，请先在面板连接",
                "pages": [],
            }
        adapter = PlatformAdapter(spec, self.crawler)
        pages = adapter.read_pages(limit=limit)
        return {
            "ok": any(page.get("ok") for page in pages),
            "status": "ok" if pages else "no_page",
            "platform": platform_id,
            "pages": pages,
        }

    def status(self) -> dict[str, Any]:
        connected = [row["id"] for row in self.available() if row.get("connected")]
        return {
            "platforms": self.available(),
            "connected": connected,
            "cookie_count": len(list(self.crawler.cookiejar)),
        }
