# 太空发射插件 —— 查询全球下一次及未来即将进行的太空发射任务
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Space Launch Plugin（太空发射）

查询全球下一次及未来即将进行的太空发射任务，并跨来源检索航天资料。

数据来源：
- Launch Library 2 API   https://thespacedevs.com/llapi
                         https://ll.thespacedevs.com/2.3.0/launches/upcoming/
- NASA NTRS              https://ntrs.nasa.gov/
- 百度百科                https://baike.baidu.com/
- 卫星百科（灰机wiki）    https://sat.huijiwiki.com/
- Bing 网页兜底           https://cn.bing.com/
（以上均为国内可直连来源；Wikipedia / Wikidata 需要代理，未纳入。）

功能：
- next_launch            查询「下一次太空发射」（核心功能）
- upcoming_launches      查询未来多次发射，支持按发射服务商 / 火箭 / 发射场筛选
- search_space_objects   检索航天器 / 火箭型号 / 空间站 / 航天机构 / 宇航员
- search_nasa_documents  检索 NASA 技术报告与会议论文
- baike_search           检索百度百科中文条目
- sat_wiki_search        检索卫星百科（灰机wiki）中文条目
- web_fallback_search    网页兜底搜索
- launch_stats           发射数据概览
- 以及六个供猫娘在对话中主动调用的 LLM 工具。

说明：Launch Library 2 免费额度约为每小时 15 次请求，因此插件内置了
带 TTL 的内存缓存来避免重复请求。
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
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

# ---------------------------------------------------------------------------
# 常量与工具函数
# ---------------------------------------------------------------------------

DEFAULT_API_BASE = "https://ll.thespacedevs.com/2.3.0/"
DEFAULT_NTRS_BASE = "https://ntrs.nasa.gov/"
DEFAULT_BAIKE_BASE = "https://baike.baidu.com/"
DEFAULT_BING_BASE = "https://cn.bing.com/"
# 国内卫星百科（灰机wiki / sat.huijiwiki.com），中文航天资料覆盖面广
DEFAULT_HUIJIWIKI_BASE = "https://sat.huijiwiki.com/"
_USER_AGENT = "N.E.K.O-space-launch-plugin/1.3 (+https://project-neko.online)"
# 百度百科、Bing 与卫星百科（灰机wiki）会对脚本 UA 做风控，这里使用常规浏览器标识
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# LL2 中可检索的实体类型 -> (API 路径, 中文名)
_LL2_SEARCH_TARGETS: Dict[str, Tuple[str, str]] = {
    "spacecraft": ("spacecraft_configurations/", "航天器"),
    "launcher": ("launcher_configurations/", "火箭型号"),
    "station": ("space_stations/", "空间站"),
    "agency": ("agencies/", "航天机构"),
    "astronaut": ("astronauts/", "宇航员"),
}

# 本地筛选时最多向 API 请求的条目数，避免一次拉取过多数据
_MAX_FETCH_LIMIT = 50
# 对外可返回的最大条目数
_MAX_RESULT_LIMIT = 20


class _ApiError(Exception):
    """表示一次外部 API 调用失败，携带面向用户的友好信息。"""


# 检索无结果时给猫娘的明确指引：避免它在没有资料的情况下编造参数。
_NO_RESULT_GUIDANCE = (
    "本地数据库没有收录这个条目，因此没有查到任何可靠的参数或数据。"
    "请直接告诉用户「我这边没有查到」，绝对不要凭猜测描述它的型号、尺寸、"
    "推力、服役时间等具体信息。"
)


