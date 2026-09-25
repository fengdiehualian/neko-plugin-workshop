"""Hosted-UI actions for plugin-private Luogu credentials.

Mirrors ``plugin/plugins/netease_music/credential_ui.py``: the ``@ui.context``
endpoint only ever returns booleans (never the cookie value), and save/clear are
``@ui.action`` + ``@plugin_entry`` with ``writeOnly`` fields plus a per-process
``ui_token`` that restricts calls to the plugin's own Hosted UI.
"""

from __future__ import annotations

import secrets
from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui

from .credentials import CredentialError, CredentialStore, normalize_luogu_cookies


class LuoguCredentialUiMixin:
    """Keep credential management isolated from the public query entries."""

    _credential_store: CredentialStore
    _luogu_cookies: dict[str, str]
    _credential_ui_token: str
    i18n: Any

    def _init_credential_store(self) -> None:
        self._credential_store = CredentialStore(self.data_path())  # type: ignore[attr-defined]
        self._luogu_cookies = {}
        self._credential_ui_token = secrets.token_urlsafe(32)

    async def _load_luogu_cookies(self) -> None:
        self._luogu_cookies = await self._credential_store.load()

    @ui.context(id="credentials", title=tr("panel.title", default="洛谷"))
    async def get_credentials_ui_context(self) -> dict[str, object]:
        return {
            "cookie_configured": bool(self._luogu_cookies),
            "cookie_count": len(self._luogu_cookies),
            "storage": "plugin_private_encrypted",
            "action_token": self._credential_ui_token,
        }

    def _valid_credential_ui_token(self, candidate: object) -> bool:
        if not isinstance(candidate, str):
            return False
        try:
            return secrets.compare_digest(candidate, self._credential_ui_token)
        except TypeError:
            return False

    async def _invalidate_credential_users(self) -> None:
        invalidator = getattr(self, "_invalidate_credential_requests", None)
        if callable(invalidator):
            await invalidator()

    @ui.action(
        id="save_luogu_cookie",
        label=tr("actions.save.label", default="保存登录凭据"),
        icon="💾",
        tone="success",
        group="credentials",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="save_luogu_cookie",
        name=tr("entry.save.name", default="保存洛谷凭据"),
        description=tr(
            "entry.save.description",
            default="将洛谷登录 Cookie 白名单字段加密保存到插件私有数据目录。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "cookie": {
                    "type": "string",
                    "writeOnly": True,
                    "minLength": 1,
                    "maxLength": 8192,
                },
                "ui_token": {
                    "type": "string",
                    "writeOnly": True,
                    "minLength": 1,
                },
            },
            "required": ["cookie", "ui_token"],
            "additionalProperties": False,
        },
    )
    async def save_luogu_cookie(self, cookie: str = "", ui_token: str = "", **_: object):
        if not self._valid_credential_ui_token(ui_token):
            return Err(SdkError("该凭据操作只能从插件 Hosted UI 调用。", code="ui_only"))
        cookies = normalize_luogu_cookies(cookie)
        if not cookies:
            return Err(
                SdkError(self.i18n.t("errors.invalid_cookie", default="cookie 格式无效（需含登录 uid 字段）。"))
            )
        try:
            await self._credential_store.save(cookies)
        except CredentialError:
            return Err(SdkError(self.i18n.t("errors.save_cookie", default="凭据保存失败。")))
        self._luogu_cookies = cookies
        await self._invalidate_credential_users()
        return Ok({"cookie_configured": True, "cookie_count": len(cookies)})

    @ui.action(
        id="clear_luogu_cookie",
        label=tr("actions.clear.label", default="清除登录凭据"),
        icon="🗑️",
        tone="danger",
        group="credentials",
        order=20,
        confirm=True,
        refresh_context=True,
    )
    @plugin_entry(
        id="clear_luogu_cookie",
        name=tr("entry.clear.name", default="清除洛谷凭据"),
        description=tr(
            "entry.clear.description",
            default="删除洛谷插件私有数据目录中保存的登录 Cookie。",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "ui_token": {"type": "string", "writeOnly": True, "minLength": 1}
            },
            "required": ["ui_token"],
            "additionalProperties": False,
        },
    )
    async def clear_luogu_cookie(self, ui_token: str = "", **_: object):
        if not self._valid_credential_ui_token(ui_token):
            return Err(SdkError("该凭据操作只能从插件 Hosted UI 调用。", code="ui_only"))
        try:
            await self._credential_store.clear()
        except CredentialError:
            return Err(SdkError(self.i18n.t("errors.clear_cookie", default="凭据清除失败。")))
        self._luogu_cookies = {}
        await self._invalidate_credential_users()
        return Ok({"cookie_configured": False, "cookie_count": 0})


__all__ = ["LuoguCredentialUiMixin"]
