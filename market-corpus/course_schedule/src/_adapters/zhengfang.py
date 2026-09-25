"""ZhengFang（正方教务管理系统）适配器。

典型 URL：https://jwglxt.xxx.edu.cn/_data/home_login.aspx
登录方式：ASP.NET __VIEWSTATE + schoolNumber（学校代码）+ 双重 MD5 加密
课表获取：ZNPK/Pri_StuSel.aspx 或 xsxk/XskbView.aspx（GBK 编码）

依赖：纯标准库，符合 N.E.K.O 发布要求。
"""

from __future__ import annotations

import asyncio
import hashlib
import http.cookiejar
import re
import ssl
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from .._academic_adapter import AcademicAdapter, AcademicAdapterError

_SSL_CTX = ssl._create_unverified_context()

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
_DEFAULT_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 常见 schoolNumber（学校代码）
_SCHOOL_NUMBERS = [
    "10479",
    "10919",
    "10086",
    "10280",
    "10290",
    "10301",
    "10422",
    "10425",
    "10456",
    "10460",
    "10475",
    "10484",
    "10543",
    "10602",
    "10694",
    "10710",
    "10712",
    "10730",
    "10749",
    "10792",
    "10812",
    "10834",
    "10856",
    "10947",
]


class _HttpSession:
    def __init__(self):
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.cookie_jar),
            urllib.request.HTTPSHandler(context=_SSL_CTX),
        )

    def cookies_get(self, name: str) -> str | None:
        for c in self.cookie_jar:
            if c.name == name:
                return c.value
        return None

    def _decode(self, raw: bytes) -> str:
        for enc in ("gbk", "gb18030", "utf-8"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _request(
        self, method: str, url: str, headers: dict | None = None, data: bytes | None = None, timeout: float = 10.0
    ):
        hdrs = {**_DEFAULT_HEADERS, **(headers or {})}
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            resp = self.opener.open(req, timeout=timeout)
            raw = resp.read()
            return self._decode(raw), resp.status, dict(resp.headers)
        except urllib.error.HTTPError as e:
            raw = e.read() if hasattr(e, "read") else b""
            return self._decode(raw), e.code, dict(e.headers) if e.headers else {}
        except urllib.error.URLError as e:
            raise AcademicAdapterError(
                f"网络请求失败：{getattr(e, 'reason', e)}。请检查地址是否正确、关闭系统代理后重试。"
            ) from e

    def get(self, url: str, headers: dict | None = None, timeout: float = 10.0):
        return self._request("GET", url, headers=headers, timeout=timeout)

    def post(self, url: str, data: bytes, headers: dict | None = None, timeout: float = 10.0):
        return self._request("POST", url, data=data, headers=headers, timeout=timeout)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _normalize_base_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        parsed = urllib.parse.urlparse(f"https://{raw}")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _l(msg: str) -> None:
    """调试日志：委托 _debuglog.dlog（宿主 logger + 插件文件 + stdout 三路输出）。"""
    from . import _debuglog

    _debuglog.dlog(msg)


class ZhengFangAdapter(AcademicAdapter):
    """正方教务系统适配器。

    登录流程：
      1. GET / → 取 ASP.NET_SessionId Cookie
      2. GET /_data/home_login.aspx → 取 __VIEWSTATE
      3. POST /_data/home_login.aspx → 双重 MD5 加密后的表单

    注意：部分学校的正方教务也启用了验证码，这种情况下会返回明确错误提示。
    """

    adapter_id = "zhengfang"
    adapter_name = "正方教务系统"

    def __init__(self, **kwargs):
        self.base_url: str = (kwargs.get("base_url") or "").rstrip("/")
        self._session: _HttpSession | None = None
        self._username: str = ""
        self._semester_keyword: str = ""
        self._school_number: str = str(kwargs.get("school_number") or "").strip()
        if self.base_url:
            self.base_url = _normalize_base_url(self.base_url)

    async def authenticate(self, creds: dict[str, Any]) -> None:
        raw_base = creds.get("base_url") or creds.get("website") or self.base_url
        self.base_url = _normalize_base_url(raw_base)
        username = (creds.get("username") or creds.get("student_id") or "").strip()
        password = creds.get("password", "")
        school_number = (creds.get("school_number") or self._school_number or "").strip()

        if not self.base_url:
            raise AcademicAdapterError("缺少 base_url（教务系统地址），如 https://jwglxt.aynu.edu.cn")
        if not username or not password:
            raise AcademicAdapterError("缺少 username（学号）或 password（密码）")

        loop = asyncio.get_running_loop()
        sess, user = await loop.run_in_executor(
            None, self._do_login_sync, self.base_url, username, password, school_number
        )
        self._session = sess
        self._username = user

    def _do_login_sync(self, base_url: str, username: str, password: str, school_number: str | None):
        T = 10
        s = _HttpSession()
        _l(f"[zhengfang.login] START base={base_url} user={username}")

        # 1) 访问根路径拿 Session Cookie
        _, status, _ = s.get(f"{base_url}/", timeout=T)
        _l(f"[zhengfang.login] root status={status} cookies={[c.name for c in s.cookie_jar]}")

        # 2) 登录页
        login_url = f"{base_url}/_data/home_login.aspx"
        html, status, _ = s.get(login_url, timeout=T)
        if status != 200:
            # 部分正方用 /default.aspx 作为入口
            login_url = f"{base_url}/default.aspx"
            html, status, _ = s.get(login_url, timeout=T)
            if status != 200:
                raise AcademicAdapterError(
                    f"无法访问正方教务登录页：HTTP {status}。请确认地址是 jwglxt.xxx.edu.cn 格式。"
                )

        # 检查是否有验证码
        if "ValidateCode" in html or "yzm" in html.lower() or "captcha" in html.lower():
            raise AcademicAdapterError(
                "该正方教务系统启用了验证码，当前版本暂不支持自动识别。"
                "请尝试使用喜鹊儿（青果）适配器，或关闭验证码后重试。"
            )

        # 取 __VIEWSTATE
        vs_match = re.search(
            r'<input\s+type=["\']hidden["\']\s+name=["\']__VIEWSTATE["\']\s+value=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if not vs_match:
            raise AcademicAdapterError(f"登录页未找到 __VIEWSTATE。可能不是正方教务系统。前300字：{html[:300]}")
        viewstate = vs_match.group(1)
        _l(f"[zhengfang.login] __VIEWSTATE OK len={len(viewstate)}")

        # 3) 确定 schoolNumber — 探测或用预设
        sn = school_number or self._detect_school_number(html)
        if not sn:
            # 尝试常见值
            for candidate in _SCHOOL_NUMBERS:
                if self._try_login(s, base_url, login_url, viewstate, username, password, candidate, T):
                    _l(f"[zhengfang.login] school_number auto-detected: {candidate}")
                    return s, username
            raise AcademicAdapterError(
                "登录失败，可能需要指定 school_number（学校代码）。"
                "请在导入表单填写 school_number 字段，常见值有：10479, 10919, 10086 等。"
            )

        if not self._try_login(s, base_url, login_url, viewstate, username, password, sn, T):
            raise AcademicAdapterError(
                f"登录失败（school_number={sn}）。请检查账号密码是否正确，或尝试其他 school_number。"
            )
        return s, username

    @staticmethod
    def _detect_school_number(html: str) -> str:
        """从登录页 HTML 探测 schoolNumber。"""
        for pat in (
            r'schoolNumber["\']?\s*[:=]\s*["\']?(\d{5})["\']?',
            r'sn["\']?\s*[:=]\s*["\']?(\d{5})["\']?',
            r'"(\d{5})"\s*[,}]',
        ):
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def _try_login(
        s: _HttpSession,
        base_url: str,
        login_url: str,
        viewstate: str,
        username: str,
        password: str,
        sn: str,
        timeout: float,
    ) -> bool:
        real_pwd_md5 = _md5(password).upper()[:30]
        encrypted_pwd = _md5((username + real_pwd_md5 + sn)).upper()[:30]

        login_data = {
            "__VIEWSTATE": viewstate,
            "pcInfo": f"{_UA}undefined{_UA} SN:NULL",
            "typeName": "学生",
            "dsdsdsdsdxcxdfgfg": encrypted_pwd,
            "fgfggfdgtyuuyyuuckjg": _md5("").upper()[:30],
            "Sel_Type": "STU",
            "txt_asmcdefsddsd": username,
            "txt_pewerwedsdfsdff": "",
            "txt_sdertfgsadscxcadsads": "",
        }
        body = urllib.parse.urlencode(login_data).encode("gbk")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": base_url,
            "Referer": login_url,
        }
        html, status, _ = s.post(login_url, data=body, headers=headers, timeout=timeout)
        _l(f"[zhengfang.login] POST status={status} resp_len={len(html)} first200={html[:200]!r}")

        if status == 302 or "错误" in html[:500] and "密码错误" not in html[:500]:
            pass
        if "密码错误" in html or "账号不存在" in html or "不存在" in html:
            return False

        # 尝试访问需要登录的页面确认登录态
        _, st2, _ = s.get(f"{base_url}/xsxk/XskbView.aspx", timeout=timeout)
        if st2 == 200:
            _l("[zhengfang.login] SUCCESS!")
            return True
        # 有些正方用的不同路径，再试一次
        _, st3, _ = s.get(f"{base_url}/xsxj/Stu_MyInfo_RPT.aspx", timeout=timeout)
        return st3 == 200

    async def fetch_semesters(self) -> list[dict]:
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

    async def fetch_courses(self, semester_info: dict) -> list[dict]:
        if not self._session:
            raise AcademicAdapterError("未登录")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._do_fetch_courses_sync, semester_info)

    def _do_fetch_courses_sync(self, semester_info: dict) -> list[dict]:
        T = 10
        sy = semester_info.get("school_year", "")
        term = semester_info.get("term", "1")
        # 正方 term 用 0/1（0=上学期, 1=下学期），部分用 1/2
        term_zf = "0" if term == "1" else "1"

        # 课表页面路径候选
        kb_paths = [
            f"/xsxk/XskbView.aspx?xn={sy}&xq={term_zf}",
            f"/ZNPK/Pri_StuSel.aspx?Sel_XNXQ={sy}{term_zf}&type=1",
            f"/ZNPK/Pri_StuSel_rpt.aspx?Sel_XNXQ={sy}{term_zf}&type=1",
            f"/xsxk/Xskb.aspx?xn={sy}&xq={term_zf}",
            f"/xsxk/XskbView.aspx?xn={sy}&xq={term}",
            f"/ZNPK/Pri_StuSel.aspx?Sel_XNXQ={sy}{term}&type=1",
        ]

        for path in kb_paths:
            url = f"{self.base_url}{path}"
            try:
                html, status, _ = self._session.get(
                    url,
                    headers={"Referer": f"{self.base_url}/default.aspx"},
                    timeout=T,
                )
                _l(f"[zhengfang.fetch] {path} status={status} len={len(html)} first300={html[:300]!r}")
                if (
                    status == 200
                    and len(html) > 1500
                    and "登录" not in html[:300]
                    and "type=hidden" not in html.lower()[:500]
                    and ("<tr" in html or "<TR" in html or "课程" in html)
                ):
                    courses = self._parse_html(html)
                    if courses:
                        _l(f"[zhengfang.fetch] SUCCESS via {path}! {len(courses)} courses")
                        return courses
            except Exception as e:
                _l(f"[zhengfang.fetch] {path} EXC={e}")
                continue

        raise AcademicAdapterError(
            "正方教务课表页面均未返回有效数据。"
            "可能账号密码错误、课表尚未发布、或该学校正方版本特殊。"
            "请查看日志面板中 [zhengfang.fetch] 开头的调试信息。"
        )

    # ---- HTML 解析 ----

    @staticmethod
    def _strip_tags(html: str) -> str:
        text = re.sub(r"<[^>]+>", "", html or "")
        for e, r in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
            text = text.replace(e, r)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _parse_html(html: str) -> list[dict]:
        """正方教务课表 HTML 解析。优先尝试列表型，再试表格型。"""
        clean = html.replace("\r", "").replace("\n", "")

        # 列表型
        list_courses = ZhengFangAdapter._parse_list(clean)
        if list_courses:
            return list_courses

        # 表格型（按单元格解析）
        return ZhengFangAdapter._parse_grid(clean)

    @staticmethod
    def _parse_list(clean: str) -> list[dict]:
        tr_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
        td_pat = re.compile(r"<(td|th)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
        rows = [m.group(1) for m in tr_pat.finditer(clean)]
        if not rows:
            return []

        header_idx = -1
        header_cells: list[str] = []
        for i, rh in enumerate(rows):
            cells = [m.group(2) for m in td_pat.finditer(rh)]
            if not cells:
                continue
            ht = ZhengFangAdapter._strip_tags(" ".join(cells))
            if "课程" in ht and ("星期" in ht or "周次" in ht or "节次" in ht):
                header_idx = i
                header_cells = cells
                break
        if header_idx < 0:
            return []

        idx_map = {"name": -1, "teacher": -1, "location": -1, "weeks": -1, "periods": -1, "day": -1}
        for i, hc in enumerate(header_cells):
            t = ZhengFangAdapter._strip_tags(hc)
            if idx_map["name"] < 0 and re.search(r"课程|科目", t):
                idx_map["name"] = i
            if idx_map["teacher"] < 0 and re.search(r"教师|任课", t):
                idx_map["teacher"] = i
            if idx_map["location"] < 0 and re.search(r"地点|教室|校区", t):
                idx_map["location"] = i
            if idx_map["weeks"] < 0 and re.search(r"周次|周数", t):
                idx_map["weeks"] = i
            if idx_map["periods"] < 0 and re.search(r"节次|节数", t):
                idx_map["periods"] = i
            if idx_map["day"] < 0 and re.search(r"星期|周几", t):
                idx_map["day"] = i

        if idx_map["name"] < 0:
            return []

        result: list[dict] = []
        for rh in rows[header_idx + 1 :]:
            cells = [m.group(2) for m in td_pat.finditer(rh)]
            if not cells:
                continue
            name = ZhengFangAdapter._cell_at(cells, idx_map["name"])
            if not name:
                continue
            teacher = ZhengFangAdapter._cell_at(cells, idx_map["teacher"])
            location = ZhengFangAdapter._cell_at(cells, idx_map["location"])
            weeks = ZhengFangAdapter._cell_at(cells, idx_map["weeks"])
            periods = ZhengFangAdapter._cell_at(cells, idx_map["periods"])
            day_raw = ZhengFangAdapter._cell_at(cells, idx_map["day"])
            if not day_raw:
                row_text = ZhengFangAdapter._strip_tags(" ".join(cells))
                day_raw = row_text
            day = ZhengFangAdapter._parse_day(day_raw)
            if not day or not weeks or not periods:
                continue
            result.append(
                {
                    "title": name,
                    "teacher": teacher,
                    "location": location,
                    "weekday": day,
                    "weeks": weeks,
                    "periods": periods,
                }
            )

        # 去重
        seen = set()
        uniq = []
        for c in result:
            k = (c["title"], c["weekday"], c["periods"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        return uniq

    @staticmethod
    def _parse_grid(clean: str) -> list[dict]:
        tr_pat = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
        td_pat = re.compile(r"<(td|th)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
        rows = [m.group(1) for m in tr_pat.finditer(clean)]
        if not rows:
            return []

        day_map: dict[int, int] = {}
        header_found = False
        for rh in rows:
            cells_html = [m.group(2) for m in td_pat.finditer(rh)]
            if not cells_html:
                continue
            for i, c in enumerate(cells_html):
                t = ZhengFangAdapter._strip_tags(c)
                rev = len(cells_html) - 1 - i
                if "星期一" in t or "周一" in t:
                    day_map[rev] = 1
                    header_found = True
                elif "星期二" in t or "周二" in t:
                    day_map[rev] = 2
                    header_found = True
                elif "星期三" in t or "周三" in t:
                    day_map[rev] = 3
                    header_found = True
                elif "星期四" in t or "周四" in t:
                    day_map[rev] = 4
                    header_found = True
                elif "星期五" in t or "周五" in t:
                    day_map[rev] = 5
                    header_found = True
                elif "星期六" in t or "周六" in t:
                    day_map[rev] = 6
                    header_found = True
                elif "星期日" in t or "周日" in t:
                    day_map[rev] = 7
                    header_found = True
            if header_found:
                break

        result: list[dict] = []
        if header_found:
            for rh in rows:
                cells_html = [m.group(2) for m in td_pat.finditer(rh)]
                if not cells_html:
                    continue
                for ci in range(len(cells_html)):
                    rev = len(cells_html) - 1 - ci
                    day = day_map.get(rev)
                    if not day:
                        continue
                    cell = cells_html[ci]
                    result.extend(ZhengFangAdapter._parse_cell(cell, day))

        seen = set()
        uniq = []
        for c in result:
            k = (c["title"], c["weekday"], c["periods"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        return uniq

    @staticmethod
    def _parse_cell(cell: str, weekday: int) -> list[dict]:
        if not cell or "nokb" in cell.lower():
            return []
        div_pat = re.compile(r"<div[^>]*>(.*?)</div>", re.IGNORECASE | re.DOTALL)
        blocks = [m.group(1) for m in div_pat.finditer(cell)]
        if not blocks:
            blocks = [cell]
        results: list[dict] = []
        for block in blocks:
            if not block.strip() or "nokb" in block.lower():
                continue
            results.append(
                {
                    "title": ZhengFangAdapter._strip_tags(block),
                    "teacher": "",
                    "location": "",
                    "weekday": weekday,
                    "weeks": "",
                    "periods": "",
                }
            )
        return results

    @staticmethod
    def _cell_at(cells: list[str], idx: int) -> str:
        if idx < 0 or idx >= len(cells):
            return ""
        return ZhengFangAdapter._strip_tags(cells[idx])

    @staticmethod
    def _parse_day(text: str) -> int:
        if not text:
            return 0
        raw = ZhengFangAdapter._strip_tags(text).replace(" ", "")
        for kw, n in [
            ("星期一", 1),
            ("周二", 2),
            ("星期三", 3),
            ("周四", 4),
            ("星期五", 5),
            ("周六", 6),
            ("星期日", 7),
            ("星期天", 7),
        ]:
            if kw in raw:
                return n
        cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
        m = re.search(r"[星期周]\s*([一二三四五六日天])", raw)
        if m and m.group(1) in cn_map:
            return cn_map[m.group(1)]
        m2 = re.search(r"[星期周]?\s*([1-7])", raw)
        if m2:
            return int(m2.group(1))
        return 0
