"""猫娘邮件插件 —— 让猫娘帮你收发邮件。

支持任意 SMTP / IMAP / POP3 邮箱（QQ、163、Gmail、Outlook、企业邮等），
可配置多个账号，定时查收并喵一声提醒主人，也能代主人发信。

遵循 N.E.K.O 插件开发标准实现：
- ``@neko_plugin`` + ``NekoPluginBase``
- ``@plugin_entry`` 暴露给 LLM / 用户的能力
- ``@lifecycle(startup/shutdown)`` 生命周期
- ``@timer_interval`` 定时查信
- 账号凭据持久化到 ``PluginStore``
- 新邮件通过 ``self.ctx.push_message`` 推送给主人
"""
from __future__ import annotations

import asyncio
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    timer_interval,
)

from .mail_core import Account, count_unseen, fetch_message, fetch_unseen, send_message

_STORE_KEY = "accounts"


@neko_plugin
class MailAllPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        try:
            self.logger = self.enable_file_logging(log_level="INFO")
        except Exception:
            self.logger = None
        # 已推送过的邮件 uid（进程内去重；重启后重新推送一次，安全无害）
        self._seen: dict[str, set[str]] = {}

    # ----------------------------- 工具方法 -----------------------------
    def _log(self, level: str, msg: str, *args: Any) -> None:
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg, *args)

    def _load_accounts(self) -> dict[str, dict[str, Any]]:
        if not self.store.enabled:
            return {}
        return self.store._read_value(_STORE_KEY, {}) or {}

    def _save_accounts(self, accounts: dict[str, dict[str, Any]]) -> None:
        if not self.store.enabled:
            raise SdkError("PluginStore 未启用，无法保存账号")
        self.store._write_value(_STORE_KEY, accounts)

    def _send_push(self, account: str, mail: dict[str, Any]) -> None:
        body = (mail.get("body") or "").strip().replace("\r", "")
        preview = body[:400] + ("…" if len(body) > 400 else "")
        text = (
            f"📬 喵~  `{account}` 收到来自 {mail.get('from','?')} 的新邮件！\n"
            f"主题：{mail.get('subject') or '(无主题)'}\n"
            f"时间：{mail.get('date','')}\n\n"
            f"{preview}"
        )
        self.ctx.push_message(
            source="mail_all",
            visibility=[],
            ai_behavior="respond",
            parts=[{"type": "text", "text": text}],
            priority=6,
            metadata={
                "account": account,
                "uid": mail.get("uid"),
                "event_type": "new_mail",
                "subject": mail.get("subject"),
                "from": mail.get("from"),
            },
        )

    # ----------------------------- 生命周期 -----------------------------
    @lifecycle(id="startup")
    async def startup(self, **_):
        if not self.store.enabled:
            self.store.enabled = True
            self._log("warning", "Store 未启用，已强制开启")
        return Ok({"status": "running"})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self._log("info", "MailAll 插件已停止")
        return Ok({"status": "shutdown"})

    # ----------------------------- 账号管理 -----------------------------
    @plugin_entry(
        id="add_account",
        name="添加邮箱账号",
        description=(
            "配置一个邮箱账号（IMAP/POP3 收件 + SMTP 发件），猫娘就能帮你收发邮件啦~\n"
            "recv_type 可选 imap / pop3；recv_ssl / smtp_ssl 为 true 表示用 SSL 加密。\n"
            "不填 recv_user / smtp_user 时默认用邮箱地址作为登录名。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "账号别名，例如 '我的QQ邮箱'"},
                "email": {"type": "string", "description": "邮箱地址"},
                "recv_type": {"type": "string", "description": "收件协议 imap 或 pop3", "default": "imap"},
                "recv_host": {"type": "string", "description": "收件服务器地址，如 imap.qq.com / pop.qq.com"},
                "recv_port": {"type": "integer", "description": "收件端口，IMAP SSL=993，POP3 SSL=995", "default": 993},
                "recv_ssl": {"type": "boolean", "description": "收件是否使用 SSL", "default": True},
                "recv_user": {"type": "string", "description": "收件登录名（默认=邮箱地址）", "default": ""},
                "recv_pass": {"type": "string", "description": "收件密码 / 授权码"},
                "smtp_host": {"type": "string", "description": "发件服务器地址，如 smtp.qq.com"},
                "smtp_port": {"type": "integer", "description": "发件端口，SSL=465，STARTTLS=587", "default": 465},
                "smtp_ssl": {"type": "boolean", "description": "true=SSL(465)，false=STARTTLS(587)", "default": True},
                "smtp_user": {"type": "string", "description": "发件登录名（默认=邮箱地址）", "default": ""},
                "smtp_pass": {"type": "string", "description": "发件密码 / 授权码（不填则复用 recv_pass）", "default": ""},
                "display_name": {"type": "string", "description": "发件时显示的名字", "default": ""},
            },
            "required": ["name", "email", "recv_host", "recv_pass", "smtp_host"],
        },
        llm_result_fields=["status", "account"],
    )
    async def add_account(
        self,
        name: str,
        email: str,
        recv_type: str = "imap",
        recv_host: str = "",
        recv_port: int = 993,
        recv_ssl: bool = True,
        recv_user: str = "",
        recv_pass: str = "",
        smtp_host: str = "",
        smtp_port: int = 465,
        smtp_ssl: bool = True,
        smtp_user: str = "",
        smtp_pass: str = "",
        display_name: str = "",
        **_,
    ):
        accounts = self._load_accounts()
        if name in accounts:
            return Err(SdkError(f"账号 '{name}' 已存在，请先删除或用别的名字"))
        if not recv_host or not smtp_host:
            return Err(SdkError("recv_host（收件服务器）和 smtp_host（发件服务器）都必须填写"))
        acc = Account(
            name=name,
            email=email,
            recv_type=recv_type,
            recv_host=recv_host,
            recv_port=int(recv_port),
            recv_ssl=bool(recv_ssl),
            recv_user=recv_user or email,
            recv_pass=recv_pass,
            smtp_host=smtp_host,
            smtp_port=int(smtp_port),
            smtp_ssl=bool(smtp_ssl),
            smtp_user=smtp_user or email,
            smtp_pass=smtp_pass or recv_pass,
            display_name=display_name,
        )
        accounts[name] = acc.to_dict()
        self._save_accounts(accounts)
        return Ok({"status": "added", "account": name, "email": email, "recv_type": recv_type})

    @plugin_entry(
        id="list_accounts",
        name="列出邮箱账号",
        description="列出已配置的所有邮箱账号（密码已隐藏，喵）。",
        llm_result_fields=["count", "accounts"],
    )
    async def list_accounts(self, **_):
        accounts = self._load_accounts()
        safe = [
            {
                "name": a.get("name"),
                "email": a.get("email"),
                "recv_type": a.get("recv_type"),
                "recv_host": a.get("recv_host"),
                "smtp_host": a.get("smtp_host"),
            }
            for a in accounts.values()
        ]
        return Ok({"count": len(safe), "accounts": safe})

    @plugin_entry(
        id="remove_account",
        name="删除邮箱账号",
        description="删除一个已配置的邮箱账号。",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "要删除的账号别名"}},
            "required": ["name"],
        },
        llm_result_fields=["status", "removed"],
    )
    async def remove_account(self, name: str, **_):
        accounts = self._load_accounts()
        if name not in accounts:
            return Err(SdkError(f"未找到账号 '{name}'"))
        accounts.pop(name)
        self._save_accounts(accounts)
        self._seen.pop(name, None)
        return Ok({"status": "removed", "removed": name})

    # ----------------------------- 发件 -----------------------------
    @plugin_entry(
        id="send_mail",
        name="发送邮件",
        description="用指定账号给收件人发一封邮件，猫娘帮你代笔投递~ 支持抄送、HTML 正文和附件。",
        input_schema={
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "发件账号别名（add_account 时设定的 name）"},
                "to": {"type": "string", "description": "收件人，多个用逗号分隔"},
                "subject": {"type": "string", "description": "邮件主题"},
                "body": {"type": "string", "description": "纯文本正文"},
                "cc": {"type": "string", "description": "抄送，多个用逗号分隔", "default": ""},
                "html_body": {"type": "string", "description": "可选 HTML 正文", "default": ""},
                "attachments": {"type": "array", "items": {"type": "string"}, "description": "附件本地路径列表", "default": []},
            },
            "required": ["account", "to", "subject", "body"],
        },
        llm_result_fields=["status", "to", "subject"],
    )
    async def send_mail(
        self,
        account: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        html_body: str = "",
        attachments: list[str] | None = None,
        **_,
    ):
        accounts = self._load_accounts()
        if account not in accounts:
            return Err(SdkError(f"未找到账号 '{account}'，请先用 add_account 配置"))
        acc = Account.from_dict(accounts[account])
        try:
            await asyncio.to_thread(
                send_message, acc, to, subject, body, cc, html_body, attachments or []
            )
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"发送失败：{exc}"))
        return Ok({"status": "sent", "account": account, "to": to, "subject": subject})

    # ----------------------------- 收件 -----------------------------
    @plugin_entry(
        id="check_mail",
        name="检查新邮件",
        description="立即检查账号的新邮件（IMAP 取未读，POP3 取最近若干封）。不填 account 则检查全部。",
        input_schema={
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "指定账号别名，留空检查全部", "default": ""},
                "limit": {"type": "integer", "description": "每个账号最多返回几封", "default": 5},
            },
        },
        llm_result_fields=["count", "mails"],
    )
    async def check_mail(self, account: str = "", limit: int = 5, **_):
        accounts = self._load_accounts()
        if not accounts:
            return Ok({"count": 0, "mails": [], "hint": "还没有配置任何邮箱账号，先用 add_account 喵~"})
        results: list[dict[str, Any]] = []
        for name, raw in accounts.items():
            if account and name != account:
                continue
            acc = Account.from_dict(raw)
            try:
                mails = await asyncio.to_thread(fetch_unseen, acc, int(limit))
            except Exception as exc:  # noqa: BLE001
                self._log("error", "check_mail %s failed: %s", name, exc)
                continue
            for m in mails:
                m["account"] = name
                results.append(m)
        return Ok({"count": len(results), "mails": results})

    @plugin_entry(
        id="read_mail",
        name="阅读邮件",
        description="读取指定邮件的完整内容。",
        input_schema={
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "账号别名"},
                "uid": {"type": "string", "description": "邮件 uid（来自 check_mail 的返回）"},
            },
            "required": ["account", "uid"],
        },
        llm_result_fields=["subject", "from", "body"],
    )
    async def read_mail(self, account: str, uid: str, **_):
        accounts = self._load_accounts()
        if account not in accounts:
            return Err(SdkError(f"未找到账号 '{account}'"))
        acc = Account.from_dict(accounts[account])
        try:
            mail = await asyncio.to_thread(fetch_message, acc, uid)
        except Exception as exc:  # noqa: BLE001
            return Err(SdkError(f"读取失败：{exc}"))
        if not mail:
            return Err(SdkError(f"没找到 uid={uid} 的邮件"))
        mail["account"] = account
        return Ok(mail)

    @plugin_entry(
        id="inbox_summary",
        name="收件箱摘要",
        description="汇总所有账号的未读邮件情况（IMAP 可靠；POP3 无法统计未读，会标注 unknown）。",
        llm_result_fields=["accounts"],
    )
    async def inbox_summary(self, **_):
        accounts = self._load_accounts()
        if not accounts:
            return Ok({"accounts": [], "hint": "还没有配置任何邮箱账号，先用 add_account 喵~"})
        summary: list[dict[str, Any]] = []
        for name, raw in accounts.items():
            acc = Account.from_dict(raw)
            try:
                n = await asyncio.to_thread(count_unseen, acc)
            except Exception as exc:  # noqa: BLE001
                n = -1
                self._log("error", "inbox_summary %s failed: %s", name, exc)
            summary.append({
                "account": name,
                "email": acc.email,
                "recv_type": acc.recv_type,
                "unseen": n,
                "unseen_label": "未知(POP3)" if n < 0 else str(n),
            })
        return Ok({"accounts": summary})

    # ----------------------------- 定时查信 -----------------------------
    @timer_interval(id="auto_check", seconds=300)
    async def auto_check(self, **_):
        accounts = self._load_accounts()
        for name, raw in accounts.items():
            acc = Account.from_dict(raw)
            try:
                mails = await asyncio.to_thread(fetch_unseen, acc, 5)
            except Exception as exc:  # noqa: BLE001
                self._log("error", "auto_check %s failed: %s", name, exc)
                continue
            seen = self._seen.setdefault(name, set())
            for m in mails:
                uid = m.get("uid")
                if uid in seen:
                    continue
                seen.add(uid)
                self._send_push(name, m)
        return Ok({"status": "checked", "accounts": len(accounts)})
