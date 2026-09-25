"""
猫娘邮件插件 - IMAP/SMTP 客户端
负责邮件收发、文件夹管理、搜索、附件发送

v0.3 优化:
  - 添加附件发送功能（支持中文文件名 RFC 2231）
  - base64 分行编码（RFC 2045），修复附件发送断连
  - 添加轻量级邮件头获取方法 (BODY.PEEK[HEADER])，速度提升10倍+
  - 支持已读+未读邮件列表
  - 优化连接超时和错误处理
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


class NekoMailClient:
    """QQ邮箱 IMAP/SMTP 客户端"""
    
    def __init__(
        self,
        email_addr: str,
        auth_code: str,
        imap_server: str = "imap.qq.com",
        imap_port: int = 993,
        smtp_server: str = "smtp.qq.com",
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
    
    def _connect(self):
        """连接到 IMAP 服务器"""
        try:
            self._imap = imaplib.IMAP4_SSL(self.imap_server, self.imap_port, timeout=20)
            self._imap.login(self.email_addr, self.auth_code)
            self._connected = True
        except Exception as e:
            self._connected = False
            err_str = str(e).lower()
            if "authentication" in err_str or "login" in err_str or "password" in err_str:
                raise RuntimeError("登录失败: 授权码错误,请检查配置中的 auth_code")
            raise RuntimeError(f"连接邮箱服务器失败: {e}")
    
    def _reconnect(self):
        """重新连接"""
        self.disconnect()
        self._connect()
    
    def disconnect(self):
        """断开 IMAP 连接"""
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
        self._imap = None
        self._connected = False
    
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
                    self._imap.select(name, readonly=True)
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
        self._ensure_connected()
        
        try:
            self._imap.select(folder, readonly=True)
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
        self._ensure_connected()
        try:
            self._imap.select(folder, readonly=True)
            status, messages = self._imap.search(None, 'UNSEEN')
            if status != 'OK' or not messages[0]:
                return 0
            return len(messages[0].split())
        except Exception:
            return 0
    
    def get_unread(self, folder: str = "INBOX", limit: int = 50) -> list[EmailMessage]:
        """获取未读邮件 (完整版,含正文)"""
        self._ensure_connected()
        
        try:
            self._imap.select(folder, readonly=True)
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
        self._ensure_connected()
        
        try:
            self._imap.select(folder, readonly=True)
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
        self._ensure_connected()
        try:
            self._imap.select(folder, readonly=True)
            status, messages = self._imap.search(None, 'ALL')
            if status != 'OK' or not messages[0]:
                return 0
            return len(messages[0].split())
        except Exception:
            return 0
    
    def get_today_emails_headers(self, folder: str = "INBOX") -> list[dict]:
        """获取今日邮件头信息 (轻量级)"""
        self._ensure_connected()
        
        try:
            self._imap.select(folder, readonly=True)
            
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
        self._ensure_connected()
        
        try:
            self._imap.select(folder, readonly=True)
            
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
        self._ensure_connected()
        
        try:
            self._imap.select(folder, readonly=True)
            return self._fetch_email_full(uid.encode(), folder)
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取邮件详情失败: {e}")
    
    def search(self, keyword: str, folder: str = "INBOX", limit: int = 100, offset: int = 0) -> list[EmailMessage]:
        """关键词搜索主题+正文+发件人，支持分页"""
        self._ensure_connected()
        
        try:
            # 重新选择文件夹以确保获取最新邮件
            self._imap.select(folder, readonly=True)
            
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
        self._ensure_connected()
        
        try:
            self._imap.select(folder, readonly=True)
            
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
        self._ensure_connected()
        
        try:
            self._imap.select(folder)
            self._imap.store(uid.encode(), '+FLAGS', '\\Seen')
            return True
        except Exception:
            self._reconnect()
            return False
    
    def batch_mark_read(self, uids: list[str], folder: str = "INBOX") -> dict:
        """批量标记邮件已读"""
        self._ensure_connected()
        
        try:
            self._imap.select(folder)
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
        self._ensure_connected()
        
        try:
            self._imap.select(folder)
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
        self._ensure_connected()
        
        try:
            self._imap.select(folder)
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
    
    def send(
        self,
        to: str,
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        html: bool = False,
        attachments: Optional[list[str]] = None,
    ) -> bool:
        """发送邮件 - 手动构建邮件格式，不依赖 email.mime 模块

        Args:
            to: 收件人邮箱地址
            subject: 邮件主题
            body: 邮件正文
            cc: 抄送列表
            html: 是否为 HTML 格式
            attachments: 附件文件路径列表
        """
        try:
            # 手动构建邮件内容
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

            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                server.login(self.email_addr, self.auth_code)
                server.sendmail(self.email_addr, recipients, msg_str)

            return True
        except Exception as e:
            raise RuntimeError(f"发送邮件失败: {e}")
    
    # ── 新邮件监听 ──
    
    def get_latest_uid(self, folder: str = "INBOX") -> Optional[str]:
        """获取文件夹中最新的邮件 UID"""
        self._ensure_connected()
        
        try:
            self._imap.select(folder, readonly=True)
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
    
    def get_new_emails_since_uid(
        self,
        last_uid: str,
        folder: str = "INBOX",
        limit: int = 20,
        unread_only: bool = False,
    ) -> list[dict]:
        """获取自上次 UID 之后的新邮件（轻量级邮件头）

        Args:
            last_uid: 基线 UID，只返回 UID 大于此值的邮件
            folder: 邮箱文件夹
            limit: 最多返回数量
            unread_only: 为 True 时只返回未读邮件（uid > baseline 且 \\Seen 不在 flags 中）
        """
        self._ensure_connected()

        try:
            self._imap.select(folder, readonly=True)
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
                    if not header_info:
                        continue
                    # unread_only 模式：跳过已读邮件
                    if unread_only:
                        flags = header_info.get("flags", [])
                        if "\\Seen" in flags:
                            continue
                    results.append(header_info)
                except Exception:
                    continue

            return results
        except Exception as e:
            self._reconnect()
            raise RuntimeError(f"获取新邮件失败: {e}")
