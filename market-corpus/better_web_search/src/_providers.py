"""Keyless search/fetch backends.

Ordered by how well they survive a real desktop network:

* ``exa``      - hosted MCP gateway, no key, returns page text (search + read).
* ``anysearch``- hosted API, anonymous access allowed, returns snippets+content.
* ``bing``     - HTML scrape, reachable directly in mainland China.
* ``sogou``    - HTML scrape, direct, Chinese coverage.
* ``baidu``    - HTML scrape, direct, but IP-burst sensitive (安全验证).
* ``duckduckgo``- HTML scrape, needs a proxy in mainland China.
* ``searxng``  - your own instance; the only route nobody can rate-limit.

Network calls are synchronous (:mod:`_net`) and are run on worker threads by the
plugin so the event loop stays responsive.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import threading
import urllib.parse
import weakref
from typing import Any, Callable

from . import _net, _parsing
from ._net import HttpStatusCodeError
from ._resilience import (
    ApiKeyRejectedError,
    BlockedError,
    QuotaExhaustedError,
    SearchProviderError,
)

EXA_MCP_URL = "https://mcp.exa.ai/mcp"
ANYSEARCH_URL = "https://api.anysearch.com/v1/search"
BING_URL = "https://www.bing.com/search"
BAIDU_HOME = "https://www.baidu.com/"
BAIDU_URL = "https://www.baidu.com/s"
DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
SOGOU_URL = "https://www.sogou.com/web"

ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

_NOISE_MARKERS = (
    # Ads wrap their anchors rather than marking them, so these are matched
    # against the anchor's own class plus its enclosing tags. Matching the
    # *container* class only: a legitimate target URL may itself contain
    # "ad_provider", and dropping that would throw away real results.
    "result--ad",
    "data-tuiguang",
    "b_ad",
    "b_top",
    "ad_container",
)
# DuckDuckGo routes sponsored slots through its own ad endpoint rather than the
# uddg redirect, so it has to be matched on the target URL.
_AD_URL_MARKERS = ("duckduckgo.com/y.js", "/a/display", "bing.com/a/dynamic")


class Provider:
    """A search backend: how to reach it and what to look for in the HTML."""

    def __init__(
        self,
        name: str,
        *,
        anchor_markers: tuple[str, ...] = (),
        container_markers: tuple[str, ...] = (),
        url: str = "",
        headers: dict[str, str] | None = None,
        block_markers: tuple[str, ...] = (),
        unwrappers: tuple[Callable[[str], str], ...] = (),
        prefer_h2_h3: bool = False,
        needs_key: bool = False,
    ) -> None:
        self.name = name
        self.anchor_markers = anchor_markers
        self.container_markers = container_markers
        self.url = url
        self.headers = headers or {}
        self.block_markers = block_markers
        self.unwrappers = unwrappers
        self.prefer_h2_h3 = prefer_h2_h3
        self.needs_key = needs_key


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise


def _raw_query_value(href: str, name: str) -> str:
    """Read a query parameter without letting urlsplit percent-decode it first.

    DuckDuckGo encodes the destination URL once into ``uddg``; ``parse_qs`` would
    decode that too, so the explicit unquote below would become a second decode
    and silently rewrite targets containing a literal percent sign.
    """
    query = urllib.parse.urlsplit(href).query
    for part in query.split("&"):
        key, sep, value = part.partition("=")
        if sep and key == name:
            return value
    return ""


def unwrap_ddg(href: str) -> str:
    """``//duckduckgo.com/l/?uddg=<pct>&rut=`` -> the real destination."""
    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urllib.parse.urlsplit(href)
    except ValueError:
        return href
    if "duckduckgo.com" not in (parsed.hostname or ""):
        return href
    raw = _raw_query_value(href, "uddg")
    if raw:
        # Exactly one decode pass, so a target containing %25 survives as %25.
        destination = urllib.parse.unquote(raw)
        return _absolute(destination, href)
    return href