def _no_result_payload(
    *,
    query: str,
    label: str,
    suggestions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """构造“没有查到”的返回结构。

    关键点是 ``found: False`` 加上显式的禁止编造提示 —— 检索工具返回空结果时，
    如果只给一句「没找到」，LLM 往往会用训练数据里的记忆去补全细节，
    这些细节可能是过时或错误的。这里把“不要编造”写进返回值，让模型有据可依。
    """
    payload: Dict[str, Any] = {
        "found": False,
        "summary": f"没有找到与「{query}」相关的{label}。{_NO_RESULT_GUIDANCE}",
        "results": [],
        "count": 0,
        "total": 0,
        "guidance": _NO_RESULT_GUIDANCE,
        "next_steps": [
            "换用其他关键词或外文原名再试一次",
            "改用 web_search 插件检索公开资料",
            "改用 baike_search 检索中文百科条目",
            "改用 sat_wiki_search 检索国内卫星百科（灰机wiki）条目",
            "改用 web_fallback_search 获取相关网页链接",
        ],
    }
    if suggestions:
        payload["suggestions"] = suggestions
        payload["next_steps"].insert(
            0, f"尝试相近条目：{'、'.join(suggestions)}"
        )
    return payload


def _as_dict(value: Any) -> Dict[str, Any]:
    """安全地把任意值转成 dict。"""
    return value if isinstance(value, dict) else {}


def _as_text(value: Any) -> str:
    """安全地把任意值转成去空白的字符串。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _parse_iso(value: Any) -> Optional[datetime]:
    """解析 LL2 返回的 ISO 8601 时间（通常以 Z 结尾）。"""
    text = _as_text(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_local_text(value: Optional[datetime]) -> str:
    """把时间转换成用户本地时区的可读字符串。"""
    if value is None:
        return ""
    local = value.astimezone()
    tzname = local.tzname() or ""
    return f"{local.strftime('%Y-%m-%d %H:%M:%S')} {tzname}".strip()


def _humanize_delta(delta_seconds: float) -> str:
    """把秒数差转成“还有 X 天 Y 小时”这样的中文描述。"""
    future = delta_seconds >= 0
    total = abs(int(delta_seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: List[str] = []
    if days:
        parts.append(f"{days} 天")
    if hours:
        parts.append(f"{hours} 小时")
    if minutes:
        parts.append(f"{minutes} 分钟")
    if not parts:
        parts.append(f"{seconds} 秒")

    text = " ".join(parts)
    return f"还有 {text}" if future else f"已过去 {text}"


def _coerce_limit(value: Any, default: int) -> int:
    """把外部传入的 limit 规整到合法区间。"""
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SdkError(f"limit 必须是整数，收到：{value!r}") from None
    if number < 1:
        number = 1
    return min(number, _MAX_RESULT_LIMIT)


# LL2 中表示任务已经结束的状态缩写
_FINISHED_STATUS = {"Success", "Failure", "Partial Failure"}


def _is_still_upcoming(item: Dict[str, Any]) -> bool:
    """判断一条记录是否仍然算“即将进行”。

    LL2 的 ``launches/upcoming/`` 端点偶尔会返回刚刚发射完、状态尚未刷新
    的条目。如果不过滤，``next_launch`` 就可能把已经结束的任务当成“下一次
    发射”报给用户。
    """
    if _as_text(item.get("status_abbrev")) in _FINISHED_STATUS:
        return False
    seconds = item.get("countdown_seconds")
    if seconds is None:
        # 发射时间待定的任务仍然可能发生，保留
        return True
    # 留 60 秒容差，避免刚过 T-0 的任务被立刻剔除
    return seconds > -60


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------

def _summarize_launch(
    raw: Dict[str, Any],
    *,
    include_description: bool,
    now: datetime,
) -> Dict[str, Any]:
    """把 LL2 的一条发射记录裁剪成插件自己的结构。"""
    rocket_cfg = _as_dict(_as_dict(raw.get("rocket")).get("configuration"))
    mission = _as_dict(raw.get("mission"))
    pad = _as_dict(raw.get("pad"))
    location = _as_dict(pad.get("location"))
    country = _as_dict(pad.get("country"))
    status = _as_dict(raw.get("status"))
    provider = _as_dict(raw.get("launch_service_provider"))
    orbit = _as_dict(mission.get("orbit"))
    net_precision = _as_dict(raw.get("net_precision"))
    image = _as_dict(raw.get("image"))

    net_dt = _parse_iso(raw.get("net"))
    countdown_seconds: Optional[float] = None
    countdown = ""
    if net_dt is not None:
        countdown_seconds = (net_dt - now).total_seconds()
        countdown = _humanize_delta(countdown_seconds)

    item: Dict[str, Any] = {
        "name": _as_text(raw.get("name")),
        "status": _as_text(status.get("name")),
        "status_abbrev": _as_text(status.get("abbrev")),
        "provider": _as_text(provider.get("name")),
        "provider_abbrev": _as_text(provider.get("abbrev")),
        "rocket": _as_text(rocket_cfg.get("full_name")) or _as_text(rocket_cfg.get("name")),
        "mission": _as_text(mission.get("name")),
        "mission_type": _as_text(mission.get("type")),
        "orbit": _as_text(orbit.get("name")),
        "pad": _as_text(pad.get("name")),
        "location": _as_text(location.get("name")),
        "country": _as_text(country.get("name")),
        "net_utc": net_dt.astimezone(timezone.utc).isoformat() if net_dt else "",
        "net_local": _to_local_text(net_dt),
        "net_precision": _as_text(net_precision.get("name")),
        "window_start_local": _to_local_text(_parse_iso(raw.get("window_start"))),
        "window_end_local": _to_local_text(_parse_iso(raw.get("window_end"))),
        "countdown": countdown,
        "countdown_seconds": countdown_seconds,
        "webcast_live": bool(raw.get("webcast_live")),
        "url": _as_text(raw.get("url")),
        "image": _as_text(image.get("image_url")),
    }
    if include_description:
        item["description"] = _as_text(mission.get("description"))
    return item


def _describe_launch(item: Dict[str, Any]) -> str:
    """生成一句适合猫娘念出来的中文描述。"""
    name = item.get("name") or "未知任务"
    text = f"下一次太空发射是「{name}」"

    rocket = item.get("rocket") or ""
    if rocket:
        text += f"，使用 {rocket} 火箭"
    provider = item.get("provider") or ""
    if provider:
        text += f"，由 {provider} 执行"

    net_local = item.get("net_local") or ""
    if net_local:
        text += f"，计划于 {net_local} 发射"
    else:
        text += "，发射时间尚未确定"

    site = item.get("location") or item.get("pad") or ""
    if site:
        text += f"，发射场：{site}"

    countdown = item.get("countdown") or ""
    if countdown:
        text += f"（{countdown}）"

    status = item.get("status") or ""
    if status:
        text += f"。当前状态：{status}"

    mission_type = item.get("mission_type") or ""
    if mission_type:
        text += f"，任务类型：{mission_type}"

    return text + "。"


def _describe_launch_list(items: List[Dict[str, Any]]) -> str:
    """生成发射列表的中文概述。"""
    if not items:
        return "暂时没有查询到符合条件的发射任务。"

    lines = [f"接下来有 {len(items)} 次太空发射："]
    for index, item in enumerate(items, start=1):
        name = item.get("name") or "未知任务"
        net_local = item.get("net_local") or "时间待定"
        countdown = item.get("countdown") or ""
        provider = item.get("provider") or ""
        suffix = f"，{provider}" if provider else ""
        tail = f"（{countdown}）" if countdown else ""
        lines.append(f"{index}. {name}{suffix} — {net_local}{tail}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 自动播报
# ---------------------------------------------------------------------------

# 同一个 key 的排队播报会互相合并，只留最新一条，避免上下文越堆越长。
AUTO_BRIEF_COALESCE_KEY = "space_launch_auto_brief"
AUTO_BRIEF_PREFIX = "【发射计划自动播报】"


def _auto_brief_line(items: List[Dict[str, Any]]) -> str:
    """把「下一次发射」压成一行，塞进角色的上下文。

    刻意只取第一条：这是要在每一轮对话里一直跟着走的东西，越长越浪费。
    查不到数据时返回空串，由调用方决定「闭嘴」还是「说一声查不到」。
    """
    if not items:
        return ""
    text = _describe_launch(items[0]).strip()
    if not text:
        return ""
    return AUTO_BRIEF_PREFIX + text


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------

def _join_names(values: Any) -> str:
    """把 LL2 的 ``[{"name": ...}]`` 这类列表拼成顿号分隔的字符串。"""
    if not isinstance(values, list):
        return ""
    names = [_as_text(_as_dict(item).get("name")) for item in values]
    return "、".join(name for name in names if name)


def _summarize_ll2_entity(raw: Dict[str, Any], category: str) -> Dict[str, Any]:
    """把 LL2 搜索命中的实体裁剪成插件自己的结构。"""
    image = _as_dict(raw.get("image"))
    item: Dict[str, Any] = {
        "category": category,
        "category_label": _LL2_SEARCH_TARGETS.get(category, ("", category))[1],
        "name": _as_text(raw.get("name")),
        "description": _as_text(raw.get("description")),
        "image": _as_text(image.get("image_url")),
        "url": _as_text(raw.get("url")),
    }

    if category == "spacecraft":
        item["type"] = _as_text(_as_dict(raw.get("type")).get("name"))
        item["agency"] = _as_text(_as_dict(raw.get("agency")).get("name"))
        item["in_use"] = bool(raw.get("in_use"))
        item["family"] = _join_names(raw.get("family"))
        families = raw.get("family")
        if isinstance(families, list) and families:
            item["maiden_flight"] = _as_text(
                _as_dict(families[0]).get("maiden_flight")
            )
    elif category == "launcher":
        item["full_name"] = _as_text(raw.get("full_name"))
        item["family"] = _as_text(raw.get("family"))
        item["variant"] = _as_text(raw.get("variant"))
        item["manufacturer"] = _as_text(
            _as_dict(raw.get("manufacturer")).get("name")
        )
        item["maiden_flight"] = _as_text(raw.get("maiden_flight"))
    elif category == "station":
        item["status"] = _as_text(_as_dict(raw.get("status")).get("name"))
        item["orbit"] = _as_text(raw.get("orbit"))
        item["founded"] = _as_text(raw.get("founded"))
        item["owners"] = _join_names(raw.get("owners"))
        item["crew_size"] = raw.get("crew_size")
    elif category == "agency":
        item["abbrev"] = _as_text(raw.get("abbrev"))
        item["type"] = _as_text(_as_dict(raw.get("type")).get("name"))
        item["country"] = _join_names(raw.get("country"))
        item["founding_year"] = _as_text(raw.get("founding_year"))
        item["administrator"] = _as_text(raw.get("administrator"))
    elif category == "astronaut":
        item["status"] = _as_text(_as_dict(raw.get("status")).get("name"))
        item["nationality"] = _as_text(raw.get("nationality"))
        item["agency"] = _as_text(_as_dict(raw.get("agency")).get("name"))
        item["flights_count"] = raw.get("flights_count")
        item["bio"] = _as_text(raw.get("bio"))

    return item


def _describe_ll2_entity(item: Dict[str, Any]) -> str:
    """生成一条 LL2 实体的中文描述。"""
    label = item.get("category_label") or "条目"
    name = item.get("name") or "未知"
    text = f"{label}「{name}」"

    details: List[str] = []
    for field, prefix in (
        ("type", "类型"),
        ("abbrev", "缩写"),
        ("agency", "所属机构"),
        ("manufacturer", "制造商"),
        ("family", "系列"),
        ("country", "国家/地区"),
        ("status", "状态"),
        ("orbit", "轨道"),
        ("founding_year", "成立年份"),
        ("founded", "成立"),
        ("maiden_flight", "首飞"),
        ("nationality", "国籍"),
        ("administrator", "负责人"),
    ):
        value = item.get(field)
        if value in (None, "", 0):
            continue
        details.append(f"{prefix}{value}")

    if details:
        text += "：" + "；".join(details)

    description = item.get("description") or item.get("bio") or ""
    if description:
        text += f"。{description}"

    return text


def _describe_ll2_list(
    items: List[Dict[str, Any]],
    *,
    query: str,
    label: str,
) -> str:
    """生成 LL2 搜索结果的简短列表描述。"""
    if not items:
        return f"没有找到与「{query}」相关的{label}。"

    lines = [f"找到 {len(items)} 条与「{query}」相关的{label}："]
    for index, item in enumerate(items, start=1):
        name = item.get("name") or "未知"
        extra = item.get("type") or item.get("abbrev") or item.get("agency") or ""
        suffix = f"（{extra}）" if extra else ""
        lines.append(f"{index}. {name}{suffix}")
    return "\n".join(lines)


def _summarize_ntrs_document(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把 NASA NTRS 技术文献裁剪成插件自己的结构。"""
    authors: List[str] = []
    for entry in raw.get("authorAffiliations") or []:
        meta = _as_dict(_as_dict(entry).get("meta"))
        name = _as_text(_as_dict(meta.get("author")).get("name"))
        if name:
            authors.append(name)

    downloads = raw.get("downloads") or []
    full_text_url = ""
    if isinstance(downloads, list) and downloads:
        links = _as_dict(_as_dict(downloads[0]).get("links"))
        full_text_url = _as_text(links.get("fulltext")) or _as_text(links.get("pdf"))
    if full_text_url.startswith("/"):
        full_text_url = f"{DEFAULT_NTRS_BASE.rstrip('/')}{full_text_url}"

    doc_id = raw.get("id")
    return {
        "title": _as_text(raw.get("title")),
        "abstract": _as_text(raw.get("abstract")),
        "authors": authors,
        "center": _as_text(_as_dict(raw.get("center")).get("name")),
        "document_type": _as_text(raw.get("stiTypeDetails"))
        or _as_text(raw.get("stiType")),
        "published": _as_text(raw.get("distributionDate"))
        or _as_text(raw.get("created")),
        "keywords": [k for k in (raw.get("keywords") or []) if isinstance(k, str)],
        "subject_categories": [
            c for c in (raw.get("subjectCategories") or []) if isinstance(c, str)
        ],
        "document_id": doc_id,
        "url": f"{DEFAULT_NTRS_BASE}citations/{doc_id}" if doc_id else "",
        "full_text_url": full_text_url,
    }


def _describe_ntrs_document(item: Dict[str, Any]) -> str:
    """生成一篇 NTRS 文献的中文描述。"""
    title = item.get("title") or "未知文献"
    text = f"NASA 技术文献《{title}》"

    authors = item.get("authors") or []
    if authors:
        shown = "、".join(authors[:3])
        more = " 等" if len(authors) > 3 else ""
        text += f"，作者：{shown}{more}"

    center = item.get("center") or ""
    if center:
        text += f"，所属中心：{center}"

    doc_type = item.get("document_type") or ""
    if doc_type:
        text += f"，类型：{doc_type}"

    abstract = item.get("abstract") or ""
    if abstract:
        short = abstract if len(abstract) <= 240 else abstract[:240] + "…"
        text += f"。摘要：{short}"

    return text


def _describe_ntrs_list(
    items: List[Dict[str, Any]],
    *,
    query: str,
) -> str:
    """生成 NTRS 搜索结果的简短列表描述。"""
    if not items:
        return f"没有找到与「{query}」相关的 NASA 技术文献。"

    lines = [f"找到 {len(items)} 篇与「{query}」相关的 NASA 技术文献："]
    for index, item in enumerate(items, start=1):
        title = item.get("title") or "未知文献"
        center = item.get("center") or ""
        suffix = f"（{center}）" if center else ""
        lines.append(f"{index}. {title}{suffix}")
    return "\n".join(lines)


def _summarize_baike_lemma(raw: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    """把百度百科条目裁剪成插件结构；未收录时返回 None。

    百度百科的开放接口在“没有该词条”时返回一个空对象（``{}``），
    用 ``abstract``/``title`` 是否存在即可判断命中与否。
    """
    abstract = _as_text(raw.get("abstract"))
    title = _as_text(raw.get("title")) or _as_text(raw.get("key")) or query
    if not abstract and not _as_text(raw.get("title")):
        return None

    image = _as_text(raw.get("image"))
    if image and image.startswith("//"):
        image = f"https:{image}"

    return {
        "source": "百度百科",
        "title": title,
        "abstract": abstract,
        "url": _as_text(raw.get("url")),
        "image": image,
    }


def _describe_baike_lemma(item: Dict[str, Any]) -> str:
    """生成百度百科条目的中文描述。"""
    title = item.get("title") or "未知条目"
    text = f"百度百科条目「{title}」"

    abstract = item.get("abstract") or ""
    if abstract:
        short = abstract if len(abstract) <= 400 else abstract[:400] + "…"
        text += f"：{short}"

    url = item.get("url") or ""
    if url:
        text += f"\n原文：{url}"

    return text


def _parse_bing_rss(text: str) -> List[Dict[str, str]]:
    """解析 Bing 的 RSS 搜索结果。

    使用 ``format=rss`` 而不是解析 HTML：RSS 是稳定的结构化格式，
    站点改版也不会影响解析。
    """
    items: List[Dict[str, str]] = []
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return items

    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        description = (node.findtext("description") or "").strip()
        if not title and not link:
            continue
        items.append({
            "title": title,
            "url": link,
            "snippet": description,
        })
    return items


# 网页结果里常见的无关领域词，命中说明搜索结果跑偏了
_IRRELEVANT_HINTS = (
    "官方商城", "旗舰店", "服装", "服饰", "女装", "男装", "穿搭", "时尚",
    "淘宝", "天猫", "京东", "拼多多", "优惠券", "折扣", "购买", "价格",
    "招聘", "加盟", "贷款", "彩票", "手游", "下载安装",
)
# 与航天相关的领域词，命中说明结果至少沾边
_AEROSPACE_HINTS = (
    "航天", "火箭", "卫星", "飞船", "空间站", "运载", "发射", "轨道", "探测器",
    "宇航", "太空", "登月", "导弹", "nasa", "space", "rocket", "satellite",
    "launch", "orbit", "spacecraft", "spaceflight", "soyuz", "proton", "kosmos",
)


def _filter_relevant_web_results(
    items: List[Dict[str, str]],
    query: str,
) -> List[Dict[str, str]]:
    """过滤掉明显跑偏的网页结果。

    搜索引擎对生僻的航天型号（例如 ``UR-700A``）容易返回毫不相关的结果
    （实测会返回同名服装品牌）。把这种结果交给 LLM，比返回空结果更容易
    造成幻觉，所以这里做一次保守的相关性筛除。
    """
    tokens = [t for t in re.split(r"[\s\-_]+", query) if len(t) >= 3]
    if not tokens:
        tokens = [query] if query else []

    kept: List[Dict[str, str]] = []
    for item in items:
        blob = f"{item.get('title', '')} {item.get('snippet', '')}".lower()

        # 命中无关领域词、且完全没有航天线索 -> 丢弃
        if any(hint in blob for hint in _IRRELEVANT_HINTS):
            if not any(hint in blob for hint in _AEROSPACE_HINTS):
                continue

        # 结果至少要沾一点航天边，或者包含查询里的关键片段
        has_aero = any(hint in blob for hint in _AEROSPACE_HINTS)
        has_token = any(token.lower() in blob for token in tokens)
        if not has_aero and not has_token:
            continue

        kept.append(item)

    return kept


def _describe_bing_results(items: List[Dict[str, str]], *, query: str) -> str:
    """生成网页搜索结果的中文描述。"""
    if not items:
        return f"没有找到与「{query}」相关的网页结果。"

    lines = [f"与「{query}」相关的网页结果："]
    for index, item in enumerate(items, start=1):
        title = item.get("title") or "（无标题）"
        lines.append(f"{index}. {title}")
        snippet = item.get("snippet") or ""
        if snippet:
            short = snippet if len(snippet) <= 120 else snippet[:120] + "…"
            lines.append(f"   {short}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 卫星百科（灰机wiki / sat.huijiwiki.com）检索
# ---------------------------------------------------------------------------

# 卫星百科（灰机wiki）的官方名称，用于返回结构与日志
_SAT_WIKI_SOURCE = "卫星百科（灰机wiki）"

# 灰机wiki 站点启用了 Cloudflare 托管挑战（Managed Challenge）。被拦截时接口
# 返回的是 HTTP 403 + HTML 挑战页（响应头带 ``cf-mitigated: challenge``），
# 而不是 JSON。必须把这种情况和「接口真的报错」区分开：否则只会抛出一句
# 没有信息量的「返回错误状态 403」，用户既不知道是风控，也不知道该换条路。
_CLOUDFLARE_CHALLENGE_HINTS = (
    "just a moment",
    "challenges.cloudflare.com",
    "cf-chl",
    "cf_clearance",
)


def _looks_like_cloudflare_challenge(response: Any) -> bool:
    """判断响应是否为 Cloudflare 风控挑战页。

    先看 ``cf-mitigated`` 响应头（Cloudflare 拦截时会置为 ``challenge``），
    再看 ``server: cloudflare`` 加挑战页正文特征。任一命中即认为是风控拦截。
    """
    headers = getattr(response, "headers", None)
    try:
        if headers is not None:
            mitigated = str(headers.get("cf-mitigated", "") or "").lower()
            if "challenge" in mitigated:
                return True
            server = str(headers.get("server", "") or "").lower()
            if "cloudflare" not in server:
                return False
        body = str(getattr(response, "text", "") or "")[:4000].lower()
        return any(hint in body for hint in _CLOUDFLARE_CHALLENGE_HINTS)
    except Exception:  # pragma: no cover - 头部/正文读取异常不应影响主流程
        return False


def _strip_wiki_snippet(text: str) -> str:
    """去掉 MediaWiki 搜索摘要里的 HTML 标签并反转义实体。

    灰机wiki 的搜索摘要会带 ``<span class="searchmatch">…</span>`` 高亮，
    以及 ``&quot;``、``&#39;`` 之类的实体。这里只保留纯文本，便于朗读与展示。
    """
    if not text:
        return ""
    # 去掉所有标签（含 searchmatch 高亮），再反转义 HTML 实体
    cleaned = re.sub(r"<[^>]+>", "", text)
    return html.unescape(cleaned).strip()


def _summarize_sat_wiki(raw: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    """把卫星百科（灰机wiki）的搜索结果裁剪成插件结构。"""
    title = _as_text(raw.get("title"))
    snippet = _strip_wiki_snippet(_as_text(raw.get("snippet")))
    encoded = quote(title, safe="")
    url = f"{base_url.rstrip('/')}/wiki/{encoded}"
    return {
        "source": _SAT_WIKI_SOURCE,
        "title": title,
        "snippet": snippet,
        "pageid": raw.get("pageid"),
        "url": url,
        "wordcount": raw.get("wordcount"),
        "size": raw.get("size"),
        "timestamp": _as_text(raw.get("timestamp")),
    }


def _describe_sat_wiki_item(item: Dict[str, Any]) -> str:
    """生成卫星百科单条条目的中文描述。"""
    title = item.get("title") or "未知条目"
    text = f"{_SAT_WIKI_SOURCE}条目「{title}」"

    snippet = item.get("snippet") or ""
    if snippet:
        short = snippet if len(snippet) <= 400 else snippet[:400] + "…"
        text += f"：{short}"

    url = item.get("url") or ""
    if url:
        text += f"\n原文：{url}"

    return text


def _describe_sat_wiki_list(items: List[Dict[str, Any]], *, query: str) -> str:
    """生成卫星百科搜索结果的简短列表描述。"""
    if not items:
        return f"没有找到与「{query}」相关的卫星百科条目。"

    lines = [f"{_SAT_WIKI_SOURCE}中与「{query}」相关的 {len(items)} 个条目："]
    for index, item in enumerate(items, start=1):
        title = item.get("title") or "未知"
        lines.append(f"{index}. {title}")
        snippet = item.get("snippet") or ""
        if snippet:
            short = snippet if len(snippet) <= 120 else snippet[:120] + "…"
            lines.append(f"   {short}")
    return "\n".join(lines)


@neko_plugin
class SpaceLaunchPlugin(NekoPluginBase):
    """太空发射查询插件。"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger

        # 运行配置（在 startup 中从 self.config 读取）
        self._cfg: Dict[str, Any] = {}
        self._api_base: str = DEFAULT_API_BASE
        self._ntrs_base: str = DEFAULT_NTRS_BASE
        self._baike_base: str = DEFAULT_BAIKE_BASE
        self._bing_base: str = DEFAULT_BING_BASE
        self._huijiwiki_base: str = DEFAULT_HUIJIWIKI_BASE
        self._timeout: float = 15.0
        self._default_limit: int = 5
        self._include_descriptions: bool = True
        self._cache_ttl: float = 300.0

        # 按事件循环缓存 httpx 客户端：宿主会在不同的 asyncio.run()
        # 调用中执行 startup、入口命令与 shutdown，跨循环复用客户端会报错。
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Any = None

        # key -> (过期时间, 响应数据)
        self._cache: Dict[str, Tuple[float, Any]] = {}

        # 自动播报：只要插件在跑就定期把「下一次发射」写进角色的上下文，
        # 这样她不用等你开口才去调工具。外部接口有速率限制，默认间隔很长。
        self._auto_brief_enabled: bool = True
        self._auto_brief_interval: float = 180.0
        self._auto_brief_task: Any = None
        self._auto_brief_stop: Any = None
        self._auto_brief_last_text: str = ""
        self._auto_brief_last_ok: Optional[bool] = None

    # -- 基础设施 ---------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        """获取当前事件循环下可用的 httpx 客户端。"""
        try:
            loop: Any = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - 正常情况下不会发生
            loop = None

        if self._client is None or self._client.is_closed or self._client_loop is not loop:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._timeout,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/json",
                },
            )
            self._client_loop = loop
        return self._client

    def _cache_get(self, key: str) -> Any:
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: str, value: Any) -> None:
        # 简单的容量保护，避免无限增长
        if len(self._cache) > 32:
            self._cache.clear()
        self._cache[key] = (time.monotonic() + self._cache_ttl, value)

    async def _request_json(
        self,
        path: str,
        params: Dict[str, Any],
        *,
        base_url: Optional[str] = None,
        service: str = "Launch Library 2",
    ) -> Dict[str, Any]:
        """请求外部 JSON 接口。

        成功结果会按 TTL 缓存，失败抛出 :class:`_ApiError`。
        ``base_url`` 省略时使用 Launch Library 2 的地址；``service``
        只用于拼装面向用户的错误信息。
        """
        raw_base = base_url or self._api_base
        base = raw_base if raw_base.endswith("/") else raw_base + "/"
        url = f"{base}{path.lstrip('/')}"
        cache_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        cached = self._cache_get(cache_key)
        if cached is not None:
            self.logger.debug("{} 命中缓存: {}", service, cache_key)
            return cached

        client = self._get_client()
        try:
            response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise _ApiError(
                f"请求 {service} 超时（{self._timeout:.0f} 秒），请稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise _ApiError(f"无法连接 {service}：{exc}") from exc

        if response.status_code == 429:
            if base_url is None:
                raise _ApiError(
                    "Launch Library 2 免费额度已用完（约每小时 15 次请求），请稍后再试。"
                )
            raise _ApiError(f"{service} 请求过于频繁，请稍后再试。")
        if response.status_code >= 400:
            raise _ApiError(f"{service} 返回错误状态 {response.status_code}。")

        try:
            data = response.json()
        except ValueError as exc:
            raise _ApiError(f"{service} 返回的内容不是合法 JSON。") from exc

        if not isinstance(data, dict):
            raise _ApiError(f"{service} 返回了非预期的数据结构。")

        self._cache_set(cache_key, data)
        return data

    async def _fetch_upcoming(
        self,
        *,
        limit: int,
        provider: str = "",
        rocket: str = "",
        location: str = "",
    ) -> List[Dict[str, Any]]:
        """查询即将进行的发射，并在本地完成关键字筛选。"""
        provider = _as_text(provider)
        rocket = _as_text(rocket)
        location = _as_text(location)
        needs_filter = bool(provider or rocket or location)

        # 多取一些条目：本地筛选会丢弃部分结果，而且 API 的 upcoming 列表
        # 偶尔混有刚刚结束、状态尚未刷新的任务，需要留出过滤余量。
        fetch_limit = min(max(limit * 4, limit + 10), _MAX_FETCH_LIMIT)

        payload = await self._request_json(
            "launches/upcoming/",
            {"limit": fetch_limit, "mode": "normal"},
        )
        results = payload.get("results")
        if not isinstance(results, list):
            raise _ApiError("Launch Library 2 没有返回 results 字段。")

        now = datetime.now(timezone.utc)
        items = [
            _summarize_launch(
                _as_dict(raw),
                include_description=self._include_descriptions,
                now=now,
            )
            for raw in results
            if isinstance(raw, dict)
        ]

        # 丢弃已经结束的任务，避免把“昨天发射成功”当成“下一次发射”
        items = [item for item in items if _is_still_upcoming(item)]

        # 有确切发射时间的排前面并按时间升序，时间待定的排在最后
        items.sort(key=lambda item: (
            1 if not item.get("net_utc") else 0,
            item.get("net_utc") or "",
        ))

        if needs_filter:
            def matches(item: Dict[str, Any]) -> bool:
                haystack = " ".join(
                    str(item.get(field) or "")
                    for field in ("provider", "provider_abbrev", "rocket", "location", "pad", "name")
                ).lower()
                for keyword in (provider, rocket, location):
                    if keyword and keyword.lower() not in haystack:
                        return False
                return True

            items = [item for item in items if matches(item)]

        return items[:limit]

    async def _search_ll2_entities(
        self,
        *,
        query: str,
        category: str,
        limit: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """在 LL2 指定端点中检索实体，返回 (结果列表, 命中总数)。"""
        path, label = _LL2_SEARCH_TARGETS[category]
        payload = await self._request_json(
            path,
            {"search": query, "limit": limit, "mode": "normal"},
        )

        results = payload.get("results")
        if not isinstance(results, list):
            raise _ApiError(f"Launch Library 2 没有返回{label}的 results 字段。")

        items = [
            _summarize_ll2_entity(_as_dict(raw), category)
            for raw in results
            if isinstance(raw, dict)
        ]

        total = payload.get("count")
        try:
            total_int = int(total) if total is not None else len(items)
        except (TypeError, ValueError):
            total_int = len(items)

        return items, total_int

    async def _search_ntrs_documents(
        self,
        *,
        query: str,
        limit: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """检索 NASA NTRS 技术文献，返回 (结果列表, 命中总数)。"""
        payload = await self._request_json(
            "api/citations/search",
            {"q": query, "page.size": limit},
            base_url=self._ntrs_base,
            service="NASA NTRS",
        )

        results = payload.get("results")
        if not isinstance(results, list):
            raise _ApiError("NASA NTRS 没有返回 results 字段。")

        items = [
            _summarize_ntrs_document(_as_dict(raw))
            for raw in results
            if isinstance(raw, dict)
        ]

        stats = _as_dict(payload.get("stats"))
        total = stats.get("total")
        try:
            total_int = int(total) if total is not None else len(items)
        except (TypeError, ValueError):
            total_int = len(items)

        return items, total_int

    async def _fetch_baike_lemma(self, query: str) -> Optional[Dict[str, Any]]:
        """查询百度百科条目摘要，未收录时返回 None。"""
        base = self._baike_base if self._baike_base.endswith("/") else self._baike_base + "/"
        url = f"{base}api/openapi/BaikeLemmaCardApi"
        params = {
            "scope": 103,
            "format": "json",
            "appid": 379020,
            "bk_key": query,
            "bk_length": 600,
        }
        cache_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached or None

        client = self._get_client()
        try:
            response = await client.get(
                url,
                params=params,
                headers={"User-Agent": _BROWSER_UA},
            )
        except httpx.TimeoutException as exc:
            raise _ApiError(
                f"请求百度百科超时（{self._timeout:.0f} 秒），请稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise _ApiError(f"无法连接百度百科：{exc}") from exc

        if response.status_code >= 400:
            raise _ApiError(f"百度百科返回错误状态 {response.status_code}。")

        try:
            data = response.json()
        except ValueError as exc:
            raise _ApiError("百度百科返回的内容不是合法 JSON。") from exc

        item = _summarize_baike_lemma(_as_dict(data), query)
        # 用空 dict 作为“查过但没有”的缓存标记，避免重复请求
        self._cache_set(cache_key, item if item is not None else {})
        return item

    async def _search_web(self, query: str, limit: int) -> List[Dict[str, str]]:
        """通过 Bing RSS 检索网页，返回 [{title, url, snippet}]。"""
        base = self._bing_base if self._bing_base.endswith("/") else self._bing_base + "/"
        url = f"{base}search"
        params = {"q": query, "format": "rss"}
        cache_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        cached = self._cache_get(cache_key)
        if cached is not None:
            return list(cached)[:limit]

        client = self._get_client()
        try:
            response = await client.get(
                url,
                params=params,
                headers={"User-Agent": _BROWSER_UA},
            )
        except httpx.TimeoutException as exc:
            raise _ApiError(
                f"请求搜索服务超时（{self._timeout:.0f} 秒），请稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise _ApiError(f"无法连接搜索服务：{exc}") from exc

        if response.status_code >= 400:
            raise _ApiError(f"搜索服务返回错误状态 {response.status_code}。")

        items = _parse_bing_rss(response.text)
        self._cache_set(cache_key, items)
        return _filter_relevant_web_results(items, query)[:limit]

    async def _search_sat_wiki(
        self,
        *,
        query: str,
        limit: int,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """检索卫星百科（sat.huijiwiki.com / 灰机wiki），返回 (结果列表, 命中总数)。

        灰机wiki 是 MediaWiki，直接用其 ``api.php`` 的 ``list=search`` 接口拿
        结构化 JSON，比解析搜索结果页（CustomSearch / Special:Search）更稳。
        站点开启了 Cloudflare 风控，偶尔会把脚本请求拦成 HTML 挑战页，
        这种情况会被识别为「暂时无法访问」而不是当作空结果。
        """
        base = (
            self._huijiwiki_base
            if self._huijiwiki_base.endswith("/")
            else self._huijiwiki_base + "/"
        )
        url = f"{base}api.php"
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": "0",
            "srwhat": "text",
            "srlimit": limit,
            "format": "json",
        }
        cache_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        cached = self._cache_get(cache_key)
        if cached is not None:
            cached_items, cached_total = cached
            return list(cached_items)[:limit], cached_total

        client = self._get_client()
        try:
            response = await client.get(
                url,
                params=params,
                headers={"User-Agent": _BROWSER_UA},
            )
        except httpx.TimeoutException as exc:
            raise _ApiError(
                f"请求卫星百科超时（{self._timeout:.0f} 秒），请稍后重试。"
            ) from exc
        except httpx.HTTPError as exc:
            raise _ApiError(f"无法连接卫星百科：{exc}") from exc

        if _looks_like_cloudflare_challenge(response):
            raise _ApiError(
                f"卫星百科（灰机wiki）暂时无法访问：站点风控拦截了本次请求"
                f"（HTTP {response.status_code}）。这通常发生在机房 / 代理 IP 上，"
                f"家用宽带一般不受影响。可以稍后重试，或先用 baike_search "
                f"检索百度百科条目。"
            )
        if response.status_code == 429:
            raise _ApiError("卫星百科请求过于频繁，请稍后再试。")
        if response.status_code >= 400:
            raise _ApiError(f"卫星百科返回错误状态 {response.status_code}。")

        try:
            data = response.json()
        except ValueError as exc:
            # 极端情况下风控页没有带挑战特征头，只能靠「JSON 解析失败」兜底
            raise _ApiError(
                "卫星百科（灰机wiki）暂时无法访问，返回内容不是合法 JSON，"
                "可能被风控拦截。可以稍后重试，或先用 baike_search 检索百度百科条目。"
            ) from exc

        query_obj = _as_dict(data.get("query"))
        search_list = query_obj.get("search")
        if not isinstance(search_list, list):
            raise _ApiError("卫星百科返回了非预期的数据结构。")

        items = [
            _summarize_sat_wiki(_as_dict(raw), base)
            for raw in search_list
            if isinstance(raw, dict)
        ]

        searchinfo = _as_dict(query_obj.get("searchinfo"))
        total = searchinfo.get("totalhits")
        try:
            total_int = int(total) if total is not None else len(items)
        except (TypeError, ValueError):
            total_int = len(items)

        self._cache_set(cache_key, (items, total_int))
        return items[:limit], total_int

    # -- 自动播报 ---------------------------------------------------------

    async def _auto_brief_once(self) -> None:
        """读一次「下一次发射」，把结果作为只读线索塞进上下文。"""
        # 依赖 _request_json 的 TTL 缓存，所以即使间隔很短也不会真的打
        # 那么多次外部接口；免费额度约每小时 15 次。
        try:
            items = await self._fetch_upcoming(limit=1)
            connected = True
        except _ApiError as exc:
            self.logger.debug("自动播报取数失败: {}", exc)
            items = []
            connected = False
        except Exception as exc:  # pragma: no cover - 意外错误不该影响插件
            self.logger.debug("自动播报取数异常: {}", exc)
            return

        if connected:
            text = _auto_brief_line(items)
            if not text:
                # 查到了但列表是空的，没什么可播的——保持沉默，别刷噪音。
                self._auto_brief_last_ok = True
                return
        else:
            # 从来没成功过就别播报；本来连着、现在断了才说一声，
            # 免得模型拿着旧数据当现状。
            if not self._auto_brief_last_ok:
                return
            text = f"{AUTO_BRIEF_PREFIX}现在查不到发射计划接口，之前那条数据可能已经过时了。"

        if connected == self._auto_brief_last_ok and text == self._auto_brief_last_text:
            return
        self._auto_brief_last_ok = connected
        self._auto_brief_last_text = text

        try:
            result = self.push_message(
                visibility=[],
                ai_behavior="read",
                coalesce_key=AUTO_BRIEF_COALESCE_KEY,
                source="space_launch_auto_brief",
                parts=[{"type": "text", "text": text}],
            )
        except Exception as exc:  # pragma: no cover - 推送失败不该影响插件
            self.logger.debug("自动播报推送失败: {}", exc)
            return
        if not _as_dict(result).get("submitted"):
            self.logger.debug("自动播报未进队: {}", _as_dict(result).get("reason"))

    def _start_auto_brief(self) -> None:
        """启动后台轮询任务（幂等）。"""
        task = getattr(self, "_auto_brief_task", None)
        if task is not None and not task.done():
            return
        if not getattr(self, "_auto_brief_enabled", False):
            return
        try:
            self._auto_brief_stop = asyncio.Event()
            self._auto_brief_task = asyncio.create_task(self._auto_brief_loop())
        except RuntimeError:  # pragma: no cover - 没有运行中的事件循环
            self._auto_brief_task = None
            self._auto_brief_stop = None

    async def _stop_auto_brief(self) -> None:
        """停掉后台轮询任务。用 getattr：测试里常用 __new__ 造实例，没走 __init__。"""
        task = getattr(self, "_auto_brief_task", None)
        stop = getattr(self, "_auto_brief_stop", None)
        self._auto_brief_task = None
        self._auto_brief_stop = None
        if stop is not None:
            stop.set()
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - 都要吞掉
                pass

    async def _auto_brief_loop(self) -> None:
        """按间隔循环播报，直到被叫停。"""
        stop = self._auto_brief_stop
        if stop is None:  # pragma: no cover - 正常不会发生
            return
        while not stop.is_set():
            try:
                await self._auto_brief_once()
            except asyncio.CancelledError:  # pragma: no cover - 停机路径
                raise
            except Exception as exc:  # pragma: no cover - 单次失败继续下一轮
                self.logger.debug("自动播报循环异常: {}", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._auto_brief_interval)
            except asyncio.TimeoutError:
                continue
            else:
                return

    # -- 生命周期 ---------------------------------------------------------

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        try:
            cfg = await self.config.dump(timeout=5.0)
        except Exception as exc:  # pragma: no cover - 配置读取失败时退回默认值
            self.logger.warning("读取插件配置失败，使用默认值: {}", exc)
            cfg = {}
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg

        section = _as_dict(cfg.get("space_launch"))

        api_base = _as_text(section.get("api_base_url")) or DEFAULT_API_BASE
        self._api_base = api_base

        ntrs_base = _as_text(section.get("ntrs_base_url")) or DEFAULT_NTRS_BASE
        self._ntrs_base = ntrs_base

        self._baike_base = (
            _as_text(section.get("baike_base_url")) or DEFAULT_BAIKE_BASE
        )
        self._bing_base = _as_text(section.get("bing_base_url")) or DEFAULT_BING_BASE

        self._huijiwiki_base = (
            _as_text(section.get("huijiwiki_base_url")) or DEFAULT_HUIJIWIKI_BASE
        )

        try:
            timeout = float(section.get("timeout_seconds", 15))
        except (TypeError, ValueError):
            timeout = 15.0
        self._timeout = min(max(timeout, 3.0), 60.0)

        try:
            default_limit = int(section.get("default_limit", 5))
        except (TypeError, ValueError):
            default_limit = 5
        self._default_limit = min(max(default_limit, 1), _MAX_RESULT_LIMIT)

        self._include_descriptions = bool(section.get("include_descriptions", True))

        try:
            cache_ttl = float(section.get("cache_ttl_seconds", 300))
        except (TypeError, ValueError):
            cache_ttl = 300.0
        self._cache_ttl = max(cache_ttl, 0.0)

        self._auto_brief_enabled = bool(section.get("auto_brief_enabled", True))
        try:
            brief_interval = float(section.get("auto_brief_interval_seconds", 180))
        except (TypeError, ValueError):
            brief_interval = 180.0
        # 外部接口有速率限制，间隔下限放到 30 秒；上限一小时。
        self._auto_brief_interval = min(max(brief_interval, 30.0), 3600.0)

        self.logger.info(
            "SpaceLaunch 已就绪，API={} 默认条数={} 缓存={}s 自动播报={}",
            self._api_base,
            self._default_limit,
            int(self._cache_ttl),
            f"开/{self._auto_brief_interval:g}s" if self._auto_brief_enabled else "关",
        )
        if self._auto_brief_enabled:
            self._start_auto_brief()
        return Ok({
            "status": "ready",
            "api_base_url": self._api_base,
            "default_limit": self._default_limit,
            "cache_ttl_seconds": int(self._cache_ttl),
            "auto_brief": self._auto_brief_enabled,
            "auto_brief_interval_seconds": int(self._auto_brief_interval),
        })

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        await self._stop_auto_brief()
        client = self._client
        self._client = None
        self._client_loop = None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception as exc:  # pragma: no cover - 关闭失败不影响停机
                self.logger.debug("关闭 HTTP 客户端时出错: {}", exc)
        self._cache.clear()
        self.logger.info("SpaceLaunch 已停止")
        return Ok({"status": "stopped"})

    # -- 插件入口 ---------------------------------------------------------

    @plugin_entry(
        id="next_launch",
        name="下一次太空发射",
        description=(
            "查询全球下一次即将进行的太空发射任务，返回任务名称、火箭型号、发射服务商、"
            "发射时间（本地时区与倒计时）、发射场以及任务简介。可选按发射服务商、"
            "火箭型号或发射场关键字过滤。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "可选，按发射服务商关键字过滤，例如 SpaceX、Roscosmos、CASC",
                },
                "rocket": {
                    "type": "string",
                    "description": "可选，按火箭型号关键字过滤，例如 Falcon 9、Long March",
                },
                "location": {
                    "type": "string",
                    "description": "可选，按发射场关键字过滤，例如 Cape Canaveral、Wenchang",
                },
            },
        },
        timeout=30,
        llm_result_fields=["summary", "launch", "count"],
    )
    async def next_launch(
        self,
        provider: str = "",
        rocket: str = "",
        location: str = "",
        **_,
    ):
        """查询下一次太空发射。"""
        try:
            items = await self._fetch_upcoming(
                limit=1,
                provider=provider,
                rocket=rocket,
                location=location,
            )
        except _ApiError as exc:
            self.logger.warning("查询下一次发射失败: {}", exc)
            return Err(SdkError(str(exc)))
        except Exception as exc:  # pragma: no cover - 兜底，避免插件进程崩溃
            self.logger.exception("查询下一次发射时发生未预期错误")
            return Err(SdkError(f"查询下一次发射失败：{exc}"))

        if not items:
            return Err(SdkError("暂时没有查询到符合条件的即将发射任务。"))

        launch = items[0]
        summary = _describe_launch(launch)
        if launch.get("description"):
            summary += f"\n任务简介：{launch['description']}"

        self.logger.info("下一次发射: {}", launch.get("name"))
        return Ok({
            "summary": summary,
            "launch": launch,
            "count": 1,
        })

    @plugin_entry(
        id="upcoming_launches",
        name="即将进行的发射列表",
        description=(
            "查询未来即将进行的一系列太空发射任务列表，可指定返回条数（1-20），"
            "并可按发射服务商、火箭型号或发射场关键字过滤。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-20，默认使用插件配置值",
                    "minimum": 1,
                    "maximum": 20,
                },
                "provider": {
                    "type": "string",
                    "description": "可选，按发射服务商关键字过滤，例如 SpaceX",
                },
                "rocket": {
                    "type": "string",
                    "description": "可选，按火箭型号关键字过滤，例如 Falcon 9",
                },
                "location": {
                    "type": "string",
                    "description": "可选，按发射场关键字过滤，例如 Wenchang",
                },
            },
        },
        timeout=30,
        llm_result_fields=["summary", "launches", "count"],
    )
    async def upcoming_launches(
        self,
        limit: Any = None,
        provider: str = "",
        rocket: str = "",
        location: str = "",
        **_,
    ):
        """查询未来多次太空发射。"""
        try:
            wanted = _coerce_limit(limit, self._default_limit)
        except SdkError as exc:
            return Err(exc)

        try:
            items = await self._fetch_upcoming(
                limit=wanted,
                provider=provider,
                rocket=rocket,
                location=location,
            )
        except _ApiError as exc:
            self.logger.warning("查询发射列表失败: {}", exc)
            return Err(SdkError(str(exc)))
        except Exception as exc:  # pragma: no cover
            self.logger.exception("查询发射列表时发生未预期错误")
            return Err(SdkError(f"查询发射列表失败：{exc}"))

        if not items:
            return Err(SdkError("暂时没有查询到符合条件的即将发射任务。"))

        self.logger.info("返回 {} 条即将进行的发射", len(items))
        return Ok({
            "summary": _describe_launch_list(items),
            "launches": items,
            "count": len(items),
        })

    @plugin_entry(
        id="launch_stats",
        name="发射数据概览",
        description=(
            "返回 Launch Library 2 中即将进行的发射总数，以及插件当前使用的 API 地址与缓存设置。"
        ),
        timeout=30,
        llm_result_fields=["summary", "total_upcoming", "api_base_url"],
    )
    async def launch_stats(self, **_):
        """返回即将进行的发射总数等概览信息。"""
        try:
            payload = await self._request_json(
                "launches/upcoming/",
                {"limit": 1, "mode": "list"},
            )
        except _ApiError as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:  # pragma: no cover
            self.logger.exception("查询发射概览时发生未预期错误")
            return Err(SdkError(f"查询发射概览失败：{exc}"))

        total = payload.get("count")
        try:
            total_int = int(total) if total is not None else 0
        except (TypeError, ValueError):
            total_int = 0

        return Ok({
            "total_upcoming": total_int,
            "api_base_url": self._api_base,
            "cache_ttl_seconds": int(self._cache_ttl),
            "summary": f"Launch Library 2 目前记录了 {total_int} 次即将进行的发射。",
        })

    # -- LLM 工具（对话中由猫娘主动调用）---------------------------------

    @llm_tool(
        name="get_next_space_launch",
        description=(
            "获取全球下一次即将进行的太空发射信息。当用户询问“下一次火箭发射是什么时候”、"
            "“最近有什么发射任务”等问题时调用。返回任务名称、火箭、发射服务商、"
            "本地发射时间和倒计时。可选按发射服务商或火箭关键字过滤。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "可选，发射服务商关键字，例如 SpaceX。留空表示不限制。",
                },
                "rocket": {
                    "type": "string",
                    "description": "可选，火箭型号关键字，例如 Falcon 9。留空表示不限制。",
                },
            },
        },
        timeout=30,
    )
    async def get_next_space_launch(
        self,
        provider: str = "",
        rocket: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """LLM 工具：返回下一次发射。"""
        try:
            items = await self._fetch_upcoming(limit=1, provider=provider, rocket=rocket)
        except Exception as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        if not items:
            return {"output": {"summary": "暂时没有查询到符合条件的即将发射任务。"}}

        launch = items[0]
        return {
            "output": {
                "summary": _describe_launch(launch),
                "name": launch.get("name"),
                "rocket": launch.get("rocket"),
                "provider": launch.get("provider"),
                "net_local": launch.get("net_local"),
                "countdown": launch.get("countdown"),
                "location": launch.get("location") or launch.get("pad"),
                "status": launch.get("status"),
            }
        }

    @llm_tool(
        name="list_upcoming_space_launches",
        description=(
            "获取未来即将进行的一系列太空发射任务列表。当用户询问“接下来有哪些发射”、"
            "“这个月有什么火箭发射”等问题时调用。可按发射服务商或火箭关键字过滤。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-20，默认 5",
                },
                "provider": {
                    "type": "string",
                    "description": "可选，发射服务商关键字，例如 SpaceX。留空表示不限制。",
                },
                "rocket": {
                    "type": "string",
                    "description": "可选，火箭型号关键字，例如 Long March。留空表示不限制。",
                },
            },
        },
        timeout=30,
    )
    async def list_upcoming_space_launches(
        self,
        limit: Any = None,
        provider: str = "",
        rocket: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """LLM 工具：返回发射列表。"""
        try:
            wanted = _coerce_limit(limit, self._default_limit)
        except SdkError as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        try:
            items = await self._fetch_upcoming(limit=wanted, provider=provider, rocket=rocket)
        except Exception as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        if not items:
            return {"output": {"summary": "暂时没有查询到符合条件的即将发射任务。"}}

        return {
            "output": {
                "summary": _describe_launch_list(items),
                "launches": [
                    {
                        "name": item.get("name"),
                        "rocket": item.get("rocket"),
                        "provider": item.get("provider"),
                        "net_local": item.get("net_local"),
                        "countdown": item.get("countdown"),
                        "location": item.get("location") or item.get("pad"),
                        "status": item.get("status"),
                    }
                    for item in items
                ],
                "count": len(items),
            }
        }

    @plugin_entry(
        id="search_space_objects",
        name="航天器与机构检索",
        description=(
            "在 Launch Library 2 数据库中检索航天器、火箭型号、空间站、航天机构或宇航员的资料，"
            "返回名称、类型、所属机构、系列、首飞时间等结构化信息。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词，例如 Dragon、Falcon 9、ISS、NASA",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "spacecraft",
                        "launcher",
                        "station",
                        "agency",
                        "astronaut",
                    ],
                    "description": (
                        "检索类别：spacecraft=航天器（默认）、launcher=火箭型号、"
                        "station=空间站、agency=航天机构、astronaut=宇航员"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-20，默认使用插件配置值",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
        timeout=30,
        llm_result_fields=["summary", "results", "count", "total", "category_label"],
    )
    async def search_space_objects(
        self,
        query: str,
        category: str = "spacecraft",
        limit: Any = None,
        **_,
    ):
        """检索 LL2 中的航天实体资料。"""
        query = _as_text(query)
        if not query:
            return Err(SdkError("检索关键词不能为空。"))

        category = _as_text(category).lower() or "spacecraft"
        if category not in _LL2_SEARCH_TARGETS:
            allowed = "、".join(_LL2_SEARCH_TARGETS)
            return Err(SdkError(f"不支持的检索类别：{category}。可选：{allowed}"))

        try:
            wanted = _coerce_limit(limit, self._default_limit)
        except SdkError as exc:
            return Err(exc)

        label = _LL2_SEARCH_TARGETS[category][1]
        ll2_error = ""
        try:
            items, total = await self._search_ll2_entities(
                query=query,
                category=category,
                limit=wanted,
            )
        except _ApiError as exc:
            # LL2 免费额度很小，429 很常见。这并不代表“这个词条不存在”，
            # 所以降级到其他来源，而不是直接失败。
            self.logger.warning("检索{}失败，尝试其他来源: {}", label, exc)
            items, total = [], 0
            ll2_error = str(exc)

        # LL2 没结果或不可用时，依次尝试百度百科与网页兜底
        if not items:
            try:
                lemma = await self._fetch_baike_lemma(query)
            except _ApiError as exc:
                self.logger.warning("百度百科查询失败: {}", exc)
                lemma = None

            if lemma is not None:
                self.logger.info("【{}】改由百度百科命中：{}", query, lemma.get("title"))
                return Ok({
                    "found": True,
                    "source": "百度百科",
                    "summary": _describe_baike_lemma(lemma),
                    "results": [lemma],
                    "count": 1,
                    "total": 1,
                    "category": category,
                    "category_label": label,
                    "note": f"{label}数据库没有收录，以下为百度百科条目。",
                })

            try:
                web_items = await self._search_web(query, wanted)
            except _ApiError as exc:
                self.logger.warning("网页兜底检索失败: {}", exc)
                web_items = []

            if web_items:
                self.logger.info("【{}】改由网页兜底命中 {} 条", query, len(web_items))
                return Ok({
                    "found": True,
                    "source": "网页搜索",
                    "summary": _describe_bing_results(web_items, query=query),
                    "results": web_items,
                    "count": len(web_items),
                    "total": len(web_items),
                    "category": category,
                    "category_label": label,
                    "note": (
                        f"{label}数据库没有收录，以下为网页检索结果，"
                        "请以链接原文为准，不要补充未提及的参数。"
                    ),
                })

            self.logger.info("{}未收录「{}」", label, query)
            payload = _no_result_payload(query=query, label=label)
            if ll2_error:
                payload["ll2_error"] = ll2_error
            return Ok(payload)

        self.logger.info("检索到 {} 条{}（关键词：{}）", len(items), label, query)
        return Ok({
            "summary": _describe_ll2_list(items, query=query, label=label),
            "results": items,
            "count": len(items),
            "total": total,
            "category": category,
            "category_label": label,
        })

    @plugin_entry(
        id="search_nasa_documents",
        name="NASA 技术文献检索",
        description=(
            "在 NASA 技术报告库（NTRS）中检索航天相关的技术文献、会议论文与报告，"
            "返回标题、摘要、作者、所属研究中心与全文链接。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "检索关键词，建议使用英文术语，"
                        "例如 James Webb Space Telescope、ion thruster"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-20，默认使用插件配置值",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
        timeout=30,
        llm_result_fields=["summary", "documents", "count", "total"],
    )
    async def search_nasa_documents(self, query: str, limit: Any = None, **_):
        """检索 NASA 技术文献。"""
        query = _as_text(query)
        if not query:
            return Err(SdkError("检索关键词不能为空。"))

        try:
            wanted = _coerce_limit(limit, self._default_limit)
        except SdkError as exc:
            return Err(exc)

        try:
            items, total = await self._search_ntrs_documents(
                query=query,
                limit=wanted,
            )
        except _ApiError as exc:
            self.logger.warning("检索 NASA 技术文献失败: {}", exc)
            return Err(SdkError(str(exc)))
        except Exception as exc:  # pragma: no cover - 兜底
            self.logger.exception("检索 NASA 技术文献时发生未预期错误")
            return Err(SdkError(f"检索 NASA 技术文献失败：{exc}"))

        if not items:
            self.logger.info("NTRS 未收录「{}」", query)
            return Ok(
                _no_result_payload(query=query, label="NASA 技术文献")
            )

        self.logger.info("检索到 {} 篇 NASA 技术文献（关键词：{}）", len(items), query)
        return Ok({
            "summary": _describe_ntrs_list(items, query=query),
            "documents": items,
            "count": len(items),
            "total": total,
        })

    @plugin_entry(
        id="baike_search",
        name="中文百科检索",
        description=(
            "查询百度百科的中文条目摘要，适合检索中文航天资料，"
            "例如「长征五号」「东方红一号」「天宫空间站」。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查询的中文词条名，例如 长征五号",
                },
            },
            "required": ["query"],
        },
        timeout=30,
        llm_result_fields=["found", "summary", "title", "abstract", "url"],
    )
    async def baike_search(self, query: str, **_):
        """查询百度百科条目。"""
        query = _as_text(query)
        if not query:
            return Err(SdkError("检索关键词不能为空。"))

        try:
            item = await self._fetch_baike_lemma(query)
        except _ApiError as exc:
            self.logger.warning("百度百科检索失败: {}", exc)
            return Err(SdkError(str(exc)))
        except Exception as exc:  # pragma: no cover - 兜底
            self.logger.exception("百度百科检索时发生未预期错误")
            return Err(SdkError(f"百度百科检索失败：{exc}"))

        if item is None:
            self.logger.info("百度百科未收录「{}」", query)
            return Ok(_no_result_payload(query=query, label="百科条目"))

        self.logger.info("百度百科命中：{}", item.get("title"))
        return Ok({
            "found": True,
            "summary": _describe_baike_lemma(item),
            "title": item.get("title"),
            "abstract": item.get("abstract"),
            "url": item.get("url"),
            "image": item.get("image"),
            "source": item.get("source"),
        })

    @plugin_entry(
        id="web_fallback_search",
        name="网页资料兜底检索",
        description=(
            "当本地航天数据库和百科都没有收录时，用网页搜索获取相关资料链接。"
            "返回标题、链接与摘要，便于进一步查阅原文。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词，例如 UR-700A 火箭",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-10，默认 5",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
        timeout=30,
        llm_result_fields=["found", "summary", "results", "count"],
    )
    async def web_fallback_search(self, query: str, limit: Any = 5, **_):
        """网页兜底检索。"""
        query = _as_text(query)
        if not query:
            return Err(SdkError("检索关键词不能为空。"))

        try:
            wanted = _coerce_limit(limit, 5)
        except SdkError as exc:
            return Err(exc)

        try:
            items = await self._search_web(query, wanted)
        except _ApiError as exc:
            self.logger.warning("网页检索失败: {}", exc)
            return Err(SdkError(str(exc)))
        except Exception as exc:  # pragma: no cover - 兜底
            self.logger.exception("网页检索时发生未预期错误")
            return Err(SdkError(f"网页检索失败：{exc}"))

        if not items:
            self.logger.info("网页检索无结果：「{}」", query)
            return Ok(
                _no_result_payload(query=query, label="网页结果")
            )

        self.logger.info("网页检索到 {} 条（关键词：{}）", len(items), query)
        return Ok({
            "found": True,
            "summary": _describe_bing_results(items, query=query),
            "results": items,
            "count": len(items),
        })

    @plugin_entry(
        id="sat_wiki_search",
        name="卫星百科检索",
        description=(
            "检索国内卫星百科（sat.huijiwiki.com / 灰机wiki）的中文航天条目，"
            "覆盖火箭、卫星、航天器、发射场、航天机构与任务等。"
            "适合回答「风云四号是什么卫星」「长征七号」「文昌发射场」"
            "这类中文航天百科问题。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要检索的中文词条或关键词，例如 风云卫星、长征五号、文昌发射场",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-20，默认使用插件配置值",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
        timeout=30,
        llm_result_fields=["found", "summary", "results", "count", "total"],
    )
    async def sat_wiki_search(self, query: str, limit: Any = None, **_):
        """检索卫星百科（灰机wiki）条目。"""
        query = _as_text(query)
        if not query:
            return Err(SdkError("检索关键词不能为空。"))

        try:
            wanted = _coerce_limit(limit, self._default_limit)
        except SdkError as exc:
            return Err(exc)

        try:
            items, total = await self._search_sat_wiki(query=query, limit=wanted)
        except _ApiError as exc:
            self.logger.warning("卫星百科检索失败: {}", exc)
            return Err(SdkError(str(exc)))
        except Exception as exc:  # pragma: no cover - 兜底
            self.logger.exception("卫星百科检索时发生未预期错误")
            return Err(SdkError(f"卫星百科检索失败：{exc}"))

        if not items:
            self.logger.info("卫星百科未收录「{}」", query)
            return Ok(_no_result_payload(query=query, label="卫星百科条目"))

        self.logger.info("卫星百科命中 {} 条（关键词：{}）", len(items), query)
        return Ok({
            "found": True,
            "source": _SAT_WIKI_SOURCE,
            "summary": _describe_sat_wiki_list(items, query=query),
            "results": items,
            "count": len(items),
            "total": total,
        })

    @llm_tool(
        name="lookup_satellite_wiki",
        description=(
            "检索国内卫星百科（sat.huijiwiki.com / 灰机wiki）的中文航天条目，"
            "涵盖火箭、卫星、航天器、发射场、航天机构等权威中文资料。"
            "当用户提到具体的中文航天名词（如「风云四号」「长征七号」"
            "「文昌发射场」「神舟飞船」）并想了解其资料时调用。"
            "若卫星百科没有结果，返回值会明确说明未收录，请如实告诉用户。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查询的卫星/火箭/航天器/发射场中文名称或关键词",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-20，默认 3",
                },
            },
            "required": ["query"],
        },
        timeout=30,
    )
    async def lookup_satellite_wiki(
        self,
        query: str,
        limit: Any = 3,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """LLM 工具：检索卫星百科（灰机wiki）条目。"""
        query = _as_text(query)
        if not query:
            return {"output": None, "is_error": True, "error": "检索关键词不能为空。"}

        try:
            wanted = _coerce_limit(limit, 3)
        except SdkError as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        try:
            items, total = await self._search_sat_wiki(query=query, limit=wanted)
        except Exception as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        if not items:
            return {
                "output": _no_result_payload(query=query, label="卫星百科条目"),
                "is_error": False,
            }

        return {
            "output": {
                "found": True,
                "source": _SAT_WIKI_SOURCE,
                "summary": _describe_sat_wiki_list(items, query=query),
                "results": [_describe_sat_wiki_item(i) for i in items],
                "total": total,
            }
        }

    @llm_tool(
        name="search_space_knowledge",
        description=(
            "在航天数据库中检索航天器、火箭、空间站、航天机构或宇航员的资料。"
            "当用户询问「猎鹰九号是什么火箭」「国际空间站的信息」「NASA 是什么机构」"
            "这类问题时调用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词，例如 Falcon 9、ISS、NASA、Soyuz",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "spacecraft",
                        "launcher",
                        "station",
                        "agency",
                        "astronaut",
                    ],
                    "description": (
                        "检索类别：spacecraft=航天器（默认）、launcher=火箭型号、"
                        "station=空间站、agency=航天机构、astronaut=宇航员"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-20，默认 3",
                },
            },
            "required": ["query"],
        },
        timeout=30,
    )
    async def search_space_knowledge(
        self,
        query: str,
        category: str = "spacecraft",
        limit: Any = 3,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """LLM 工具：检索航天实体资料。"""
        query = _as_text(query)
        if not query:
            return {"output": None, "is_error": True, "error": "检索关键词不能为空。"}

        category = _as_text(category).lower() or "spacecraft"
        if category not in _LL2_SEARCH_TARGETS:
            return {
                "output": None,
                "is_error": True,
                "error": f"不支持的检索类别：{category}",
            }

        try:
            wanted = _coerce_limit(limit, 3)
        except SdkError as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        label = _LL2_SEARCH_TARGETS[category][1]
        try:
            items, total = await self._search_ll2_entities(
                query=query,
                category=category,
                limit=wanted,
            )
        except Exception as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        if not items:
            return {
                "output": _no_result_payload(query=query, label=label),
                "is_error": False,
            }

        return {
            "output": {
                "found": True,
                "summary": _describe_ll2_list(items, query=query, label=label),
                "details": [_describe_ll2_entity(item) for item in items],
                "total": total,
                "category_label": label,
            }
        }

    @llm_tool(
        name="search_nasa_literature",
        description=(
            "在 NASA 技术报告库中检索航天技术文献与论文。"
            "当用户想深入了解某项航天技术的原理或研究资料时调用，关键词建议使用英文。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词（建议英文），例如 ion thruster、reentry",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，1-20，默认 3",
                },
            },
            "required": ["query"],
        },
        timeout=30,
    )
    async def search_nasa_literature(
        self,
        query: str,
        limit: Any = 3,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """LLM 工具：检索 NASA 技术文献。"""
        query = _as_text(query)
        if not query:
            return {"output": None, "is_error": True, "error": "检索关键词不能为空。"}

        try:
            wanted = _coerce_limit(limit, 3)
        except SdkError as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        try:
            items, total = await self._search_ntrs_documents(
                query=query,
                limit=wanted,
            )
        except Exception as exc:
            return {"output": None, "is_error": True, "error": str(exc)}

        if not items:
            return {
                "output": _no_result_payload(query=query, label="NASA 技术文献"),
                "is_error": False,
            }

        return {
            "output": {
                "found": True,
                "summary": _describe_ntrs_list(items, query=query),
                "details": [_describe_ntrs_document(item) for item in items],
                "total": total,
            }
        }

    @llm_tool(
        name="lookup_space_info",
        description=(
            "查询任何航天相关名词的权威资料，会自动依次检索航天数据库、"
            "百度百科、卫星百科（灰机wiki）与网页兜底搜索。当用户询问某个火箭、"
            "卫星、航天器、机构或型号（例如「UR-700A 是什么」「长征五号」）时"
            "优先调用这个工具。如果所有来源都没有结果，返回值会明确说明未收录，"
            "请如实告诉用户。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查询的航天名词，中文或英文均可",
                },
            },
            "required": ["query"],
        },
        timeout=45,
    )
    async def lookup_space_info(self, query: str, **kwargs: Any) -> Dict[str, Any]:
        """LLM 工具：跨来源综合查询。

        按「航天数据库 -> 中文百科 -> 网页」的顺序依次尝试，任一来源命中即返回，
        避免让模型在没有资料时凭空作答。
        """
        query = _as_text(query)
        if not query:
            return {"output": None, "is_error": True, "error": "检索关键词不能为空。"}

        attempts: List[str] = []

        # 1) Launch Library 2：先按航天器查，再按火箭型号查
        for category in ("spacecraft", "launcher"):
            label = _LL2_SEARCH_TARGETS[category][1]
            try:
                items, total = await self._search_ll2_entities(
                    query=query,
                    category=category,
                    limit=3,
                )
            except Exception as exc:
                attempts.append(f"{label}：查询失败（{type(exc).__name__}）")
                continue
            if items:
                return {
                    "output": {
                        "found": True,
                        "source": f"Launch Library 2（{label}）",
                        "summary": _describe_ll2_list(
                            items, query=query, label=label
                        ),
                        "details": [_describe_ll2_entity(i) for i in items],
                        "total": total,
                    }
                }
            attempts.append(f"{label}：未收录")

        # 2) 百度百科（中文资料覆盖面更广）
        try:
            lemma = await self._fetch_baike_lemma(query)
        except Exception:
            lemma = None
            attempts.append("百度百科：查询失败")
        else:
            if lemma is not None:
                return {
                    "output": {
                        "found": True,
                        "source": "百度百科",
                        "summary": _describe_baike_lemma(lemma),
                        "url": lemma.get("url"),
                    }
                }
            attempts.append("百度百科：未收录")

        # 2.5) 卫星百科（灰机wiki）：中文航天百科，覆盖火箭/卫星/发射场等
        try:
            sat_items, sat_total = await self._search_sat_wiki(query=query, limit=3)
        except Exception:
            sat_items, sat_total = [], 0
            attempts.append("卫星百科：查询失败")
        else:
            if sat_items:
                return {
                    "output": {
                        "found": True,
                        "source": _SAT_WIKI_SOURCE,
                        "summary": _describe_sat_wiki_list(sat_items, query=query),
                        "results": [_describe_sat_wiki_item(i) for i in sat_items],
                        "total": sat_total,
                    }
                }
            attempts.append("卫星百科：未收录")

        # 3) 网页兜底
        try:
            web_items = await self._search_web(query, 5)
        except Exception:
            web_items = []
            attempts.append("网页搜索：查询失败")
        else:
            if web_items:
                return {
                    "output": {
                        "found": True,
                        "source": "网页搜索",
                        "summary": _describe_bing_results(web_items, query=query),
                        "results": web_items,
                        "note": "以下为网页检索结果，请以链接原文为准，不要补充未提及的参数。",
                    }
                }
            attempts.append("网页搜索：无结果")

        self.logger.info("综合查询未命中「{}」：{}", query, "；".join(attempts))
        payload = _no_result_payload(query=query, label="资料")
        payload["attempts"] = attempts
        payload["summary"] = (
            f"所有来源都没有查到「{query}」：{'；'.join(attempts)}。"
            f"{_NO_RESULT_GUIDANCE}"
        )
        return {"output": payload, "is_error": False}


__all__ = ["SpaceLaunchPlugin"]
