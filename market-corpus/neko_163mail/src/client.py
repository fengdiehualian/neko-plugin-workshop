"""
网易邮箱助手 - IMAP/SMTP 客户端
负责邮件收发、文件夹管理、搜索、附件发送

v0.1: 基于 neko_mail 适配网易163邮箱
"""

import base64 as _b64
import email
import imaplib
import os
import re
import smtplib
from datetime import date, datetime
from email.header import Header
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from typing import Optional
from urllib.parse import quote as url_quote

from .models import EmailMessage, FolderInfo
from .parser import (
    classify_priority,
    decode_header_value,
    extract_email_address,
    html_to_text,
    parse_attachment,
)


def _encode_filename_for_header(filename: str) -> str:
    """编码文件名用于 Content-Disposition 头 (RFC 2231)

    纯 ASCII 文件名直接使用，非 ASCII（如中文）使用 filename*=UTF-8'' 格式。
    """
    try:
        filename.encode('ascii')
        # 纯 ASCII，直接加引号
        return f'filename="{filename}"'
    except UnicodeEncodeError:
        # 非 ASCII，使用 RFC 2231 编码
        encoded = url_quote(filename, safe='')
        return f"filename*=UTF-8''{encoded}"


def _base64_encode_lines(data: bytes, line_length: int = 76) -> str:
    """Base64 编码并按 RFC 2045 要求分行

    RFC 2045 规定 base64 编码每行不超过 76 个字符。
    SMTP 服务器对单行过长（通常 >998 字符）会断开连接。
    """
    encoded = _b64.b64encode(data).decode('ascii')
    # 每 line_length 个字符切一行
    chunks = [encoded[i:i+line_length] for i in range(0, len(encoded), line_length)]
    return "\r\n".join(chunks)


