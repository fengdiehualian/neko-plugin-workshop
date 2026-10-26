"""零依赖 HTTP 客户端。

插件运行在 N.E.K.O 自带的 Python 运行时里，不能假设 `httpx` / `requests` 一定存在，
所以这里只用标准库 `urllib`，并用 `asyncio.to_thread` 转成异步。

另外提供带退避的重试与多端点降级，翻译/天气这类公共免费接口经常偶发 429/5xx。
"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable, Optional

DEFAULT_UA = "N.E.K.O-Plugin/0.1.0 (+https://github.com/Project-N-E-K-O/N.E.K.O)"


class HttpError(RuntimeError):
    """网络层失败。`retryable` 表示调用方可以换端点或稍后重试。"""

    def __init__(self, message: str, *, status: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _sync_request(
    url: str,
    *,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 15.0,
) -> tuple[int, str]:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", DEFAULT_UA)
    req.add_header("Accept", "application/json, text/plain, */*")
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 仅访问固定 https 端点
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.status, resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # pragma: no cover - 读取错误响应体失败不影响主流程
            pass
        retryable = exc.code in {408, 425, 429, 500, 502, 503, 504}
        raise HttpError(f"HTTP {exc.code}: {body}", status=exc.code, retryable=retryable) from exc
    except urllib.error.URLError as exc:
        raise HttpError(f"网络不可达: {exc.reason}", retryable=True) from exc
    except socket.timeout as exc:
        raise HttpError("请求超时", retryable=True) from exc


async def _request(
    url: str,
    *,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 15.0,
) -> tuple[int, str]:
    return await asyncio.to_thread(
        _sync_request, url, method=method, data=data, headers=headers, timeout=timeout
    )


async def get_text(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 15.0,
    retries: int = 2,
    retry_delay: float = 0.8,
) -> str:
    """GET 并返回响应文本，失败按 `retries` 退避重试。"""
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(clean)}"

    last: Optional[HttpError] = None
    for attempt in range(max(1, retries + 1)):
        try:
            _status, body = await _request(url, headers=headers, timeout=timeout)
            return body
        except HttpError as exc:
            last = exc
            if not exc.retryable or attempt >= retries:
                break
            await asyncio.sleep(retry_delay * (2**attempt))
    raise last or HttpError("请求失败", retryable=True)


async def get_json(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 15.0,
    retries: int = 2,
) -> Any:
    """GET 并解析 JSON。"""
    text = await get_text(url, params=params, headers=headers, timeout=timeout, retries=retries)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HttpError(f"响应不是合法 JSON: {text[:200]}") from exc


async def post_json(
    url: str,
    payload: Any,
    *,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 15.0,
    retries: int = 2,
) -> Any:
    """POST JSON 并解析 JSON 响应。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})

    last: Optional[HttpError] = None
    for attempt in range(max(1, retries + 1)):
        try:
            _status, text = await _request(
                url, method="POST", data=body, headers=merged, timeout=timeout
            )
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise HttpError(f"响应不是合法 JSON: {text[:200]}") from exc
        except HttpError as exc:
            last = exc
            if not exc.retryable or attempt >= retries:
                break
            await asyncio.sleep(0.8 * (2**attempt))
    raise last or HttpError("请求失败", retryable=True)


async def first_success(
    attempts: Iterable[Callable[[], Any]],
) -> tuple[Any, Optional[Exception]]:
    """依次尝试多个协程工厂，返回第一个成功的结果。

    返回 `(结果, 最后一个异常)`；全部失败时结果为 None。
    这里故意不吞掉最后一个异常，方便把真实原因写进日志。
    """
    last_exc: Optional[Exception] = None
    for make in attempts:
        try:
            return await make(), None
        except Exception as exc:  # noqa: BLE001 - 降级链需要捕获任意失败
            last_exc = exc
            continue
    return None, last_exc
