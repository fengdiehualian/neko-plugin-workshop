"""内置图库访问：WebDAV 列目录与取图。

纯标准库实现（urllib），阻塞式函数，调用方请用 asyncio.to_thread 包装。
图库地址与凭据由插件主体内置注入，本模块不做任何配置解析。
"""

from __future__ import annotations

import base64
import urllib.error
import urllib.parse
import urllib.request

try:
    from .core import is_image
except ImportError:  # 顶级模块导入场景（pytest 收集等）
    from core import is_image

USER_AGENT = "neko-plugin-quote-extract/0.1"
DEFAULT_TIMEOUT = 20.0


class FetchError(Exception):
    """图库访问失败；message 中的敏感信息由 redact() 脱敏。"""


def _basic_auth(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def _request(url: str, *, method: str = "GET", headers: dict | None = None,
             timeout: float = DEFAULT_TIMEOUT) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise FetchError(f"图库响应异常：HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"网络错误：{e.reason}") from e
    except TimeoutError as e:
        raise FetchError("图库请求超时") from e


def auth_headers(cfg: dict) -> dict:
    return {
        "Authorization": _basic_auth(cfg.get("webdav_username", ""),
                                     cfg.get("webdav_password", ""))
    }


def _origin(cfg: dict) -> str:
    parsed = urllib.parse.urlparse(cfg.get("webdav_url", ""))
    return f"{parsed.scheme}://{parsed.netloc}"


def list_images(cfg: dict, folder: str) -> list[str]:
    """PROPFIND 列出图库目录，返回其中全部图片的服务器路径。"""
    base = (cfg.get("webdav_url") or "").rstrip("/")
    url = f"{base}{folder}"
    raw = _request(url, method="PROPFIND",
                   headers={**auth_headers(cfg), "Depth": "1"})
    try:
        from .core import parse_propfind
    except ImportError:
        from core import parse_propfind
    prefix = urllib.parse.unquote(urllib.parse.urlparse(url).path).rstrip("/")
    images: list[str] = []
    for href in parse_propfind(raw):
        path = urllib.parse.unquote(href)
        if not path.startswith(prefix + "/") or path.rstrip("/") == prefix:
            continue
        if is_image(path):
            images.append(path)
    return images


def fetch_image(cfg: dict, path: str) -> bytes:
    """按服务器路径取回图片字节。"""
    return _request(f"{_origin(cfg)}{urllib.parse.quote(path)}",
                    headers=auth_headers(cfg))


def redact(text: str, cfg: dict) -> str:
    """把错误信息中的地址与凭据替换为占位符，避免泄露给最终用户。"""
    for key in ("webdav_url", "webdav_username", "webdav_password"):
        value = cfg.get(key)
        if value:
            text = text.replace(str(value), "〔内置图库〕")
    return text
