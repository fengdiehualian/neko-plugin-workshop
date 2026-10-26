"""抓取层：限速 / 重试 / Cookie 持久化 / robots 友好 / 正文抽取。

设计约束（务必遵守）：

1. 全部为**同步**实现，插件侧一律用 ``asyncio.to_thread`` 调度，绝不在事件循环里做网络 IO。
2. 零第三方依赖：只用标准库 ``urllib`` / ``http.cookiejar``。
3. 同一站点强制最小请求间隔，默认 1.5 秒；这是避免触发风控的关键，不要调小。
4. Cookie 落在插件自己的 ``data/`` 目录，绝不进 git。
5. 只做读取（GET）与登录（POST 凭据），不向平台写入任何业务数据。
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ── 冻结宿主兼容层（重要，别删）────────────────────────────────────
# N.E.K.O 的插件宿主是 PyInstaller 冻结进程：标准库里**只有宿主自身或其依赖
# 引用过的模块**才会被打包。已实证宿主缺少 ``urllib.robotparser``——仅仅这一行
# 就让整个插件在 import 阶段死掉，而宿主把 stderr 吞了，日志里只剩
# ``PLUGIN_START_FAILED``，非常难查。
#
# 约定：凡是"冷门"标准库模块，一律软导入 + 自带兜底；
# 添加任何新 import 前先自问「宿主会引用它吗」，拿不准就走这里。

try:  # 宿主自带就用官方实现
    from urllib.robotparser import RobotFileParser as _HostRobotParser
except Exception:  # pragma: no cover - 冻结宿主路径
    _HostRobotParser = None

try:
    import gzip as _gzip
except Exception:  # pragma: no cover - 冻结宿主路径
    _gzip = None

try:
    import zlib as _zlib
except Exception:  # pragma: no cover - 冻结宿主路径
    _zlib = None

try:
    from html import unescape as _html_unescape
except Exception:  # pragma: no cover - 冻结宿主路径
    _html_unescape = None

_BASIC_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
    "&nbsp;": " ",
}
_NUMERIC_ENTITY_RE = re.compile(r"&#(x?)([0-9a-fA-F]+);")


def _unescape_entities(text: str) -> str:
    """HTML 实体反转义。宿主没打包 ``html`` 时用最小实现兜底。"""
    if _html_unescape is not None:
        try:
            return _html_unescape(text)
        except Exception:
            pass
    text = _NUMERIC_ENTITY_RE.sub(
        lambda m: _safe_chr(int(m.group(2), 16 if m.group(1) else 10)), text
    )
    for entity, char in _BASIC_ENTITIES.items():
        if entity in text:
            text = text.replace(entity, char)
    return text


def _safe_chr(code: int) -> str:
    try:
        return chr(code)
    except (ValueError, OverflowError):
        return ""


class _MiniRobots:
    """``urllib.robotparser`` 的极小替身，只认最常见的三类指令。

    匹配规则按 Google 的通行做法：Allow / Disallow 各自取**最长前缀**，
    长度相同则 Allow 优先；认不出来就放行。
    """

    def __init__(self, text: str, user_agent: str = "*") -> None:
        self.rules: list[tuple[str, bool]] = []
        self._parse(text or "", user_agent)

    def _parse(self, text: str, user_agent: str) -> None:
        ua_token = (user_agent or "").lower()
        groups: list[tuple[list[str], list[tuple[str, bool]]]] = []
        current: Optional[tuple[list[str], list[tuple[str, bool]]]] = None
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, _, value = line.partition(":")
            field = field.strip().lower()
            value = value.strip()
            if field == "user-agent":
                if current is None or current[1]:
                    current = ([], [])
                    groups.append(current)
                current[0].append(value.lower())
            elif field in ("allow", "disallow") and current is not None:
                current[1].append((value, field == "allow"))
        for agents, rules in groups:
            if any(agent == "*" or (agent and agent in ua_token) for agent in agents):
                for path, allowed in rules:
                    if path:
                        self.rules.append((path, allowed))

    def can_fetch(self, user_agent: str, url: str) -> bool:
        if not self.rules:
            return True
        try:
            path = urllib.parse.urlparse(url).path or "/"
        except Exception:
            return True
        best_len = -1
        best_allow = True
        for prefix, allowed in self.rules:
            if path.startswith(prefix) and len(prefix) > best_len:
                best_len = len(prefix)
                best_allow = allowed
        return best_allow

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_META_CHARSET_RE = re.compile(rb'<meta[^>]+charset=["\']?\s*([\w-]+)', re.I)
_SCRIPT_RE = re.compile(r"<script[\s\S]*?</script>", re.I)
_STYLE_RE = re.compile(r"<style[\s\S]*?</style>", re.I)
_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<(?:br|/p|/div|/li|/tr)[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_RE = re.compile(r"\n\s*\n\s*\n+")


def _safe_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


@dataclass
class FetchResult:
    """一次抓取的结果。``ok`` 为 True 时 ``text`` 才有意义。"""

    ok: bool
    status: int
    url: str
    text: str
    error: str = ""
    final_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "url": self.url,
            "final_url": self.final_url or self.url,
            "text": self.text,
            "error": self.error,
        }


@dataclass
class Throttle:
    """按站点做最小间隔限速。"""

    interval_ms: int = 1500
    _last: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self, host: str) -> None:
        if self.interval_ms <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                last = self._last.get(host, 0.0)
                remaining = (self.interval_ms / 1000.0) - (now - last)
                if remaining <= 0:
                    self._last[host] = now
                    return
            time.sleep(min(remaining, 2.0))


def _decode_body(raw: bytes, encoding: str, content_type: str, declared: str = "") -> str:
    encoding = (encoding or "").lower()
    if "gzip" in encoding:
        if _gzip is not None:
            try:
                raw = _gzip.decompress(raw)
            except Exception:
                pass
    elif "deflate" in encoding:
        if _zlib is not None:
            try:
                raw = _zlib.decompress(raw)
            except Exception:
                try:
                    raw = _zlib.decompress(raw, -_zlib.MAX_WBITS)
                except Exception:
                    pass
    elif "br" in encoding:
        # Brotli 标准库不支持，直接原样返回，交给上层判断是否可用
        pass
    charset = ""
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if match:
        charset = match.group(1)
    if not charset:
        match = _META_CHARSET_RE.search(raw[:4096])
        if match:
            charset = match.group(1).decode("ascii", errors="ignore")
    if not charset:
        charset = declared or "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def html_to_text(raw: str, limit: int = 12000) -> str:
    """把 HTML 压成可读纯文本，用于喂给大模型或做正文抽取。"""
    if not raw:
        return ""
    text = _COMMENT_RE.sub(" ", raw)
    text = _SCRIPT_RE.sub(" ", text)
    text = _STYLE_RE.sub(" ", text)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _unescape_entities(text)
    text = text.replace("\u00a0", " ")
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    text = _BLANK_RE.sub("\n\n", text)
    if limit > 0 and len(text) > limit:
        text = text[:limit]
    return text.strip()


def extract_json_object(text: str) -> dict[str, Any]:
    """从大模型输出或网页片段里抠出第一个 JSON 对象。"""
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(candidate[start : end + 1])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class Crawler:
    """一个插件实例共用的抓取器。"""

    def __init__(
        self,
        data_dir: Path,
        interval_ms: int = 1500,
        respect_robots: bool = True,
        timeout: float = 15.0,
        max_pages: int = 6,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = float(timeout)
        self.max_pages = int(max_pages)
        self.respect_robots = bool(respect_robots)
        self.user_agent = user_agent
        self.throttle = Throttle(interval_ms=interval_ms)
        self.cookie_path = self.data_dir / "cookies.txt"
        self.cookiejar = http.cookiejar.MozillaCookieJar(str(self.cookie_path))
        try:
            if self.cookie_path.exists():
                self.cookiejar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookiejar)
        )
        # 值可能是官方 RobotFileParser、_MiniRobots，或 None（拿不到 robots.txt）
        self._robots: dict[str, Any] = {}
        self._robots_lock = threading.Lock()

    # ── Cookie ────────────────────────────────────────────────
    def save_cookies(self) -> None:
        try:
            self.cookiejar.save(ignore_discard=True, ignore_expires=True)
        except Exception:
            pass

    def cookie_header(self, url: str) -> str:
        try:
            request = urllib.request.Request(url)
            self.cookiejar.add_cookie_header(request)
            return request.get_header("Cookie", "")
        except Exception:
            return ""

    def clear_cookies(self) -> None:
        try:
            self.cookiejar.clear()
        except Exception:
            pass
        self.save_cookies()

    # ── robots ────────────────────────────────────────────────
    def robots_allows(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urllib.parse.urlparse(url)
        if parts.scheme not in ("http", "https"):
            return False
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._robots_lock:
            parser = self._robots.get(origin)
        if parser is None:
            parser = self._load_robots(origin)
            with self._robots_lock:
                self._robots[origin] = parser
        if parser is None:
            return True
        try:
            return bool(parser.can_fetch(self.user_agent, url))
        except Exception:
            return True

    def _load_robots(self, origin: str) -> Any:
        """取一次 robots.txt。拿不到（404 / 超时 / 无网络）返回 None，调用方放行。"""
        robots_url = urllib.parse.urljoin(origin, "/robots.txt")
        if _HostRobotParser is not None:
            parser = _HostRobotParser()
            parser.set_url(robots_url)
            # RobotFileParser.read() 内部用 urlopen 且不带超时，
            # 站点不响应时会把整个请求挂死，这里临时压一个全局超时
            import socket

            previous = socket.getdefaulttimeout()
            socket.setdefaulttimeout(min(self.timeout, 5.0))
            try:
                parser.read()
            except Exception:
                return None
            finally:
                socket.setdefaulttimeout(previous)
            return parser
        # 宿主没打包 urllib.robotparser：自己取，自己解析
        try:
            request = urllib.request.Request(robots_url)
            request.add_header("User-Agent", self.user_agent)
            with urllib.request.urlopen(request, timeout=min(self.timeout, 5.0)) as resp:  # noqa: S310
                text = resp.read().decode("utf-8", errors="replace")
        except Exception:
            return None
        return _MiniRobots(text, self.user_agent)

    # ── 核心请求 ──────────────────────────────────────────────
    def request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        allow_redirect: bool = True,
    ) -> FetchResult:
        if not url:
            return FetchResult(False, 0, url, "", "空 URL")
        if not self.robots_allows(url):
            return FetchResult(False, 0, url, "", "robots.txt 不允许抓取该地址")
        host = _host_of(url)
        self.throttle.wait(host)
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("User-Agent", self.user_agent)
        request.add_header("Accept", "text/html,application/json,application/xhtml+xml,*/*;q=0.8")
        request.add_header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        request.add_header("Connection", "close")
        for key, value in (headers or {}).items():
            if value:
                request.add_header(key, value)

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = self._opener
        if not allow_redirect:
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self.cookiejar), _NoRedirect
            )
        try:
            with opener.open(request, timeout=self.timeout) as resp:
                raw = resp.read()
                text = _decode_body(
                    raw,
                    resp.headers.get("Content-Encoding", ""),
                    resp.headers.get("Content-Type", ""),
                )
                return FetchResult(True, resp.status, url, text, final_url=resp.geturl())
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read()
                text = _decode_body(
                    raw,
                    exc.headers.get("Content-Encoding", "") if exc.headers else "",
                    exc.headers.get("Content-Type", "") if exc.headers else "",
                )
            except Exception:
                text = ""
            return FetchResult(False, exc.code, url, text, str(exc), getattr(exc, "url", url))
        except Exception as exc:
            return FetchResult(False, 0, url, "", str(exc))

    def get(self, url: str, headers: Optional[dict[str, str]] = None, as_text: bool = True) -> FetchResult:
        result = self.request(url, "GET", headers=headers)
        if result.ok and as_text and "<" in result.text[:512]:
            result.text = html_to_text(result.text)
        return result

    def get_json(self, url: str, headers: Optional[dict[str, str]] = None) -> tuple[bool, Any]:
        result = self.request(url, "GET", headers=headers)
        if not result.ok:
            return False, result.error or f"HTTP {result.status}"
        try:
            return True, json.loads(result.text)
        except Exception:
            return False, "响应不是合法 JSON"

    def post_form(
        self,
        url: str,
        form: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
    ) -> FetchResult:
        body = urllib.parse.urlencode(form, encoding="utf-8").encode("utf-8")
        merged = {"Content-Type": "application/x-www-form-urlencoded"}
        merged.update(headers or {})
        return self.request(url, "POST", data=body, headers=merged)

    def fetch_page_text(self, url: str, limit: int = 6000) -> str:
        result = self.get(url)
        if not result.ok:
            return ""
        return result.text[:limit]
