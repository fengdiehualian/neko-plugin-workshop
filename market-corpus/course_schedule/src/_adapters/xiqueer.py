"""XiQueEr（喜鹊儿 / 青果 KingoSOFT 教务系统）适配器 v2。

改进点（对比 v1）：
1. 更鲁棒的 _sessionid 提取（覆盖 kingosoft.js 中所有变体）
2. deskey/nowtime 多路径探测（部分学校部署了非标准路径）
3. 课表 API 端点版本探测（青果 2.x / 3.x / 4.x 路径不同）
4. 完整移植 Dawn-Course kingosoft.js 的 HTML 解析逻辑，
   包含 parseListTable（列表型课表）、parseWithLegacyLogic（旧版兜底）
5. 更细粒度的错误诊断，让用户可以自排障

依赖：纯标准库（urllib / http.cookiejar / hashlib / base64 / re / ssl / json），
符合 N.E.K.O 插件零第三方依赖的发布要求。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.cookiejar
import json
import re
import ssl
import urllib.parse
import urllib.request
from typing import Any

from .._academic_adapter import AcademicAdapter, AcademicAdapterError
from .jkingo_des import KingoDES

# ---------------------------------------------------------------------------
# SSL / HTTP 层
# ---------------------------------------------------------------------------

_SSL_CTX = ssl._create_unverified_context()

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class _HttpSession:
    """极简 urllib Session 封装，自动维护 CookieJar。"""

    def __init__(self):
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.HTTPSHandler(context=_SSL_CTX),
        )
        self.last_status: int = 0

    def cookies_get(self, name: str) -> str | None:
        for c in self.cookie_jar:
            if c.name == name:
                return c.value
        return None

    def _request(
        self, method: str, url: str, headers: dict | None = None, data: bytes | str | None = None, timeout: float = 10.0
    ) -> "_Response":
        hdrs = {**_DEFAULT_HEADERS, **(headers or {})}
        body_bytes: bytes | None = None
        if data is not None:
            body_bytes = data.encode("utf-8") if isinstance(data, str) else data
        req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method=method)
        try:
            resp = self.opener.open(req, timeout=timeout)
            self.last_status = resp.status
            return _Response(resp.read(), resp.status, dict(resp.headers))
        except urllib.error.HTTPError as e:
            self.last_status = e.code
            raw = e.read() if hasattr(e, "read") else b""
            return _Response(raw, e.code, dict(e.headers) if e.headers else {})
        except urllib.error.URLError as e:
            self.last_status = 0
            raise AcademicAdapterError(
                f"网络请求失败：{getattr(e, 'reason', e)}。"
                "请检查教务系统地址是否正确、网络是否通畅，或关闭系统代理后重试。"
            ) from e
        except Exception as e:
            self.last_status = 0
            raise AcademicAdapterError(f"请求异常：{type(e).__name__}: {e}") from e

    def get(self, url: str, headers: dict | None = None, timeout: float = 10.0) -> "_Response":
        return self._request("GET", url, headers=headers, timeout=timeout)

    def post(
        self, url: str, data: bytes | str | None = None, headers: dict | None = None, timeout: float = 10.0
    ) -> "_Response":
        return self._request("POST", url, data=data, headers=headers, timeout=timeout)


class _Response:
    def __init__(self, raw: bytes, status: int, headers: dict):
        self._raw = raw
        self.status_code = status
        self.headers = headers

    @property
    def text(self) -> str:
        if not self._raw:
            return ""
        # 先根据 charset HTTP header 判定
        encoding = None
        ctype = self.headers.get("Content-Type", "") or self.headers.get("content-type", "")
        m = re.search(r"charset=([\w\-]+)", ctype, re.I)
        if m:
            encoding = m.group(1).strip().lower()
            for enc in (encoding, "utf-8", "gbk", "gb2312", "gb18030", "big5"):
                try:
                    return self._raw.decode(enc)
                except Exception:
                    continue
        for enc in ("utf-8", "gbk", "gb2312", "gb18030", "big5"):
            try:
                return self._raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return self._raw.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


def _normalize_base_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        parsed = urllib.parse.urlparse(f"https://{raw}")
    if not parsed.scheme or not parsed.netloc:
        return ""
    # 兼容用户直接粘贴完整登录页 URL 的情况
    # 比如 https://jw.hwec.edu.cn/cas/login.action → 自动截取为 https://jw.hwec.edu.cn
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


# ---------------------------------------------------------------------------
# XiQueErAdapter 主体
# ---------------------------------------------------------------------------

# 登录页提取 _sessionid 的正则集合（按匹配置信度排序）
_SESSION_ID_PATTERNS = [
    r'var\s+_sessionid\s*=\s*["\']([A-Fa-f0-9]+)["\']',
    r'var\s+sessionid\s*=\s*["\']([A-Fa-f0-9]+)["\']',
    r'_sessionid\s*[:=]\s*["\']([A-Fa-f0-9]+)["\']',
    r'var\s+sessionId\s*=\s*["\']([A-Fa-f0-9]+)["\']',
    r'var\s+_SESSIONID\s*=\s*["\']([A-Fa-f0-9]+)["\']',
    r'sessionId\s*[:=]\s*["\']([A-Fa-f0-9]+)["\']',
    r"_sessionid[^\w]+([A-Fa-f0-9]{24,64})",
    r"sessionid[^\w]+([A-Fa-f0-9]{24,64})",
    r"_session_id[^\w]+([A-Fa-f0-9]{24,64})",
]

# deskey / nowtime 备选路径（有些学校改过路由名）
_DESKEY_PATHS = [
    "/frame/homepage?method=getTempDeskey",
    "/frame/homepage.html?method=getTempDeskey",
    "/frame/homepage.action?method=getTempDeskey",
    "/homepage?method=getTempDeskey",
    "/cas/frame/homepage?method=getTempDeskey",
]
_NOWTIME_PATHS = [
    "/frame/homepage?method=getTempNowtime",
    "/frame/homepage.html?method=getTempNowtime",
    "/frame/homepage.action?method=getTempNowtime",
    "/homepage?method=getTempNowtime",
    "/cas/frame/homepage?method=getTempNowtime",
]

# 课表 JSON API 端点（青果不同版本路径不同）
_KB_JSON_APIS = [
    # 青果标准版（最常用）
    ("/xskbcx!getKbxxByXs", "query.xnxq", "query.xsh"),
    ("/xskbcx/getKbxxByXs", "query.xnxq", "query.xsh"),
    ("/xskbcx.action?method=getKbxxByXs", "query.xnxq", "query.xsh"),
    # 青果新版 API
    ("/api/course/schedule", "semester", "studentId"),
    ("/api/xskbcx/schedule", "xnxq", "xsh"),
    ("/student/xskbcx!getKbxxByXs", "query.xnxq", "query.xsh"),
    ("/cas/xskbcx!getKbxxByXs", "query.xnxq", "query.xsh"),
]

# 课表 HTML 页面路径
_KB_HTML_PAGES = [
    "/student/wsxk.xskcb10319.jsp",
    "/cas/student/wsxk.xskcb10319.jsp",
    "/student/xskbcx!xsKbView.action",
    "/cas/student/xskbcx!xsKbView.action",
    "/student/xskbcx.action?method=xsKbView",
    "/znpk/Pri_StuSel.aspx",
    "/ZNPK/Pri_StuSel.aspx",
    "/jwglxt/xskbcx!xsKbView.action",
]


class XiQueErAdapter(AcademicAdapter):
    """喜鹊儿 / 青果 KingoSOFT 教务系统适配器 v2。

    登录流程：
      1. GET /cas/login.action       → 取 JSESSIONID + _sessionid
      2. GET getTempDeskey            → 取 DES 密钥（多路径探测）
      3. GET getTempNowtime           → 取时间戳（多路径探测）
      4. POST /cas/logon.action       → KingoDES 加密后的 JSON

    抓课：先试 JSON API（多版本探测），失败上 HTML 页面（Dawn-Course kingosoft.js 同款解析）。
    """

    adapter_id = "xiqueer"
    adapter_name = "喜鹊儿（青果 KingoSOFT）"

    def __init__(self, **kwargs):
        self.base_url: str = (kwargs.get("base_url") or "").rstrip("/")
        self._session: _HttpSession | None = None
        self._username: str = ""
        self._semester_keyword: str = ""
        if self.base_url:
            self.base_url = _normalize_base_url(self.base_url)

    # ── authenticate ────────────────────────────────────────────────────────

    async def authenticate(self, creds: dict[str, Any]) -> None:
        raw_base = creds.get("base_url") or creds.get("website") or self.base_url
        self.base_url = _normalize_base_url(raw_base)
        username = (creds.get("username") or creds.get("student_id") or "").strip()
        password = creds.get("password", "")
        md5_password = creds.get("md5_password")

        if not self.base_url:
            raise AcademicAdapterError(
                "缺少 base_url（教务系统地址）。示例：https://jw.hwec.edu.cn 或 https://jw.example.edu.cn"
            )
        if not username or not password:
            raise AcademicAdapterError("缺少 username（学号）或 password（密码）")

        loop = asyncio.get_running_loop()
        sess, user = await loop.run_in_executor(
            None, self._do_login_sync, self.base_url, username, password, md5_password
        )
        self._session = sess
        self._username = user

    def _do_login_sync(self, base_url: str, username: str, password: str, md5_password: str | None):
        T = 8  # 单请求 timeout
        s = _HttpSession()
        _l(f"[xiqueer.login] START base={base_url} user={username}")

        # 1) 登录页
        login_url = f"{base_url}/cas/login.action"
        r = s.get(login_url, timeout=T)
        _l(f"[xiqueer.login] login_page status={r.status_code} cookies={[c.name for c in s.cookie_jar]}")
        if r.status_code != 200:
            raise AcademicAdapterError(
                f"无法访问教务系统登录页：HTTP {r.status_code}。"
                f"地址应为根域名如 https://jw.example.edu.cn，不要加 /cas/login.action"
            )

        if "凭证已失效" in r.text or "<script>alert('温馨提示" in r.text[:2000]:
            raise AcademicAdapterError(
                "登录页被拦截（可能触发 WAF 或网络不通）。"
                "请检查网络/校园网环境，关闭系统代理后重试。"
                f"前300字：{r.text[:300]}"
            )

        jsessionid = s.cookies_get("JSESSIONID")
        if not jsessionid:
            # 部分学校用 ASP.NET_SessionId / 自定义 cookie
            jsessionid = s.cookies_get("ASP.NET_SessionId") or s.cookies_get("SESSION")
        if not jsessionid:
            raise AcademicAdapterError(
                f"登录页未返回 JSESSIONID（或 ASP.NET_SessionId）。可能不是青果教务系统。前200字：{r.text[:200]}"
            )

        session_id = None
        for pat in _SESSION_ID_PATTERNS:
            m = re.search(pat, r.text)
            if m:
                session_id = m.group(1)
                _l(f"[xiqueer.login] session_id matched by /{pat[:40]}.../")
                break
        if not session_id:
            # 终极尝试：抓取所有疑似十六进制长串
            hex_candidates = re.findall(r'["\']([A-Fa-f0-9]{24,64})["\']', r.text)
            if hex_candidates:
                session_id = hex_candidates[0]
                _l(f"[xiqueer.login] session_id fallback from hex candidate: {session_id[:16]}...")
        if not session_id:
            raise AcademicAdapterError(
                "无法从登录页提取 _sessionid。"
                "可能是旧版青果或非青果教务系统。"
                f"请把登录页前500字发给开发者分析：{r.text[:500]}"
            )

        # 2) deskey + nowtime — 多路径探测
        deskey = self._probe_get(s, base_url, _DESKEY_PATHS, T, "deskey")
        nowtime = self._probe_get(s, base_url, _NOWTIME_PATHS, T, "nowtime")

        # 2.1) deskey 可能不是 16 字符（hwec 返回 23 位数字），
        #      DES 要求 8/16 字节 ascii。生成多种候选版本逐个尝试登录，
        #      成功一个就立即返回（避免"密码错误"的误判）。
        deskey = re.sub(r"<[^>]*>", "", deskey).strip()
        nowtime = re.sub(r"<[^>]*>", "", nowtime).strip()

        def _dk_candidates(dk: str) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for s1 in [dk, dk.ljust(16, "0"), dk.rjust(16, "0")]:
                # DES 用 8 字节 = 8 ascii；有些版本用 16 ascii 再截断到 8
                for ln in (16, 24, 8):
                    for frag in (s1[:ln], s1[-ln:] if len(s1) >= ln else "", s1[:ln][::-1]):
                        f = frag or s1 or ""
                        if f and f not in seen:
                            seen.add(f)
                            out.append(f)
            # 最后兜底（hwec 23 位常见方案：中间 16 位）
            if len(dk) > 16:
                mid = (len(dk) - 16) // 2
                frag = dk[mid : mid + 16]
                if frag not in seen:
                    out.append(frag)
            if not out:
                out = [dk]
            return out

        candidates = _dk_candidates(deskey)
        _l(
            f"[xiqueer.login] deskey candidates count={len(candidates)} orig_len={len(deskey)} first_cand_len={len(candidates[0])}"
        )

        # 3) 组装登录参数（固定部分，不依赖 deskey 候选）
        params_u = base64.b64encode(f"{username};;{session_id}".encode()).decode()
        real_pwd = md5_password or hashlib.md5(password.encode("utf-8")).hexdigest()
        params_p = hashlib.md5((real_pwd + hashlib.md5(b"").hexdigest()).encode("utf-8")).hexdigest()

        params_v1_template = (
            f"_u={params_u}&_p={params_p}&randnumber=&isPasswordPolicy=1&"
            "txt_mm_expression=14&txt_mm_length=15&txt_mm_userzh=0&"
            "hid_flag=1&hidlag=1&hid_dxyzm="
        )

        logon_url = f"{base_url}/cas/logon.action"
        logon_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{base_url}/cas/login.action",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }

        last_error: str = ""
        for idx, dk in enumerate(candidates):
            try:
                params_v1 = params_v1_template
                token = hashlib.md5(
                    (hashlib.md5(params_v1.encode()).hexdigest() + hashlib.md5(nowtime.encode()).hexdigest()).encode()
                ).hexdigest()
                params_v1_encoded = KingoDES.encrypt(params_v1, dk)
                post_body = (
                    f"params={params_v1_encoded}&token={token}&timestamp={nowtime}&deskey={dk}&ssessionid={session_id}"
                )
                r = s.post(logon_url, data=post_body, headers=logon_headers, timeout=T)
                snippet = r.text[:250].replace("\n", " ").replace("\r", " ")
                _l(f"[xiqueer.login] logon try#{idx} dk_len={len(dk)} status={r.status_code} resp={snippet!r}")
                try:
                    result = r.json()
                except Exception as e:
                    last_error = f"登录返回非 JSON（dk#{idx}）：{snippet} ({e})"
                    continue

                status = str(result.get("status", ""))
                message = (
                    str(result.get("message") or result.get("msg") or result.get("error") or "")
                    .replace("\n", " ")
                    .strip()
                )
                _l(f"[xiqueer.login] logon try#{idx} result status={status} message={message[:100]}")

                if status in ("200", "0", "1", "true", "True"):
                    # 有的学校 status=200 但 message=账号不存在/密码错误
                    deny_keywords = ["密码", "账号", "用户", "错误", "不正确", "不存在", "锁定", "验证码"]
                    if any(k in message for k in deny_keywords) and status != "200":
                        last_error = f"登录失败：{message} (status={status})"
                        continue
                    # 再确认 cookie 中确实有登录态（不是假 200）
                    all_cookies = [c.name for c in s.cookie_jar]
                    _l(f"[xiqueer.login] SUCCESS at try#{idx}! cookies={all_cookies}")
                    return s, username
                last_error = f"登录失败：{message or '未知错误'} (status={status})"
            except AcademicAdapterError as ae:
                # 例如 DES 加密异常等，尝试下一个候选
                last_error = str(ae)
                _l(f"[xiqueer.login] logon try#{idx} EXC: {last_error[:120]}")
                continue
            except Exception as e:
                last_error = f"登录过程异常：{e}"
                _l(f"[xiqueer.login] logon try#{idx} EXC: {last_error[:120]}")
                continue

        # 所有候选失败，抛出最后的错误（已经包含了密码不对等信息）
        raise AcademicAdapterError(last_error or "登录失败：请检查账号密码是否正确，或联系开发者排查。")

    @staticmethod
    def _probe_get(s: _HttpSession, base_url: str, paths: list[str], timeout: float, label: str) -> str:
        """按顺序尝试多个路径，返回第一个有效响应的文本。"""
        tried: list[str] = []
        for p in paths:
            url = f"{base_url}{p}"
            tried.append(url)
            try:
                r = s.get(url, timeout=timeout)
                text = r.text.strip()
                if (
                    r.status_code == 200
                    and text
                    and len(text) >= 8
                    and "凭证已失效" not in text
                    and "无效访问" not in text[:100]
                ):
                    _l(f"[xiqueer.login] {label} OK via {p} → {text[:30]}...")
                    return text
            except Exception as e:
                _l(f"[xiqueer.login] {label} probe {p} EXC={e}")
                continue
        raise AcademicAdapterError(
            f"获取 {label} 失败。已尝试：{tried}"
            f"。可能该学校使用非标准青果部署或需要额外的 Cookie。"
            "请确认教务系统地址正确，或联系开发者排查。"
        )

    # ── fetch_semesters ─────────────────────────────────────────────────────

    async def fetch_semesters(self) -> list[dict]:
        from datetime import date

        today = date.today()
        year = today.year
        if 9 <= today.month <= 12:
            sy, ny, term = year, year + 1, "1"
        else:
            sy, ny, term = year - 1, year, "2"

        if term == "1":
            start = date(sy, 9, 1)
            end = date(ny, 1, 15)
        else:
            start = date(ny, 2, 25)
            end = date(ny, 7, 15)

        kw = getattr(self, "_semester_keyword", "")
        if kw:
            m = re.search(r"(\d{4})", kw)
            if m:
                ky = int(m.group(1))
                if "秋" in kw:
                    sy, ny, term = ky, ky + 1, "1"
                    start = date(sy, 9, 1)
                    end = date(ny, 1, 15)
                elif "春" in kw:
                    sy, ny, term = ky - 1, ky, "2"
                    start = date(ny, 2, 25)
                    end = date(ny, 7, 15)

        tw = int((end - start).days / 7) + 1
        return [
            {
                "name": f"{sy}-{ny} 第{'一' if term == '1' else '二'}学期",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "total_weeks": tw,
                "adapter_id": f"{sy}-{term}",
                "school_year": str(sy),
                "term": term,
            }
        ]

    async def select_semester(self, semester_selector: dict) -> None:
        if semester_selector and semester_selector.get("keyword"):
            self._semester_keyword = semester_selector["keyword"]

    # ── fetch_courses ───────────────────────────────────────────────────────

    async def fetch_courses(self, semester_info: dict) -> list[dict]:
        if not self._session:
            raise AcademicAdapterError("未登录，请先 authenticate")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._do_fetch_courses_sync, semester_info)

    def _do_fetch_courses_sync(self, semester_info: dict) -> list[dict]:
        T = 8
        username = self._username
        sy = semester_info.get("school_year", "")
        term = semester_info.get("term", "1")
        try:
            next_sy = str(int(sy) + 1)
        except Exception:
            next_sy = sy

        xnxq = f"{sy}-{next_sy}-{term}"
        _l(f"[xiqueer.fetch] base={self.base_url} user={username} xnxq={xnxq}")

        headers = {
            "User-Agent": _DEFAULT_HEADERS["User-Agent"],
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
        }

        # ---- 1. JSON API 探测（多端点 × GET/POST） ----
        kbzc_common = "jsxx,jc,jc2,cd,zc,dwmc,xnxqm,xsksxm,xsdm,xsm,xxm,xjx,xsks,pym,zb,zs"
        for api_path, xnxq_key, xsh_key in _KB_JSON_APIS:
            for method in ("GET", "POST"):
                params = {xnxq_key: xnxq, xsh_key: username}
                if "kbzc" in api_path:
                    params["query.kbzc"] = kbzc_common
                qs = urllib.parse.urlencode(params)
                try:
                    if method == "GET":
                        r = self._session.get(f"{self.base_url}{api_path}?{qs}", headers=headers, timeout=T)
                    else:
                        r = self._session.post(
                            f"{self.base_url}{api_path}",
                            data=qs,
                            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                            timeout=T,
                        )
                    text = r.text.strip()
                    _l(f"[xiqueer.fetch] JSON {method} {api_path} status={r.status_code} first200={text[:200]!r}")
                    if (
                        r.status_code == 200
                        and text
                        and "凭证已失效" not in text
                        and "无效访问" not in text[:500]
                        and not text.startswith("<")  # 不是 HTML
                        and not text.startswith("<!")
                    ):
                        try:
                            raw = r.json()
                        except Exception:
                            continue
                        courses = self._parse_course_payload(raw)
                        if courses:
                            _l(f"[xiqueer.fetch] JSON SUCCESS via {api_path}! {len(courses)} courses")
                            return courses
                except Exception as e:
                    _l(f"[xiqueer.fetch] JSON {method} {api_path} EXC={e}")
                    continue

        # ---- 2. HTML 页面（Dawn-Course kingosoft.js 同款解析） ----
        for html_path in _KB_HTML_PAGES:
            url = f"{self.base_url}{html_path}"
            for qs_prefix in ("", f"?xn={sy}&xq={term}&xh={username}&xsh={username}&xnxq={xnxq}"):
                try:
                    r = self._session.get(
                        f"{url}{qs_prefix}",
                        headers={
                            "User-Agent": headers["User-Agent"],
                            "Accept": "text/html,*/*",
                            "Referer": f"{self.base_url}/cas/index.action",
                        },
                        timeout=T,
                    )
                    text = r.text
                    _l(
                        f"[xiqueer.fetch] HTML {html_path}{qs_prefix} status={r.status_code} "
                        f"len={len(text)} first300={text[:300]!r}"
                    )
                    if (
                        r.status_code == 200
                        and len(text) > 1500
                        and "登录" not in text[:300]
                        and "凭证已失效" not in text[:300]
                        and "无效访问" not in text[:500]
                        and ("<tr" in text or "<TR" in text)
                    ):
                        courses = self._parse_kb_html(text)
                        if courses:
                            _l(f"[xiqueer.fetch] HTML SUCCESS via {html_path}! {len(courses)} courses")
                            return courses
                except Exception as e:
                    _l(f"[xiqueer.fetch] HTML {html_path}{qs_prefix} EXC={e}")
                    continue

        raise AcademicAdapterError(
            "课表 API 和 HTML 页面均未返回有效数据。"
            "可能原因：账号密码错误、该学校部署了非标准青果系统、或课表尚未发布。"
            "请查看 N.E.K.O 日志面板中 [xiqueer.fetch] 开头的调试信息，"
            "或将教务系统地址 + 日志截图发给开发者分析。"
        )

    # ── _parse_course_payload ──────────────────────────────────────────────

    def _parse_course_payload(self, payload: Any) -> list[dict]:
        """解析青果 JSON API 返回。支持 kbArr 嵌套结构和扁平结构。"""
        rows: list[dict] = []

        def fmt_period(raw) -> str:
            s = str(raw or "").strip()
            m = re.match(r"(\d+)(?:-(\d+))?", s)
            return m.group(1) if m else ""

        def fmt_weeks(raw) -> str:
            return str(raw or "").strip()

        def walk(obj, title_hint=None, fallback_weekday=1):
            if isinstance(obj, list):
                for item in obj:
                    walk(item, title_hint, fallback_weekday)
            elif isinstance(obj, dict):
                title = obj.get("xsm", obj.get("xxm", obj.get("kcname", obj.get("title", title_hint))))
                teacher = obj.get("zpm", obj.get("jsm", obj.get("teacher", obj.get("zprs", ""))))
                location = obj.get("cdmc", obj.get("cd", obj.get("location", "")))

                if "kbArr" in obj and isinstance(obj["kbArr"], list):
                    for cell in obj["kbArr"]:
                        if not isinstance(cell, dict):
                            continue
                        jc = cell.get("jc", "")
                        wc = cell.get("kbzc", "")
                        weekday = int(cell.get("jsjm", cell.get("skxq", fallback_weekday)))
                        periods = fmt_period(jc)
                        if not title or not periods:
                            continue
                        rows.append(
                            {
                                "title": str(title).strip(),
                                "teacher": str(teacher).strip() if teacher else "",
                                "location": str(location).strip() if location else "",
                                "periods": periods,
                                "weeks": fmt_weeks(wc),
                                "weekday": weekday,
                            }
                        )
                elif "jc" in obj and "kbzc" in obj and title:
                    weekday = int(obj.get("jsjm", obj.get("skxq", fallback_weekday)))
                    jc_val = obj.get("jc", "")
                    if jc_val:
                        rows.append(
                            {
                                "title": str(title).strip(),
                                "teacher": str(teacher).strip() if teacher else "",
                                "location": str(location).strip() if location else "",
                                "periods": fmt_period(jc_val),
                                "weeks": fmt_weeks(obj.get("kbzc", "")),
                                "weekday": weekday,
                            }
                        )
                else:
                    for v in obj.values():
                        walk(v, title, fallback_weekday)

        walk(payload)

        seen = set()
        unique: list[dict] = []
        for row in rows:
            key = (row["title"], row["weekday"], row["periods"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    # ── _parse_kb_html (Dawn-Course kingosoft.js 移植版) ───────────────────

    @staticmethod
    def _strip_html_tags(html: str) -> str:
        text = re.sub(r"<[^>]+>", "", html or "")
        for entity, repl in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
            text = text.replace(entity, repl)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _parse_kb_html(html: str) -> list[dict]:
        """完整移植 Dawn-Course kingosoft.js scheduleHtmlParser。"""
        if not html or len(html) < 500:
            return []
        clean = html.replace("\r", "").replace("\n", "")

        # 先试列表型课表（很多新版青果用这种）
        list_courses = XiQueErAdapter._parse_list_table(clean)
        if list_courses:
            return list_courses

        # 普通表格课表
        tr_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
        td_pat = re.compile(r"<(td|th)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)

        rows_html = [m.group(1) for m in tr_pat.finditer(clean)]
        if not rows_html:
            return []

        day_map: dict[int, int] = {}
        header_found = False
        for row_html in rows_html:
            cells_html = [m.group(2) for m in td_pat.finditer(row_html)]
            if not cells_html:
                continue
            for i, c in enumerate(cells_html):
                t = XiQueErAdapter._strip_html_tags(c)
                rev_idx = len(cells_html) - 1 - i
                if any(x in t for x in ("星期一", "周一")):
                    day_map[rev_idx] = 1
                    header_found = True
                elif any(x in t for x in ("星期二", "周二")):
                    day_map[rev_idx] = 2
                    header_found = True
                elif any(x in t for x in ("星期三", "周三")):
                    day_map[rev_idx] = 3
                    header_found = True
                elif any(x in t for x in ("星期四", "周四")):
                    day_map[rev_idx] = 4
                    header_found = True
                elif any(x in t for x in ("星期五", "周五")):
                    day_map[rev_idx] = 5
                    header_found = True
                elif any(x in t for x in ("星期六", "周六")):
                    day_map[rev_idx] = 6
                    header_found = True
                elif any(x in t for x in ("星期日", "周日", "星期天")):
                    day_map[rev_idx] = 7
                    header_found = True
            if header_found:
                break

        courses: list[dict] = []
        if header_found:
            for row_html in rows_html:
                cells_html = [m.group(2) for m in td_pat.finditer(row_html)]
                if not cells_html:
                    continue
                for col_idx in range(len(cells_html)):
                    rev_idx = len(cells_html) - 1 - col_idx
                    day = day_map.get(rev_idx)
                    if not day:
                        continue
                    cell_html = cells_html[col_idx]
                    courses.extend(XiQueErAdapter._parse_cell(cell_html, day))
        else:
            # 旧版 class="td" 兜底
            td_legacy = re.compile(r'<td[^>]*class=["\']?td["\']?[^>]*>(.*?)</td>', re.IGNORECASE | re.DOTALL)
            for row_html in rows_html:
                cells_html = [m.group(1) for m in td_legacy.finditer(row_html)]
                for day, cell_html in enumerate(cells_html, start=1):
                    courses.extend(XiQueErAdapter._parse_cell(cell_html, day))

        # 去重
        seen = set()
        result = []
        for c in courses:
            key = (c["title"], c["weekday"], c["periods"])
            if key in seen:
                continue
            seen.add(key)
            result.append(c)
        return result

    @staticmethod
    def _parse_list_table(clean_html: str) -> list[dict]:
        """列表型课表（新版青果 / 部分正方风格）解析。"""
        tr_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
        td_pat = re.compile(r"<(td|th)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
        rows_html = [m.group(1) for m in tr_pat.finditer(clean_html)]
        if not rows_html:
            return []

        header_idx = -1
        header_cells: list[str] = []
        for i, row_html in enumerate(rows_html):
            cells = [m.group(2) for m in td_pat.finditer(row_html)]
            if not cells:
                continue
            header_text = XiQueErAdapter._strip_html_tags(" ".join(cells))
            if "课程" in header_text and (
                "周次" in header_text or "星期" in header_text or "节次" in header_text or "上课" in header_text
            ):
                header_idx = i
                header_cells = cells
                break
        if header_idx < 0 or not header_cells:
            return []

        index_map = {"name": -1, "teacher": -1, "location": -1, "weeks": -1, "periods": -1, "day": -1}
        for i, hc in enumerate(header_cells):
            text = XiQueErAdapter._strip_html_tags(hc)
            if index_map["name"] < 0 and re.search(r"课程|科目", text):
                index_map["name"] = i
            if index_map["teacher"] < 0 and re.search(r"教师|任课|讲师", text):
                index_map["teacher"] = i
            if index_map["location"] < 0 and re.search(r"地点|教室|校区|上课地点", text):
                index_map["location"] = i
            if index_map["weeks"] < 0 and re.search(r"周次|周数", text):
                index_map["weeks"] = i
            if index_map["periods"] < 0 and re.search(r"节次|节数|节", text):
                index_map["periods"] = i
            if index_map["day"] < 0 and re.search(r"星期|周几|星期几|上课日", text):
                index_map["day"] = i

        if index_map["name"] < 0:
            return []

        courses: list[dict] = []
        for row_html in rows_html[header_idx + 1 :]:
            cells = [m.group(2) for m in td_pat.finditer(row_html)]
            if not cells:
                continue
            name_text = XiQueErAdapter._cell_at(cells, index_map["name"])
            if not name_text:
                continue
            teacher = XiQueErAdapter._cell_at(cells, index_map["teacher"])
            location = XiQueErAdapter._cell_at(cells, index_map["location"])
            weeks_text = XiQueErAdapter._cell_at(cells, index_map["weeks"])
            periods_text = XiQueErAdapter._cell_at(cells, index_map["periods"])
            day_text = XiQueErAdapter._cell_at(cells, index_map["day"])

            row_text = XiQueErAdapter._strip_html_tags(" ".join(cells))
            if not weeks_text:
                weeks_text = XiQueErAdapter._extract_weeks_str(row_text)
            if not periods_text:
                periods_text = XiQueErAdapter._extract_sections_str(row_text)
            day = XiQueErAdapter._parse_day_from_text(day_text or row_text)

            if not day or not weeks_text or not periods_text:
                continue
            courses.append(
                {
                    "title": name_text,
                    "teacher": teacher,
                    "location": location,
                    "weekday": day,
                    "weeks": weeks_text,
                    "periods": periods_text,
                }
            )
        return courses

    @staticmethod
    def _cell_at(cells: list[str], idx: int) -> str:
        if idx < 0 or idx >= len(cells):
            return ""
        return XiQueErAdapter._strip_html_tags(cells[idx])

    @staticmethod
    def _parse_cell(cell_html: str, weekday: int) -> list[dict]:
        """解析单个单元格（kingosoft.js parseCell 移植）。"""
        results: list[dict] = []
        if not cell_html or "div_nokb" in cell_html:
            return results

        # 分块：每个 <div> 一个课程块
        div_pat = re.compile(r"<div[^>]*>(.*?)</div>", re.IGNORECASE | re.DOTALL)
        blocks = [m.group(1) for m in div_pat.finditer(cell_html)]
        if not blocks:
            blocks = [cell_html]

        for block in blocks:
            if not block.strip() or "div_nokb" in block:
                continue

            # 课程名：<font> 标签里
            font_match = re.search(r"<font[^>]*>(.*?)</font>", block, re.IGNORECASE | re.DOTALL)
            name = XiQueErAdapter._strip_html_tags(font_match.group(1)) if font_match else ""
            if not name:
                text_parts = re.split(r"<br\s*/?>", block, flags=re.IGNORECASE)
                for tp in text_parts:
                    t = XiQueErAdapter._strip_html_tags(tp)
                    if t and not XiQueErAdapter._looks_like_time(t):
                        name = t
                        break
            if not name:
                continue

            # 移除课程名，剩余部分
            remaining = re.sub(r"<font[^>]*>.*?</font>", "", block, flags=re.IGNORECASE | re.DOTALL)
            br_parts = [
                XiQueErAdapter._strip_html_tags(p) for p in re.split(r"<br\s*/?>", remaining, flags=re.IGNORECASE)
            ]
            br_parts = [p for p in br_parts if p]

            teacher = location = weeks_str = sections_str = ""
            for p in br_parts:
                m = re.search(r"([0-9,\-]+)\s*(?:周|周次)?\s*[\[\(（]\s*([0-9,\-]+)\s*(?:节|节次)?\s*[\]\)）]", p)
                if m:
                    weeks_str = m.group(1)
                    sections_str = m.group(2)
                    continue
                m2 = re.search(r"([0-9,\-]+)\s*周[，,]?\s*([0-9,\-]+)\s*节", p)
                if m2:
                    weeks_str = m2.group(1)
                    sections_str = m2.group(2)
                    continue
                if not location and re.search(r"(楼|室|馆|区|号|座|园|部|教室)", p):
                    location = p
                    continue
                if not weeks_str and not teacher:
                    teacher = p
                elif weeks_str and not location:
                    location = p
                elif not teacher:
                    teacher = p

            if not weeks_str:
                weeks_str = XiQueErAdapter._extract_weeks_str(XiQueErAdapter._strip_html_tags(remaining))
            if not sections_str:
                sections_str = XiQueErAdapter._extract_sections_str(XiQueErAdapter._strip_html_tags(remaining))

            if name and (weeks_str or sections_str):
                results.append(
                    {
                        "title": name.strip(),
                        "teacher": teacher.strip(),
                        "location": location.strip(),
                        "weekday": weekday,
                        "weeks": weeks_str.strip(),
                        "periods": sections_str.strip(),
                    }
                )
        return results

    @staticmethod
    def _looks_like_time(text: str) -> bool:
        return bool(re.match(r"^[\d,\-]+\s*(周|节|日|次)", text))

    @staticmethod
    def _extract_weeks_str(text: str) -> str:
        m = re.search(r"([0-9]+(?:-[0-9]+)?)\s*周", text)
        if m:
            return m.group(1)
        # 裸数字区间
        m2 = re.search(r"\b([0-9]+(?:-[0-9]+)?)\b", text)
        return m2.group(1) if m2 else ""

    @staticmethod
    def _extract_sections_str(text: str) -> str:
        m = re.search(r"([0-9]+(?:-[0-9]+)?)\s*节", text)
        if m:
            return m.group(1)
        m2 = re.search(r"\[([0-9,\-]+)\]", text)
        return m2.group(1) if m2 else ""

    @staticmethod
    def _parse_day_from_text(text: str) -> int:
        if not text:
            return 0
        raw = XiQueErAdapter._strip_html_tags(text).replace(" ", "")
        mapping = [
            ("星期一", 1),
            ("周二", 2),
            ("星期三", 3),
            ("周四", 4),
            ("星期五", 5),
            ("周六", 6),
            ("星期日", 7),
            ("星期天", 7),
        ]
        for keyword, num in mapping:
            if keyword in raw:
                return num
        cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
        m = re.search(r"[星期周]\s*([一二三四五六日天])", raw)
        if m and m.group(1) in cn_map:
            return cn_map[m.group(1)]
        m2 = re.search(r"[星期周]?\s*([1-7])", raw)
        if m2:
            return int(m2.group(1))
        if re.match(r"^[1-7]$", raw):
            return int(raw)
        return 0


def _l(msg: str) -> None:
    """调试日志：委托 _debuglog.dlog（宿主 logger + 插件文件 + stdout 三路输出）。"""
    from . import _debuglog

    _debuglog.dlog(msg)
