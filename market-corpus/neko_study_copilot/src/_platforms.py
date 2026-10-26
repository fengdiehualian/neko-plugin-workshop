"""学习平台适配器。

边界声明（很重要）：

- 每个适配器**只访问登录者本人账号下的数据**，不抓取、不猜测、不存储他人信息。
- 只做两种网络动作：GET 读取页面、POST 提交登录表单。
  **绝不向平台写入任何业务数据**（不替你交作业、不改资料、不发帖）。
- 不逆向加密参数、不识别验证码。遇到验证码就把图片地址回传，由本人在面板里输入。
- 页面结构化交给大模型做（见 ``_connectors.py``），适配器只负责"把页面文本拿到手"，
  这样平台改版时最多是抽取不到，不会让插件崩溃。

新增平台：往 :data:`PLATFORMS` 里加一条 :class:`PlatformSpec` 即可，无需改其它代码。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ._crawl import Crawler, html_to_text

_HIDDEN_INPUT_RE = re.compile(
    r"<input\b[^>]*type=[\"']?hidden[\"']?[^>]*>",
    re.I,
)
_ATTR_RE = re.compile(r"(name|value|id)=[\"']([^\"']*)[\"']", re.I)


def _extract_hidden_inputs(raw_html: str) -> dict[str, str]:
    """抽出登录页里的隐藏表单字段（CSRF token / lt / execution 等）。"""
    found: dict[str, str] = {}
    for tag in _HIDDEN_INPUT_RE.findall(raw_html or ""):
        attrs = {key.lower(): value for key, value in _ATTR_RE.findall(tag)}
        name = attrs.get("name") or attrs.get("id")
        if name:
            found[name] = attrs.get("value", "")
    return found


def _looks_like_captcha(raw_html: str) -> Optional[str]:
    """在登录页里找验证码图片地址。找不到返回 None。"""
    if not raw_html:
        return None
    patterns = (
        r"<img[^>]+src=[\"']([^\"']*(?:captcha|verify|validate|code)[^\"']*)[\"']",
        r"<img[^>]+id=[\"']([^\"']*(?:captcha|verify|validate)[^\"']*)[\"'][^>]+src=[\"']([^\"']+)[\"']",
        r"data-src=[\"']([^\"']*(?:captcha|verify|validate)[^\"']*)[\"']",
    )
    for pattern in patterns:
        match = re.search(pattern, raw_html, re.I)
        if match:
            groups = [item for item in match.groups() if item]
            if groups:
                return groups[-1]
    return None


@dataclass
class PlatformSpec:
    """一个平台的接入描述。"""

    id: str
    name: str
    kind: str  # mooc 在线课程 / jwc 教务系统 / exam 考试服务 / open 免登录公开
    home: str
    login_url: str = ""
    username_field: str = ""
    password_field: str = ""
    captcha_field: str = ""
    extra_fields: dict[str, str] = field(default_factory=dict)
    success_cookie: str = ""
    success_text: str = ""
    fail_texts: tuple[str, ...] = ()
    # 登录后要读取的相对路径（相对 home）
    read_paths: tuple[str, ...] = ()
    # 免登录公开平台可直接查询的检索地址模板（{q} 为关键词）
    search_template: str = ""
    notes: str = ""

    def absolute(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.home.rstrip('/')}/{path.lstrip('/')}"


PLATFORMS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        id="chaoxing",
        name="学习通（超星）",
        kind="mooc",
        home="https://passport.chaoxing.com",
        login_url="https://passport.chaoxing.com/login",
        username_field="uname",
        password_field="password",
        captcha_field="numcode",
        success_cookie="UID",
        fail_texts=("用户名或密码错误", "验证码错误", "请输入正确的"),
        read_paths=(
            "https://mooc1-1.chaoxing.com/visit/courses",
            "https://mooc1-1.chaoxing.com/visit/stucourselist",
        ),
        notes="多数学校的学习通需要图形验证码；看到验证码输入框时，面板会回传图片地址等你手填。",
    ),
    PlatformSpec(
        id="zhihuishu",
        name="知到（智慧树）",
        kind="mooc",
        home="https://passport.zhihuishu.com",
        login_url="https://passport.zhihuishu.com/login",
        username_field="loginName",
        password_field="password",
        success_cookie="CASLOGIN",
        fail_texts=("账号或密码错误", "验证码不正确"),
        read_paths=("https://onlineweb.zhihuishu.com/onlinestuh5/studentHomePage",),
        notes="共享课与校内课分别在不同域名下，读不到时请确认课程类型。",
    ),
    PlatformSpec(
        id="icourse163",
        name="中国大学 MOOC",
        kind="mooc",
        home="https://www.icourse163.org",
        login_url="https://www.icourse163.org/passport/login",
        username_field="email",
        password_field="password",
        success_cookie="NTESSTUDYSI",
        fail_texts=("手机号或密码错误", "验证码错误"),
        read_paths=("https://www.icourse163.org/member/mycourse.htm",),
        notes="登录态在网易通行证域下，若提示需要手机验证码请改用网页登录。",
    ),
    PlatformSpec(
        id="xuetangx",
        name="学堂在线",
        kind="mooc",
        home="https://www.xuetangx.com",
        login_url="https://www.xuetangx.com/api/v1/auth/login",
        username_field="username",
        password_field="password",
        success_text="success",
        read_paths=("https://www.xuetangx.com/mycourse",),
        notes="接口为 JSON，登录成功标志以响应体为准。",
    ),
    PlatformSpec(
        id="jwc",
        name="教务系统（通用 / 正方·青果）",
        kind="jwc",
        home="",
        login_url="",
        username_field="username",
        password_field="password",
        captcha_field="captcha",
        fail_texts=("用户名或密码错误", "验证码不正确", "密码错误"),
        read_paths=("",),
        notes=(
            "教务系统没有统一地址：请在面板里填你学校教务系统的首页 URL，"
            "插件会自动探测登录表单并提交。成绩与课表页面因校而异，取不到时走手动导入。"
        ),
    ),
    PlatformSpec(
        id="neea",
        name="中国教育考试网（四六级 / 考研）",
        kind="exam",
        home="https://www.neea.edu.cn",
        login_url="",
        read_paths=("https://www.neea.edu.cn/html1/report/index.htm",),
        notes="成绩查询与报名信息需要本人身份证号与姓名，插件只读取不代填，请在面板手动填写。",
    ),
)

PLATFORM_BY_ID: dict[str, PlatformSpec] = {spec.id: spec for spec in PLATFORMS}


class PlatformAdapter:
    """按 :class:`PlatformSpec` 执行登录与读取。"""

    def __init__(self, spec: PlatformSpec, crawler: Crawler) -> None:
        self.spec = spec
        self.crawler = crawler

    # ── 登录 ──────────────────────────────────────────────────
    def login(self, username: str, password: str, captcha: str = "") -> dict[str, Any]:
        spec = self.spec
        if not spec.login_url:
            return {"ok": False, "status": "not_supported", "message": f"{spec.name} 不需要或不提供自动登录"}
        if not username or not password:
            return {"ok": False, "status": "credential_missing", "message": "请先在面板填写账号与密码"}

        page = self.crawler.request(spec.login_url, "GET", headers={"Referer": spec.home})
        hidden = _extract_hidden_inputs(page.text) if page.ok else {}
        captcha_url = _looks_like_captcha(page.text) if page.ok else None
        if captcha_url and spec.captcha_field and not captcha:
            return {
                "ok": False,
                "status": "captcha_required",
                "message": "该平台要求图形验证码，请在面板里看图输入",
                "captcha_url": self.spec.absolute(captcha_url),
            }

        form: dict[str, Any] = dict(hidden)
        form[spec.username_field] = username
        form[spec.password_field] = password
        if spec.captcha_field and captcha:
            form[spec.captcha_field] = captcha
        form.update(spec.extra_fields)

        result = self.crawler.post_form(
            spec.login_url,
            form,
            headers={"Referer": spec.login_url, "Origin": spec.home or spec.login_url},
        )
        self.crawler.save_cookies()
        if self._judge_success(result.text, result.status):
            return {"ok": True, "status": "ok", "message": f"已登录 {spec.name}"}
        if captcha_url:
            return {
                "ok": False,
                "status": "captcha_required",
                "message": "登录未通过，可能是验证码过期，请重新输入",
                "captcha_url": self.spec.absolute(captcha_url),
            }
        return {
            "ok": False,
            "status": "failed",
            "message": "登录未通过，请检查账号密码；若平台启用了短信或扫码验证，请改用网页登录",
        }

    def _judge_success(self, text: str, status: int) -> bool:
        spec = self.spec
        for marker in spec.fail_texts:
            if marker and marker in (text or ""):
                return False
        if spec.success_text and spec.success_text in (text or ""):
            return True
        if spec.success_cookie:
            try:
                names = {cookie.name for cookie in self.crawler.cookiejar}
            except Exception:
                names = set()
            if spec.success_cookie in names:
                return True
        return status in (200, 302) and bool(spec.success_text) and spec.success_text in (text or "")

    # ── 读取 ──────────────────────────────────────────────────
    def read_pages(self, limit: int = 5000) -> list[dict[str, Any]]:
        """读取登录后可访问的页面，返回「原始文本」列表（结构化交给大模型）。"""
        pages: list[dict[str, Any]] = []
        max_pages = max(1, self.crawler.max_pages)
        for path in self.spec.read_paths:
            if len(pages) >= max_pages:
                break
            if not path:
                continue
            url = self.spec.absolute(path)
            result = self.crawler.request(url, "GET", headers={"Referer": self.spec.home or url})
            if not result.ok:
                pages.append({"url": url, "ok": False, "error": result.error or f"HTTP {result.status}", "text": ""})
                continue
            pages.append(
                {
                    "url": url,
                    "ok": True,
                    "error": "",
                    "text": html_to_text(result.text, limit=limit),
                }
            )
        return pages

    def is_public(self) -> bool:
        return not self.spec.login_url


def list_platforms() -> list[dict[str, Any]]:
    return [
        {
            "id": spec.id,
            "name": spec.name,
            "kind": spec.kind,
            "home": spec.home,
            "needs_login": bool(spec.login_url),
            "needs_captcha": bool(spec.captcha_field),
            "notes": spec.notes,
        }
        for spec in PLATFORMS
    ]
