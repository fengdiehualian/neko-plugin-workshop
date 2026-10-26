"""免费公开学习资源检索。

只接无需登录、公开可访问的源；全部是「搜索结果 → 标题 + 链接 + 摘要」，
正文是否抓取取决于该站 robots.txt 是否允许。被 robots 拒绝时仍返回直达链接，
用户在面板里点开浏览器看，或交给其它联网插件读取。
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional

try:  # 宿主冻结环境可能没打包 concurrent.futures，缺了就退化成串行检索
    from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
except Exception:  # pragma: no cover - 冻结宿主路径
    _ThreadPoolExecutor = None

from . import _fmt as fmt
from ._crawl import Crawler, html_to_text

_WBI_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22,
    25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

_LINK_RE = re.compile(r"<a\b[^>]+href=[\"']([^\"']+)[\"'][^>]*>([\s\S]{0,200}?)</a>", re.I)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


@dataclass
class SourceSpec:
    id: str
    name: str
    kind: str  # course 课程 / video 视频 / article 词条 / exam 真题
    search_template: str
    lang: str = "zh"
    free: bool = True
    note: str = ""


SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec(
        id="smartedu_basic",
        name="国家中小学智慧教育平台",
        kind="course",
        search_template="https://basic.smartedu.cn/search?q={q}",
        note="教育部官方资源，覆盖小学到高中全部学科的课件、微课与习题，完全免费。",
    ),
    SourceSpec(
        id="smartedu_higher",
        name="国家高等教育智慧教育平台",
        kind="course",
        search_template="https://higher.smartedu.cn/search?keyword={q}",
        note="教育部官方高校课程平台，含大量免费大学课程与教材。",
    ),
    SourceSpec(
        id="icourse163",
        name="中国大学 MOOC",
        kind="course",
        search_template="https://www.icourse163.org/search.htm?search={q}",
        note="多数课程免费旁听，证书可能收费。",
    ),
    SourceSpec(
        id="xuetangx",
        name="学堂在线",
        kind="course",
        search_template="https://www.xuetangx.com/search?query={q}",
        note="清华发起，大量免费慕课。",
    ),
    SourceSpec(
        id="bilibili",
        name="B 站学习区",
        kind="video",
        search_template="https://search.bilibili.com/all?keyword={q}",
        note="免费视频讲解最多；请求间隔已强制限速，遇到风控会短暂失效。",
    ),
    SourceSpec(
        id="wikipedia",
        name="维基百科",
        kind="article",
        search_template="https://zh.wikipedia.org/w/index.php?search={q}",
        note="概念背景与定义的快速核验。",
    ),
    SourceSpec(
        id="khan",
        name="Khan Academy",
        kind="course",
        lang="en",
        search_template="https://www.khanacademy.org/search?page_search_query={q}",
        note="英文免费课程，数学与科学部分讲得非常细。",
    ),
    SourceSpec(
        id="mit_ocw",
        name="MIT OpenCourseWare",
        kind="course",
        lang="en",
        search_template="https://ocw.mit.edu/search/?q={q}",
        note="MIT 全部公开课教材与习题，免费开放。",
    ),
)

SOURCE_BY_ID: dict[str, SourceSpec] = {spec.id: spec for spec in SOURCES}

# 未指定来源时用这一批：中文考试最常用、且都是真正免费的
DEFAULT_SOURCE_IDS: tuple[str, ...] = (
    "smartedu_basic",
    "smartedu_higher",
    "icourse163",
    "xuetangx",
    "bilibili",
    "wikipedia",
)

# 检索是 IO 密集且互相独立的，按 host 并行能显著缩短等待；
# 限速是按站点做的，所以并行不会突破单站的最小间隔。
MAX_PARALLEL_SOURCES = 3


@dataclass
class ResourceItem:
    title: str
    url: str
    source: str
    source_name: str
    kind: str = "course"
    summary: str = ""
    blocked_by_robots: bool = False
    # ── 内容分析（抓正文后由大模型填，见 N.E.K.O 插件侧 _analyze_resources）──
    covers: str = ""   # 这份材料到底讲了什么
    fit: str = ""      # 与用户当前需求哪里对得上
    level: str = ""    # 适合什么阶段
    how: str = ""      # 建议怎么用
    # 抓下来的正文摘录，只用于喂模型，不落库也不返给前端
    excerpt: str = field(default="", repr=False, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "source_name": self.source_name,
            "kind": self.kind,
            "summary": self.summary,
            "blocked_by_robots": self.blocked_by_robots,
            "covers": self.covers,
            "fit": self.fit,
            "level": self.level,
            "how": self.how,
        }


def _strip_tags(fragment: str) -> str:
    text = _TAG_STRIP_RE.sub(" ", fragment or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_links(raw_html: str, base_url: str, limit: int) -> list[tuple[str, str]]:
    """从搜索结果页里抽出候选链接（标题, 绝对地址）。"""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, inner in _LINK_RE.findall(raw_html or ""):
        title = _strip_tags(inner)
        if len(title) < 6 or len(title) > 80:
            continue
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        rows.append((title, url))
        if len(rows) >= limit * 4:
            break
    return rows


def _mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[index] for index in _WBI_TAB if index < len(raw))[:32]


def _wbi_sign(params: dict[str, Any], img_key: str, sub_key: str) -> dict[str, Any]:
    signed = {key: value for key, value in params.items()}
    signed["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(signed.items()))
    digest = hashlib.md5((query + _mixin_key(img_key, sub_key)).encode("utf-8")).hexdigest()
    signed["w_rid"] = digest
    return signed


class ResourceSearcher:
    """在免费公开源里检索学习资料。"""

    def __init__(self, crawler: Crawler) -> None:
        self.crawler = crawler
        self._bili_ready = False

    # ── B 站 ──────────────────────────────────────────────────
    def _ensure_bili_cookie(self) -> None:
        if self._bili_ready:
            return
        try:
            self.crawler.get_json("https://api.bilibili.com/x/frontend/finger/spi")
        except Exception:
            pass
        self._bili_ready = True

    def _bili_keys(self) -> Optional[tuple[str, str]]:
        ok, payload = self.crawler.get_json("https://api.bilibili.com/x/web-interface/nav")
        if not ok or not isinstance(payload, dict):
            return None
        data = payload.get("data") or {}
        wbi = data.get("wbi_img") or {}
        img_url = str(wbi.get("img_url") or "")
        sub_url = str(wbi.get("sub_url") or "")
        if not img_url or not sub_url:
            return None
        img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
        sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
        return img_key, sub_key

    def _search_bilibili(self, query: str, limit: int) -> list[ResourceItem]:
        self._ensure_bili_cookie()
        keys = self._bili_keys()
        items: list[ResourceItem] = []
        if keys:
            params = _wbi_sign(
                {"search_type": "video", "keyword": query, "page": 1},
                keys[0],
                keys[1],
            )
            url = "https://api.bilibili.com/x/web-interface/wbi/search/type?" + urllib.parse.urlencode(params)
            ok, payload = self.crawler.get_json(url)
            if ok and isinstance(payload, dict):
                for row in (payload.get("data") or {}).get("result") or []:
                    if len(items) >= limit:
                        break
                    title = re.sub(r"<[^>]+>", "", str(row.get("title") or ""))
                    bvid = str(row.get("bvid") or "")
                    if not title or not bvid:
                        continue
                    items.append(
                        ResourceItem(
                            title=title,
                            url=f"https://www.bilibili.com/video/{bvid}",
                            source="bilibili",
                            source_name="B 站学习区",
                            kind="video",
                            summary=_strip_tags(str(row.get("description") or ""))[:160],
                        )
                    )
        if items:
            return items
        # 降级：搜索页 HTML
        url = SOURCE_BY_ID["bilibili"].search_template.format(q=urllib.parse.quote(query))
        result = self.crawler.request(url, "GET")
        if not result.ok:
            return []
        for title, link in _extract_links(result.text, url, limit)[:limit]:
            items.append(
                ResourceItem(
                    title=title,
                    url=link,
                    source="bilibili",
                    source_name="B 站学习区",
                    kind="video",
                )
            )
        return items

    def _search_wikipedia(self, query: str, limit: int) -> list[ResourceItem]:
        url = (
            "https://zh.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit="
            f"{limit}&srsearch={urllib.parse.quote(query)}"
        )
        ok, payload = self.crawler.get_json(url)
        items: list[ResourceItem] = []
        if not ok or not isinstance(payload, dict):
            return items
        for row in ((payload.get("query") or {}).get("search") or [])[:limit]:
            title = str(row.get("title") or "")
            if not title:
                continue
            items.append(
                ResourceItem(
                    title=title,
                    url=f"https://zh.wikipedia.org/wiki/{urllib.parse.quote(title)}",
                    source="wikipedia",
                    source_name="维基百科",
                    kind="article",
                    summary=_strip_tags(str(row.get("snippet") or ""))[:160],
                )
            )
        return items

    def _search_generic(self, spec: SourceSpec, query: str, limit: int) -> list[ResourceItem]:
        url = spec.search_template.format(q=urllib.parse.quote(query))
        if not self.crawler.robots_allows(url):
            return [
                ResourceItem(
                    title=f"在{spec.name}查看「{query}」",
                    url=url,
                    source=spec.id,
                    source_name=spec.name,
                    kind=spec.kind,
                    summary="该站点 robots.txt 限制自动抓取，请在浏览器中打开查看。",
                    blocked_by_robots=True,
                )
            ]
        result = self.crawler.request(url, "GET")
        items: list[ResourceItem] = []
        if not result.ok:
            return items
        for title, link in _extract_links(result.text, url, limit)[:limit]:
            items.append(
                ResourceItem(
                    title=title,
                    url=link,
                    source=spec.id,
                    source_name=spec.name,
                    kind=spec.kind,
                )
            )
        return items

    # ── 对外 ──────────────────────────────────────────────────
    def _search_one(self, spec: SourceSpec, query: str, limit: int) -> list[ResourceItem]:
        try:
            if spec.id == "bilibili":
                return self._search_bilibili(query, limit)
            if spec.id == "wikipedia":
                return self._search_wikipedia(query, limit)
            return self._search_generic(spec, query, limit)
        except Exception:
            return []

    def search(
        self,
        query: str,
        source_ids: Optional[list[str]] = None,
        limit: int = 4,
    ) -> list[ResourceItem]:
        query = (query or "").strip()
        if not query:
            return []
        wanted = list(source_ids) if source_ids else list(DEFAULT_SOURCE_IDS)
        specs = [SOURCE_BY_ID[sid] for sid in wanted if sid in SOURCE_BY_ID]
        if not specs:
            return []
        items: list[ResourceItem] = []
        if _ThreadPoolExecutor is None:
            for spec in specs:
                try:
                    items.extend(self._search_one(spec, query, limit))
                except Exception:
                    continue
            return items
        with _ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_SOURCES, len(specs))) as pool:
            futures = [pool.submit(self._search_one, spec, query, limit) for spec in specs]
            for future in futures:
                try:
                    items.extend(future.result(timeout=self.crawler.timeout * 3))
                except Exception:
                    continue
        return items

    def fetch_detail(self, url: str, limit: int = 4000) -> dict[str, Any]:
        if not self.crawler.robots_allows(url):
            return {"ok": False, "url": url, "text": "", "error": "robots.txt 不允许抓取该地址"}
        result = self.crawler.request(url, "GET")
        if not result.ok:
            return {"ok": False, "url": url, "text": "", "error": result.error or f"HTTP {result.status}"}
        return {"ok": True, "url": url, "text": html_to_text(result.text, limit=limit), "error": ""}

    def search_with_excerpt(
        self,
        query: str,
        source_ids: Optional[list[str]] = None,
        limit: int = 4,
        excerpt_chars: int = 1500,
    ) -> list[ResourceItem]:
        """检索 + 把候选页正文抓下来（放进 ``excerpt`` 供大模型做内容分析）。

        只做"取回"，不做判断——判断那一层交给模型，因为它需要理解用户的需求。
        抓不到正文的条目照样返回，只是没有 excerpt，前端会退化成链接展示。
        """
        items = self.search(query, source_ids, limit)
        if not items:
            return []
        picked = items[: max(1, limit)]
        results: dict[int, str] = {}

        def grab(index: int, url: str) -> None:
            try:
                detail = self.fetch_detail(url, limit=excerpt_chars)
            except Exception:
                return
            if not detail.get("ok"):
                return
            text = detail.get("text")
            if isinstance(text, str) and text.strip():
                results[index] = text.strip()

        if _ThreadPoolExecutor is not None and len(picked) > 1:
            with _ThreadPoolExecutor(max_workers=min(3, len(picked))) as pool:
                futures = [pool.submit(grab, index, item.url) for index, item in enumerate(picked)]
                for future in futures:
                    try:
                        future.result(timeout=self.crawler.timeout * 3)
                    except Exception:
                        continue
        else:
            for index, item in enumerate(picked):
                grab(index, item.url)
        for index, item in enumerate(picked):
            text = results.get(index, "")
            if text:
                item.excerpt = text
            elif not item.summary:
                item.summary = f"（未能自动抓取正文，可在 {item.source_name} 手动打开）"
        return picked


def list_sources() -> list[dict[str, Any]]:
    return [
        {
            "id": spec.id,
            "name": spec.name,
            "kind": spec.kind,
            "lang": spec.lang,
            "free": spec.free,
            "note": spec.note,
        }
        for spec in SOURCES
    ]


def build_query(topic: str, subject: str = "", exam_type: str = "") -> str:
    """把知识点拼成更适合检索的查询串。"""
    parts = [part for part in (exam_type, subject, topic) if part]
    return " ".join(parts).strip()


def build_links(keyword: str, source_ids: Optional[list[str]] = None) -> list[tuple[str, str, str]]:
    """按关键词生成各免费平台的**搜索直链**（不需要抓取，必定可用）。

    这是「模型可以自己联网」时最好的搭档：插件负责给出准确的落点，
    模型负责去读、去总结，用户也能直接点进去。
    """
    text = (keyword or "").strip()
    if not text:
        return []
    wanted = list(source_ids) if source_ids else list(DEFAULT_SOURCE_IDS)
    quoted = urllib.parse.quote(text)
    rows: list[tuple[str, str, str]] = []
    for sid in wanted:
        spec = SOURCE_BY_ID.get(sid)
        if spec is None:
            continue
        rows.append((spec.name, spec.search_template.format(q=quoted), spec.note))
    return rows


def format_resource_plan(keyword: str, source_ids: Optional[list[str]] = None) -> str:
    """把「去哪找」整理成清单：平台 + 直链 + 这个平台适合找什么。"""
    text = (keyword or "").strip()
    if not text:
        return "想找哪个知识点/题型的资料？给我个关键词。"
    rows = build_links(text, source_ids)
    if not rows:
        return "没有可用的资源平台配置。"
    lines = []
    for name, url, note in rows:
        line = f"{fmt.bold(name)}\n　　{url}"
        if note:
            line += f"\n　　{fmt.kv('适合', note)}"
        lines.append(line)
    return fmt.join(
        fmt.section(f"「{text}」的免费资源检索清单", "\n\n".join(lines)),
        fmt.note("链接都已经带上关键词，点开就是结果页；中文平台优先，B 站视频量最大但质量参差。"),
    )


def format_resources(items: list[ResourceItem], limit: int = 8) -> str:
    """资源输出：每条都给出「讲了什么 / 与你需求的关系 / 适合阶段 / 怎么用」。

    没做过内容分析（例如抓取失败）时，自动退化成「平台 + 标题 + 链接」，
    不会出现空白条目。
    """
    if not items:
        return "这次没检索到合适的公开资源。可以换个更具体的说法，或直接用『资源清单』让我给你各平台搜索直链。"
    blocks: list[str] = []
    for index, item in enumerate(items[:limit], start=1):
        flag = "（该站不允许自动抓取，需手动打开）" if item.blocked_by_robots else ""
        head = f"{fmt.bold(f'{index}. {item.title}')}　{fmt.bold('来源')}：{item.source_name}{flag}"
        rows: list[str] = []
        if item.covers:
            rows.append(fmt.kv("讲了什么", item.covers))
        if item.fit:
            rows.append(fmt.kv("和你的需求", item.fit))
        if item.level:
            rows.append(fmt.kv("适合阶段", item.level))
        if item.how:
            rows.append(fmt.kv("建议用法", item.how))
        if not rows:
            if item.summary:
                rows.append(fmt.kv("简介", item.summary))
            rows.append(fmt.kv("链接", item.url))
        blocks.append(head + "\n" + "\n".join(f"　{r}" for r in rows))
    return fmt.join(*blocks)
