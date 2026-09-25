"""
Web Searching Plugin — 联网搜索 v1.6.0

独立开发的联网搜索插件，让猫娘具备联网搜索能力。
注册为 @llm_tool，猫娘可在遇到知识盲区时主动调用。

搜索策略（按查询语言智能选择）：
  - 中文查询：知乎 → 百度 → 搜狗 → Bing → DuckDuckGo
  - 英文查询：DuckDuckGo → Bing → 百度
  - 知识层（并行）：DDG Instant Answer API + Wikipedia API

前端 UI：白蓝粉渐变，樱花飘落动画，分区展示搜索结果。
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, unquote

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]
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

# ─── 常量 ──────────────────────────────────────────────────────────────────

_UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# 知识层 API
_DDG_IA_URL = "https://api.duckduckgo.com/"
_WIKI_API_URL = "https://zh.wikipedia.org/w/api.php"
_WIKI_API_URL_EN = "https://en.wikipedia.org/w/api.php"

# Web 搜索端点
_BAIDU_URL = "https://www.baidu.com/s"
_SOGOU_URL = "https://www.sogou.com/web"
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_BING_URL = "https://www.bing.com/search"

# Jina AI — 免费搜索+阅读器（无需 API key，支持 JS 渲染）
_JINA_SEARCH_URL = "https://s.jina.ai/"
_JINA_READER_URL = "https://r.jina.ai/"

# 知乎搜索 API（官方开放平台）
_ZHIHU_SEARCH_URL = "https://developer.zhihu.com/api/v1/content/zhihu_search"
_ZHIHU_API_TOKEN = "4c29ac01ee6e73575bf03a895d5a8725770ccfa8"

# 中文 unicode 范围
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')

# 攻略/指南类关键词（搜索相关性加分）
_GUIDE_KEYWORDS = frozenset({
    "攻略", "指南", "教程", "详解", "解析", "评测", "测评",
    "guide", "tutorial", "walkthrough", "tips", "build",
    "技能", "培养", "搭配", "阵容", "输出", "辅助", "副c", "主c",
    "定位", "强度", "排行", "推荐", "用法", "解析",
})


def _is_chinese_query(query: str) -> bool:
    """检测查询是否包含中文字符。"""
    return bool(_CJK_RE.search(query))


# ─── HTTP 客户端辅助 ─────────────────────────────────────────────────────────

async def _fetch(url: str, params: dict, headers: dict, timeout: float,
                 method: str = "GET", data: Optional[dict] = None) -> Optional[httpx.Response]:
    """统一的 HTTP 请求封装"""
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers,
        ) as client:
            if method == "POST":
                resp = await client.post(url, params=params, data=data)
            else:
                resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp
    except Exception:
        return None


# ─── 策略 1: DuckDuckGo Instant Answer API ──────────────────────────────────

async def _ddg_instant_answer(query: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """DuckDuckGo Instant Answer API - 结构化即时答案（无需抓取）"""
    params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
    resp = await _fetch(
        _DDG_IA_URL, params,
        {"User-Agent": _UA_PC, "Accept": "application/json"},
        timeout,
    )
    if not resp:
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    abstract = (data.get("AbstractText") or "").strip()
    definition = (data.get("Definition") or "").strip()
    related_raw = data.get("RelatedTopics", [])[:5]

    related: List[Dict[str, str]] = []
    for topic in related_raw:
        if isinstance(topic, dict) and topic.get("Text"):
            related.append({
                "text": topic["Text"][:200],
                "url": topic.get("FirstURL", ""),
            })

    if not (abstract or definition or related):
        return None

    return {
        "heading": (data.get("Heading") or query).strip(),
        "abstract": abstract,
        "definition": definition,
        "abstract_source": data.get("AbstractSource", ""),
        "abstract_url": data.get("AbstractURL", ""),
        "related_topics": related,
    }


# ─── 策略 2: Wikipedia API ─────────────────────────────────────────────────

async def _wikipedia_summary(query: str, timeout: float = 10.0) -> Optional[Dict[str, str]]:
    """Wikipedia API - 百科知识增强（中英文双语）"""
    is_zh = _is_chinese_query(query)
    api_urls = [(_WIKI_API_URL, "zh"), (_WIKI_API_URL_EN, "en")] if is_zh else [
        (_WIKI_API_URL_EN, "en"), (_WIKI_API_URL, "zh")]

    for api_url, lang in api_urls:
        params = {
            "action": "query", "format": "json", "prop": "extracts",
            "exintro": "1", "explaintext": "1", "titles": query,
            "redirects": "1", "exsentences": "5",
        }
        resp = await _fetch(
            api_url, params,
            {"User-Agent": _UA_PC, "Accept": "application/json"},
            timeout,
        )
        if not resp:
            continue
        try:
            data = resp.json()
        except Exception:
            continue

        for page_id, page in data.get("query", {}).get("pages", {}).items():
            if page_id == "-1":
                continue
            extract = (page.get("extract") or "").strip()
            if extract and len(extract) > 20:
                title = page.get("title", query)
                return {
                    "title": title,
                    "summary": extract,
                    "url": f"https://{lang}.wikipedia.org/wiki/{quote_plus(title)}",
                    "lang": lang,
                }
    return None


# ─── 策略 3: 百度搜索（中文最强）────────────────────────────────────────────

async def _search_baidu(query: str, max_results: int = 8, timeout: float = 15.0) -> List[Dict[str, str]]:
    """百度搜索 - 中文查询首选"""
    headers = {
        "User-Agent": _UA_PC,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.baidu.com/",
        "Accept-Encoding": "gzip, deflate",
    }
    params = {"wd": query, "rn": str(min(max_results, 50))}

    resp = await _fetch(_BAIDU_URL, params, headers, timeout)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict[str, str]] = []

    # 多选择器适配
    containers = soup.select("div.result, div.c-container, div.result-op, div[class*='result']")
    for item in containers:
        # 跳过广告
        if "result--ad" in str(item.get("class", [])):
            continue
        # 找标题和链接
        link = item.find("h3")
        if link:
            link = link.find("a", href=True)
        if not link:
            link = item.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href = str(link.get("href", ""))
        if not title or not href:
            continue

        # 摘要 - 多选择器
        snippet = ""
        for sel in [".c-abstract", ".content-right_8Zs40", ".c-span-last",
                    ".c-abstract-l", ".c-color-text", "span.c-font-normal",
                    "div[class*='abstract']", "div[class*='desc']"]:
            sn = item.select_one(sel)
            if sn:
                snippet = sn.get_text(strip=True)
                break

        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


# ─── 策略 4: 搜狗搜索（中文 fallback）───────────────────────────────────────

async def _search_sogou(query: str, max_results: int = 8, timeout: float = 15.0) -> List[Dict[str, str]]:
    """搜狗搜索 - 中文查询 fallback"""
    headers = {
        "User-Agent": _UA_PC,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.sogou.com/",
    }
    params = {"query": query}

    resp = await _fetch(_SOGOU_URL, params, headers, timeout)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict[str, str]] = []

    # 搜狗结果容器
    containers = soup.select("div.vrwrap, div.rb, div.result, div.results div[lang]")
    for item in containers:
        link = item.find("a", href=True)
        if not link:
            title_tag = item.find(["h3", "h4", "a.title"])
            if title_tag:
                link = title_tag.find("a", href=True) if title_tag.name != "a" else title_tag
        if not link:
            continue

        title = link.get_text(strip=True)
        href = str(link.get("href", ""))
        if not title or not href:
            continue

        snippet = ""
        for sel in [".str_info", ".rb-info", ".vr-info", ".abstract",
                    "p[class*='info']", ".desc", "div[class*='content']"]:
            sn = item.select_one(sel)
            if sn:
                snippet = sn.get_text(strip=True)
                break

        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


# ─── 策略 5: DuckDuckGo HTML ────────────────────────────────────────────────

async def _search_ddg_html(query: str, max_results: int = 8, timeout: float = 15.0) -> List[Dict[str, str]]:
    """DuckDuckGo HTML"""
    headers = {
        "User-Agent": _UA_PC,
        "Accept": "text/html",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Referer": "https://duckduckgo.com/",
    }
    data = {"q": query, "kl": "wt-wt"}

    resp = await _fetch(_DDG_HTML_URL, {}, headers, timeout, method="POST", data=data)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict[str, str]] = []

    for link in soup.select("a.result__a"):
        parent = link.find_parent("div", class_=re.compile(r"result--ad"))
        if parent:
            continue

        title = link.get_text(strip=True)
        href = str(link.get("href", ""))
        if "uddg=" in href:
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                href = unquote(m.group(1))
        if not title or not href or "duckduckgo.com/y.js" in href:
            continue

        snippet = ""
        container = link.find_parent("div", class_="result")
        if container:
            sn = container.select_one("a.result__snippet")
            if sn:
                snippet = sn.get_text(strip=True)

        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


# ─── 策略 6: Bing ───────────────────────────────────────────────────────────

async def _search_bing(query: str, max_results: int = 8, timeout: float = 15.0) -> List[Dict[str, str]]:
    """Bing 搜索"""
    headers = {
        "User-Agent": _UA_PC,
        "Accept": "text/html",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    params = {"q": query, "count": str(min(max_results, 50))}

    resp = await _fetch(_BING_URL, params, headers, timeout)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict[str, str]] = []

    for item in soup.select("li.b_algo"):
        link = item.find("a", href=True)
        if not link:
            continue
        title = link.get_text(strip=True)
        url = str(link.get("href", ""))
        if not title or not url:
            continue

        snippet = ""
        sn = item.select_one("p, div.b_caption > p, div.b_caption p")
        if sn:
            snippet = sn.get_text(strip=True)

        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


# ─── Jina AI 搜索 + 阅读器（免费，支持JS渲染，无需API key）──────────────────

async def _jina_search(query: str, max_results: int = 8, timeout: float = 20.0) -> List[Dict[str, str]]:
    """Jina Search API — 搜索并自动提取页面内容，返回 Markdown 格式结果。

    优势：
    - 自带内容提取（不需要二次抓取页面）
    - 支持 JS 渲染站点
    - 返回干净的结构化文本
    - 免费无需 API key（~20 RPM）
    """
    headers = {
        "User-Agent": _UA_PC,
        "Accept": "text/plain",
    }
    params = {"q": query}

    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers,
        ) as client:
            resp = await client.get(_JINA_SEARCH_URL, params=params)
            resp.raise_for_status()
            text = resp.text
    except Exception:
        return []

    # Jina 返回 Markdown 格式，解析标题/URL/内容
    results: List[Dict[str, str]] = []
    blocks = re.split(r'^Title:\s*', text, flags=re.MULTILINE)

    for block in blocks[1:]:  # 跳过第一个（空或前导文本）
        lines = block.strip().split('\n')
        if not lines:
            continue

        title = lines[0].strip()
        url = ""
        content_lines = []
        for line in lines[1:]:
            line = line.strip()
            if line.startswith("URL Source:") or line.startswith("URL:"):
                url = line.split(":", 1)[1].strip()
            elif line.startswith("Markdown Content:"):
                continue
            elif line:
                content_lines.append(line)

        content = "\n".join(content_lines).strip()
        # 截取内容预览
        snippet = content[:300] if content else ""

        if title and url:
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "page_content": content[:800] if content else "",
            })

        if len(results) >= max_results:
            break

    return results


async def _jina_reader(url: str, timeout: float = 15.0) -> Dict[str, str]:
    """Jina Reader API — 抓取任意 URL 的页面内容，支持 JS 渲染。

    用法：在 URL 前加 https://r.jina.ai/
    返回干净的 Markdown 文本，自动处理 JS 渲染页面。
    """
    jina_url = f"{_JINA_READER_URL}{url}"
    headers = {
        "User-Agent": _UA_PC,
        "Accept": "text/plain",
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers,
        ) as client:
            resp = await client.get(jina_url)
            resp.raise_for_status()
            content = resp.text
    except Exception as e:
        return {"url": url, "final_url": url, "title": "",
                "content": "", "content_length": 0, "error": str(e)}

    # 尝试提取标题（Jina 通常在第一行返回 Title: ...）
    title = ""
    content_lines = content.split("\n")
    if content_lines and content_lines[0].startswith("Title:"):
        title = content_lines[0][6:].strip()
        content = "\n".join(content_lines[1:]).strip()

    # 移除 URL Source 行
    content = re.sub(r'^URL Source:.*$', '', content, flags=re.MULTILINE).strip()
    content = re.sub(r'^Markdown Content:', '', content, flags=re.MULTILINE).strip()

    # 截取（最多 2000 字符）
    if len(content) > 2000:
        content = content[:2000] + "..."

    return {
        "url": url,
        "final_url": url,
        "title": title,
        "content": content,
        "content_length": len(content),
        "error": "",
    }


# ─── 策略 7: 知乎搜索 API（中文查询首选）────────────────────────────────────

async def _search_zhihu(query: str, max_results: int = 5, timeout: float = 15.0) -> List[Dict[str, str]]:
    """知乎搜索 API - 中文查询首选渠道
    
    使用知乎官方开放平台 API，返回结构化数据。
    优势：内容质量高、专业性强、响应稳定。
    """
    headers = {
        "Authorization": f"Bearer {_ZHIHU_API_TOKEN}",
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
        "User-Agent": _UA_PC,
        "Accept": "application/json",
    }
    params = {
        "Query": query,
        "Count": min(max_results, 10),  # 知乎 API 最大支持 10
    }

    resp = await _fetch(_ZHIHU_SEARCH_URL, params, headers, timeout)
    if not resp:
        return []

    try:
        data = resp.json()
    except Exception:
        return []

    # 检查响应状态
    if data.get("Code") != 0:
        return []

    items = data.get("Data", {}).get("Items", [])
    results: List[Dict[str, str]] = []

    for item in items:
        title = item.get("Title", "").strip()
        url = item.get("Url", "").strip()
        content_text = item.get("ContentText", "").strip()
        
        if not title or not url:
            continue

        # 构建摘要（优先用 ContentText）
        snippet = content_text[:300] if content_text else ""

        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "source": "zhihu",
            "content_type": item.get("ContentType", ""),
            "vote_up_count": item.get("VoteUpCount", 0),
            "comment_count": item.get("CommentCount", 0),
            "author_name": item.get("AuthorName", ""),
            "author_badge": item.get("AuthorBadgeText", ""),
            "edit_time": item.get("EditTime", 0),
            "authority_level": item.get("AuthorityLevel", ""),
            "ranking_score": item.get("RankingScore", 0.0),
        })

        if len(results) >= max_results:
            break

    return results


# ─── 页面正文提取（解决"点不开链接"痛点）──────────────────────────────────

# 需要移除的噪音标签
_NOISE_TAGS = (
    "script", "style", "nav", "footer", "header", "aside",
    "form", "iframe", "noscript", "svg", "button", "input",
)

# 正文区域选择器（按优先级尝试）
_CONTENT_SELECTORS = (
    "article", "main", "div.article-body", "div.article-content",
    "div.post-content", "div.entry-content", "div.content-body",
    "div#article", "div#content", "div#main-content",
    "div[class*='article']", "div[class*='content']",
    "div[class*='post-body']", "div[class*='detail']",
)


async def _fetch_page_content(url: str, timeout: float = 15.0) -> Dict[str, str]:
    """抓取指定 URL 的页面正文内容。

    优先使用 Jina Reader（支持 JS 渲染），失败则回退到直接 HTTP + HTML 解析。
    百度链接会自动跟随重定向获取真实 URL。

    返回 {"url", "final_url", "title", "content", "content_length", "error"}
    """
    # ── 策略 1: Jina Reader（支持 JS 渲染，微信文章，动态站点）──
    try:
        jina_result = await asyncio.wait_for(
            _jina_reader(url, timeout=timeout),
            timeout=timeout + 5,
        )
        if jina_result.get("content") and len(jina_result["content"]) > 50:
            return jina_result
    except (asyncio.TimeoutError, Exception):
        pass

    # ── 策略 2: 直接 HTTP + HTML 解析（fallback）──
    headers = {
        "User-Agent": _UA_PC,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=headers,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            final_url = str(resp.url)
            html = resp.text
    except Exception as e:
        return {"url": url, "final_url": url, "title": "",
                "content": "", "content_length": 0, "error": str(e)}

    return _extract_text_from_html(html, url, final_url)


def _extract_text_from_html(html: str, url: str, final_url: str) -> Dict[str, str]:
    """从 HTML 提取正文文本。"""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return {"url": url, "final_url": final_url, "title": "",
                "content": "", "content_length": 0, "error": "parse_failed"}

    # 提取标题
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()

    # 移除噪音标签
    for tag_name in _NOISE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # 尝试找到正文区域
    content_elem = None
    for selector in _CONTENT_SELECTORS:
        content_elem = soup.select_one(selector)
        if content_elem:
            break

    # 没找到正文区域，用整个 body
    if not content_elem:
        content_elem = soup.body or soup

    # 提取段落文本
    paragraphs = []
    for p in content_elem.find_all(["p", "div", "li", "td", "span"]):
        text = p.get_text(strip=True)
        if text and len(text) > 10:
            paragraphs.append(text)

    # 如果段落太少，直接取所有文本
    if len(paragraphs) < 3:
        all_text = content_elem.get_text(separator="\n", strip=True)
        paragraphs = [line.strip() for line in all_text.split("\n") if line.strip() and len(line.strip()) > 5]

    # 合并并截取（最多 2000 字符，足够猫娘理解页面内容）
    content = "\n".join(paragraphs)
    if len(content) > 2000:
        content = content[:2000] + "..."

    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "content": content,
        "content_length": len(content),
        "error": "",
    }


def _deduplicate_results(results: List[Dict[str, str]], query: str) -> List[Dict[str, str]]:
    """去重 + 相关性排序 + 攻略类优先 + 同域名限制 + 长尾关键词精确匹配。"""
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    query_lower = query.lower()
    # 长尾关键词检测：>5 个词时优先完整短语匹配
    word_count = len(query_lower.split())
    is_long_query = word_count > 5
    query_phrase = query_lower.replace(" ", "")
    # 提取查询中的所有词（中文按字分，英文按词分）
    query_words = set(re.findall(r'[a-zA-Z]+', query_lower))
    # 中文按 2-4 字组合提取关键词
    cjk_chars = re.findall(r'[\u4e00-\u9fff]+', query)
    for segment in cjk_chars:
        if len(segment) >= 2:
            query_words.add(segment.lower())
            # 也加入 2-gram
            for i in range(len(segment) - 1):
                query_words.add(segment[i:i+2].lower())

    # 无关内容特征（降权或排除）
    _IRRELEVANT_PATTERNS = [
        "字典", "词典", "百科解释", "汉语词典", "新华字典",
        "百度汉语", "汉典", "definition", "dictionary",
    ]

    deduped: List[Dict[str, str]] = []
    domain_count: Dict[str, int] = {}  # 同域名出现次数
    for r in results:
        title = r.get("title", "").strip().lower()
        url = r.get("url", "").strip().lower()

        # 去重：相同标题或相同 URL
        if title in seen_titles or url in seen_urls:
            continue
        seen_titles.add(title)
        seen_urls.add(url)

        # 同域名限制：同一域名最多出现 2 次
        from urllib.parse import urlparse
        try:
            domain = urlparse(r.get("url", "")).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            if domain and domain_count.get(domain, 0) >= 2:
                continue
            if domain:
                domain_count[domain] = domain_count.get(domain, 0) + 1
        except Exception:
            pass

        title_text = r.get("title", "").lower()
        snippet_text = r.get("snippet", "").lower()
        content_text = (r.get("page_content", "") or "").lower()

        # 排除明显无关内容（字典/词典解释）
        is_irrelevant = any(p in title_text for p in _IRRELEVANT_PATTERNS)
        if is_irrelevant:
            continue

        # 计算相关性分数
        score = 0
        # 长尾查询：完整短语匹配高分
        if is_long_query and query_phrase:
            title_nospace = title_text.replace(" ", "")
            snippet_nospace = snippet_text.replace(" ", "")
            if query_phrase in title_nospace:
                score += 10
            elif query_phrase in snippet_nospace:
                score += 5
        # 逐词匹配
        for word in query_words:
            if len(word) > 1:
                if word in title_text:
                    score += 5  # 标题命中权重最高
                if word in snippet_text:
                    score += 2  # 摘要命中
                if word in content_text:
                    score += 1  # 正文命中

        # 攻略/指南类内容加分
        for kw in _GUIDE_KEYWORDS:
            if kw in title_text or kw in snippet_text:
                score += 3
                break

        # 有正文内容的结果加分（更有价值）
        if r.get("page_content") and len(r["page_content"]) > 100:
            score += 2

        r["_relevance_score"] = score
        deduped.append(r)

    # 按相关性排序（分数高的在前）
    deduped.sort(key=lambda x: x.get("_relevance_score", 0), reverse=True)

    # 移除临时字段
    for r in deduped:
        r.pop("_relevance_score", None)

    return deduped


# ─── 插件类 ────────────────────────────────────────────────────────────────

@neko_plugin
class WebSearchingPlugin(NekoPluginBase):
    """独立的联网搜索插件 — 智能选择搜索引擎 + 历史记录 + 前端 UI。"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}
        self._db_path: Optional[Path] = None

    # ── 生命周期 ────────────────────────────────────────────────────────

    @lifecycle(id="startup")
    async def startup(self, **_):
        """启动钩子 — 健壮性优先，单个失败不影响整体。"""
        try:
            cfg = await self.config.dump(timeout=5.0)
            cfg = cfg if isinstance(cfg, dict) else {}
            self._cfg = cfg.get("search") if isinstance(cfg.get("search"), dict) else {}
        except Exception as e:
            self.logger.warning("Failed to load config: {}", e)
            self._cfg = {}

        # 初始化数据库（独立 try/except）
        try:
            self._db_path = self.data_path("search_history.db")
            self.data_path().mkdir(parents=True, exist_ok=True)
            self._init_db()
        except Exception as e:
            self.logger.error("DB init failed: {}", e)
            # 继续启动，不让 DB 失败阻塞插件

        # 注册静态 UI（独立 try/except）
        try:
            self.register_static_ui("static")
        except Exception as e:
            self.logger.warning("register_static_ui failed: {}", e)

        # 设置列表动作（独立 try/except）
        try:
            self.set_list_actions([
                {
                    "id": "open_ui",
                    "label": "搜索记录",
                    "kind": "ui",
                    "target": f"/plugin/{self.plugin_id}/ui/",
                    "open_in": "new_tab",
                },
            ])
        except Exception as e:
            self.logger.warning("set_list_actions failed: {}", e)

        self.logger.info("WebSearchingPlugin started v1.6.0")
        return Ok({
            "status": "running",
            "version": "1.6.0",
            "engines": {
                "chinese": ["zhihu", "baidu", "sogou", "bing", "ddg"],
                "english": ["ddg", "bing", "baidu"],
                "knowledge": ["ddg_ia", "wikipedia"],
            }
        })

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self.logger.info("WebSearchingPlugin shutdown")
        return Ok({"status": "shutdown"})

    @lifecycle(id="reload")
    async def reload(self, **_):
        self.logger.info("WebSearchingPlugin reload")
        return Ok({"status": "reloaded"})

    # ── SQLite 历史记录 ──────────────────────────────────────────────────

    def _init_db(self):
        if not self._db_path:
            return
        conn = self._get_db()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    is_chinese INTEGER DEFAULT 0,
                    engine TEXT,
                    result_count INTEGER DEFAULT 0,
                    results_json TEXT,
                    instant_answer TEXT,
                    wiki_summary TEXT,
                    searched_at REAL,
                    searched_at_str TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_time ON search_history(searched_at DESC)"
            )
            conn.commit()
        finally:
            conn.close()

    def _get_db(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    def _save_history(
        self, query: str, engine: str,
        results: List[Dict[str, str]],
        instant_answer: str = "",
        wiki_summary: str = "",
    ):
        if not self._db_path:
            return
        try:
            ts = time.time()
            ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            is_zh = 1 if _is_chinese_query(query) else 0
            conn = self._get_db()
            try:
                conn.execute(
                    "INSERT INTO search_history "
                    "(query, is_chinese, engine, result_count, results_json, "
                    "instant_answer, wiki_summary, searched_at, searched_at_str) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (query, is_zh, engine, len(results),
                     json.dumps(results, ensure_ascii=False),
                     instant_answer, wiki_summary, ts, ts_str),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.warning("Save history failed: {}", e)

    def _load_history(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        if not self._db_path:
            return []
        try:
            conn = self._get_db()
            rows = conn.execute(
                "SELECT id, query, is_chinese, engine, result_count, results_json, "
                "instant_answer, wiki_summary, searched_at, searched_at_str "
                "FROM search_history ORDER BY searched_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            conn.close()
        except Exception as e:
            self.logger.warning("Load history failed: {}", e)
            return []

        history = []
        for r in rows:
            try:
                results = json.loads(r[5]) if r[5] else []
            except (json.JSONDecodeError, TypeError):
                results = []
            history.append({
                "id": r[0], "query": r[1], "is_chinese": bool(r[2]),
                "engine": r[3], "result_count": r[4], "results": results,
                "instant_answer": r[6] or "", "wiki_summary": r[7] or "",
                "searched_at": r[8], "searched_at_str": r[9],
            })
        return history

    def _clear_history(self) -> int:
        if not self._db_path:
            return 0
        try:
            conn = self._get_db()
            n = conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0]
            conn.execute("DELETE FROM search_history")
            conn.commit()
            conn.close()
            return n
        except Exception as e:
            self.logger.warning("Clear history failed: {}", e)
            return 0

    def _get_stats(self) -> Dict[str, Any]:
        if not self._db_path:
            return {"total_searches": 0, "engines": {}, "languages": {}}
        try:
            conn = self._get_db()
            total = conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0]
            engines = dict(conn.execute(
                "SELECT engine, COUNT(*) FROM search_history GROUP BY engine"
            ).fetchall())
            languages = dict(conn.execute(
                "SELECT is_chinese, COUNT(*) FROM search_history GROUP BY is_chinese"
            ).fetchall())
            conn.close()
            return {
                "total_searches": total,
                "engines": engines,
                "languages": {"chinese": languages.get(1, 0), "english": languages.get(0, 0)},
            }
        except Exception as e:
            self.logger.warning("Get stats failed: {}", e)
            return {"total_searches": 0, "engines": {}, "languages": {}}

    # ── 搜索逻辑 ────────────────────────────────────────────────────────

    def _defaults(self) -> Dict[str, Any]:
        try:
            mr = int(self._cfg.get("max_results", 8))
        except (TypeError, ValueError):
            mr = 8
        try:
            to = float(self._cfg.get("timeout_seconds", 25))
        except (TypeError, ValueError):
            to = 25.0
        return {"max_results": max(3, min(mr, 50)), "timeout": max(10.0, to)}

    async def _execute_search(
        self, query: str, max_results: int, timeout: float
    ) -> Tuple[List[Dict[str, str]], str, str, str]:
        """
        智能搜索：按查询语言选择引擎优先级。
        返回 (web_results, strategy, instant_answer, wiki_summary)。
        """
        instant_answer_text = ""
        wiki_summary_text = ""
        strategy_used = "none"
        is_chinese = _is_chinese_query(query)

        # ── 知识层：DDG IA + Wikipedia（并行，各自独立超时）──
        knowledge_timeout = min(14.0, timeout)
        ia_task = asyncio.create_task(_ddg_instant_answer(query, knowledge_timeout))
        wiki_task = asyncio.create_task(_wikipedia_summary(query, knowledge_timeout))

        ia_result = None
        wiki_result = None
        try:
            ia_result = await asyncio.wait_for(ia_task, timeout=knowledge_timeout + 2)
        except (asyncio.TimeoutError, Exception) as e:
            if not isinstance(e, asyncio.TimeoutError):
                self.logger.warning("DDG IA failed: {}", e)

        try:
            wiki_result = await asyncio.wait_for(wiki_task, timeout=knowledge_timeout + 2)
        except (asyncio.TimeoutError, Exception) as e:
            if not isinstance(e, asyncio.TimeoutError):
                self.logger.warning("Wikipedia failed: {}", e)

        if ia_result:
            strategy_used = "ddg_ia"
            parts = []
            if ia_result.get("abstract"):
                parts.append(ia_result["abstract"])
            elif ia_result.get("definition"):
                parts.append(ia_result["definition"])
            if ia_result.get("related_topics"):
                for t in ia_result["related_topics"][:3]:
                    parts.append(f"  - {t['text']}")
            instant_answer_text = "\n".join(parts) if parts else ""

        if wiki_result:
            if strategy_used == "none":
                strategy_used = "wikipedia"
            else:
                strategy_used = strategy_used + "+wiki"
            wiki_summary_text = wiki_result.get("summary", "")

        # ── Web 结果层：按国内可达性排序引擎 ──
        # 中文查询：知乎优先（内容质量高、专业性强）
        # Jina/DDG 在国内可能不可达，降权到最后作为 fallback
        if is_chinese:
            engines = [
                ("zhihu", _search_zhihu),
                ("baidu", _search_baidu),
                ("bing", _search_bing),
                ("sogou", _search_sogou),
                ("ddg", _search_ddg_html),
                ("jina", _jina_search),
            ]
        else:
            engines = [
                ("bing", _search_bing),
                ("ddg", _search_ddg_html),
                ("baidu", _search_baidu),
                ("jina", _jina_search),
            ]

        web_results: List[Dict[str, str]] = []
        # 每个引擎独立短超时（12s），避免单个引擎卡住整个搜索
        per_engine_timeout = min(12.0, timeout)
        for engine_name, engine_fn in engines:
            try:
                results = await asyncio.wait_for(
                    engine_fn(query, max_results, per_engine_timeout),
                    timeout=per_engine_timeout + 3,
                )
                if results:
                    web_results = results
                    if strategy_used == "none":
                        strategy_used = engine_name
                    else:
                        strategy_used = strategy_used + "+" + engine_name
                    break  # 找到结果就停止
            except (asyncio.TimeoutError, Exception) as e:
                if not isinstance(e, asyncio.TimeoutError):
                    self.logger.warning("{} failed: {}", engine_name, e)

        # ── 后处理：去重 + 相关性排序（排除字典解释，攻略类加分）──
        if web_results:
            web_results = _deduplicate_results(web_results, query)

        # ── 正文增强：对没有 page_content 的 Top 3 结果用 Jina Reader 抓取 ──
        if web_results:
            enrich_timeout = min(12.0, timeout)
            enrich_tasks = []
            enrich_indices = []
            for idx, r in enumerate(web_results[:5]):
                # Jina Search 已返回内容的结果跳过
                if r.get("page_content") and len(r["page_content"]) > 50:
                    continue
                url = r.get("url", "")
                if url and url.startswith("http"):
                    enrich_tasks.append(_fetch_page_content(url, enrich_timeout))
                    enrich_indices.append(idx)

            if enrich_tasks:
                self.logger.info("Enriching {} pages via Jina Reader", len(enrich_tasks))
                enriched = await asyncio.gather(*enrich_tasks, return_exceptions=True)
                for i, result in enumerate(enriched):
                    idx = enrich_indices[i]
                    if isinstance(result, dict) and result.get("content"):
                        content_preview = result["content"][:500]
                        web_results[idx]["page_content"] = content_preview
                        web_results[idx]["final_url"] = result.get("final_url", "")
                        if not web_results[idx].get("title") and result.get("title"):
                            web_results[idx]["title"] = result["title"]

        return web_results, strategy_used, instant_answer_text, wiki_summary_text

    @staticmethod
    def _build_summary(
        query: str,
        results: List[Dict[str, str]],
        instant_answer: str = "",
        wiki_summary: str = "",
        strategy: str = "",
    ) -> str:
        """给 LLM 的搜索摘要 — 包含即时答案、百科、网页摘要、正文预览。"""
        lines: list[str] = []

        if instant_answer:
            lines.append(f"【即时答案】\n{instant_answer}\n")

        if wiki_summary:
            lines.append(f"【百科知识】\n{wiki_summary[:500]}\n")

        if results:
            lang_tag = "中文网络" if _is_chinese_query(query) else "Web"
            lines.append(f"【{lang_tag}搜索】(\"{query}\" 共 {len(results)} 条)")
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.get('title', '')}")
                # 摘要：优先用 snippet，为空则从 page_content 提取前 200 字
                snippet = r.get("snippet", "").strip()
                if not snippet and r.get("page_content"):
                    snippet = r["page_content"][:200].strip()
                if snippet:
                    lines.append(f"   摘要: {snippet}")
                # 正文预览（让猫娘看到页面内容，不用点链接）
                if r.get("page_content") and len(r["page_content"]) > 200:
                    lines.append(f"   正文: {r['page_content'][:300]}")
                lines.append("")

        if not lines:
            lines.append(f'搜索 "{query}" 未找到相关结果。')

        return "\n".join(lines)

    # ── LLM 工具 ────────────────────────────────────────────────────────

    @llm_tool(
        name="web_searching",
        description=(
            "联网搜索工具。当你遇到以下情况时应主动调用此工具进行联网搜索：\n"
            "1. 用户询问的事物超出了你自身训练数据的知识范围；\n"
            "2. 你在自己的记忆库中无法检索到相关事物的信息；\n"
            "3. 你判断用户询问的事物发生时间在你自身训练数据截止时间之后"
            "（即最近发生的事、最新新闻、实时信息等）。\n\n"
            "调用后你会获得搜索结果，包括：即时答案、百科摘要、网页标题+摘要，"
            "以及 Top 3 结果的页面正文预览（无需点击链接即可看到页面内容）。\n"
            "请基于这些搜索结果进行准确、有根据的回复。\n\n"
            "智能引擎选择：中文查询用百度/搜狗优先，英文查询用 DuckDuckGo/Bing。\n"
            "结果已自动去重并按关键词相关性排序。\n\n"
            "参数说明：\n"
            "  query (必填): 搜索关键词。保留用户原始语言，不要翻译。\n"
            "  max_results (可选): 返回网页结果数量，默认 8 条。\n\n"
            "如果搜索结果的正文预览信息不够，你可以用 fetch_page_content 工具"
            "抓取某个具体 URL 的完整页面内容。\n\n"
            "示例：用户问「GPT5.6的发布时间」→ 调用 web_searching(query=\"GPT5.6的发布时间\")"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，保留用户原始语言，不要翻译",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大网页结果数，默认 8",
                    "default": 8,
                },
            },
            "required": ["query"],
        },
        timeout=60.0,
    )
    async def web_searching_tool(self, *, query: str, max_results: int = 8, **_):
        """LLM 可主动调用的联网搜索工具。"""
        if not query or not query.strip():
            return {"output": {"reason": "关键词为空"}, "is_error": True, "error": "EMPTY"}

        defs = self._defaults()
        max_r = max(3, max_results if max_results and max_results > 0 else defs["max_results"])
        # 英文查询自动增加结果数量（英文搜索引擎结果更丰富）
        if not _is_chinese_query(query) and max_r < 10:
            max_r = 10
        timeout = defs["timeout"]

        self.logger.info("web_searching tool: query={!r}", query)

        results, strategy, ia_text, wiki_text = await self._execute_search(
            query, max_r, timeout
        )

        summary = self._build_summary(query, results, ia_text, wiki_text, strategy)
        self.logger.info("Search done: strategy={} results={}", strategy, len(results))

        self._save_history(query, strategy, results, ia_text, wiki_text)

        return {"output": {
            "summary": summary,
            "count": len(results),
            "strategy": strategy,
            "is_chinese": _is_chinese_query(query),
            "instant_answer": ia_text,
            "wiki_summary": wiki_text,
            "results": results,
        }}

    @llm_tool(
        name="fetch_page_content",
        description=(
            "抓取指定 URL 的页面正文内容。当你需要阅读某个网页的具体内容时使用此工具。\n\n"
            "使用场景：\n"
            "1. 搜索结果中某个链接看起来很有用，但摘要信息不够，你想看页面正文；\n"
            "2. 用户给了你一个 URL，你想了解这个页面的内容；\n"
            "3. 你需要从某个页面提取具体信息（如数值、版本号、角色定位等）。\n\n"
            "返回页面正文文本（最多 2000 字符），已去除广告、导航等噪音内容。\n"
            "百度链接会自动跟随重定向获取真实 URL。\n\n"
            "参数说明：\n"
            "  url (必填): 要抓取的网页 URL。\n"
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的网页 URL",
                },
            },
            "required": ["url"],
        },
        timeout=30.0,
    )
    async def fetch_page_content_tool(self, *, url: str, **_):
        """LLM 可调用的页面正文抓取工具。"""
        if not url or not url.strip():
            return {"output": {"reason": "URL 不能为空"}, "is_error": True, "error": "EMPTY"}

        url = url.strip()
        if not url.startswith(("http://", "https://")):
            return {"output": {"reason": "URL 必须以 http:// 或 https:// 开头"},
                    "is_error": True, "error": "INVALID_URL"}

        self.logger.info("fetch_page_content: url={!r}", url)

        try:
            result = await asyncio.wait_for(
                _fetch_page_content(url, timeout=20.0),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            return {"output": {"reason": "抓取超时"}, "is_error": True, "error": "TIMEOUT"}
        except Exception as e:
            return {"output": {"reason": f"抓取失败: {e}"}, "is_error": True, "error": "FETCH_FAILED"}

        if result.get("error") and not result.get("content"):
            return {"output": {"reason": result["error"]}, "is_error": True, "error": "FETCH_FAILED"}

        return {"output": {
            "url": result.get("url", url),
            "final_url": result.get("final_url", url),
            "title": result.get("title", ""),
            "content": result.get("content", ""),
            "content_length": result.get("content_length", 0),
        }}

    # ── 前端入口 ────────────────────────────────────────────────────────

    @plugin_entry(
        id="search",
        name="网络搜索",
        description="多策略联网搜索（中文:百度/搜狗 英文:DDG/Bing）+ 知识层 API",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    )
    async def search(self, query: str, max_results: int = 0, **_):
        if not query or not query.strip():
            return Err(SdkError("搜索关键词不能为空"))

        defs = self._defaults()
        max_r = max(3, max_results if max_results > 0 else defs["max_results"])
        timeout = defs["timeout"]

        self.logger.info("Manual search: query={!r}", query)

        results, strategy, ia_text, wiki_text = await self._execute_search(
            query, max_r, timeout
        )

        summary = self._build_summary(query, results, ia_text, wiki_text, strategy)
        self.logger.info("Search done: strategy={} results={}", strategy, len(results))

        self._save_history(query, strategy, results, ia_text, wiki_text)

        return Ok({
            "query": query, "strategy": strategy,
            "is_chinese": _is_chinese_query(query),
            "count": len(results), "summary": summary,
            "instant_answer": ia_text, "wiki_summary": wiki_text,
            "results": results,
        })

    @plugin_entry(
        id="get_history",
        name="获取搜索记录",
        description="获取历史搜索记录",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
        },
    )
    async def get_history(self, limit: int = 50, offset: int = 0, **_):
        history = self._load_history(limit=limit, offset=offset)
        stats = self._get_stats()
        return Ok({"history": history, "total": stats["total_searches"], "stats": stats})

    @plugin_entry(
        id="clear_history",
        name="清空搜索记录",
        description="清空所有搜索历史",
        input_schema={"type": "object", "properties": {}},
    )
    async def clear_history(self, **_):
        deleted = self._clear_history()
        self.logger.info("Cleared {} records", deleted)
        return Ok({"deleted": deleted})

    @plugin_entry(
        id="get_stats",
        name="搜索统计",
        description="获取搜索统计数据",
        input_schema={"type": "object", "properties": {}},
    )
    async def get_stats(self, **_):
        return Ok(self._get_stats())

    @plugin_entry(
        id="get_status",
        name="插件状态",
        description="获取插件运行状态",
        input_schema={"type": "object", "properties": {}},
    )
    async def get_status(self, **_):
        return Ok({
            "version": "1.6.0",
            "engines": {
                "chinese": ["zhihu", "baidu", "sogou", "bing", "ddg"],
                "english": ["ddg", "bing", "baidu"],
                "knowledge": ["ddg_ia", "wikipedia"],
            },
            "config": self._defaults(),
        })
