"""
网易邮箱助手 v0.2.0 (Neko 163Mail)

让猫娘能读取用户的网易163邮箱,生成邮件摘要、判断优先级、
帮用户标记已读、发送邮件(支持附件)、回复邮件、转发邮件、下载附件。

数据模型:
  - EmailMessage: 完整邮件
  - EmailSummary: 今日摘要(按优先级分类)
  - EmailSnippet: 邮件摘要片段

LLM 工具:
  - neko_163mail_get_summary: 获取今日邮件摘要
  - neko_163mail_get_unread: 获取未读邮件列表
  - neko_163mail_get_all_emails: 获取所有邮件列表(分页)
  - neko_163mail_get_email_detail: 获取单封邮件详情
  - neko_163mail_search: 搜索邮件
  - neko_163mail_mark_read: 标记邮件已读
  - neko_163mail_batch_mark_read: 批量标记已读
  - neko_163mail_mark_all_read: 标记所有邮件已读
  - neko_163mail_send: 发送邮件(支持附件)
  - neko_163mail_reply: 回复邮件(支持附件)
  - neko_163mail_forward: 转发邮件
  - neko_163mail_download_attachment: 下载邮件附件到本地
  - neko_163mail_list_folders: 列出邮箱文件夹
  - neko_163mail_check_new: 检查新邮件
  - neko_163mail_batch_delete: 批量删除邮件
  - neko_163mail_get_daily_briefing: 获取每日邮件简报
  - neko_163mail_get_operation_logs: 获取操作日志
  - neko_163mail_get_category_stats: 获取分类统计
  - neko_163mail_get_pending_items: 获取待处理事项
  - neko_163mail_get_important_emails: 获取重要邮件日志
  - neko_163mail_get_overview: 获取今日概览
  - neko_163mail_start_polling: 启动邮件轮询(支持 interval_seconds)
  - neko_163mail_stop_polling: 停止邮件轮询
  - neko_163mail_get_polling_status: 获取轮询状态
  - neko_163mail_get_names: 获取称呼配置
  - neko_163mail_set_names: 设置称呼配置
"""

from __future__ import annotations

import threading
from typing import Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from .plugin import Neko163MailPlugin

# ── 常量 ──────────────────────────────────────────────────────────────

_PLUGIN_ID = "neko_163mail"
_DEFAULT_IMAP_SERVER = "imap.163.com"
_DEFAULT_IMAP_PORT = 993
_DEFAULT_SMTP_SERVER = "smtp.163.com"
_DEFAULT_SMTP_PORT = 465


# ── 插件主类 ──────────────────────────────────────────────────────────