def unwrap_bing(href: str) -> str:
    """Bing wraps result links in ``/ck/a?...&u=a1<base64>``."""
    href = href.strip()
    if "/ck/a" not in href:
        return _absolute(href, BING_URL)
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
    except ValueError:
        return _absolute(href, BING_URL)
    encoded = (query.get("u") or [""])[0]
    if not encoded:
        return _absolute(href, BING_URL)
    candidate = encoded[2:] if encoded.startswith("a1") else encoded
    import base64

    try:
        padded = candidate + "=" * (-len(candidate) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "replace")
    except Exception:
        return _absolute(href, BING_URL)
    return _absolute(decoded.strip(), href)


def unwrap_sogou(href: str) -> str:
    """Keep only absolute http(s) targets; Sogou uses root-relative link paths."""
    absolute = _absolute(href, SOGOU_URL)
    return absolute if absolute.startswith(("http://", "https://")) else ""


def _absolute(href: str, base: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    try:
        return urllib.parse.urljoin(base, href)
    except ValueError:
        return href


_HTML_HEADERS = {
    "Accept": ACCEPT_HTML,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

PROVIDERS: dict[str, Provider] = {
    "bing": Provider(
        "bing",
        container_markers=("b_algo",),
        url=BING_URL,
        headers=_HTML_HEADERS,
        unwrappers=(unwrap_bing,),
        block_markers=("此验证码", "验证一下"),
        prefer_h2_h3=True,
    ),
    "baidu": Provider(
        "baidu",
        # "result" alone matches half the page chrome; c-container is the card.
        container_markers=("c-container", "result c-container", "c-container-top"),
        url=BAIDU_URL,
        headers=dict(_HTML_HEADERS, Referer=BAIDU_HOME),
        block_markers=("百度安全验证", "wappass.baidu.com"),
    ),
    "duckduckgo": Provider(
        "duckduckgo",
        anchor_markers=("result__a",),
        container_markers=("result", "web-result"),
        url=DDG_HTML_URL,
        headers={"Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"},
        unwrappers=(unwrap_ddg,),
        block_markers=("anomaly-modal", "anomaly.js"),
    ),
    "sogou": Provider(
        "sogou",
        anchor_markers=("vr-title", "result-title"),
        container_markers=("vr-title", "result"),
        url=SOGOU_URL,
        headers=dict(_HTML_HEADERS, Referer="https://www.sogou.com/"),
        unwrappers=(unwrap_sogou,),
        block_markers=("验证码", "antispider"),
        prefer_h2_h3=True,
    ),
}

# Cookie jars are process-global per engine so a warm session survives restarts.
_jar_lock = threading.Lock()
_jars: dict[str, http.cookiejar.CookieJar] = {}
_baidu_warmup_lock = threading.Lock()
_baidu_warmed_jars = weakref.WeakSet()


def _jar_for(name: str) -> http.cookiejar.CookieJar:
    with _jar_lock:
        jar = _jars.get(name)
        if jar is None:
            jar = http.cookiejar.CookieJar()
            _jars[name] = jar
        return jar


def _has_cookie(jar: http.cookiejar.CookieJar, name: str) -> bool:
    wanted = name.casefold()
    return any(cookie.name.casefold() == wanted for cookie in jar)


def _claim_baidu_warmup(jar: http.cookiejar.CookieJar) -> bool:
    """Claim one warmup per jar, unless BAIDUID has already arrived."""
    if _has_cookie(jar, "BAIDUID"):
        return False
    with _baidu_warmup_lock:
        if _has_cookie(jar, "BAIDUID") or jar in _baidu_warmed_jars:
            return False
        _baidu_warmed_jars.add(jar)
        return True


# ---------------------------------------------------------------------------
# hosted keyless JSON APIs
# ---------------------------------------------------------------------------


# Exa reports tool failures as HTTP 200 + ``result.isError``, so the only signal
# left is free text. Statuses must be matched as standalone numbers: an
# unrelated message such as "1401 results" or "error at 4018" must not be
# misread as "your key is invalid", which would make the UI blame the user's
# credential for a server-side problem.
_KEY_STATUS_RE = re.compile(r"(?<!\d)40[13](?!\d)")
_QUOTA_STATUS_RE = re.compile(r"(?<!\d)402(?!\d)")
_KEY_TEXT_MARKERS = ("invalid api key", "invalid api-key", "api key is invalid",
                     "unauthorized", "forbidden")
_QUOTA_TEXT_MARKERS = ("quota", "exhausted", "payment required", "credit")


def _mcp_error(message: str, *, status: int | None = None,
                api_key: str = "", retry_after_seconds: float | None = None,
                http_status: bool = False) -> None:
    """Raise the stable Exa error type for an HTTP or MCP error."""
    text = str(message or "Exa 返回错误")
    lowered = text.casefold()
    if status in {401, 403}:
        if http_status and not api_key:
            raise BlockedError(f"Exa 拒绝匿名访问（{status}）", retry_after_seconds)
        raise ApiKeyRejectedError(text[:200])
    if status == 429:
        if not api_key:
            raise BlockedError("Exa 免配额已用完（429）", retry_after_seconds)
        # Exa answers 402 when credits are spent and 429 when we only asked too
        # fast (docs/reference/billing). A keyed 429 that reads as "out of
        # credits" lets one fast double-click mark a healthy key as dead.
        raise BlockedError("Exa 请求过于频繁（429）", retry_after_seconds)
    if status == 402 or _QUOTA_STATUS_RE.search(lowered) or any(
        marker in lowered for marker in _QUOTA_TEXT_MARKERS
    ):
        raise QuotaExhaustedError(text[:200], retry_after_seconds)
    if _KEY_STATUS_RE.search(lowered) or any(
        marker in lowered for marker in _KEY_TEXT_MARKERS
    ):
        raise ApiKeyRejectedError(text[:200])
    raise SearchProviderError(text[:200])


def _mcp_call(tool: str, arguments: dict[str, Any], *, timeout: float, policy: str,
              proxy_url: str, api_key: str = "") -> dict[str, Any]:
    key = (api_key or "").strip()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "x-exa-source": "neko-better-web-search",
    }
    if key:
        headers["x-api-key"] = key
    try:
        response = _net.post(
            f"{EXA_MCP_URL}?tools={tool}",
            json_body={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments}},
            headers=headers, policy=policy, proxy_url=proxy_url, timeout=timeout,
        )
    except _net.HttpStatusCodeError as error:
        _mcp_error(
            str(error), status=error.status, api_key=key,
            retry_after_seconds=error.retry_after_seconds, http_status=True,
        )
        raise AssertionError("_mcp_error always raises")  # pragma: no cover

    status = _as_int(response.status)
    if status >= 400:
        _mcp_error(
            f"Exa 请求失败（HTTP {status}）", status=status, api_key=key,
            retry_after_seconds=getattr(response, "retry_after_seconds", None), http_status=True,
        )
        raise AssertionError("_mcp_error always raises")  # pragma: no cover

    text = _parsing.decode_body(response.body)
    payload: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            candidate = json.loads(line[5:].strip())
        except (ValueError, TypeError):
            continue
        if isinstance(candidate, dict) and (candidate.get("result") or candidate.get("error")):
            payload = candidate
            break
    if payload is None:
        try:
            candidate = json.loads(text)
            payload = candidate if isinstance(candidate, dict) else None
        except (ValueError, TypeError):
            payload = None
    if payload is None:
        raise SearchProviderError(f"Exa 返回了无法解析的响应: {text[:200]}")

    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "Exa 返回错误")
        if error.get("code") == -32602:
            raise SearchProviderError(f"Exa 参数无效: {message[:160]}")
        _mcp_error(message, api_key=key)
        raise AssertionError("_mcp_error always raises")  # pragma: no cover

    result = payload.get("result")
    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content") or []
        message = "\n".join(
            str(item.get("text") or "") for item in content if isinstance(item, dict)
        ).strip()
        _mcp_error(message or "Exa 返回错误", api_key=key)
        raise AssertionError("_mcp_error always raises")  # pragma: no cover
    return result if isinstance(result, dict) else {}


