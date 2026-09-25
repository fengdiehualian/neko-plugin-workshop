"""
猫娘邮件插件 - 数据模型
定义邮件、附件、摘要等 Pydantic 模型
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """邮件附件元数据"""
    filename: str = Field(..., description="附件文件名")
    size: int = Field(..., description="附件大小(字节)")
    content_type: str = Field(..., description="MIME类型")
    
    def size_human(self) -> str:
        """人类可读的文件大小"""
        size = self.size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f}{unit}" if unit != 'B' else f"{size}{unit}"
            size /= 1024
        return f"{size:.1f}TB"


class EmailMessage(BaseModel):
    """完整邮件消息"""
    uid: str = Field(..., description="IMAP唯一ID")
    subject: str = Field(..., description="邮件主题")
    sender: str = Field(..., description="发件人")
    recipients: list[str] = Field(default_factory=list, description="收件人列表")
    cc: list[str] = Field(default_factory=list, description="抄送列表")
    date: datetime = Field(..., description="发送时间")
    body_text: str = Field(..., description="纯文本正文(HTML已转换)")
    body_html: Optional[str] = Field(None, description="原始HTML(可选)")
    attachments: list[Attachment] = Field(default_factory=list, description="附件元数据")
    flags: list[str] = Field(default_factory=list, description="邮件状态标签")
    priority: str = Field(default="medium", description="优先级: high/medium/low")
    folder: str = Field(default="INBOX", description="所在文件夹")
    
    def preview(self, max_len: int = 100) -> str:
        """生成正文预览"""
        text = self.body_text.replace('\n', ' ').strip()
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."
    
    def time_str(self, fmt: str = "%Y-%m-%d %H:%M") -> str:
        """格式化时间"""
        return self.date.strftime(fmt)
    
    def has_attachments(self) -> bool:
        """是否有附件"""
        return len(self.attachments) > 0
    
    def attachments_summary(self) -> str:
        """附件摘要"""
        if not self.attachments:
            return ""
        parts = [f"{a.filename} ({a.size_human()})" for a in self.attachments]
        return "📎 附件: " + ", ".join(parts)


class EmailSnippet(BaseModel):
    """邮件摘要片段"""
    uid: str = Field(..., description="邮件UID")
    subject: str = Field(..., description="主题")
    sender: str = Field(..., description="发件人")
    preview: str = Field(..., description="正文前100字")
    time: str = Field(..., description="格式化时间")
    priority: str = Field(default="medium", description="优先级")
    folder: str = Field(default="INBOX", description="文件夹")


class EmailSummary(BaseModel):
    """邮件摘要统计"""
    total_unread: int = Field(default=0, description="未读邮件总数")
    total_today: int = Field(default=0, description="今日邮件总数")
    high_priority: list[EmailSnippet] = Field(default_factory=list, description="高优先级邮件")
    medium_priority: list[EmailSnippet] = Field(default_factory=list, description="中优先级邮件")
    low_priority: list[EmailSnippet] = Field(default_factory=list, description="低优先级邮件")
    
    def to_catgirl_text(self, catgirl_name: str = "喵喵", master_name: str = "主人") -> str:
        """生成猫娘友好的摘要文本"""
        parts = []
        
        if self.total_unread == 0 and self.total_today == 0:
            return f"{master_name}今天没有新邮件喵~可以安心做事啦!"
        
        parts.append(f"{master_name}有 {self.total_unread} 封未读邮件喵~")
        
        if self.total_today > 0:
            parts.append(f"今天一共收到 {self.total_today} 封邮件。")
        
        if self.high_priority:
            parts.append(f"其中 {len(self.high_priority)} 封是高优先级:")
            for snippet in self.high_priority[:3]:  # 最多显示3封
                parts.append(f"  • 来自 {snippet.sender}: {snippet.subject}")
            if len(self.high_priority) > 3:
                parts.append(f"  ...还有 {len(self.high_priority) - 3} 封高优先级邮件")
        
        if self.medium_priority:
            parts.append(f"\n中等优先级有 {len(self.medium_priority)} 封")
        
        if self.low_priority:
            parts.append(f"低优先级有 {len(self.low_priority)} 封(可能是广告或通知)")
        
        return "\n".join(parts)


class FolderInfo(BaseModel):
    """文件夹信息"""
    name: str = Field(..., description="文件夹名称")
    unread_count: int = Field(default=0, description="未读邮件数")
    total_count: int = Field(default=0, description="总邮件数")
