"""猫娘邮件底层协议：IMAP / POP3 收件 + SMTP 发件。

仅依赖 Python 标准库，可适配任意邮箱服务商（QQ、163、Gmail、Outlook、企业邮等）。
所有网络调用都是阻塞式的，插件层会用 ``asyncio.to_thread`` 包裹，避免阻塞事件循环。
"""
from __future__ import annotations

import email
import imaplib
import poplib
import smtplib
import ssl
from dataclasses import dataclass
from email import encoders
from email.header import decode_header, make_header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, parseaddr
from pathlib import Path
from typing import Any


def _decode_hdr(value: str) -> str:
    """解码 RFC2047 编码的邮件头（发件人 / 主题等）。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _decode_body(payload: bytes, charset: str | None) -> str:
    try:
        return payload.decode(charset or "utf-8", errors="replace")
    except Exception:
        return payload.decode("utf-8", errors="replace")


@dataclass
class Account:
    """一个邮箱账号的完整连接配置。密码以明文形式持久化（见插件 README 安全说明）。"""

    name: str
    email: str
    # 收件（IMAP 或 POP3）
    recv_type: str = "imap"          # "imap" | "pop3"
    recv_host: str = ""
    recv_port: int = 993
    recv_ssl: bool = True
    recv_user: str = ""
    recv_pass: str = ""
    # 发件（SMTP）
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_ssl: bool = True             # True -> SSL(465) / False -> STARTTLS(587)
    smtp_user: str = ""
    smtp_pass: str = ""
    # 展示名（From 里显示的名字）
    display_name: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Account":
        name = str(d.get("name", ""))
        email_addr = str(d.get("email", ""))
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        known.pop("name", None)
        known.pop("email", None)
        # 端口等数值字段做兜底转换
        for key in ("recv_port", "smtp_port"):
            if key in known:
                try:
                    known[key] = int(known[key])
                except (TypeError, ValueError):
                    known[key] = 993 if key == "recv_port" else 465
        for key in ("recv_ssl", "smtp_ssl"):
            if key in known and not isinstance(known[key], bool):
                known[key] = str(known[key]).lower() in ("1", "true", "yes", "on")
        return cls(name=name, email=email_addr, **known)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "email": self.email,
            "recv_type": self.recv_type,
            "recv_host": self.recv_host,
            "recv_port": self.recv_port,
            "recv_ssl": self.recv_ssl,
            "recv_user": self.recv_user,
            "recv_pass": self.recv_pass,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_ssl": self.smtp_ssl,
            "smtp_user": self.smtp_user,
            "smtp_pass": self.smtp_pass,
            "display_name": self.display_name,
        }


# ---------------------------------------------------------------------------
# 发件
# ---------------------------------------------------------------------------

def _smtp_client(acc: Account):
    if acc.smtp_ssl:
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=30)
        server.starttls(context=ssl.create_default_context())
    user = acc.smtp_user or acc.email
    pwd = acc.smtp_pass or acc.recv_pass
    if pwd:
        server.login(user, pwd)
    return server


def send_message(
    acc: Account,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    html_body: str = "",
    attachments: list[str] | None = None,
) -> str:
    """用账号 *acc* 发送一封邮件，返回 Message-ID。"""
    msg = MIMEMultipart("alternative" if html_body else "mixed")
    sender_name = acc.display_name or acc.email
    msg["From"] = formataddr((sender_name, acc.email))
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)

    msg.attach(MIMEText(body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    for path in attachments or []:
        p = Path(path)
        if not p.exists():
            continue
        part = MIMEBase("application", "octet-stream")
        part.set_payload(p.read_bytes())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=_decode_hdr(p.name))
        msg.attach(part)

    recipients = [a.strip() for a in (to.split(",") + (cc.split(",") if cc else [])) if a.strip()]
    with _smtp_client(acc) as server:
        server.sendmail(acc.email, recipients, msg.as_string())
    return msg["Message-ID"] or "sent"


# ---------------------------------------------------------------------------
# 收件解析
# ---------------------------------------------------------------------------

def _extract_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        texts: list[str] = []
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    texts.append(_decode_body(payload, part.get_content_charset()))
        if texts:
            return "\n".join(texts)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return _decode_body(payload, part.get_content_charset())
        return ""
    payload = msg.get_payload(decode=True)
    return _decode_body(payload, msg.get_content_charset()) if payload else ""


def _parse(raw: bytes, uid: str) -> dict[str, Any]:
    msg = email.message_from_bytes(raw)
    from_name, from_addr = parseaddr(msg.get("From", ""))
    from_label = f"{_decode_hdr(from_name)} <{from_addr}>" if from_addr else _decode_hdr(from_name)
    return {
        "uid": uid,
        "subject": _decode_hdr(msg.get("Subject", "")),
        "from": from_label,
        "from_addr": from_addr,
        "to": _decode_hdr(msg.get("To", "")),
        "date": str(msg.get("Date", "")),
        "body": _extract_text(msg),
    }


def _imap_client(acc: Account):
    if acc.recv_ssl:
        return imaplib.IMAP4_SSL(acc.recv_host, acc.recv_port, timeout=30)
    return imaplib.IMAP4(acc.recv_host, acc.recv_port, timeout=30)


def _imap_unseen(acc: Account, limit: int) -> list[dict[str, Any]]:
    conn = _imap_client(acc)
    try:
        conn.login(acc.recv_user or acc.email, acc.recv_pass)
        conn.select("INBOX")
        typ, data = conn.search(None, "UNSEEN")
        ids = data[0].split() if data and data[0] else []
        target = ids[-limit:] if limit and limit > 0 else ids
        out: list[dict[str, Any]] = []
        for uid in target:
            typ, msg_data = conn.fetch(uid, "(RFC822)")
            raw = None
            if msg_data and isinstance(msg_data[0], tuple):
                raw = msg_data[0][1]
            if raw:
                out.append(_parse(raw, uid.decode()))
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()


def _pop3_client(acc: Account):
    if acc.recv_ssl:
        return poplib.POP3_SSL(acc.recv_host, acc.recv_port, timeout=30)
    return poplib.POP3(acc.recv_host, acc.recv_port, timeout=30)


def _pop3_unseen(acc: Account, limit: int) -> list[dict[str, Any]]:
    conn = _pop3_client(acc)
    try:
        conn.user(acc.recv_user or acc.email)
        conn.pass_(acc.recv_pass)
        count = len(conn.list()[1])
        start = max(1, count - limit + 1) if limit and limit > 0 else 1
        out: list[dict[str, Any]] = []
        for i in range(start, count + 1):
            raw = b"\n".join(conn.retr(i)[1])
            out.append(_parse(raw, f"pop3-{i}"))
        return out
    finally:
        try:
            conn.quit()
        except Exception:
            pass


def fetch_unseen(acc: Account, limit: int = 5) -> list[dict[str, Any]]:
    """获取账号的未读邮件（IMAP 用 UNSEEN 标记；POP3 取最近若干封）。"""
    if acc.recv_type == "pop3":
        return _pop3_unseen(acc, limit)
    return _imap_unseen(acc, limit)


def fetch_message(acc: Account, uid: str) -> dict[str, Any] | None:
    """按 uid 读取单封邮件完整内容（用于 read_mail）。"""
    if acc.recv_type == "pop3":
        try:
            i = int(str(uid).split("-")[-1])
        except Exception:
            return None
        conn = _pop3_client(acc)
        try:
            conn.user(acc.recv_user or acc.email)
            conn.pass_(acc.recv_pass)
            raw = b"\n".join(conn.retr(i)[1])
            return _parse(raw, uid)
        finally:
            try:
                conn.quit()
            except Exception:
                pass
    else:
        conn = _imap_client(acc)
        try:
            conn.login(acc.recv_user or acc.email, acc.recv_pass)
            conn.select("INBOX")
            typ, msg_data = conn.fetch(uid.encode(), "(RFC822)")
            raw = None
            if msg_data and isinstance(msg_data[0], tuple):
                raw = msg_data[0][1]
            return _parse(raw, uid) if raw else None
        finally:
            try:
                conn.close()
            except Exception:
                pass
            conn.logout()


def count_unseen(acc: Account) -> int:
    """返回收件箱未读数量（仅 IMAP 可靠；POP3 返回 -1 表示未知）。"""
    if acc.recv_type == "pop3":
        return -1
    conn = _imap_client(acc)
    try:
        conn.login(acc.recv_user or acc.email, acc.recv_pass)
        conn.select("INBOX")
        typ, data = conn.search(None, "UNSEEN")
        return len(data[0].split()) if data and data[0] else 0
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()