def _json_results(raw: dict[str, Any], limit: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = _parsing.collapse(item.get("url") or item.get("id") or "")
        if not url.startswith(("http://", "https://")):
            continue
        title = _parsing.sanitize_text(item.get("title"), _parsing.MAX_TITLE_LEN)
        # snippet and content are different fields with different jobs: AnySearch
        # returns a short `snippet` plus the full page `content`, and collapsing
        # them onto one value throws the useful half away.
        content = str(item.get("content") or item.get("text") or item.get("snippet") or "")
        snippet_source = str(item.get("snippet") or item.get("text") or item.get("content") or "")
        snippet = _parsing.sanitize_text(snippet_source, _parsing.MAX_SNIPPET_LEN)
        if not title:
            title = snippet.split("\n", 1)[0][: _parsing.MAX_TITLE_LEN] or url
        out.append({"title": title, "url": url, "snippet": snippet,
                    "content": _parsing.sanitize_text(content, 0),
                    "published": _parsing.collapse(item.get("publishedDate"))})
        if len(out) >= limit:
            break
    return out


_BLOCK_TEXT_SPLIT = re.compile(r"(?m)^Title:\s")


def _text_results(blob: str, limit: int) -> list[dict[str, str]]:
    """Parse Exa's plain-text form: ``Title: … \\n URL: … \\n Highlights: …``."""
    out: list[dict[str, str]] = []
    for chunk in _BLOCK_TEXT_SPLIT.split(blob):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields: dict[str, str] = {}
        body_lines: list[str] = []
        for line in chunk.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip().casefold() in {"url", "title", "published", "author"}:
                fields[key.strip().casefold()] = value.strip()
            else:
                body_lines.append(line)
        url = fields.get("url", "")
        if not url.startswith(("http://", "https://")):
            continue
        body = _parsing.collapse("\n".join(body_lines).replace("...", " "))
        title = _parsing.sanitize_text(fields.get("title"), _parsing.MAX_TITLE_LEN) or body[:60]
        out.append({"title": title, "url": url,
                    "snippet": _parsing.sanitize_text(body, _parsing.MAX_SNIPPET_LEN),
                    "content": body, "published": fields.get("published", "")})
        if len(out) >= limit:
            break
    return out


def _exa_content(result: dict[str, Any]) -> str:
    content = result.get("content") or []
    return "\n".join(
        str(item.get("text") or "") for item in content if isinstance(item, dict)
    ).strip()


def _exa_parse_error(blob: str) -> SearchProviderError:
    detail = blob[:200].strip()
    suffix = f": {detail}" if detail else ""
    return SearchProviderError(f"Exa 未返回可解析结果{suffix}")


def _search_exa_tool(query: str, limit: int, *, timeout: float, policy: str,
                     proxy_url: str, api_key: str, tool: str,
                     live_crawl: bool) -> list[dict[str, str]]:
    arguments: dict[str, Any] = {"query": query, "numResults": max(1, min(_as_int(limit), 20))}
    if tool == "web_search_advanced_exa":
        arguments["text"] = True
        if live_crawl:
            arguments["liveCrawl"] = True
    result = _mcp_call(tool, arguments, timeout=timeout, policy=policy,
                       proxy_url=proxy_url, api_key=api_key)
    blob = _exa_content(result)
    if tool == "web_search_exa":
        parsed = _text_results(blob, limit)
        if not parsed:
            raise _exa_parse_error(blob)
        return parsed

    collected: list[dict[str, str]] = []
    for item in (result.get("content") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "")
        try:
            envelope = json.loads(text)
        except (ValueError, TypeError):
            collected.extend(_text_results(text, limit))
            continue
        if isinstance(envelope, dict) and isinstance(envelope.get("results"), list):
            collected.extend(_json_results(envelope, limit))
    if not collected:
        raise _exa_parse_error(blob)
    return collected[: max(1, _as_int(limit))]


def search_exa(query: str, limit: int, *, timeout: float, policy: str, proxy_url: str,
               live_crawl: bool = False, api_key: str = "", tool: str = "auto") -> list[dict[str, str]]:
    """Search Exa MCP, selecting the keyless fast path or keyed advanced path."""
    key = (api_key or "").strip()
    if tool not in {"auto", "advanced", "simple"}:
        raise ValueError("Exa tool must be one of: auto, advanced, simple")
    # ``auto`` is the fast path whether or not a key is present. Measured on the
    # live endpoint: web_search_exa 0.9-3.3s, web_search_advanced_exa 3.7-11.7s
    # *regardless of key* (advanced crawls pages itself, that is the variance)
    # and 11.7s already exceeds the plugin's 12s per-backend budget. Keying buys
    # quota headroom, not speed, so auto must not trade a guaranteed timeout for
    # richer text. ``advanced`` stays available as an explicit choice.
    selected = "simple" if tool == "auto" else tool
    selected_tool = "web_search_advanced_exa" if selected == "advanced" else "web_search_exa"

    try:
        return _search_exa_tool(
            query, limit, timeout=timeout, policy=policy, proxy_url=proxy_url,
            api_key=key, tool=selected_tool, live_crawl=live_crawl,
        )
    except (ApiKeyRejectedError, QuotaExhaustedError):
        raise
    except BlockedError:
        raise
    except SearchProviderError:
        if selected_tool != "web_search_advanced_exa":
            raise
        return _search_exa_tool(
            query, limit, timeout=timeout, policy=policy, proxy_url=proxy_url,
            api_key=key, tool="web_search_exa", live_crawl=False,
        )


def search_anysearch(query: str, limit: int, *, timeout: float, policy: str, proxy_url: str,
                     api_key: str = "", zone: str = "") -> list[dict[str, str]]:
    """AnySearch allows anonymous access; a key only raises the limits."""
    payload: dict[str, Any] = {"query": query, "max_results": max(1, min(_as_int(limit), 20))}
    if zone in {"cn", "intl"}:
        payload["zone"] = zone
    headers = {"Content-Type": "application/json"}
    key = (api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        response = _net.post(ANYSEARCH_URL, json_body=payload, headers=headers,
                             policy=policy, proxy_url=proxy_url, timeout=timeout)
    # pi-lens-ignore: no-boolean-in-except
    except HttpStatusCodeError as error:
        if error.status in {401, 403} and key:
            # Its own type, not a generic provider failure: the panel must be able
            # to say "this key is not valid" without also blaming the network.
            raise ApiKeyRejectedError("AnySearch API Key 无效或已失效") from error
        if error.status == 429:
            raise BlockedError("AnySearch 请求受限（429）", error.retry_after_seconds) from error
        raise SearchProviderError(f"AnySearch 请求失败（HTTP {error.status}）") from error

    try:
        envelope = json.loads(_parsing.decode_body(response.body))
    except ValueError as error:
        raise SearchProviderError("AnySearch 返回了无效 JSON") from error
    if not isinstance(envelope, dict) or envelope.get("code") != 0:
        raise SearchProviderError("AnySearch 返回了非成功状态")
    data = envelope.get("data")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise SearchProviderError("AnySearch 缺少 results 字段")
    return _json_results({"results": results}, limit)


# ---------------------------------------------------------------------------
# HTML scraping engines
# ---------------------------------------------------------------------------


def _check_block(provider: Provider, markup: str, status: int) -> None:
    if status in {403, 429}:
        raise BlockedError(f"{provider.name} 请求受限（HTTP {status}）")
    if status == 202:
        raise BlockedError(f"{provider.name} 返回反自动化验证页（HTTP 202）")
    lowered = markup.casefold()
    head = lowered[:8000]
    for marker in provider.block_markers:
        if not marker:
            continue
        needle = marker.casefold()
        # Challenge pages put the notice in a big header block, so a page that is
        # mostly the challenge (short document) is matched anywhere in the body.
        if needle in head or (needle in lowered and len(lowered) < 60_000):
            raise BlockedError(f"{provider.name} 返回了人机验证页")
    if _parsing.looks_like_redirect_shell(markup):
        raise BlockedError(f"{provider.name} 只返回了跳转壳页")


def _has_no_results_marker(markup: str) -> bool:
    lowered = markup[:200000].casefold()
    return bool(
        "no-results" in lowered
        or "没有找到" in lowered
        or "抱歉，没有找到" in lowered
        or 'class="b_no"' in lowered
    )


def search_html(provider: Provider, query: str, limit: int, *, timeout: float,
                policy: str, proxy_url: str, method: str = "GET",
                warmup: bool = True) -> list[dict[str, str]]:
    """Scrape one HTML engine and return normalized results."""
    jar = _jar_for(provider.name)
    if provider.name == "baidu":
        params: dict[str, str] = {
            "wd": query, "rn": str(max(10, min(_as_int(limit), 50))), "ie": "utf-8"
        }
    elif provider.name == "bing":
        params = {"q": query, "count": str(max(10, min(_as_int(limit), 30))), "setlang": "zh-hans"}
    elif provider.name == "sogou":
        params = {"query": query, "num": str(max(10, min(_as_int(limit), 20)))}
    elif provider.name == "duckduckgo":
        params = {"q": query, "kl": "wt-wt"}
    else:  # pragma: no cover - defensive
        params = {"q": query}

    if provider.name == "baidu" and warmup and _claim_baidu_warmup(jar):
        # A bare /s request without a session cookie almost always lands on the
        # 安全验证 page, so collect BAIDUID first. Failures here are not fatal:
        # the search request below reports the real outcome.
        try:
            _net.get(BAIDU_HOME, headers=provider.headers, policy=policy,
                     proxy_url=proxy_url, timeout=min(timeout, 6.0), cookie_jar=jar)
        except _net.NetworkError:
            pass

    as_post = method.upper() == "POST"
    headers = dict(provider.headers)
    if as_post:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        response = _net.request(
            "POST" if as_post else "GET", provider.url,
            params=None if as_post else params,
            data=urllib.parse.urlencode(params).encode("utf-8") if as_post else None,
            headers=headers, policy=policy, proxy_url=proxy_url, timeout=timeout,
            cookie_jar=jar,
        )
    except _net.HttpStatusCodeError as error:
        _raise_status(provider, error)
        return []
    except _net.NetworkError as error:
        raise SearchProviderError(f"{provider.name} 无法连接（{error}）") from error

    markup = _parsing.decode_body(response.body, response.header("content-type"))
    _check_block(provider, markup, response.status)

    raw = _parsing.extract_results(
        markup,
        anchor_markers=provider.anchor_markers,
        container_markers=provider.container_markers,
        skip_markers=_NOISE_MARKERS,
        prefer_h2_h3=provider.prefer_h2_h3,
        limit=max(_as_int(limit) * 2, _as_int(limit)),
    )
    results: list[tuple[int, int, str, dict[str, str]]] = []
    seen: set[str] = set()
    for rank, item in enumerate(raw):
        url = item["url"]
        for unwrap in provider.unwrappers:
            url = unwrap(url)
            if not url:
                break
        if not url.startswith(("http://", "https://")):
            continue
        if _is_unresolved_redirect(url) or _is_engine_results_page(provider, url):
            # A wrapper we could not open, or a link back to the engine's own
            # results page, is worse than no result: the model would fetch it
            # and get a search page instead of a document.
            continue
        if any(marker in url.casefold() for marker in _AD_URL_MARKERS):
            continue
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append((_as_int(item.get("score", 0)), rank, url, {
            "title": item["title"], "url": url, "snippet": item["snippet"],
            "content": "", "published": "",
        }))

    # Real results outrank nav chrome: keep the parser's marker/container score,
    # then the original document order as the tie-break.
    results.sort(key=lambda entry: (-entry[0], entry[1]))
    picked = [entry[3] for entry in results if entry[0] >= 20][: _as_int(limit)]

    if not picked and not _has_no_results_marker(markup):
        raise SearchProviderError(f"{provider.name} 未返回可解析结果")
    return picked


def _is_unresolved_redirect(url: str) -> bool:
    """True for wrapper links the fetch path cannot turn into a document.

    Baidu's ``/link?url=`` is deliberately *not* listed. Measured against the live
    service after the BAIDUID warm-up, it answers ``302`` with a real ``Location``
    (e.g. ``space.bilibili.com/...``), and ``_net.request`` follows redirects, so
    dropping it threw away every single baidu card -- the backend parsed 8 results
    with scores of 75-85 and then reported "no parseable results". A wrapper we
    can open is a result; the JS-shell case is handled separately by
    :func:`_check_block`.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return True
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = parsed.path.casefold()
    if host.endswith("duckduckgo.com") and path.startswith("/l/"):
        return True
    if host.endswith("bing.com") and ("/ck/a" in path or path.startswith("/search")):
        return True
    if host.endswith("sogou.com") and path.startswith("/link"):
        return True
    return False


# Landing on the engine's own results page is nav chrome, not a result. Note the
# distinction from a result *hosted by* the engine: baike.baidu.com and
# zhuanlan.zhihu.com are legitimate targets, /s?wd=... is not.
_ENGINE_RESULTS_PAGES = {
    "baidu": (("baidu.com",), ("/s", "/search")),
    "bing": (("bing.com", "msn.com"), ("/search", "/ck/")),
    "sogou": (("sogou.com",), ("/web", "/link")),
    "duckduckgo": (("duckduckgo.com",), ("/html", "/lite", "/search", "/l/")),
}


def _is_engine_results_page(provider: Provider, url: str) -> bool:
    config = _ENGINE_RESULTS_PAGES.get(provider.name)
    if config is None:
        return False
    domains, paths = config
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return True
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if not any(host == domain or host.endswith("." + domain) for domain in domains):
        return False
    path = parsed.path.casefold()
    if path in {"", "/"}:
        # The engine's own homepage is chrome; `/item/n/1915` is real content.
        return True
    return any(marker in path for marker in paths)


def _raise_status(provider: Provider, error: _net.HttpStatusCodeError) -> None:
    if error.status in {403, 429, 202}:
        raise BlockedError(f"{provider.name} 请求受限（HTTP {error.status}）",
                           error.retry_after_seconds)
    raise SearchProviderError(f"{provider.name} 请求失败（HTTP {error.status}）")


def search_searxng(query: str, limit: int, *, base_url: str, timeout: float, policy: str,
                   proxy_url: str) -> list[dict[str, str]]:
    """Query a SearXNG instance. With your own instance there is no third party."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise SearchProviderError("未配置 searxng_base_url")
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    try:
        parsed = urllib.parse.urlsplit(base)
    except ValueError as error:
        raise SearchProviderError("searxng_base_url 无效") from error
    if parsed.scheme != "https" and (parsed.hostname or "") not in {
        "localhost", "127.0.0.1", "::1", "0.0.0.0"
    }:
        raise SearchProviderError("远程 SearXNG 必须使用 https，避免搜索词被明文转发")
    try:
        response = _net.get(f"{base}/search", params={"q": query, "format": "json"},
                            headers={"Accept": "application/json"},
                            policy=policy, proxy_url=proxy_url, timeout=timeout)
    except _net.HttpStatusCodeError as error:
        if error.status in {403, 429}:
            raise BlockedError(f"SearXNG 拒绝访问（HTTP {error.status}）",
                               error.retry_after_seconds) from error
        raise SearchProviderError(f"SearXNG 请求失败（HTTP {error.status}）") from error
    except _net.NetworkError as error:
        raise SearchProviderError(f"SearXNG 无法连接（{error}）") from error
    try:
        payload = json.loads(_parsing.decode_body(response.body))
    except (ValueError, TypeError) as error:
        raise SearchProviderError(
            "SearXNG 返回了无效 JSON（多数公共实例关闭了 format=json，请自建实例）") from error
    if not isinstance(payload, dict):
        raise SearchProviderError("SearXNG 返回结构无效")
    results = payload.get("results")
    if not isinstance(results, list):
        raise SearchProviderError("SearXNG 缺少 results 字段")
    return _json_results({"results": [
        {"url": item.get("url"), "title": item.get("title"),
         "text": item.get("content") or ""}
        for item in results if isinstance(item, dict)
    ]}, limit)


# ---------------------------------------------------------------------------
# page reading
# ---------------------------------------------------------------------------


def fetch_direct(url: str, *, timeout: float, policy: str, proxy_url: str,
                 max_chars: int) -> dict[str, str]:
    """Fetch and linearize a page locally (no third party sees the URL)."""
    response = _net.get(url, headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"},
                        policy=policy, proxy_url=proxy_url, timeout=timeout)
    if response.status in {401, 403}:
        raise BlockedError(f"目标站点拒绝访问（HTTP {response.status}）")
    if response.status >= 400:
        raise SearchProviderError(f"目标站点返回 HTTP {response.status}")

    content_type = response.content_type
    raw = response.body
    if content_type == "application/pdf":
        raise SearchProviderError("暂不支持 PDF 正文抽取，请改用普通网页链接")
    if content_type in {"application/json", "text/json"}:
        text = _parsing.decode_body(raw, "application/json")
        return {"title": url, "content": _parsing.sanitize_text(text)[:max_chars],
               "final_url": response.url, "mode": "json"}
    if content_type.startswith("text/") and "html" not in content_type:
        text = _parsing.decode_body(raw, content_type)
        heading = text.splitlines()[0].strip() if text.strip() else ""
        return {"title": _parsing.sanitize_text(heading, _parsing.MAX_TITLE_LEN) or url,
               "content": _parsing.sanitize_text(text)[:max_chars],
               "final_url": response.url, "mode": "text"}

    markup = _parsing.decode_body(raw, response.header("content-type"))
    title, text = _parsing.readable_text(markup, max_chars)
    if len(text) < 80:
        raise SearchProviderError("页面正文过短，可能是需要 JavaScript 的站点")
    return {"title": title or url, "content": text, "final_url": response.url, "mode": "direct"}


def fetch_exa(url: str, *, timeout: float, policy: str, proxy_url: str,
              max_chars: int, api_key: str = "") -> dict[str, str]:
    """Ask Exa's crawler for readable text — works when the site blocks plain HTTP."""
    result = _mcp_call("web_fetch_exa", {"urls": [url], "maxCharacters": max_chars},
                       timeout=timeout, policy=policy, proxy_url=proxy_url,
                       api_key=api_key)
    content = result.get("content") or []
    blob = "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
    if not blob.strip():
        raise SearchProviderError("Exa 未返回正文")
    first_line, _, remainder = blob.partition("\n")
    title = first_line.strip("# ").strip() or url
    body = remainder.strip() or blob
    return {"title": _parsing.sanitize_text(title, _parsing.MAX_TITLE_LEN),
            "content": _parsing.sanitize_text(body)[:max_chars],
            "final_url": url, "mode": "exa"}