class Neko163MailClient:
    """网易163邮箱 IMAP/SMTP 客户端"""

    def __init__(
        self,
        email_addr: str,
        auth_code: str,
        imap_server: str = "imap.163.com",
        imap_port: int = 993,
        smtp_server: str = "smtp.163.com",
        smtp_port: int = 465,
        high_priority_senders: Optional[list[str]] = None,
        ignore_folders: Optional[list[str]] = None,
    ):
        self.email_addr = email_addr
        self.auth_code = auth_code
        self.imap_server = imap_server
        self.imap_port = imap_port
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.high_priority_senders = high_priority_senders or []
        self.ignore_folders = ignore_folders or []

        self._imap: Optional[imaplib.IMAP4_SSL] = None
        self._connected = False

        # SMTP 连接缓存
        self._smtp: Optional[smtplib.SMTP_SSL] = None
        self._smtp_connected = False

    def _ensure_connected(self):
        """确保 IMAP 连接可用，支持自动重连"""
        if self._imap is None or not self._connected:
            self._connect()
        else:
            # 检查连接是否仍然有效
            try:
                self._imap.noop()
            except Exception:
                # 连接已断开，尝试重连
                self._reconnect()

    def _ensure_smtp_connected(self):
        """确保 SMTP 连接可用，支持自动重连和复用"""
        if self._smtp is not None and self._smtp_connected:
            # 检查连接是否仍然有效
            try:
                status = self._smtp.noop()[0]
                if status == 250:
                    return  # 连接正常
            except Exception:
                # 连接已断开，关闭旧连接
                try:
                    self._smtp.quit()
                except Exception:
                    pass
                self._smtp = None
                self._smtp_connected = False

        # 创建新连接
        try:
            self._smtp = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port, timeout=30)
            self._smtp.login(self.email_addr, self.auth_code)
            self._smtp_connected = True
        except Exception as e:
            self._smtp = None
            self._smtp_connected = False
            raise RuntimeError(f"SMTP 连接失败: {e}")

    def _ensure_folder_selected(self, folder: str = "INBOX", readonly: bool = True):
        """确保指定文件夹已选中，支持自动重连和重试

        Args:
            folder: 文件夹名称
            readonly: 是否只读模式

        Raises:
            RuntimeError: 无法选中文件夹
        """
        self._ensure_connected()
        try:
            status, data = self._imap.select(folder, readonly=readonly)
            if status != 'OK':
                # 解析服务器返回的错误信息
                err_detail = data[0].decode('utf-8', errors='replace') if data and data[0] else str(data)
                if 'Unsafe' in err_detail or 'kefu@188.com' in err_detail:
                    raise RuntimeError(
                        f"163邮箱IMAP服务被限制: {err_detail}\n"
                        f"请登录163邮箱网页版 → 设置 → POP3/SMTP/IMAP，确认IMAP服务已开启，"
                        f"或重置授权码后更新plugin.toml中的auth_code"
                    )
                raise RuntimeError(f"选中文件夹 {folder} 失败: status={status}, {err_detail}")
        except RuntimeError:
            raise
        except Exception:
            # SELECT 失败，尝试重连后重试
            self._reconnect()
            try:
                status, data = self._imap.select(folder, readonly=readonly)
                if status != 'OK':
                    err_detail = data[0].decode('utf-8', errors='replace') if data and data[0] else str(data)
                    if 'Unsafe' in err_detail or 'kefu@188.com' in err_detail:
                        raise RuntimeError(
                            f"163邮箱IMAP服务被限制: {err_detail}\n"
                            f"请登录163邮箱网页版 → 设置 → POP3/SMTP/IMAP，确认IMAP服务已开启，"
                            f"或重置授权码后更新plugin.toml中的auth_code"
                        )
                    raise RuntimeError(f"选中文件夹 {folder} 失败（重试后仍失败）: {err_detail}")
            except RuntimeError:
                raise
            except Exception as retry_err:
                raise RuntimeError(f"选中文件夹 {folder} 失败（重试后仍失败）: {retry_err}")

    def _connect(self):
        """连接到 IMAP 服务器"""
        try:
            # 将 IMAP ID 命令 (RFC 2971) 注入 imaplib 命令集
            # 163邮箱强制要求 LOGIN 后、SELECT 前发送 ID 命令声明客户端身份
            if 'ID' not in imaplib.Commands:
                imaplib.Commands['ID'] = ('NONAUTH', 'AUTH', 'SELECTED', 'LOGOUT')

            self._imap = imaplib.IMAP4_SSL(self.imap_server, self.imap_port, timeout=20)
            status, data = self._imap.login(self.email_addr, self.auth_code)
            if status != 'OK':
                err_detail = data[0].decode('utf-8', errors='replace') if data and data[0] else str(data)
                self._connected = False
                raise RuntimeError(f"登录失败: {err_detail}")

            # 发送 IMAP ID 命令，声明客户端身份（163邮箱必须）
            try:
                client_id = (
                    '("name" "NekoMail163"'
                    ' "version" "1.0.0"'
                    ' "vendor" "NekoProject"'
                    ' "support-email" "' + self.email_addr + '")'
                )
                self._imap._simple_command('ID', client_id)
            except Exception:
                pass  # ID 命令失败不阻断连接，非163服务器可能不需要

            self._connected = True
        except RuntimeError:
            self._connected = False
            self._imap = None
            raise
        except Exception as e:
            self._connected = False
            self._imap = None
            err_str = str(e).lower()
            if "authentication" in err_str or "login" in err_str or "password" in err_str:
                raise RuntimeError("登录失败: 授权码错误,请检查配置中的 auth_code")
            raise RuntimeError(f"连接邮箱服务器失败: {e}")

    def _reconnect(self):
        """重新连接"""
        self.disconnect()
        self._connect()

    def disconnect(self):
        """断开 IMAP 和 SMTP 连接"""
        # 断开 IMAP
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
        self._imap = None
        self._connected = False

        # 断开 SMTP
        if self._smtp:
            try:
                self._smtp.quit()
            except Exception:
                pass
        self._smtp = None
        self._smtp_connected = False

    def list_folders(self) -> list[FolderInfo]:
        """列出所有文件夹及未读数"""
        self._ensure_connected()

        try:
            status, folder_data = self._imap.list()
            if status != 'OK':
                return []

            result = []
            for folder_line in folder_data:
                if folder_line is None:
                    continue

                folder_str = folder_line.decode('utf-8') if isinstance(folder_line, bytes) else str(folder_line)

                match = re.search(r'"([^"]*)"$', folder_str)
                if not match:
                    continue
                name = match.group(1)

                if any(ign.lower() in name.lower() for ign in self.ignore_folders):
                    continue

                if name.startswith('[') or '\\Noselect' in folder_str:
                    continue

                try:
                    self._ensure_folder_selected(name, readonly=True)
                    status, messages = self._imap.search(None, 'UNSEEN')
                    unread_count = len(messages[0].split()) if status == 'OK' and messages[0] else 0

                    status, all_messages = self._imap.search(None, 'ALL')
                    total_count = len(all_messages[0].split()) if status == 'OK' and all_messages[0] else 0

                    result.append(FolderInfo(
                        name=name,
                        unread_count=unread_count,
                        total_count=total_count,
                    ))
                except Exception:
                    continue

            return result
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"列出文件夹失败: {e}")

    # ── 轻量级邮件头获取 (速度关键优化) ──

    def _fetch_email_headers(self, uid: bytes, folder: str) -> Optional[dict]:
        """
        轻量级获取邮件头信息 (不下载正文)
        返回 dict: {uid, subject, sender, recipients, cc, date, flags, has_attachments, priority}
        """
        try:
            status, data = self._imap.fetch(uid, '(BODY.PEEK[HEADER] FLAGS)')

            if status != 'OK' or not data or not data[0]:
                return None

            # data[0] = (b'1 (FLAGS (...))', b'邮件头内容')
            raw_headers = data[0][1]
            flags_str = data[0][0].decode('utf-8') if isinstance(data[0][0], bytes) else str(data[0][0])
            flags_match = re.search(r'FLAGS \(([^)]*)\)', flags_str)
            flags = flags_match.group(1).split() if flags_match else []

            # 解析邮件头
            msg = email.message_from_bytes(raw_headers)

            subject = decode_header_value(msg.get('Subject', ''))
            sender = extract_email_address(msg.get('From', ''))

            to_header = msg.get('To', '')
            recipients = [extract_email_address(addr) for addr in to_header.split(',')]
            recipients = [r for r in recipients if r]

            cc_header = msg.get('Cc', '')
            cc = [extract_email_address(addr) for addr in cc_header.split(',')]
            cc = [c for c in cc if c]

            date_str = msg.get('Date', '')
            try:
                email_date = parsedate_to_datetime(date_str)
            except Exception:
                email_date = datetime.now()

            # 检查是否有附件 (通过 Content-Type)
            content_type = msg.get('Content-Type', '')
            has_attachments = 'multipart' in content_type.lower()

            # 创建临时 EmailMessage 用于优先级分类
            uid_str = uid.decode('utf-8') if isinstance(uid, bytes) else str(uid)
            temp_email = EmailMessage(
                uid=uid_str,
                subject=subject,
                sender=sender,
                recipients=recipients,
                cc=cc,
                date=email_date,
                body_text="",
                body_html=None,
                attachments=[],
                flags=[f.decode() if isinstance(f, bytes) else f for f in flags],
                priority="medium",
                folder=folder,
            )
            priority = classify_priority(temp_email, self.high_priority_senders)

            return {
                "uid": uid_str,
                "subject": subject,
                "sender": sender,
                "recipients": recipients,
                "cc": cc,
                "date": email_date,
                "flags": [f.decode() if isinstance(f, bytes) else f for f in flags],
                "has_attachments": has_attachments,
                "priority": priority,
                "folder": folder,
            }
        except Exception:
            return None

    def _fetch_email_full(self, uid: bytes, folder: str) -> Optional[EmailMessage]:
        """获取完整邮件 (含正文和附件)"""
        try:
            status, data = self._imap.fetch(uid, '(RFC822 FLAGS)')

            if status != 'OK' or not data or not data[0]:
                return None

            raw_email = data[0][1]
            flags_str = data[0][0].decode('utf-8') if isinstance(data[0][0], bytes) else str(data[0][0])
            flags_match = re.search(r'FLAGS \(([^)]*)\)', flags_str)
            flags = flags_match.group(1).split() if flags_match else []

            msg = email.message_from_bytes(raw_email)

            subject = decode_header_value(msg.get('Subject', ''))
            sender = extract_email_address(msg.get('From', ''))

            to_header = msg.get('To', '')
            recipients = [extract_email_address(addr) for addr in to_header.split(',')]
            recipients = [r for r in recipients if r]

            cc_header = msg.get('Cc', '')
            cc = [extract_email_address(addr) for addr in cc_header.split(',')]
            cc = [c for c in cc if c]

            date_str = msg.get('Date', '')
            try:
                email_date = parsedate_to_datetime(date_str)
            except Exception:
                email_date = datetime.now()

            body_text = ""
            body_html = ""
            attachments = []

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get('Content-Disposition', ''))

                    if 'attachment' in content_disposition:
                        att = parse_attachment(part)
                        if att:
                            attachments.append(att)
                    else:
                        if content_type == 'text/plain':
                            payload = part.get_payload(decode=True)
                            if payload:
                                charset = part.get_content_charset() or 'utf-8'
                                try:
                                    body_text += payload.decode(charset, errors='replace')
                                except Exception:
                                    body_text += payload.decode('utf-8', errors='replace')
                        elif content_type == 'text/html':
                            payload = part.get_payload(decode=True)
                            if payload:
                                charset = part.get_content_charset() or 'utf-8'
                                try:
                                    body_html += payload.decode(charset, errors='replace')
                                except Exception:
                                    body_html += payload.decode('utf-8', errors='replace')
            else:
                content_type = msg.get_content_type()
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or 'utf-8'
                    try:
                        text = payload.decode(charset, errors='replace')
                    except Exception:
                        text = payload.decode('utf-8', errors='replace')

                    if content_type == 'text/html':
                        body_html = text
                    else:
                        body_text = text

            if body_html and not body_text:
                body_text = html_to_text(body_html)

            if attachments:
                att_summary = "\n\n" + "📎 附件: " + ", ".join(
                    f"{a.filename} ({a.size_human()})" for a in attachments
                )
                body_text += att_summary

            uid_str = uid.decode('utf-8') if isinstance(uid, bytes) else str(uid)
            email_msg = EmailMessage(
                uid=uid_str,
                subject=subject,
                sender=sender,
                recipients=recipients,
                cc=cc,
                date=email_date,
                body_text=body_text,
                body_html=body_html if body_html else None,
                attachments=attachments,
                flags=[f.decode() if isinstance(f, bytes) else f for f in flags],
                priority="medium",
                folder=folder,
            )

            email_msg.priority = classify_priority(email_msg, self.high_priority_senders)

            return email_msg
        except Exception:
            return None

    # ── 邮件列表方法 ──

    def get_unread_headers(self, folder: str = "INBOX", limit: int = 50, offset: int = 0) -> list[dict]:
        """获取未读邮件头信息 (轻量级,不下载正文)，支持分页"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            status, messages = self._imap.search(None, 'UNSEEN')

            if status != 'OK' or not messages[0]:
                return []

            uids = messages[0].split()
            # 从最新的开始取（倒序）
            if offset > 0:
                uids = uids[:-offset] if offset < len(uids) else []
            uids = uids[-limit:] if limit > 0 else uids

            results = []
            for uid in uids:
                try:
                    header_info = self._fetch_email_headers(uid, folder)
                    if header_info:
                        results.append(header_info)
                except Exception:
                    continue

            return results
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取未读邮件失败: {e}")

    def get_unread_count(self, folder: str = "INBOX") -> int:
        """获取未读邮件总数"""
        self._ensure_folder_selected(folder, readonly=True)
        try:
            status, messages = self._imap.search(None, 'UNSEEN')
            if status != 'OK' or not messages[0]:
                return 0
            return len(messages[0].split())
        except Exception:
            return 0

    def get_unread(self, folder: str = "INBOX", limit: int = 50) -> list[EmailMessage]:
        """获取未读邮件 (完整版,含正文)"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            status, messages = self._imap.search(None, 'UNSEEN')

            if status != 'OK' or not messages[0]:
                return []

            uids = messages[0].split()
            uids = uids[-limit:]

            emails = []
            for uid in uids:
                try:
                    email_msg = self._fetch_email_full(uid, folder)
                    if email_msg:
                        emails.append(email_msg)
                except Exception:
                    continue

            return emails
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取未读邮件失败: {e}")

    def get_all_emails_headers(self, folder: str = "INBOX", limit: int = 50, offset: int = 0) -> list[dict]:
        """获取所有邮件头信息 (已读+未读,轻量级)，支持分页"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            status, messages = self._imap.search(None, 'ALL')

            if status != 'OK' or not messages[0]:
                return []

            uids = messages[0].split()
            # 从最新的开始取（倒序）
            if offset > 0:
                uids = uids[:-offset] if offset < len(uids) else []
            uids = uids[-limit:] if limit > 0 else uids

            results = []
            for uid in uids:
                try:
                    header_info = self._fetch_email_headers(uid, folder)
                    if header_info:
                        results.append(header_info)
                except Exception:
                    continue

            return results
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取邮件列表失败: {e}")

    def get_all_emails_count(self, folder: str = "INBOX") -> int:
        """获取所有邮件总数"""
        self._ensure_folder_selected(folder, readonly=True)
        try:
            status, messages = self._imap.search(None, 'ALL')
            if status != 'OK' or not messages[0]:
                return 0
            return len(messages[0].split())
        except Exception:
            return 0

    def get_today_emails_headers(self, folder: str = "INBOX") -> list[dict]:
        """获取今日邮件头信息 (轻量级)"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            today = date.today()
            since_str = today.strftime("%d-%b-%Y")

            status, messages = self._imap.search(None, f'(SINCE {since_str})')

            if status != 'OK' or not messages[0]:
                return []

            uids = messages[0].split()

            results = []
            for uid in uids:
                try:
                    header_info = self._fetch_email_headers(uid, folder)
                    if header_info:
                        results.append(header_info)
                except Exception:
                    continue

            return results
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取今日邮件失败: {e}")

    def get_today_emails(self, folder: str = "INBOX") -> list[EmailMessage]:
        """获取今日邮件 (完整版,含正文)"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            today = date.today()
            since_str = today.strftime("%d-%b-%Y")

            status, messages = self._imap.search(None, f'(SINCE {since_str})')

            if status != 'OK' or not messages[0]:
                return []

            uids = messages[0].split()

            emails = []
            for uid in uids:
                try:
                    email_msg = self._fetch_email_full(uid, folder)
                    if email_msg:
                        emails.append(email_msg)
                except Exception:
                    continue

            return emails
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取今日邮件失败: {e}")

    def get_email_detail(self, uid: str, folder: str = "INBOX") -> Optional[EmailMessage]:
        """获取单封邮件详情 (完整版)"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            return self._fetch_email_full(uid.encode(), folder)
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取邮件详情失败: {e}")

    def search(self, keyword: str, folder: str = "INBOX", limit: int = 100, offset: int = 0) -> list[EmailMessage]:
        """关键词搜索主题+正文+发件人，支持分页"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            # 构建搜索条件
            criteria = [
                f'(SUBJECT "{keyword}")',
                f'(FROM "{keyword}")',
                f'(BODY "{keyword}")',
            ]

            all_uids = set()
            for crit in criteria:
                try:
                    status, messages = self._imap.search(None, crit)
                    if status == 'OK' and messages[0]:
                        all_uids.update(messages[0].split())
                except Exception:
                    continue

            if not all_uids:
                return []

            # 排序并应用分页
            sorted_uids = sorted(all_uids, key=lambda x: int(x))
            if offset > 0:
                sorted_uids = sorted_uids[:-offset] if offset < len(sorted_uids) else []
            uids = sorted_uids[-limit:] if limit > 0 else sorted_uids

            emails = []
            for uid in uids:
                try:
                    email_msg = self._fetch_email_full(uid, folder)
                    if email_msg:
                        emails.append(email_msg)
                except Exception:
                    continue

            return emails
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"搜索邮件失败: {e}")

    def search_count(self, keyword: str, folder: str = "INBOX") -> int:
        """获取搜索结果总数"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            criteria = [
                f'(SUBJECT "{keyword}")',
                f'(FROM "{keyword}")',
                f'(BODY "{keyword}")',
            ]

            all_uids = set()
            for crit in criteria:
                try:
                    status, messages = self._imap.search(None, crit)
                    if status == 'OK' and messages[0]:
                        all_uids.update(messages[0].split())
                except Exception:
                    continue

            return len(all_uids)
        except Exception:
            return 0

    def mark_read(self, uid: str, folder: str = "INBOX") -> bool:
        """标记邮件已读"""
        self._ensure_folder_selected(folder, readonly=False)

        try:
            self._imap.store(uid.encode(), '+FLAGS', '\\Seen')
            return True
        except Exception:
            self._reconnect()
            return False

    def batch_mark_read(self, uids: list[str], folder: str = "INBOX") -> dict:
        """批量标记邮件已读"""
        self._ensure_folder_selected(folder, readonly=False)

        try:
            success_count = 0
            failed_uids = []

            for uid in uids:
                try:
                    self._imap.store(uid.encode(), '+FLAGS', '\\Seen')
                    success_count += 1
                except Exception:
                    failed_uids.append(uid)

            return {
                "success": success_count,
                "failed": len(failed_uids),
                "failed_uids": failed_uids
            }
        except Exception as e:
            self._reconnect()
            return {"success": 0, "failed": len(uids), "error": str(e)}

    def mark_all_read(self, folder: str = "INBOX") -> dict:
        """标记文件夹内所有邮件为已读"""
        self._ensure_folder_selected(folder, readonly=False)

        try:
            # 搜索所有未读邮件
            status, messages = self._imap.search(None, 'UNSEEN')

            if status != 'OK' or not messages[0]:
                return {"success": 0, "message": "没有未读邮件"}

            uids = messages[0].split()
            count = len(uids)

            # 批量标记
            if uids:
                uid_str = b','.join(uids)
                self._imap.store(uid_str, '+FLAGS', '\\Seen')

            return {"success": count, "message": f"已标记 {count} 封邮件为已读"}
        except Exception as e:
            self._reconnect()
            return {"success": 0, "error": str(e)}

    def batch_delete(self, uids: list[str], folder: str = "INBOX") -> dict:
        """批量删除邮件"""
        self._ensure_folder_selected(folder, readonly=False)

        try:
            success_count = 0
            failed_uids = []

            for uid in uids:
                try:
                    # 标记为删除
                    self._imap.store(uid.encode(), '+FLAGS', '\\Deleted')
                    success_count += 1
                except Exception:
                    failed_uids.append(uid)

            # 执行删除操作
            if success_count > 0:
                self._imap.expunge()

            return {
                "success": success_count,
                "failed": len(failed_uids),
                "failed_uids": failed_uids
            }
        except Exception as e:
            self._reconnect()
            return {"success": 0, "failed": len(uids), "error": str(e)}

    def reply(
        self,
        uid: str,
        body: str,
        folder: str = "INBOX",
        reply_all: bool = False,
        attachments: Optional[list[str]] = None,
    ) -> bool:
        """回复邮件 - 手动构建邮件格式，不依赖 email.mime 模块"""
        try:
            import base64 as _b64

            self._ensure_folder_selected(folder, readonly=True)
            original = self._fetch_email_full(uid.encode(), folder)
            if not original:
                raise RuntimeError(f"找不到邮件 UID={uid}")

            # 主题：加 Re: 前缀
            subject = original.subject or ""
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"

            # 收件人
            reply_to = original.sender
            recipients = [reply_to]
            cc_list = []
            if reply_all:
                all_addrs = list(set(original.recipients + (original.cc or [])))
                all_addrs = [a for a in all_addrs if a and a != self.email_addr]
                if all_addrs:
                    cc_list = all_addrs
                    recipients.extend(all_addrs)

            boundary = "----=_NextPart_" + make_msgid().replace('<', '').replace('>', '')
            lines = []
            lines.append(f"From: {self.email_addr}")
            lines.append(f"To: {reply_to}")
            if cc_list:
                lines.append(f"Cc: {', '.join(cc_list)}")
            lines.append(f"Subject: {Header(subject, 'utf-8').encode()}")
            lines.append(f"Date: {formatdate()}")
            lines.append(f"Message-ID: {make_msgid()}")
            lines.append(f"In-Reply-To: {original.uid}")
            lines.append(f"References: {original.uid}")
            lines.append("MIME-Version: 1.0")

            has_attachments = attachments and len(attachments) > 0

            if has_attachments:
                lines.append(f'Content-Type: multipart/mixed; boundary="{boundary}"')
                lines.append("")
                lines.append(f"--{boundary}")
                lines.append("Content-Type: text/plain; charset=utf-8")
                lines.append("Content-Transfer-Encoding: base64")
                lines.append("")
                lines.append(_b64.b64encode(body.encode('utf-8')).decode('ascii'))

                for file_path in attachments:
                    if not os.path.exists(file_path):
                        continue
                    filename = os.path.basename(file_path)
                    lines.append(f"--{boundary}")
                    lines.append("Content-Type: application/octet-stream")
                    lines.append(f'Content-Disposition: attachment; {_encode_filename_for_header(filename)}')
                    lines.append("Content-Transfer-Encoding: base64")
                    lines.append("")
                    with open(file_path, 'rb') as f:
                        lines.append(_b64.b64encode(f.read()).decode('ascii'))

                lines.append(f"--{boundary}--")
            else:
                lines.append(f'Content-Type: multipart/alternative; boundary="{boundary}"')
                lines.append("")
                lines.append(f"--{boundary}")
                lines.append("Content-Type: text/plain; charset=utf-8")
                lines.append("Content-Transfer-Encoding: base64")
                lines.append("")
                lines.append(_b64.b64encode(body.encode('utf-8')).decode('ascii'))
                lines.append(f"--{boundary}--")

            msg_str = "\r\n".join(lines)

            self._smtp_send(recipients, msg_str)
            return True
        except Exception as e:
            raise RuntimeError(f"回复邮件失败: {e}")

    def forward(
        self,
        uid: str,
        to: str,
        body: str = "",
        folder: str = "INBOX",
        include_attachments: bool = True,
    ) -> bool:
        """转发邮件 - 手动构建邮件格式，不依赖 email.mime 模块"""
        try:
            import base64 as _b64

            self._ensure_folder_selected(folder, readonly=True)
            original = self._fetch_email_full(uid.encode(), folder)
            if not original:
                raise RuntimeError(f"找不到邮件 UID={uid}")

            # 主题：加 Fwd: 前缀
            subject = original.subject or ""
            if not subject.lower().startswith("fwd:"):
                subject = f"Fwd: {subject}"

            # 转发说明 + 原始正文
            fwd_body = ""
            if body:
                fwd_body += f"{body}\n\n"
            fwd_body += "---------- 转发的邮件 ----------\n"
            fwd_body += f"发件人: {original.sender}\n"
            fwd_body += f"日期: {original.date.strftime('%Y-%m-%d %H:%M') if original.date else '未知'}\n"
            fwd_body += f"主题: {original.subject}\n"
            fwd_body += f"收件人: {', '.join(original.recipients)}\n"
            if original.cc:
                fwd_body += f"抄送: {', '.join(original.cc)}\n"
            fwd_body += f"\n{original.body_text or ''}"

            has_attachments = include_attachments and original.attachments

            boundary = "----=_NextPart_" + make_msgid().replace('<', '').replace('>', '')
            lines = []
            lines.append(f"From: {self.email_addr}")
            lines.append(f"To: {to}")
            lines.append(f"Subject: {Header(subject, 'utf-8').encode()}")
            lines.append(f"Date: {formatdate()}")
            lines.append(f"Message-ID: {make_msgid()}")
            lines.append("MIME-Version: 1.0")

            if has_attachments:
                lines.append(f'Content-Type: multipart/mixed; boundary="{boundary}"')
                lines.append("")
                lines.append(f"--{boundary}")
                lines.append("Content-Type: text/plain; charset=utf-8")
                lines.append("Content-Transfer-Encoding: base64")
                lines.append("")
                lines.append(_b64.b64encode(fwd_body.encode('utf-8')).decode('ascii'))

                # 从原始邮件中重新获取附件数据（Attachment 模型无 data 字段）
                self._ensure_folder_selected(folder, readonly=True)
                status, data = self._imap.fetch(uid.encode(), '(RFC822)')
                if status == 'OK' and data and data[0]:
                    raw_email = data[0][1]
                    orig_msg = email.message_from_bytes(raw_email)
                    for part in orig_msg.walk():
                        content_disposition = str(part.get('Content-Disposition', ''))
                        if 'attachment' in content_disposition:
                            payload = part.get_payload(decode=True)
                            if payload:
                                filename = decode_header_value(part.get_filename() or 'attachment')
                                lines.append(f"--{boundary}")
                                lines.append("Content-Type: application/octet-stream")
                                lines.append(f'Content-Disposition: attachment; {_encode_filename_for_header(filename)}')
                                lines.append("Content-Transfer-Encoding: base64")
                                lines.append("")
                                lines.append(_b64.b64encode(payload).decode('ascii'))

                lines.append(f"--{boundary}--")
            else:
                lines.append(f'Content-Type: multipart/alternative; boundary="{boundary}"')
                lines.append("")
                lines.append(f"--{boundary}")
                lines.append("Content-Type: text/plain; charset=utf-8")
                lines.append("Content-Transfer-Encoding: base64")
                lines.append("")
                lines.append(_b64.b64encode(fwd_body.encode('utf-8')).decode('ascii'))
                lines.append(f"--{boundary}--")

            msg_str = "\r\n".join(lines)

            self._smtp_send([to], msg_str)
            return True
        except Exception as e:
            raise RuntimeError(f"转发邮件失败: {e}")

    def download_attachment(self, uid: str, attachment_index: int, output_dir: str, folder: str = "INBOX") -> dict:
        """
        下载邮件附件到本地目录

        Args:
            uid: 邮件 UID
            attachment_index: 附件索引（从 0 开始）
            output_dir: 保存目录
            folder: 邮件所在文件夹

        Returns:
            {"success": True, "filename": "xxx.pdf", "path": "C:/..."}
        """
        try:
            self._ensure_folder_selected(folder, readonly=True)
            email_msg = self._fetch_email_full(uid.encode(), folder)
            if not email_msg:
                return {"error": f"找不到邮件 UID={uid}"}

            if not email_msg.attachments:
                return {"error": "该邮件没有附件"}

            if attachment_index < 0 or attachment_index >= len(email_msg.attachments):
                return {"error": f"附件索引 {attachment_index} 超出范围（共 {len(email_msg.attachments)} 个附件）"}

            att = email_msg.attachments[attachment_index]

            # 确保输出目录存在
            os.makedirs(output_dir, exist_ok=True)

            # 解码并保存附件
            filename = att.filename or f"attachment_{attachment_index}"
            filepath = os.path.join(output_dir, filename)

            # 从原始邮件中重新获取附件数据
            self._ensure_folder_selected(folder, readonly=True)
            status, data = self._imap.fetch(uid.encode(), '(RFC822)')
            if status != 'OK' or not data or not data[0]:
                return {"error": "获取邮件数据失败"}

            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            # 遍历找到对应索引的附件
            current_idx = 0
            for part in msg.walk():
                content_disposition = str(part.get('Content-Disposition', ''))
                if 'attachment' in content_disposition:
                    if current_idx == attachment_index:
                        payload = part.get_payload(decode=True)
                        if payload:
                            with open(filepath, 'wb') as f:
                                f.write(payload)
                            return {
                                "success": True,
                                "filename": filename,
                                "path": filepath,
                                "size": len(payload),
                            }
                        break
                    current_idx += 1

            return {"error": "无法解码附件数据"}
        except Exception as e:
            return {"error": f"下载附件失败: {e}"}

    def _smtp_send(self, recipients: list[str], msg_str: str, max_retries: int = 2):
        """
        通过 SMTP_SSL 发送邮件，带重试和详细错误报告。
        复用 SMTP 连接，避免频繁建立/断开连接触发163安全限制。
        """
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                self._ensure_smtp_connected()
                self._smtp.sendmail(self.email_addr, recipients, msg_str)
                return  # 成功
            except smtplib.SMTPAuthenticationError as e:
                last_error = f"SMTP 登录失败（授权码错误或未开启 SMTP 服务）: {e}"
                # 认证错误，重置连接
                self._smtp = None
                self._smtp_connected = False
                raise RuntimeError(last_error)  # 认证错误不重试
            except (smtplib.SMTPException, ConnectionError, OSError, TimeoutError) as e:
                last_error = f"SMTP 发送失败（第 {attempt + 1} 次尝试）: {type(e).__name__}: {e}"
                # 连接错误，重置连接以便下次重试
                self._smtp = None
                self._smtp_connected = False
                if attempt < max_retries:
                    import time
                    time.sleep(2 * (attempt + 1))
                    continue
            except Exception as e:
                # 未知错误，重置连接
                self._smtp = None
                self._smtp_connected = False
                raise RuntimeError(f"SMTP 发送异常: {type(e).__name__}: {e}")
        raise RuntimeError(f"SMTP 发送失败（已重试 {max_retries + 1} 次）: {last_error}")

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        html: bool = False,
        attachments: Optional[list[str]] = None,
    ) -> bool:
        """发送邮件 - 手动构建邮件格式，不依赖 email.mime 模块"""
        try:
            boundary = "----=_NextPart_" + make_msgid().replace('<', '').replace('>', '')
            lines = []
            lines.append(f"From: {self.email_addr}")
            lines.append(f"To: {to}")
            if cc:
                lines.append(f"Cc: {', '.join(cc)}")
            lines.append(f"Subject: {Header(subject, 'utf-8').encode()}")
            lines.append(f"Date: {formatdate()}")
            lines.append(f"Message-ID: {make_msgid()}")
            lines.append("MIME-Version: 1.0")

            has_attachments = attachments and len(attachments) > 0

            if has_attachments:
                lines.append(f'Content-Type: multipart/mixed; boundary="{boundary}"')
                lines.append("")
                lines.append(f"--{boundary}")
                content_type = "text/html" if html else "text/plain"
                lines.append(f"Content-Type: {content_type}; charset=utf-8")
                lines.append("Content-Transfer-Encoding: base64")
                lines.append("")
                # RFC 2045: base64 每行不超过 76 字符
                lines.append(_base64_encode_lines(body.encode('utf-8')))

                for file_path in attachments:
                    if not os.path.exists(file_path):
                        continue
                    filename = os.path.basename(file_path)
                    lines.append(f"--{boundary}")
                    lines.append("Content-Type: application/octet-stream")
                    lines.append(f'Content-Disposition: attachment; {_encode_filename_for_header(filename)}')
                    lines.append("Content-Transfer-Encoding: base64")
                    lines.append("")
                    with open(file_path, 'rb') as f:
                        file_data = f.read()
                    # RFC 2045: base64 每行不超过 76 字符，避免行过长导致 SMTP 断连
                    lines.append(_base64_encode_lines(file_data))

                lines.append(f"--{boundary}--")
            else:
                lines.append(f'Content-Type: multipart/alternative; boundary="{boundary}"')
                lines.append("")
                lines.append(f"--{boundary}")
                content_type = "text/html" if html else "text/plain"
                lines.append(f"Content-Type: {content_type}; charset=utf-8")
                lines.append("Content-Transfer-Encoding: base64")
                lines.append("")
                lines.append(_base64_encode_lines(body.encode('utf-8')))
                lines.append(f"--{boundary}--")

            msg_str = "\r\n".join(lines)

            recipients = [to]
            if cc:
                recipients.extend(cc)

            self._smtp_send(recipients, msg_str)
            return True
        except Exception as e:
            raise RuntimeError(f"发送邮件失败: {e}")

    # ── 新邮件监听 ──

    def get_latest_uid(self, folder: str = "INBOX") -> Optional[str]:
        """获取文件夹中最新的邮件 UID"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            status, messages = self._imap.search(None, 'ALL')

            if status != 'OK' or not messages[0]:
                return None

            uids = messages[0].split()
            if not uids:
                return None

            # 返回最新的 UID
            return uids[-1].decode('utf-8') if isinstance(uids[-1], bytes) else uids[-1]
        except Exception:
            return None

    def get_new_emails_since_uid(self, last_uid: str, folder: str = "INBOX", limit: int = 20) -> list[dict]:
        """获取自上次 UID 之后的新邮件（轻量级邮件头）"""
        self._ensure_folder_selected(folder, readonly=True)

        try:
            status, messages = self._imap.search(None, 'ALL')

            if status != 'OK' or not messages[0]:
                return []

            all_uids = messages[0].split()
            if not all_uids:
                return []

            # 找到 last_uid 之后的邮件
            new_uids = []
            found_last = False
            for uid in all_uids:
                uid_str = uid.decode('utf-8') if isinstance(uid, bytes) else uid
                if uid_str == last_uid:
                    found_last = True
                    continue
                if found_last:
                    new_uids.append(uid)

            # 如果没有找到 last_uid，说明是新连接，返回最新的几封
            if not found_last:
                new_uids = all_uids[-limit:] if len(all_uids) > limit else all_uids

            # 限制数量
            new_uids = new_uids[-limit:]

            results = []
            for uid in new_uids:
                try:
                    header_info = self._fetch_email_headers(uid, folder)
                    if header_info:
                        results.append(header_info)
                except Exception:
                    continue

            return results
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取新邮件失败: {e}")
