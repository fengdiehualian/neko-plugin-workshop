"""MONETA FX 云端后端客户端。

插件有两种运行模式：

* **本地模式**（默认）：插件进程内跑一个 ``MarketRuntime``，数据存在
  N.E.K.O. 的插件 store 里，完全离线可用。
* **云端模式**：插件把请求转发到部署好的 MONETA FX 服务端
  （``moneta-fx-server`` 仓库），账户、持仓、行情全部在服务端共享，
  浏览器和 N.E.K.O. 面板看到的是同一个世界。

本模块只负责 HTTP 通信与会话 cookie 管理，不掺与业务逻辑——所有
请求/响应格式与 ``server/app.py`` 的 REST 接口一一对应。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class RemoteError(Exception):
    """云端后端返回的错误，message 直接透传服务端的中文提示。"""


class CloudClient:
    """MONETA FX 云端服务端的极简 HTTP 客户端（仅标准库）。"""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookie: Optional[str] = None

    # ── 底层请求 ───────────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        # 服务端的 CSRF 防护要求这个头，缺了会被判为跨站请求。
        request.add_header("X-Moneta-Request", "1")
        if self.cookie:
            request.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                set_cookie = response.headers.get("Set-Cookie")
                if set_cookie:
                    # 只保留 name=value 部分，丢掉 Path/HttpOnly 等属性。
                    self.cookie = set_cookie.split(";", 1)[0]
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            raise RemoteError(payload.get("error") or f"服务端返回 {error.code}") from None
        except urllib.error.URLError as error:
            raise RemoteError(f"无法连接服务端：{error.reason}") from None

    # ── 认证 ───────────────────────────────────────────────────────────
    def register(self, username: str, password: str) -> Dict[str, Any]:
        return self._request("POST", "/api/auth/register", {"username": username, "password": password})

    def login(self, username: str, password: str) -> Dict[str, Any]:
        return self._request("POST", "/api/auth/login", {"username": username, "password": password})

    def developer_login(self, key: str) -> Dict[str, Any]:
        return self._request("POST", "/api/auth/developer", {"key": key})

    def logout(self) -> Dict[str, Any]:
        return self._request("POST", "/api/auth/logout")

    def session(self) -> Dict[str, Any]:
        return self._request("GET", "/api/session")

    # ── 行情 ───────────────────────────────────────────────────────────
    def config(self) -> Dict[str, Any]:
        return self._request("GET", "/api/config")

    def symbols(self) -> Dict[str, Any]:
        return self._request("GET", "/api/symbols")

    def quotes(self, symbols: list[str]) -> Dict[str, Any]:
        """批量报价。服务端返回 list，这里归一成 ``{symbol: quote}`` dict，
        与本地模式的形状保持一致。"""

        query = ",".join(symbols)
        payload = self._request("GET", f"/api/quotes?symbols={query}")
        items = payload.get("quotes") if isinstance(payload, dict) else payload
        normalised: Dict[str, Any] = {}
        for item in items or []:
            if isinstance(item, dict) and item.get("symbol"):
                normalised[item["symbol"]] = item
        result: Dict[str, Any] = {"quotes": normalised}
        if isinstance(payload, dict):
            for key in ("time", "errors"):
                if key in payload:
                    result[key] = payload[key]
        return result

    def history(self, symbol: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/history?symbol={symbol}")

    # ── 交易 ───────────────────────────────────────────────────────────
    def world_state(self, symbol: str = "EUR/USD") -> Dict[str, Any]:
        return self._request("GET", f"/api/world/state?symbol={symbol}")

    def trade(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = {"action": action}
        body.update(payload)
        return self._request("POST", "/api/world/trade", body)

    def control(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/world/control", payload)

    def admin_snapshot(self) -> Dict[str, Any]:
        return self._request("GET", "/api/world/admin")

    # ── 管理 ───────────────────────────────────────────────────────────
    def admin_accounts(self) -> Dict[str, Any]:
        return self._request("GET", "/api/admin/accounts")

    def admin_account(self, account_id: str, limit: int = 100) -> Dict[str, Any]:
        return self._request("GET", f"/api/admin/account?id={account_id}&limit={limit}")

    def admin_ledger(self, limit: int = 20) -> Dict[str, Any]:
        return self._request("GET", f"/api/admin/ledger?limit={limit}")

    def funding(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/api/admin/funding", payload)

    # ── 连通性 ─────────────────────────────────────────────────────────
    def ping(self) -> bool:
        try:
            self.config()
            return True
        except RemoteError:
            return False
