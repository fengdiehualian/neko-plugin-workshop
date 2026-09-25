"""
猫娘邮件插件 - 插件主类
提供猫娘可调用的邮件操作接口

v0.2 优化:
  - get_today_summary 使用轻量级邮件头方法,速度提升10倍+
  - 新增 get_all_emails 接口,支持已读+未读邮件列表
  - 新增 get_email_detail 接口,按需加载完整邮件
  - 新增新邮件轮询监听机制(每5分钟自动检查)
"""

import threading
from datetime import datetime
from typing import Optional

from .client import NekoMailClient
from .models import EmailMessage, EmailSnippet, EmailSummary
from .operation_log import OperationLog
from .parser import classify_email_type


class NekoMailPlugin:
    """猫娘邮件插件主类"""
    
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
        master_name: str = "主人",
        catgirl_name: str = "喵喵",
    ):
        self.client = NekoMailClient(
            email_addr=email_addr,
            auth_code=auth_code,
            imap_server=imap_server,
            imap_port=imap_port,
            smtp_server=smtp_server,
            smtp_port=smtp_port,
            high_priority_senders=high_priority_senders,
            ignore_folders=ignore_folders,
        )
        self.op_log = OperationLog()
        self.master_name = master_name
        self.catgirl_name = catgirl_name
        
        # 新邮件轮询监听
        self._polling_thread: Optional[threading.Thread] = None
        self._polling_stop_event = threading.Event()
        self._polling_interval = 300  # 5分钟 = 300秒
        self._last_known_uid: Optional[str] = None
        self._new_email_callback = None  # 外部可注册的回调函数
        self._polling_lock = threading.Lock()
        
        # 已处理 UID 的集合，用于去重防止重复推送
        self._processed_uids: set[str] = set()
        self._processed_uids_lock = threading.Lock()
        self._max_processed_uids = 1000  # 最多保留 1000 个已处理 UID
    
    # === 读取类 ===
    
    def list_folders(self) -> list[dict]:
        """列出所有文件夹及未读数"""
        try:
            folders = self.client.list_folders()
            return [
                {
                    "name": f.name,
                    "unread_count": f.unread_count,
                    "total_count": f.total_count,
                }
                for f in folders
            ]
        except Exception as e:
            return {"error": str(e)}
    
    def get_unread(self, folder: str = "INBOX", limit: int = 50, offset: int = 0) -> list[dict]:
        """获取未读邮件 (轻量级邮件头,不下载正文)，支持分页"""
        try:
            emails = self.client.get_unread_headers(folder=folder, limit=limit, offset=offset)
            total = self.client.get_unread_count(folder=folder)
            return {
                "emails": [self._header_to_dict(e) for e in emails],
                "total": total,
                "offset": offset,
                "count": len(emails)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_all_emails(self, folder: str = "INBOX", limit: int = 50, offset: int = 0) -> dict:
        """获取所有邮件 (已读+未读,轻量级邮件头)，支持分页"""
        try:
            emails = self.client.get_all_emails_headers(folder=folder, limit=limit, offset=offset)
            total = self.client.get_all_emails_count(folder=folder)
            return {
                "emails": [self._header_to_dict(e) for e in emails],
                "total": total,
                "offset": offset,
                "count": len(emails)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_email_detail(self, uid: str, folder: str = "INBOX") -> dict:
        """获取单封邮件完整详情 (含正文和附件)"""
        try:
            email_msg = self.client.get_email_detail(uid=uid, folder=folder)
            if email_msg:
                return self._email_to_dict(email_msg)
            return {"error": "邮件未找到"}
        except Exception as e:
            return {"error": str(e)}
    
    def search(self, keyword: str, folder: str = "INBOX", limit: int = 100, offset: int = 0) -> dict:
        """关键词搜索主题+正文+发件人，支持分页"""
        try:
            emails = self.client.search(keyword=keyword, folder=folder, limit=limit, offset=offset)
            total = self.client.search_count(keyword=keyword, folder=folder)
            return {
                "emails": [self._email_to_dict(e) for e in emails],
                "total": total,
                "offset": offset,
                "count": len(emails)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_today_summary(self) -> dict:
        """今日邮件摘要,按优先级分类 (使用轻量级方法)"""
        try:
            # 轻量级: 只获取邮件头,不下载正文
            today_headers = self.client.get_today_emails_headers()
            unread_headers = self.client.get_unread_headers(limit=50)
            
            high = []
            medium = []
            low = []
            
            for h in today_headers:
                snippet = EmailSnippet(
                    uid=h["uid"],
                    subject=h["subject"],
                    sender=h["sender"],
                    preview="",  # 轻量级没有正文预览
                    time=h["date"].strftime("%H:%M"),
                    priority=h["priority"],
                    folder=h["folder"],
                )
                
                if h["priority"] == "high":
                    high.append(snippet)
                elif h["priority"] == "low":
                    low.append(snippet)
                else:
                    medium.append(snippet)
            
            summary = EmailSummary(
                total_unread=len(unread_headers),
                total_today=len(today_headers),
                high_priority=high,
                medium_priority=medium,
                low_priority=low,
            )
            
            return {
                "total_unread": summary.total_unread,
                "total_today": summary.total_today,
                "high_priority": [s.model_dump() for s in summary.high_priority],
                "medium_priority": [s.model_dump() for s in summary.medium_priority],
                "low_priority": [s.model_dump() for s in summary.low_priority],
                "catgirl_text": summary.to_catgirl_text(),
            }
        except Exception as e:
            return {"error": str(e)}
    
    # === 操作类 ===
    
    def mark_read(self, uid: str, folder: str = "INBOX") -> dict:
        """标记已读"""
        try:
            success = self.client.mark_read(uid=uid, folder=folder)
            if success:
                self.op_log.log_operation(
                    operation_type="mark_read",
                    description=f"标记邮件 {uid} 为已读",
                    email_uid=uid
                )
            return {"success": success, "uid": uid}
        except Exception as e:
            return {"error": str(e)}
    
    def batch_mark_read(self, uids: list[str] | str, folder: str = "INBOX") -> dict:
        """批量标记邮件已读，支持传入 'all' 标记所有邮件"""
        try:
            # 支持 "all" 参数
            if uids == "all":
                result = self.client.mark_all_read(folder=folder)
                if "success" in result and result["success"] > 0:
                    self.op_log.log_operation(
                        operation_type="batch_mark_read",
                        description=f"批量标记文件夹 {folder} 内所有邮件为已读，共 {result['success']} 封",
                        details={"count": result["success"], "folder": folder}
                    )
                return result
            
            result = self.client.batch_mark_read(uids=uids, folder=folder)
            if result.get("success", 0) > 0:
                self.op_log.log_operation(
                    operation_type="batch_mark_read",
                    description=f"批量标记 {result['success']} 封邮件为已读",
                    details={"count": result["success"], "failed": result.get("failed", 0)}
                )
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def mark_all_read(self, folder: str = "INBOX") -> dict:
        """标记文件夹内所有邮件为已读"""
        try:
            result = self.client.mark_all_read(folder=folder)
            if result.get("success", 0) > 0:
                self.op_log.log_operation(
                    operation_type="batch_mark_read",
                    description=f"标记文件夹 {folder} 内所有邮件为已读，共 {result['success']} 封",
                    details={"count": result["success"], "folder": folder}
                )
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def batch_delete(self, uids: list[str], folder: str = "INBOX") -> dict:
        """批量删除邮件"""
        try:
            result = self.client.batch_delete(uids=uids, folder=folder)
            if result.get("success", 0) > 0:
                self.op_log.log_operation(
                    operation_type="batch_delete",
                    description=f"批量删除 {result['success']} 封邮件",
                    details={"count": result["success"], "failed": result.get("failed", 0), "folder": folder}
                )
            return result
        except Exception as e:
            return {"error": str(e)}
    
    def send(
        self,
        to: str,
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        html: bool = False,
        attachments: Optional[list[str]] = None,
    ) -> dict:
        """发送邮件

        Args:
            to: 收件人邮箱地址
            subject: 邮件主题
            body: 邮件正文
            cc: 抄送列表
            html: 是否为 HTML 格式
            attachments: 附件文件路径列表
        """
        try:
            success = self.client.send(
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                html=html,
                attachments=attachments,
            )
            if success:
                self.op_log.log_operation(
                    operation_type="send_email",
                    description=f"发送邮件给 {to}",
                    details={"to": to, "subject": subject, "cc": cc, "has_attachments": bool(attachments)}
                )
            return {"success": success, "to": to, "subject": subject}
        except Exception as e:
            return {"error": str(e)}
    
    # === 新邮件监听 ===
    
    def check_new_emails(self, last_uid: Optional[str] = None, folder: str = "INBOX", limit: int = 20, unread_only: bool = True) -> dict:
        """检查新邮件，返回自上次 UID 之后的新邮件

        Args:
            last_uid: 基线 UID，只返回 UID 大于此值的邮件
            folder: 邮箱文件夹
            limit: 最多返回数量
            unread_only: 是否只返回未读邮件（默认 True），结合 uid 基线 + 已读状态双条件判定
        """
        try:
            # 如果没有提供 last_uid，获取当前最新的
            if not last_uid:
                current_latest = self.client.get_latest_uid(folder=folder)
                return {
                    "new_emails": [],
                    "latest_uid": current_latest,
                    "has_new": False,
                    "count": 0
                }

            # 获取新邮件（使用 unread_only 参数过滤已读邮件）
            new_headers = self.client.get_new_emails_since_uid(
                last_uid=last_uid,
                folder=folder,
                limit=limit,
                unread_only=unread_only
            )
            
            if not new_headers:
                return {
                    "new_emails": [],
                    "latest_uid": last_uid,
                    "has_new": False,
                    "count": 0
                }
            
            # 转换为前端格式
            new_emails = [self._header_to_dict(h) for h in new_headers]
            
            # 获取最新的 UID
            latest_uid = self.client.get_latest_uid(folder=folder)
            
            # 筛选高优先级和低优先级邮件
            high_priority = [e for e in new_emails if e.get("priority") == "high"]
            low_priority = [e for e in new_emails if e.get("priority") == "low"]
            
            # 自动标记低优先级邮件（广告/订阅）为已读
            auto_read_count = 0
            for email in low_priority:
                if email.get("category") in ["subscription", "ads"]:
                    try:
                        self.client.mark_read(uid=email["uid"], folder=folder)
                        self.op_log.log_operation(
                            operation_type="auto_mark_read",
                            description=f"自动标记低优先级邮件为已读: {email['subject']}",
                            email_uid=email["uid"],
                            email_subject=email["subject"],
                            email_sender=email["sender"]
                        )
                        self.op_log.update_category_stats(email.get("category", "general"), "auto_read")
                        auto_read_count += 1
                    except Exception:
                        pass
            
            # 记录操作日志
            self.op_log.log_operation(
                operation_type="check_new",
                description=f"检查新邮件，发现 {len(new_emails)} 封",
                details={"count": len(new_emails), "high_priority": len(high_priority), "auto_read": auto_read_count, "folder": folder}
            )
            
            # 记录分类统计
            for email in new_emails:
                category = email.get("category", "general")
                if email.get("priority") != "low":
                    self.op_log.update_category_stats(category, "pending")
            
            # 记录高优先级邮件
            for email in high_priority:
                self.op_log.log_important_email(
                    uid=email["uid"],
                    subject=email["subject"],
                    sender=email["sender"],
                    category=email.get("category", "general"),
                    category_label=email.get("category_label", "普通邮件"),
                    priority="high",
                    key_info=email.get("key_info", {}),
                    action_taken="pushed"
                )
                # 添加到待处理事项
                self.op_log.add_pending_item(
                    uid=email["uid"],
                    subject=email["subject"],
                    sender=email["sender"],
                    category=email.get("category", "general"),
                    category_label=email.get("category_label", "普通邮件"),
                    priority="high",
                    description=f"来自 {email['sender']} 的高优先级邮件"
                )
            
            return {
                "new_emails": new_emails,
                "latest_uid": latest_uid or last_uid,
                "has_new": len(new_emails) > 0,
                "count": len(new_emails),
                "high_priority_count": len(high_priority),
                "high_priority_emails": high_priority,
                "auto_read_count": auto_read_count
            }
        except Exception as e:
            return {"error": str(e)}
    
    # === 辅助方法 ===
    
    def _header_to_dict(self, h: dict) -> dict:
        """将邮件头 dict 转换为前端需要的格式"""
        # 创建临时 EmailMessage 用于分类
        temp_email = EmailMessage(
            uid=h["uid"],
            subject=h["subject"],
            sender=h["sender"],
            recipients=h.get("recipients", []),
            cc=h.get("cc", []),
            date=h["date"] if h.get("date") else datetime.now(),
            body_text="",  # 轻量级模式没有正文
            body_html=None,
            attachments=[],
            flags=h.get("flags", []),
            priority=h.get("priority", "medium"),
            folder=h.get("folder", "INBOX"),
        )
        
        # 智能分类（基于主题和发件人）
        classification = classify_email_type(temp_email, self.master_name, self.catgirl_name)
        
        return {
            "uid": h["uid"],
            "subject": h["subject"],
            "sender": h["sender"],
            "recipients": h.get("recipients", []),
            "cc": h.get("cc", []),
            "date": h["date"].isoformat() if h.get("date") else "",
            "body_text": "",
            "attachments": [],
            "flags": h.get("flags", []),
            "priority": h.get("priority", "medium"),
            "folder": h.get("folder", "INBOX"),
            "preview": "",
            "time_str": h["date"].strftime("%Y-%m-%d %H:%M") if h.get("date") else "",
            "has_attachments": h.get("has_attachments", False),
            # 新增分类信息
            "category": classification["category"],
            "category_label": classification["category_label"],
            "catgirl_hint": classification["catgirl_hint"],
        }
    
    def _email_to_dict(self, email: EmailMessage) -> dict:
        """将 EmailMessage 转换为字典"""
        # 智能分类
        classification = classify_email_type(email, self.master_name, self.catgirl_name)
        
        return {
            "uid": email.uid,
            "subject": email.subject,
            "sender": email.sender,
            "recipients": email.recipients,
            "cc": email.cc,
            "date": email.date.isoformat(),
            "body_text": email.body_text,
            "attachments": [
                {
                    "filename": a.filename,
                    "size": a.size,
                    "content_type": a.content_type,
                    "size_human": a.size_human(),
                }
                for a in email.attachments
            ],
            "flags": email.flags,
            "priority": email.priority,
            "folder": email.folder,
            "preview": email.preview(200),
            "time_str": email.time_str(),
            "has_attachments": email.has_attachments(),
            # 新增分类信息
            "category": classification["category"],
            "category_label": classification["category_label"],
            "key_info": classification["key_info"],
            "catgirl_hint": classification["catgirl_hint"],
        }
    
    # === 监控面板 & 日志查询 ===
    
    def get_daily_briefing(self, target_date: Optional[str] = None) -> dict:
        """获取每日简报数据（供早安播报插件调用）"""
        return self.op_log.get_daily_briefing(target_date)
    
    def get_operation_logs(self, limit: int = 100) -> list[dict]:
        """获取今日操作日志"""
        return self.op_log.get_today_logs(limit)
    
    def get_category_stats(self) -> dict:
        """获取邮件分类统计"""
        return self.op_log.get_category_stats()
    
    def get_pending_items(self) -> list[dict]:
        """获取待处理事项"""
        return self.op_log.get_pending_items()
    
    def get_important_emails(self, limit: int = 50) -> list[dict]:
        """获取重要邮件日志"""
        return self.op_log.get_important_emails(limit)
    
    def get_overview(self) -> dict:
        """获取今日概览数据"""
        return self.op_log.get_today_summary()
    
    # === 新邮件轮询监听 ===
    
    def start_polling(self, interval_seconds: int = 300, callback=None, calibrate_baseline: bool = True):
        """
        启动新邮件轮询监听

        Args:
            interval_seconds: 轮询间隔（秒），默认 300 秒（5 分钟）
            callback: 新邮件回调函数，签名 callback(new_emails: list[dict])
            calibrate_baseline: 是否自动校准基线到当前最新 UID（默认 True），避免旧邮件重推
        """
        with self._polling_lock:
            if self._polling_thread and self._polling_thread.is_alive():
                return {"error": "轮询已在运行中"}

            self._polling_interval = interval_seconds
            self._new_email_callback = callback
            self._polling_stop_event.clear()

            # 获取当前最新 UID 作为起点
            try:
                latest_uid = self.client.get_latest_uid(folder="INBOX")
                if calibrate_baseline:
                    # 自动校准基线：将 last_uid 设置为当前最新，避免旧邮件重推
                    self._last_known_uid = latest_uid
                    self.op_log.log_operation(
                        operation_type="baseline_calibrated",
                        description=f"轮询启动时自动校准基线到最新 UID: {latest_uid}",
                        details={"latest_uid": latest_uid}
                    )
                else:
                    self._last_known_uid = latest_uid
            except Exception:
                self._last_known_uid = None

            # 清空已处理 UID 缓存（重启后重新开始）
            with self._processed_uids_lock:
                self._processed_uids.clear()

            self._polling_thread = threading.Thread(
                target=self._polling_worker,
                name="NekoMailPollingThread",
                daemon=True
            )
            self._polling_thread.start()

            self.op_log.log_operation(
                operation_type="polling_started",
                description=f"启动新邮件轮询，间隔 {interval_seconds} 秒",
                details={"interval": interval_seconds, "calibrated": calibrate_baseline}
            )

            return {
                "status": "started",
                "interval": interval_seconds,
                "last_uid": self._last_known_uid,
                "calibrated": calibrate_baseline
            }
    
    def stop_polling(self):
        """停止新邮件轮询"""
        with self._polling_lock:
            if not self._polling_thread or not self._polling_thread.is_alive():
                return {"status": "not_running"}
            
            self._polling_stop_event.set()
            self._polling_thread.join(timeout=10)
            
            self.op_log.log_operation(
                operation_type="polling_stopped",
                description="停止新邮件轮询"
            )
            
            return {"status": "stopped"}
    
    def get_polling_status(self) -> dict:
        """获取轮询状态"""
        is_running = bool(self._polling_thread and self._polling_thread.is_alive())
        return {
            "is_running": is_running,
            "interval": self._polling_interval,
            "last_known_uid": self._last_known_uid
        }

    def calibrate_baseline(self, folder: str = "INBOX") -> dict:
        """手动校准基线到当前最新 UID

        将 last_uid 基线同步到邮箱当前最新 UID，用于：
        - 防止旧邮件被重复推送
        - 用户主动重置轮询状态

        注意：校准后，所有当前存在的邮件都不会再被当作「新邮件」推送。
        如果有真正的新邮件尚未阅读，建议先查看未读邮件再校准。
        """
        try:
            latest_uid = self.client.get_latest_uid(folder=folder)
            if not latest_uid:
                return {"error": "无法获取最新 UID"}

            old_uid = self._last_known_uid
            self._last_known_uid = latest_uid

            # 清空已处理 UID 缓存
            with self._processed_uids_lock:
                self._processed_uids.clear()

            self.op_log.log_operation(
                operation_type="baseline_calibrated",
                description=f"手动校准基线: {old_uid} -> {latest_uid}",
                details={"old_uid": old_uid, "new_uid": latest_uid}
            )

            return {
                "success": True,
                "old_uid": old_uid,
                "new_uid": latest_uid,
                "message": f"基线已校准到最新 UID: {latest_uid}"
            }
        except Exception as e:
            return {"error": str(e)}
    
    def _polling_worker(self):
        """轮询工作线程"""
        import logging
        logger = logging.getLogger("neko_mail.polling")
        logger.info("Polling worker started")

        while not self._polling_stop_event.is_set():
            try:
                # 检查新邮件（使用 unread_only=True 双条件判定）
                result = self.check_new_emails(
                    last_uid=self._last_known_uid,
                    folder="INBOX",
                    limit=50,
                    unread_only=True  # 关键：结合 uid 基线 + 已读状态双条件
                )

                logger.info(f"Check result: has_new={result.get('has_new')}, count={result.get('count')}, error={result.get('error')}")

                if result.get("has_new") and result.get("new_emails"):
                    new_emails = result["new_emails"]
                    logger.info(f"Found {len(new_emails)} new emails")

                    # 过滤掉已处理的邮件（去重）
                    unprocessed_emails = []
                    with self._processed_uids_lock:
                        for email_header in new_emails:
                            uid = email_header.get("uid")
                            if uid and uid not in self._processed_uids:
                                unprocessed_emails.append(email_header)
                            elif uid:
                                logger.info(f"Skipping already processed email uid={uid}")

                    if not unprocessed_emails:
                        logger.info("All emails already processed, skipping")
                        # 即使没有新邮件，也要更新 last_known_uid
                        if result.get("latest_uid"):
                            self._last_known_uid = result["latest_uid"]
                            logger.info(f"Updated latest_uid to {self._last_known_uid}")
                    else:
                        logger.info(f"Processing {len(unprocessed_emails)} unprocessed emails")

                        # 获取完整邮件详情（包含正文）用于推送
                        full_emails = []
                        for email_header in unprocessed_emails:
                            try:
                                uid = email_header.get("uid")
                                if uid:
                                    logger.info(f"Fetching full detail for email uid={uid}")
                                    full_detail = self.get_email_detail(uid=uid, folder="INBOX")
                                    if "error" not in full_detail:
                                        # 幂等校验：推送前再次确认邮件未读
                                        flags = full_detail.get("flags", [])
                                        if "\\Seen" in flags:
                                            logger.info(f"Email uid={uid} already read, skipping push")
                                            # 标记为已处理，避免下次重复检查
                                            with self._processed_uids_lock:
                                                self._processed_uids.add(uid)
                                            continue
                                        full_emails.append(full_detail)
                                        logger.info(f"Got full detail for uid={uid}, subject={full_detail.get('subject')}")
                                    else:
                                        logger.warning(f"Failed to get detail for uid={uid}: {full_detail.get('error')}")
                                        # 如果获取详情失败，使用邮件头信息
                                        full_emails.append(email_header)
                                else:
                                    logger.warning("Email header has no uid")
                                    full_emails.append(email_header)
                            except Exception as e:
                                logger.exception(f"Error getting email detail: {e}")
                                self.op_log.log_operation(
                                    operation_type="polling_get_detail_error",
                                    description=f"获取邮件详情失败: {e}",
                                    details={"uid": email_header.get("uid"), "error": str(e)}
                                )
                                # 失败时使用邮件头信息
                                full_emails.append(email_header)
                        
                        logger.info(f"Collected {len(full_emails)} full emails, triggering callback")
                        
                        # 触发回调，传递完整邮件详情
                        if self._new_email_callback:
                            try:
                                logger.info("Calling callback function")
                                self._new_email_callback(full_emails)
                                logger.info("Callback completed successfully")
                                
                                # 回调成功后，将已处理的 UID 加入集合
                                with self._processed_uids_lock:
                                    for email in full_emails:
                                        uid = email.get("uid")
                                        if uid:
                                            self._processed_uids.add(uid)
                                            logger.info(f"Marked uid={uid} as processed")
                                    
                                    # 清理过多的已处理 UID，防止内存泄漏
                                    if len(self._processed_uids) > self._max_processed_uids:
                                        # 保留最新的 500 个
                                        processed_uids_list = sorted(self._processed_uids, key=lambda x: int(x) if x.isdigit() else 0)
                                        self._processed_uids = set(processed_uids_list[-500:])
                                        logger.info(f"Cleaned up processed_uids, kept {len(self._processed_uids)}")
                                
                                # 更新 last_known_uid（在回调成功后更新）
                                if result.get("latest_uid"):
                                    self._last_known_uid = result["latest_uid"]
                                    logger.info(f"Updated latest_uid to {self._last_known_uid}")
                                
                            except Exception as e:
                                logger.exception(f"Callback execution failed: {e}")
                                self.op_log.log_operation(
                                    operation_type="polling_callback_error",
                                    description=f"新邮件回调执行失败: {e}",
                                    details={"error": str(e)}
                                )
                                # 回调失败，不更新 last_known_uid，下次重试
                        else:
                            logger.warning("No callback function registered!")
                            # 没有回调函数，也要更新 last_known_uid
                            if result.get("latest_uid"):
                                self._last_known_uid = result["latest_uid"]
                                logger.info(f"Updated latest_uid to {self._last_known_uid}")
                        
                        # 记录日志
                        self.op_log.log_operation(
                            operation_type="new_emails_detected",
                            description=f"检测到 {len(unprocessed_emails)} 封新邮件",
                            details={"count": len(unprocessed_emails), "emails": [e.get("subject") for e in unprocessed_emails[:5]]}
                        )
                
            except Exception as e:
                logger.exception(f"Polling worker error: {e}")
                self.op_log.log_operation(
                    operation_type="polling_error",
                    description=f"轮询检查失败: {e}",
                    details={"error": str(e)}
                )
            
            # 等待下一轮（使用 stop_event.wait 以便快速响应停止信号）
            self._polling_stop_event.wait(self._polling_interval)
        
        logger.info("Polling worker stopped")
    
    def close(self):
        """关闭连接"""
        self.stop_polling()  # 先停止轮询
        self.client.disconnect()
