"""
猫娘邮件插件 - 邮件解析器
负责 HTML 转文本、附件提取、优先级判断
"""

import re
from email.header import decode_header
from email.utils import parseaddr
from typing import Optional

from bs4 import BeautifulSoup

from .models import Attachment, EmailMessage


def decode_header_value(value: str) -> str:
    """解码邮件头(支持 RFC2047 编码)"""
    if not value:
        return ""
    
    decoded_parts = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            try:
                charset = charset or 'utf-8'
                decoded_parts.append(part.decode(charset, errors='replace'))
            except (LookupError, UnicodeDecodeError):
                decoded_parts.append(part.decode('utf-8', errors='replace'))
        else:
            decoded_parts.append(part)
    
    return ''.join(decoded_parts)


def extract_email_address(header_value: str) -> str:
    """从邮件头提取邮箱地址"""
    if not header_value:
        return ""
    name, email = parseaddr(header_value)
    return email or header_value


def html_to_text(html: str, max_length: int = 8000) -> str:
    """
    HTML 转纯文本
    - 保留段落(用 \n\n 分隔)
    - <a href="url">文本</a> → "文本(url)"
    - 移除脚本、样式、注释
    - 超过 max_length 字符截断
    """
    if not html:
        return ""
    
    # 解析 HTML
    soup = BeautifulSoup(html, 'html.parser')
    
    # 移除不需要的标签
    for tag in soup(['script', 'style', 'head', 'meta', 'link']):
        tag.decompose()
    
    # 处理链接: <a href="url">文本</a> → "文本(url)"
    for a_tag in soup.find_all('a'):
        href = a_tag.get('href', '')
        text = a_tag.get_text(strip=True)
        if href and text:
            a_tag.replace_with(f"{text}({href})")
        elif text:
            a_tag.replace_with(text)
        else:
            a_tag.decompose()
    
    # 处理段落和换行
    for tag in soup.find_all(['p', 'br', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']):
        tag.insert_before('\n')
        tag.insert_after('\n')
    
    # 提取文本
    text = soup.get_text()
    
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    
    # 截断
    if len(text) > max_length:
        text = text[:max_length] + "\n\n...(已截断)"
    
    return text.strip()


def classify_priority(
    email: EmailMessage,
    high_priority_senders: Optional[list[str]] = None
) -> str:
    """
    判断邮件优先级
    
    HIGH 如果满足任一:
    - 发件人域名含 edu.cn / 学校 / 教务处 / 导师 / hr / boss
    - 主题含: 紧急、重要、截止、deadline、面试、offer、挂科、补考、成绩
    - 发件人在用户配置的高优先级白名单里
    
    LOW 如果满足任一:
    - 发件人含 noreply / no-reply / notification / newsletter / marketing / ads
    - 主题含: 推广、订阅、unsubscribe、广告、优惠、促销、账单已出
    
    其余为 MEDIUM
    """
    sender_lower = email.sender.lower()
    subject_lower = email.subject.lower()
    
    # 高优先级关键词
    high_keywords = [
        '紧急', '重要', '截止', 'deadline', '面试', 'offer', 
        '挂科', '补考', '成绩', 'urgent', 'important'
    ]
    
    # 高优先级域名/发件人
    high_domains = ['edu.cn', '学校', '教务处', '导师', 'hr', 'boss']
    
    # 低优先级关键词
    low_keywords = [
        '推广', '订阅', 'unsubscribe', '广告', '优惠', '促销', 
        '账单已出', 'newsletter', 'marketing'
    ]
    
    # 低优先级发件人
    low_senders = ['noreply', 'no-reply', 'notification', 'newsletter', 'marketing', 'ads']
    
    # 检查高优先级
    # 1. 主题关键词
    if any(kw in subject_lower for kw in high_keywords):
        return 'high'
    
    # 2. 发件人域名
    if any(domain in sender_lower for domain in high_domains):
        return 'high'
    
    # 3. 用户配置的高优先级发件人
    if high_priority_senders:
        for sender_pattern in high_priority_senders:
            if sender_pattern.lower() in sender_lower:
                return 'high'
    
    # 检查低优先级
    # 1. 发件人包含低优先级标识
    if any(sender in sender_lower for sender in low_senders):
        return 'low'
    
    # 2. 主题包含低优先级关键词
    if any(kw in subject_lower for kw in low_keywords):
        return 'low'
    
    # 默认为中等优先级
    return 'medium'


def parse_attachment(part) -> Optional[Attachment]:
    """解析邮件附件部分"""
    filename = part.get_filename()
    if not filename:
        return None
    
    # 解码文件名
    filename = decode_header_value(filename)
    
    # 获取大小(估算)
    payload = part.get_payload(decode=True)
    size = len(payload) if payload else 0
    
    # 获取 MIME 类型
    content_type = part.get_content_type()
    
    return Attachment(
        filename=filename,
        size=size,
        content_type=content_type
    )


def classify_email_type(email: EmailMessage, master_name: str = "主人", catgirl_name: str = "喵喵") -> dict:
    """
    智能邮件分类 + 关键信息提取
    
    返回:
        {
            "category": "verification" | "assignment" | "subscription" | 
                        "security" | "project" | "finance" | "social" | "general",
            "category_label": "验证码" | "作业提交" | "订阅广告" | "安全通知" | 
                            "项目相关" | "财务通知" | "社交通知" | "普通邮件",
            "key_info": { ... },  # 提取的关键信息
            "catgirl_hint": "猫娘提示语"
        }
    """
    subject = email.subject.lower()
    body = email.body_text.lower()
    combined = subject + " " + body
    
    # ── 1. 验证码类 ──
    verification_patterns = [
        r'验证码[是为：:\s]*(\d{4,8})',
        r'verification\s*code[^\d]*(\d{4,8})',
        r'code[^\d]*(\d{4,8})',
        r'校验码[^\d]*(\d{4,8})',
        r'动态密码[^\d]*(\d{4,8})',
        r'一次性密码[^\d]*(\d{4,8})',
        r'otp[^\d]*(\d{4,8})',
        r'confirm.*code[^\d]*(\d{4,8})',
    ]
    
    verification_keywords = ['验证码', 'verification code', 'verify your', 'one-time code', 
                            '动态密码', '校验码', 'security code', 'otp']
    
    if any(kw in combined for kw in verification_keywords):
        code = None
        for pattern in verification_patterns:
            match = re.search(pattern, email.body_text, re.IGNORECASE)
            if match:
                code = match.group(1)
                break
        
        # 从主题中也提取
        if not code:
            for pattern in verification_patterns:
                match = re.search(pattern, email.subject, re.IGNORECASE)
                if match:
                    code = match.group(1)
                    break
        
        service = _extract_service_name(email)
        
        return {
            "category": "verification",
            "category_label": "验证码",
            "key_info": {
                "code": code,
                "service": service,
            },
            "catgirl_hint": f"{master_name}~{service}的验证码是 {code} 喵~ 快去认证吧!" if code else f"{master_name}~{service}发了验证码邮件喵~"
        }
    
    # ── 2. 作业/课程类 ──
    assignment_keywords = ['作业', '课程', 'assignment', 'homework', 'deadline', 
                          '提交', '截止', '考试', 'exam', 'quiz', 'lecture',
                          '课件', '实验报告', '论文', 'thesis', '答辩']
    
    if any(kw in combined for kw in assignment_keywords):
        course = _extract_course_name(email)
        deadline = _extract_deadline(email)
        task = _extract_assignment_task(email)
        
        hint_parts = []
        if course:
            hint_parts.append(f"课程: {course}")
        if deadline:
            hint_parts.append(f"截止: {deadline}")
        if task:
            hint_parts.append(f"任务: {task}")
        
        hint = f"{master_name}~有作业提醒喵~ " + "，".join(hint_parts) if hint_parts else f"{master_name}~有课程相关邮件喵~"
        
        return {
            "category": "assignment",
            "category_label": "作业提交",
            "key_info": {
                "course": course,
                "deadline": deadline,
                "task": task,
            },
            "catgirl_hint": hint
        }
    
    # ── 3. 安全通知类 ──
    security_keywords = ['安全警告', '异常登录', 'suspicious', 'security alert',
                        'unusual activity', '登录提醒', '风险通知', 'password changed',
                        '密码修改', 'account locked', '账户锁定', 'brute force']
    
    if any(kw in combined for kw in security_keywords):
        risk_level = "high" if any(kw in combined for kw in ['异常登录', 'suspicious', '风险', 'brute force']) else "medium"
        action = _extract_security_action(email)
        
        return {
            "category": "security",
            "category_label": "安全通知",
            "key_info": {
                "risk_level": risk_level,
                "action": action,
            },
            "catgirl_hint": f"{master_name}!!有安全通知喵! {'风险等级较高，请尽快处理!' if risk_level == 'high' else '请注意查看一下喵~'}"
        }
    
    # ── 4. 订阅/广告类 ──
    subscription_keywords = ['unsubscribe', '退订', '订阅', 'newsletter', 
                            '推广', '优惠', '促销', 'marketing', 'advertisement',
                            '广告', '限时', '折扣', 'coupon']
    
    if any(kw in combined for kw in subscription_keywords):
        return {
            "category": "subscription",
            "category_label": "订阅广告",
            "key_info": {},
            "catgirl_hint": f"{master_name}~这是广告/订阅邮件喵~可以不用管它哦~"
        }
    
    # ── 5. 项目/GitHub类 ──
    project_keywords = ['github', 'pull request', 'merge request', 'issue',
                       'commit', 'repository', 'ci/cd', 'workflow', 'deploy',
                       'token', 'api key', '过期', 'expir']
    
    if any(kw in combined for kw in project_keywords):
        project_name = _extract_project_name(email)
        action_type = _extract_project_action(email)
        
        hint = f"{master_name}~GitHub有动态喵~"
        if project_name:
            hint += f" 项目: {project_name}"
        if action_type:
            hint += f" ({action_type})"
        if 'token' in combined or 'api key' in combined or '过期' in combined:
            hint = f"{master_name}!Token/API Key快要过期了喵! 记得尽快处理!"
        
        return {
            "category": "project",
            "category_label": "项目相关",
            "key_info": {
                "project": project_name,
                "action": action_type,
            },
            "catgirl_hint": hint
        }
    
    # ── 6. 财务/账单类 ──
    finance_keywords = ['账单', '付款', '支付', 'invoice', 'payment', 'receipt',
                       '消费', '充值', '扣款', '余额', 'balance', 'billing']
    
    if any(kw in combined for kw in finance_keywords):
        return {
            "category": "finance",
            "category_label": "财务通知",
            "key_info": {},
            "catgirl_hint": f"{master_name}~有财务/账单相关邮件喵~记得看一下哦~"
        }
    
    # ── 7. 社交通知类 ──
    social_keywords = ['关注了你', 'followed', 'mentioned', '提到了你',
                      'liked', '点赞', '评论', 'comment', '私信', 'message',
                      '好友申请', 'friend request']
    
    if any(kw in combined for kw in social_keywords):
        return {
            "category": "social",
            "category_label": "社交通知",
            "key_info": {},
            "catgirl_hint": f"{master_name}~有人找你喵~快去看看社交通知吧~"
        }
    
    # ── 默认 ──
    return {
        "category": "general",
        "category_label": "普通邮件",
        "key_info": {},
        "catgirl_hint": ""
    }


def _extract_service_name(email: EmailMessage) -> str:
    """从邮件中提取服务/平台名称"""
    sender = email.sender.lower()
    
    # 从发件人域名提取
    if '@' in sender:
        domain = sender.split('@')[-1]
        service_map = {
            'github.com': 'GitHub',
            'google.com': 'Google',
            'microsoft.com': 'Microsoft',
            'qq.com': 'QQ',
            'wechat.com': '微信',
            'alipay.com': '支付宝',
            'openai.com': 'OpenAI',
            'twitter.com': 'Twitter',
            'bilibili.com': 'B站',
            'zhihu.com': '知乎',
            'edu.cn': '学校',
        }
        for d, name in service_map.items():
            if d in domain:
                return name
        # 用域名前缀作为服务名
        return domain.split('.')[0].capitalize()
    
    return "未知服务"


def _extract_course_name(email: EmailMessage) -> str:
    """提取课程名称"""
    subject = email.subject
    # 尝试从主题中提取
    patterns = [
        r'《([^》]+)》',
        r'课程[：:\s]*([^\n,，]+)',
        r'([^\n]*课程[^\n]*)',
    ]
    for p in patterns:
        match = re.search(p, subject)
        if match:
            return match.group(1).strip()
    return ""


def _extract_deadline(email: EmailMessage) -> str:
    """提取截止时间"""
    combined = email.subject + " " + email.body_text
    patterns = [
        r'(?:截止|deadline|due|到期)[^\d]*(\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s*\d{1,2}:\d{2})?)',
        r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s*\d{1,2}:\d{2})?)\s*(?:前|之前|之前)',
        r'(?:截止|deadline|due)[^\d]*(\d{1,2}月\d{1,2}日(?:\s*\d{1,2}:\d{2})?)',
    ]
    for p in patterns:
        match = re.search(p, combined, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def _extract_assignment_task(email: EmailMessage) -> str:
    """提取作业任务描述"""
    subject = email.subject
    patterns = [
        r'(?:作业|assignment|homework)\s*[：:\s]*(.+?)(?:\n|$)',
        r'(?:实验报告|论文|thesis)\s*[：:\s]*(.+?)(?:\n|$)',
    ]
    for p in patterns:
        match = re.search(p, subject, re.IGNORECASE)
        if match:
            return match.group(1).strip()[:50]
    return ""


def _extract_security_action(email: EmailMessage) -> str:
    """提取安全建议操作"""
    combined = email.subject + " " + email.body_text
    if '修改密码' in combined or 'change password' in combined.lower():
        return '建议立即修改密码'
    if '锁定' in combined or 'locked' in combined.lower():
        return '账户已被锁定，请验证身份'
    if '异常登录' in combined or 'unusual' in combined.lower():
        return '检测到异常登录，请确认是否为本人操作'
    return '请查看详情确认'


def _extract_project_name(email: EmailMessage) -> str:
    """提取项目名称"""
    subject = email.subject
    patterns = [
        r'\[([^\]]+)\]',  # [project-name]
        r'([\w-]+/[\w-]+)',  # owner/repo
    ]
    for p in patterns:
        match = re.search(p, subject)
        if match:
            return match.group(1).strip()
    return ""


def _extract_project_action(email: EmailMessage) -> str:
    """提取项目操作类型"""
    combined = (email.subject + " " + email.body_text).lower()
    if 'pull request' in combined or 'merged' in combined:
        return 'PR合并'
    if 'issue' in combined:
        return 'Issue更新'
    if 'token' in combined or 'api key' in combined:
        return 'Token/Key相关'
    if 'deploy' in combined:
        return '部署通知'
    if 'commit' in combined:
        return '新提交'
    return ""