@neko_plugin
class Neko163MailPluginEntry(NekoPluginBase):
    """网易邮箱助手 - N.E.K.O 插件入口"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = threading.Lock()
        self._mail_plugin: Optional[Neko163MailPlugin] = None

    # ── lifecycle ────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        """启动插件,加载配置"""
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        section = cfg.get("neko_163mail") if isinstance(cfg.get("neko_163mail"), dict) else {}

        # 读取配置
        email_addr = str(section.get("email_addr", "")).strip()
        auth_code = str(section.get("auth_code", "")).strip()
        imap_server = str(section.get("imap_server", _DEFAULT_IMAP_SERVER)).strip()
        imap_port = int(section.get("imap_port", _DEFAULT_IMAP_PORT))
        smtp_server = str(section.get("smtp_server", _DEFAULT_SMTP_SERVER)).strip()
        smtp_port = int(section.get("smtp_port", _DEFAULT_SMTP_PORT))

        # 高优先级发件人
        high_senders_raw = str(section.get("high_priority_senders", "")).strip()
        high_priority_senders = [s.strip() for s in high_senders_raw.split(",") if s.strip()] if high_senders_raw else []

        # 忽略的文件夹
        ignore_raw = str(section.get("ignore_folders", "")).strip()
        ignore_folders = [s.strip() for s in ignore_raw.split(",") if s.strip()] if ignore_raw else []

        # 猫娘称呼配置
        master_name = str(section.get("master_name", "主人")).strip()
        catgirl_name = str(section.get("catgirl_name", "喵喵")).strip()

        if not email_addr or not auth_code:
            self.logger.error("163_EMAIL or 163_AUTH_CODE not configured")
            return Err(SdkError("邮箱配置缺失: 请在 plugin.toml 中配置 neko_163mail.email_addr 和 neko_163mail.auth_code"))

        try:
            self._mail_plugin = Neko163MailPlugin(
                email_addr=email_addr,
                auth_code=auth_code,
                imap_server=imap_server,
                imap_port=imap_port,
                smtp_server=smtp_server,
                smtp_port=smtp_port,
                high_priority_senders=high_priority_senders,
                ignore_folders=ignore_folders,
                master_name=master_name,
                catgirl_name=catgirl_name,
            )
        except Exception as e:
            self.logger.error("初始化邮件插件失败: {}", e)
            return Err(SdkError(f"初始化邮件插件失败: {e}"))

        self.logger.info(
            "Neko163Mail started: email={}, imap={}:{}, smtp={}:{}, master_name={}, catgirl_name={}",
            email_addr, imap_server, imap_port, smtp_server, smtp_port, master_name, catgirl_name,
        )

        # 自动启动轮询监听（每5分钟检查一次新邮件）
        try:
            polling_result = self._mail_plugin.start_polling(
                interval_seconds=300,
                callback=self._push_new_email_notification
            )
            if "error" not in polling_result:
                self.logger.info("邮件轮询已自动启动，间隔 300 秒")
            else:
                self.logger.warning(f"邮件轮询启动失败: {polling_result['error']}")
        except Exception as e:
            self.logger.warning(f"邮件轮询自动启动异常: {e}")

        return Ok({"status": "running", "version": "0.2.0", "email": email_addr})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        """关闭插件"""
        if self._mail_plugin:
            self._mail_plugin.close()
        self.logger.info("Neko163Mail shutdown")
        return Ok({"status": "shutdown"})

    # ── 辅助方法 ─────────────────────────────────────────────────────

    def _get_plugin(self) -> Neko163MailPlugin:
        """获取邮件插件实例"""
        if self._mail_plugin is None:
            raise RuntimeError("邮件插件未初始化,请检查配置")
        return self._mail_plugin

    def _push_new_email_notification(self, new_emails: list[dict]) -> None:
        """推送新邮件通知（包含正文内容）- 从后台线程安全调用"""
        if not new_emails:
            self.logger.warning("_push_new_email_notification: new_emails is empty")
            return

        self.logger.info(f"_push_new_email_notification: processing {len(new_emails)} emails")

        # 获取动态称呼
        plugin = self._get_plugin()
        master_name = getattr(plugin, 'master_name', '主人')

        count = len(new_emails)
        first_email = new_emails[0]
        subject = first_email.get("subject", "无主题")
        sender = first_email.get("sender", "未知发件人")
        priority = first_email.get("priority", "medium")
        body_text = first_email.get("body_text", "")

        self.logger.info(f"Email details: subject={subject}, sender={sender}, priority={priority}, has_body={bool(body_text)}")

        # 构建通知文本（包含正文）
        if count == 1:
            # 单封邮件：包含完整正文
            if body_text:
                # 截取正文前 500 字符，避免过长
                body_preview = body_text[:500].strip()
                if len(body_text) > 500:
                    body_preview += "..."

                if priority == "high":
                    text = f"🔔 {master_name}，收到一封重要邮件喵！\n\n发件人：{sender}\n主题：{subject}\n\n邮件内容：\n{body_preview}"
                    priority_level = 8
                else:
                    text = f"📬 {master_name}，收到新邮件喵~\n\n发件人：{sender}\n主题：{subject}\n\n邮件内容：\n{body_preview}"
                    priority_level = 5
            else:
                # 没有正文，只显示主题和发件人
                if priority == "high":
                    text = f"🔔 {master_name}，收到一封重要邮件！来自 {sender}：「{subject}」，需要立即查看哦~"
                    priority_level = 8
                else:
                    text = f"📬 {master_name}，收到新邮件，来自 {sender}：「{subject}」"
                    priority_level = 5
        else:
            # 多封邮件：显示摘要 + 第一封的正文
            if body_text:
                body_preview = body_text[:300].strip()
                if len(body_text) > 300:
                    body_preview += "..."

                text = f"📬 {master_name}，收到 {count} 封新邮件喵~\n\n最新一封来自 {sender}：「{subject}」\n\n邮件内容：\n{body_preview}"
                priority_level = 5
            else:
                text = f"📬 {master_name}，收到 {count} 封新邮件，最新一封来自 {sender}：「{subject}」"
                priority_level = 5

        self.logger.info(f"Pushing notification: priority={priority_level}, text_length={len(text)}")

        try:
            # 使用异步版本，确保线程安全
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            if loop.is_running():
                # 如果事件循环已在运行，创建任务
                asyncio.run_coroutine_threadsafe(
                    self.ctx.push_message_async(
                        source="neko_163mail",
                        visibility=[],
                        ai_behavior="respond",  # 修复：使用正确的 ai_behavior
                        parts=[{"type": "text", "text": text}],
                        priority=priority_level,
                        metadata={
                            "description": f"📧 新邮件通知 [{count}封]",
                            "email_count": count,
                            "first_subject": subject,
                            "first_sender": sender,
                        },
                    ),
                    loop
                )
                self.logger.info("push_message_async scheduled successfully")
            else:
                # 直接调用同步版本
                self.ctx.push_message(
                    source="neko_163mail",
                    visibility=[],
                    ai_behavior="respond",  # 修复：使用正确的 ai_behavior
                    parts=[{"type": "text", "text": text}],
                    priority=priority_level,
                    metadata={
                        "description": f"📧 新邮件通知 [{count}封]",
                        "email_count": count,
                        "first_subject": subject,
                        "first_sender": sender,
                    },
                )
                self.logger.info("push_message called successfully")
        except Exception as e:
            self.logger.exception(f"push_message failed: {type(e).__name__}: {e}")

    # ── LLM 工具 ─────────────────────────────────────────────────────

    @llm_tool(
        name="neko_163mail_get_summary",
        description="获取今日邮件摘要。返回未读数、今日邮件数、按优先级分类的邮件列表。猫娘可以用这个信息告诉用户今天有哪些重要邮件。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="get_summary",
        name="获取今日邮件摘要",
        description="获取今日邮件摘要,按优先级分类",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["total_unread", "total_today", "high_priority", "medium_priority", "low_priority", "catgirl_text"],
    )
    async def get_summary(self, **_):
        """获取今日邮件摘要"""
        try:
            plugin = self._get_plugin()
            self.logger.info("get_summary: calling get_today_summary...")
            result = plugin.get_today_summary()
            self.logger.info("get_summary: result = {}", result)
            if isinstance(result, dict) and "error" in result:
                self.logger.error("get_summary: backend returned error = {}", result["error"])
                return Err(SdkError(result["error"]))
            self.logger.info("get_summary: returning Ok")
            return Ok(result)
        except Exception as e:
            self.logger.exception("get_summary: exception")
            return Err(SdkError(f"获取邮件摘要失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_unread",
        description="获取未读邮件列表。返回邮件的详细信息,包括主题、发件人、正文预览、优先级等。支持分页。",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
                "limit": {"type": "integer", "description": "每页数量,默认 100"},
                "offset": {"type": "integer", "description": "偏移量,用于加载更多,默认 0"},
            },
            "required": [],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="get_unread",
        name="获取未读邮件",
        description="获取未读邮件列表,支持分页",
        input_schema={
            "type": "object",
            "properties": {
                "folder": {"type": "string"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
            "required": [],
        },
        llm_result_fields=["emails", "total", "offset", "count"],
    )
    async def get_unread(self, folder: str = "INBOX", limit: int = 100, offset: int = 0, **_):
        """获取未读邮件，支持分页"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_unread(folder=folder, limit=limit, offset=offset)
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"获取未读邮件失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_all_emails",
        description="获取所有邮件列表(已读+未读)。返回邮件的详细信息,包括主题、发件人、时间、优先级等。支持分页。",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
                "limit": {"type": "integer", "description": "每页数量,默认 100"},
                "offset": {"type": "integer", "description": "偏移量,用于加载更多,默认 0"},
            },
            "required": [],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="get_all_emails",
        name="获取所有邮件",
        description="获取所有邮件列表(已读+未读),支持分页",
        input_schema={
            "type": "object",
            "properties": {
                "folder": {"type": "string"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
            "required": [],
        },
        llm_result_fields=["emails", "total", "offset", "count"],
    )
    async def get_all_emails(self, folder: str = "INBOX", limit: int = 100, offset: int = 0, **_):
        """获取所有邮件(已读+未读)，支持分页"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_all_emails(folder=folder, limit=limit, offset=offset)
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"获取所有邮件失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_email_detail",
        description="获取单封邮件的完整详情,包括正文、附件等。",
        parameters={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "邮件 UID"},
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
            },
            "required": ["uid"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="get_email_detail",
        name="获取邮件详情",
        description="获取单封邮件完整详情",
        input_schema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "folder": {"type": "string"},
            },
            "required": ["uid"],
        },
        llm_result_fields=["email"],
    )
    async def get_email_detail(self, uid: str, folder: str = "INBOX", **_):
        """获取邮件详情"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_email_detail(uid=uid, folder=folder)
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok({"email": result})
        except Exception as e:
            return Err(SdkError(f"获取邮件详情失败: {e}"))

    @llm_tool(
        name="neko_163mail_search",
        description="搜索邮件。可以按关键词搜索主题、发件人、正文。支持分页。",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词"},
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
                "limit": {"type": "integer", "description": "每页数量,默认 100"},
                "offset": {"type": "integer", "description": "偏移量,用于加载更多,默认 0"},
            },
            "required": ["keyword"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="search",
        name="搜索邮件",
        description="按关键词搜索邮件,支持分页",
        input_schema={
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "folder": {"type": "string"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
            },
            "required": ["keyword"],
        },
        llm_result_fields=["emails", "total", "offset", "count"],
    )
    async def search(self, keyword: str, folder: str = "INBOX", limit: int = 100, offset: int = 0, **_):
        """搜索邮件，支持分页"""
        try:
            plugin = self._get_plugin()
            result = plugin.search(keyword=keyword, folder=folder, limit=limit, offset=offset)
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok({**result, "keyword": keyword})
        except Exception as e:
            return Err(SdkError(f"搜索邮件失败: {e}"))

    @llm_tool(
        name="neko_163mail_mark_read",
        description="标记邮件已读。当用户说'标记为已读'、'看过了'等时使用。",
        parameters={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "邮件 UID"},
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
            },
            "required": ["uid"],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="mark_read",
        name="标记邮件已读",
        description="标记邮件为已读",
        input_schema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "folder": {"type": "string"},
            },
            "required": ["uid"],
        },
        llm_result_fields=["success"],
    )
    async def mark_read(self, uid: str, folder: str = "INBOX", **_):
        """标记邮件已读"""
        try:
            plugin = self._get_plugin()
            result = plugin.mark_read(uid=uid, folder=folder)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"标记已读失败: {e}"))

    @llm_tool(
        name="neko_163mail_batch_mark_read",
        description="批量标记邮件已读。可以传入多个邮件 UID 一次性标记为已读。",
        parameters={
            "type": "object",
            "properties": {
                "uids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "邮件 UID 列表",
                },
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
            },
            "required": ["uids"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="batch_mark_read",
        name="批量标记邮件已读",
        description="批量标记多封邮件为已读",
        input_schema={
            "type": "object",
            "properties": {
                "uids": {"type": "array", "items": {"type": "string"}},
                "folder": {"type": "string"},
            },
            "required": ["uids"],
        },
        llm_result_fields=["success", "failed", "failed_uids"],
    )
    async def batch_mark_read(self, uids: list[str], folder: str = "INBOX", **_):
        """批量标记邮件已读"""
        try:
            plugin = self._get_plugin()
            result = plugin.batch_mark_read(uids=uids, folder=folder)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"批量标记已读失败: {e}"))

    @llm_tool(
        name="neko_163mail_mark_all_read",
        description="标记文件夹内所有邮件为已读。默认标记 INBOX 文件夹。",
        parameters={
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
            },
            "required": [],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="mark_all_read",
        name="标记所有邮件已读",
        description="标记文件夹内所有邮件为已读",
        input_schema={
            "type": "object",
            "properties": {
                "folder": {"type": "string"},
            },
            "required": [],
        },
        llm_result_fields=["success", "message"],
    )
    async def mark_all_read(self, folder: str = "INBOX", **_):
        """标记所有邮件已读"""
        try:
            plugin = self._get_plugin()
            result = plugin.mark_all_read(folder=folder)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"标记所有已读失败: {e}"))

    @llm_tool(
        name="neko_163mail_send",
        description="发送邮件。可以指定收件人、主题、正文,可选抄送和附件文件路径列表。",
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "收件人邮箱"},
                "subject": {"type": "string", "description": "邮件主题"},
                "body": {"type": "string", "description": "邮件正文"},
                "cc": {"type": "array", "items": {"type": "string"}, "description": "抄送列表(可选)"},
                "attachments": {"type": "array", "items": {"type": "string"}, "description": "附件文件路径列表(可选)"},
            },
            "required": ["to", "subject", "body"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="send",
        name="发送邮件",
        description="发送邮件，支持附件",
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "array", "items": {"type": "string"}},
                "attachments": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["to", "subject", "body"],
        },
        llm_result_fields=["success"],
    )
    async def send(self, to: str, subject: str, body: str, cc: Optional[list] = None, attachments: Optional[list] = None, **_):
        """发送邮件"""
        try:
            plugin = self._get_plugin()
            result = plugin.send(to=to, subject=subject, body=body, cc=cc, attachments=attachments)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"发送邮件失败: {e}"))

    @llm_tool(
        name="neko_163mail_reply",
        description="回复邮件。可以回复发件人或回复所有人,支持附件。",
        parameters={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "要回复的邮件 UID"},
                "body": {"type": "string", "description": "回复正文"},
                "folder": {"type": "string", "description": "邮件所在文件夹,默认 INBOX"},
                "reply_all": {"type": "boolean", "description": "是否回复所有人,默认 false"},
                "attachments": {"type": "array", "items": {"type": "string"}, "description": "附件文件路径列表(可选)"},
            },
            "required": ["uid", "body"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="reply",
        name="回复邮件",
        description="回复邮件,支持回复所有人和附件",
        input_schema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "body": {"type": "string"},
                "folder": {"type": "string"},
                "reply_all": {"type": "boolean"},
                "attachments": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["uid", "body"],
        },
        llm_result_fields=["success", "uid"],
    )
    async def reply(self, uid: str, body: str, folder: str = "INBOX", reply_all: bool = False, attachments: Optional[list] = None, **_):
        """回复邮件"""
        try:
            plugin = self._get_plugin()
            result = plugin.reply(uid=uid, body=body, folder=folder, reply_all=reply_all, attachments=attachments)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"回复邮件失败: {e}"))

    @llm_tool(
        name="neko_163mail_forward",
        description="转发邮件给其他人。",
        parameters={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "要转发的邮件 UID"},
                "to": {"type": "string", "description": "转发收件人邮箱"},
                "body": {"type": "string", "description": "转发说明(可选)"},
                "folder": {"type": "string", "description": "邮件所在文件夹,默认 INBOX"},
                "include_attachments": {"type": "boolean", "description": "是否包含原邮件附件,默认 true"},
            },
            "required": ["uid", "to"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="forward",
        name="转发邮件",
        description="转发邮件给其他人",
        input_schema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "to": {"type": "string"},
                "body": {"type": "string"},
                "folder": {"type": "string"},
                "include_attachments": {"type": "boolean"},
            },
            "required": ["uid", "to"],
        },
        llm_result_fields=["success", "uid", "to"],
    )
    async def forward(self, uid: str, to: str, body: str = "", folder: str = "INBOX", include_attachments: bool = True, **_):
        """转发邮件"""
        try:
            plugin = self._get_plugin()
            result = plugin.forward(uid=uid, to=to, body=body, folder=folder, include_attachments=include_attachments)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"转发邮件失败: {e}"))

    @llm_tool(
        name="neko_163mail_download_attachment",
        description="下载邮件附件到本地目录。先用 get_email_detail 查看附件列表,再用附件索引下载。",
        parameters={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "邮件 UID"},
                "attachment_index": {"type": "integer", "description": "附件索引(从0开始)"},
                "output_dir": {"type": "string", "description": "保存目录路径"},
                "folder": {"type": "string", "description": "邮件所在文件夹,默认 INBOX"},
            },
            "required": ["uid", "attachment_index", "output_dir"],
        },
        timeout=60.0,
    )
    @plugin_entry(
        id="download_attachment",
        name="下载附件",
        description="下载邮件附件到本地目录",
        input_schema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "attachment_index": {"type": "integer"},
                "output_dir": {"type": "string"},
                "folder": {"type": "string"},
            },
            "required": ["uid", "attachment_index", "output_dir"],
        },
        llm_result_fields=["success", "filename", "path", "size"],
    )
    async def download_attachment(self, uid: str, attachment_index: int, output_dir: str, folder: str = "INBOX", **_):
        """下载附件"""
        try:
            plugin = self._get_plugin()
            result = plugin.download_attachment(uid=uid, attachment_index=attachment_index, output_dir=output_dir, folder=folder)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"下载附件失败: {e}"))

    @llm_tool(
        name="neko_163mail_list_folders",
        description="列出邮箱的所有文件夹及未读数。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="list_folders",
        name="列出文件夹",
        description="列出邮箱文件夹及未读数",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["folders"],
    )
    async def list_folders(self, **_):
        """列出文件夹"""
        try:
            plugin = self._get_plugin()
            result = plugin.list_folders()
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok({"folders": result})
        except Exception as e:
            return Err(SdkError(f"列出文件夹失败: {e}"))

    @llm_tool(
        name="neko_163mail_check_new",
        description="检查新邮件。传入上次检查时记录的 latest_uid，返回新增的邮件列表。猫娘可以用这个功能定时检查是否有新邮件到达。",
        parameters={
            "type": "object",
            "properties": {
                "last_uid": {"type": "string", "description": "上次检查时记录的最新邮件 UID，首次调用可不传"},
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
                "limit": {"type": "integer", "description": "最多返回的新邮件数量,默认 20"},
            },
            "required": [],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="check_new_emails",
        name="检查新邮件",
        description="检查自上次 UID 之后的新邮件",
        input_schema={
            "type": "object",
            "properties": {
                "last_uid": {"type": "string"},
                "folder": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
        llm_result_fields=["new_emails", "latest_uid", "has_new", "count", "high_priority_count", "high_priority_emails"],
    )
    async def check_new_emails(self, last_uid: Optional[str] = None, folder: str = "INBOX", limit: int = 20, **_):
        """检查新邮件"""
        try:
            plugin = self._get_plugin()
            result = plugin.check_new_emails(last_uid=last_uid, folder=folder, limit=limit)
            if isinstance(result, dict) and "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"检查新邮件失败: {e}"))

    @llm_tool(
        name="neko_163mail_batch_delete",
        description="批量删除邮件。可以传入多个邮件 UID 一次性删除。",
        parameters={
            "type": "object",
            "properties": {
                "uids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "邮件 UID 列表",
                },
                "folder": {"type": "string", "description": "文件夹名称,默认 INBOX"},
            },
            "required": ["uids"],
        },
        timeout=30.0,
    )
    @plugin_entry(
        id="batch_delete",
        name="批量删除邮件",
        description="批量删除多封邮件",
        input_schema={
            "type": "object",
            "properties": {
                "uids": {"type": "array", "items": {"type": "string"}},
                "folder": {"type": "string"},
            },
            "required": ["uids"],
        },
        llm_result_fields=["success", "failed", "failed_uids"],
    )
    async def batch_delete(self, uids: list[str], folder: str = "INBOX", **_):
        """批量删除邮件"""
        try:
            plugin = self._get_plugin()
            result = plugin.batch_delete(uids=uids, folder=folder)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"批量删除失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_daily_briefing",
        description="获取每日邮件简报数据,供早安播报插件调用。返回今日收件总数、自动处理数、重要邮件摘要、待处理事项等。",
        parameters={
            "type": "object",
            "properties": {
                "target_date": {"type": "string", "description": "目标日期,格式YYYY-MM-DD,默认今天"},
            },
            "required": [],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="get_daily_briefing",
        name="获取每日邮件简报",
        description="获取每日邮件简报数据,供其他插件联动调用",
        input_schema={
            "type": "object",
            "properties": {
                "target_date": {"type": "string"},
            },
            "required": [],
        },
        llm_result_fields=["date", "total_received", "auto_processed", "important_unread", "highlights", "pending_items"],
    )
    async def get_daily_briefing(self, target_date: Optional[str] = None, **_):
        """获取每日邮件简报"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_daily_briefing(target_date=target_date)
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"获取每日简报失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_operation_logs",
        description="获取今日操作日志。返回猫娘今天执行的所有邮箱操作记录,按时间倒序。",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "最多返回条数,默认100"},
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="get_operation_logs",
        name="获取操作日志",
        description="获取今日猫娘邮箱操作日志",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
            },
            "required": [],
        },
        llm_result_fields=["logs"],
    )
    async def get_operation_logs(self, limit: int = 100, **_):
        """获取操作日志"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_operation_logs(limit=limit)
            return Ok({"logs": result})
        except Exception as e:
            return Err(SdkError(f"获取操作日志失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_category_stats",
        description="获取邮件分类统计。返回今日各类邮件的数量和处理情况。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="get_category_stats",
        name="获取分类统计",
        description="获取邮件分类统计数据",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["stats"],
    )
    async def get_category_stats(self, **_):
        """获取分类统计"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_category_stats()
            return Ok({"stats": result})
        except Exception as e:
            return Err(SdkError(f"获取分类统计失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_pending_items",
        description="获取待处理事项列表。返回当前未处理的重要邮件,按优先级排序。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="get_pending_items",
        name="获取待处理事项",
        description="获取当前待处理的重要邮件列表",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["items"],
    )
    async def get_pending_items(self, **_):
        """获取待处理事项"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_pending_items()
            return Ok({"items": result})
        except Exception as e:
            return Err(SdkError(f"获取待处理事项失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_important_emails",
        description="获取重要邮件日志。返回被判定为高优先级并推送过的邮件记录。",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "最多返回条数,默认50"},
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="get_important_emails",
        name="获取重要邮件日志",
        description="获取被判定为重要并推送过的邮件记录",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
            },
            "required": [],
        },
        llm_result_fields=["emails"],
    )
    async def get_important_emails(self, limit: int = 50, **_):
        """获取重要邮件日志"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_important_emails(limit=limit)
            return Ok({"emails": result})
        except Exception as e:
            return Err(SdkError(f"获取重要邮件日志失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_overview",
        description="获取今日概览数据。返回今日操作总数、各类操作计数、重要邮件数、待处理事项数等。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="get_overview",
        name="获取今日概览",
        description="获取今日猫娘邮箱操作概览数据",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["date", "total_operations", "mark_read_count", "batch_mark_read_count", "auto_mark_read_count", "push_reminder_count", "send_email_count", "important_emails_count", "pending_items_count"],
    )
    async def get_overview(self, **_):
        """获取今日概览"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_overview()
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"获取今日概览失败: {e}"))

    @llm_tool(
        name="neko_163mail_start_polling",
        description="启动新邮件轮询监听。每隔指定时间(默认5分钟)自动检查收件箱新邮件,发现新邮件立即触发后续处理流程。",
        parameters={
            "type": "object",
            "properties": {
                "interval_seconds": {
                    "type": "integer",
                    "description": "轮询间隔(秒),默认300秒(5分钟)",
                },
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="start_polling",
        name="启动邮件轮询",
        description="启动新邮件自动轮询监听",
        input_schema={
            "type": "object",
            "properties": {
                "interval_seconds": {"type": "integer"},
            },
            "required": [],
        },
        llm_result_fields=["status", "interval", "last_uid"],
    )
    async def start_polling(self, interval_seconds: int = 300, **_):
        """启动邮件轮询"""
        try:
            plugin = self._get_plugin()
            result = plugin.start_polling(interval_seconds=interval_seconds, callback=self._push_new_email_notification)
            if "error" in result:
                return Err(SdkError(result["error"]))
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"启动轮询失败: {e}"))

    @llm_tool(
        name="neko_163mail_stop_polling",
        description="停止新邮件轮询监听。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="stop_polling",
        name="停止邮件轮询",
        description="停止新邮件自动轮询监听",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["status"],
    )
    async def stop_polling(self, **_):
        """停止邮件轮询"""
        try:
            plugin = self._get_plugin()
            result = plugin.stop_polling()
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"停止轮询失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_polling_status",
        description="获取新邮件轮询监听状态。返回是否正在运行、轮询间隔、上次检查的最新邮件UID等信息。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="get_polling_status",
        name="获取轮询状态",
        description="获取新邮件轮询监听状态",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["is_running", "interval", "last_known_uid"],
    )
    async def get_polling_status(self, **_):
        """获取轮询状态"""
        try:
            plugin = self._get_plugin()
            result = plugin.get_polling_status()
            return Ok(result)
        except Exception as e:
            return Err(SdkError(f"获取轮询状态失败: {e}"))

    @llm_tool(
        name="neko_163mail_get_names",
        description="获取猫娘对用户的称呼和猫娘自己的自称。返回当前配置的 master_name 和 catgirl_name。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        timeout=5.0,
    )
    @plugin_entry(
        id="get_names",
        name="获取称呼配置",
        description="获取猫娘对用户的称呼和猫娘自己的自称",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        llm_result_fields=["master_name", "catgirl_name"],
    )
    async def get_names(self, **_):
        """获取称呼配置"""
        try:
            plugin = self._get_plugin()
            return Ok({
                "master_name": plugin.master_name,
                "catgirl_name": plugin.catgirl_name,
            })
        except Exception as e:
            return Err(SdkError(f"获取称呼配置失败: {e}"))

    @llm_tool(
        name="neko_163mail_set_names",
        description="设置猫娘对用户的称呼和/或猫娘自己的自称。猫娘可以自主决定如何称呼用户,也可以和用户协商后共同决定。",
        parameters={
            "type": "object",
            "properties": {
                "master_name": {"type": "string", "description": "猫娘对用户的称呼,如'主人'、'哥哥'、'姐姐'等"},
                "catgirl_name": {"type": "string", "description": "猫娘的自称,如'喵喵'、'小猫'等"},
            },
            "required": [],
        },
        timeout=5.0,
    )
    @plugin_entry(
        id="set_names",
        name="设置称呼配置",
        description="设置猫娘对用户的称呼和/或猫娘自己的自称",
        input_schema={
            "type": "object",
            "properties": {
                "master_name": {"type": "string"},
                "catgirl_name": {"type": "string"},
            },
            "required": [],
        },
        llm_result_fields=["success", "master_name", "catgirl_name"],
    )
    async def set_names(self, master_name: Optional[str] = None, catgirl_name: Optional[str] = None, **_):
        """设置称呼配置"""
        try:
            plugin = self._get_plugin()

            # 更新内存中的称呼
            if master_name is not None:
                plugin.master_name = master_name.strip()
            if catgirl_name is not None:
                plugin.catgirl_name = catgirl_name.strip()

            # 持久化到配置文件
            try:
                cfg = await self.config.dump(timeout=5.0)
                cfg = cfg if isinstance(cfg, dict) else {}
                if "neko_163mail" not in cfg:
                    cfg["neko_163mail"] = {}

                if master_name is not None:
                    cfg["neko_163mail"]["master_name"] = plugin.master_name
                if catgirl_name is not None:
                    cfg["neko_163mail"]["catgirl_name"] = plugin.catgirl_name

                await self.config.update(cfg, timeout=5.0)
            except Exception as e:
                self.logger.warning(f"持久化称呼配置失败: {e},但内存中的称呼已更新")

            self.logger.info(f"称呼配置已更新: master_name={plugin.master_name}, catgirl_name={plugin.catgirl_name}")

            return Ok({
                "success": True,
                "master_name": plugin.master_name,
                "catgirl_name": plugin.catgirl_name,
            })
        except Exception as e:
            return Err(SdkError(f"设置称呼配置失败: {e}"))
