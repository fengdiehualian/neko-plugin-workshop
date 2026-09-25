"""
猫娘邮件插件 - 操作日志系统
记录猫娘的所有邮箱操作，供前端展示
"""

import json
from collections import defaultdict
from datetime import date, datetime
from typing import Optional


class OperationLog:
    """操作日志记录器"""
    
    def __init__(self, max_logs_per_day: int = 500):
        self.max_logs_per_day = max_logs_per_day
        self._logs: dict[str, list[dict]] = defaultdict(list)  # date_str -> logs
        self._important_emails: list[dict] = []  # 重要邮件推送记录
        self._pending_items: list[dict] = []  # 待处理事项
        self._category_stats: dict[str, dict] = defaultdict(lambda: {
            "total": 0,
            "auto_read": 0,
            "pushed": 0,
            "pending": 0
        })
    
    def log_operation(
        self,
        operation_type: str,
        description: str,
        details: Optional[dict] = None,
        email_uid: Optional[str] = None,
        email_subject: Optional[str] = None,
        email_sender: Optional[str] = None,
    ):
        """
        记录一次操作
        
        operation_type: 
          - mark_read: 标记已读
          - batch_mark_read: 批量标记已读
          - auto_mark_read: 自动标记已读（低优先级）
          - extract_code: 提取验证码
          - push_reminder: 推送提醒
          - send_email: 发送邮件
          - classify: 分类邮件
          - check_new: 检查新邮件
        """
        today_str = date.today().isoformat()
        
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": operation_type,
            "description": description,
            "details": details or {},
            "email_uid": email_uid,
            "email_subject": email_subject,
            "email_sender": email_sender,
        }
        
        self._logs[today_str].append(log_entry)
        
        # 限制每日日志数量
        if len(self._logs[today_str]) > self.max_logs_per_day:
            self._logs[today_str] = self._logs[today_str][-self.max_logs_per_day:]
    
    def log_important_email(
        self,
        uid: str,
        subject: str,
        sender: str,
        category: str,
        category_label: str,
        priority: str,
        key_info: dict,
        action_taken: str,
    ):
        """记录重要邮件推送"""
        entry = {
            "uid": uid,
            "subject": subject,
            "sender": sender,
            "category": category,
            "category_label": category_label,
            "priority": priority,
            "key_info": key_info,
            "action_taken": action_taken,
            "timestamp": datetime.now().isoformat(),
            "time": datetime.now().strftime("%H:%M:%S"),
            "user_feedback": None,  # 用户反馈（预留）
        }
        self._important_emails.append(entry)
        
        # 限制数量
        if len(self._important_emails) > 200:
            self._important_emails = self._important_emails[-200:]
    
    def add_pending_item(
        self,
        uid: str,
        subject: str,
        sender: str,
        category: str,
        category_label: str,
        priority: str,
        description: str,
    ):
        """添加待处理事项"""
        item = {
            "uid": uid,
            "subject": subject,
            "sender": sender,
            "category": category,
            "category_label": category_label,
            "priority": priority,
            "description": description,
            "added_at": datetime.now().isoformat(),
        }
        
        # 避免重复
        if not any(p["uid"] == uid for p in self._pending_items):
            self._pending_items.append(item)
            # 按优先级排序
            self._pending_items.sort(key=lambda x: (
                0 if x["priority"] == "high" else 1 if x["priority"] == "medium" else 2
            ))
    
    def remove_pending_item(self, uid: str):
        """移除待处理事项"""
        self._pending_items = [p for p in self._pending_items if p["uid"] != uid]
    
    def update_category_stats(self, category: str, action: str):
        """
        更新分类统计
        
        action: auto_read / pushed / pending
        """
        self._category_stats[category]["total"] += 1
        if action == "auto_read":
            self._category_stats[category]["auto_read"] += 1
        elif action == "pushed":
            self._category_stats[category]["pushed"] += 1
        elif action == "pending":
            self._category_stats[category]["pending"] += 1
    
    def get_today_logs(self, limit: int = 100) -> list[dict]:
        """获取今日操作日志（按时间倒序）"""
        today_str = date.today().isoformat()
        logs = self._logs.get(today_str, [])
        return list(reversed(logs[-limit:]))
    
    def get_important_emails(self, limit: int = 50) -> list[dict]:
        """获取重要邮件日志"""
        return list(reversed(self._important_emails[-limit:]))
    
    def get_pending_items(self) -> list[dict]:
        """获取待处理事项"""
        return self._pending_items.copy()
    
    def get_category_stats(self) -> dict:
        """获取分类统计"""
        return dict(self._category_stats)
    
    def get_today_summary(self) -> dict:
        """获取今日概览数据"""
        today_str = date.today().isoformat()
        today_logs = self._logs.get(today_str, [])
        
        # 统计各类操作数量
        operation_counts = defaultdict(int)
        for log in today_logs:
            operation_counts[log["type"]] += 1
        
        return {
            "date": today_str,
            "total_operations": len(today_logs),
            "mark_read_count": operation_counts.get("mark_read", 0),
            "batch_mark_read_count": operation_counts.get("batch_mark_read", 0),
            "auto_mark_read_count": operation_counts.get("auto_mark_read", 0),
            "extract_code_count": operation_counts.get("extract_code", 0),
            "push_reminder_count": operation_counts.get("push_reminder", 0),
            "send_email_count": operation_counts.get("send_email", 0),
            "classify_count": operation_counts.get("classify", 0),
            "check_new_count": operation_counts.get("check_new", 0),
            "important_emails_count": len(self._important_emails),
            "pending_items_count": len(self._pending_items),
        }
    
    def get_daily_briefing(self, target_date: Optional[str] = None) -> dict:
        """
        获取每日简报数据（供早安播报插件调用）
        
        返回格式：
        {
            "date": "2026-08-06",
            "total_received": 234,
            "auto_processed": 180,
            "important_unread": 3,
            "highlights": [
                {"subject": "xxx", "sender": "xxx", "type": "作业/验证码/安全提醒"},
            ],
            "pending_items": ["验证码待处理", "作业待查看"]
        }
        """
        target_date = target_date or date.today().isoformat()
        logs = self._logs.get(target_date, [])
        
        # 统计自动处理数量
        auto_processed = sum(1 for log in logs if log["type"] == "auto_mark_read")
        
        # 提取重要邮件
        highlights = []
        for email_log in self._important_emails:
            if email_log["timestamp"].startswith(target_date):
                highlights.append({
                    "subject": email_log["subject"],
                    "sender": email_log["sender"],
                    "type": email_log["category_label"],
                    "key_info": email_log["key_info"],
                })
        
        # 待处理事项
        pending_descriptions = [
            f"{item['category_label']}: {item['subject']}"
            for item in self._pending_items
        ]
        
        return {
            "date": target_date,
            "total_received": len(logs),
            "auto_processed": auto_processed,
            "important_unread": len([h for h in highlights if h.get("key_info")]),
            "highlights": highlights[:10],  # 最多10条
            "pending_items": pending_descriptions[:5],  # 最多5条
        }
    
    def export_logs(self, date_str: Optional[str] = None) -> str:
        """导出日志为JSON字符串"""
        target_date = date_str or date.today().isoformat()
        data = {
            "date": target_date,
            "logs": self._logs.get(target_date, []),
            "important_emails": self._important_emails,
            "pending_items": self._pending_items,
            "category_stats": dict(self._category_stats),
        }
        return json.dumps(data, ensure_ascii=False, indent=2)
