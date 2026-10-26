"""B站看板 — 哔哩哔哩只读浏览 API 层（纯标准库实现）。

覆盖：视频信息/分P/标签、弹幕（seg.so protobuf，XML 兜底）、CC 字幕、AI 概要、
评论、搜索、直播信息、直播实时弹幕（标准库手写 WebSocket）、UP主信息与视频列表。

安全边界：只读公开内容；不登录、不发评论/弹幕/动态、不下载视频文件；
可选用环境变量 BILI_COOKIE（完整 Cookie 字符串，含 SESSDATA）提升部分
接口的可用性，但插件绝不写入或修改任何账号数据。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import re
import socket
import ssl
import struct
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_API = "https://api.bilibili.com"
_LIVE_API = "https://api.live.bilibili.com"
_TIMEOUT = 12.0

# wbi 签名：mixin key 字符打乱表（公开算法）
_MIXIN_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 25,
    0, 5, 36, 11, 33, 6, 17, 41, 49, 39, 60, 9, 1, 54, 61, 28, 19, 37, 29,
    26, 14, 44, 24, 13, 7, 20, 40, 38, 4, 57, 22, 16, 63, 55, 12, 34, 42,
    48, 21, 59, 30, 52, 62, 56, 51,
]

_BVID_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_ROOM_RE = re.compile(r"live\.bilibili\.com/(\d+)")

# 动态发布短窗去重（v0.9.22 全盘复盘）：同账号同内容 _DYN_DUP_WINDOW 秒内重复调用直接拒绝。
# 防"发一次变3条"（用户实证 v0.9.5：YUI 因回读延迟/误判重试导致重复动态）；
# 接口接受即记录（无论回读结果），重试一律被挡，杜绝连续盲发。
_DYN_DUP_WINDOW = 60.0
_dyn_last_posts: dict[tuple[str, str], float] = {}


class BiliError(Exception):
    """B站接口错误：message 为用户可读描述，code 为 B站/本地错误码。"""

    def __init__(self, message: str, code: str = "BIZ_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


# ══════════════ 基础 HTTP ══════════════

_YUI_COOKIE_FILE = "yui_cookie.txt"  # 插件目录下的 YUI 专属小号 Cookie 文件（与本机主账号 BILI_COOKIE 完全隔离）
_MAIN_COOKIE_FILE = "main_cookie.txt"  # 插件目录下的主账号 Cookie 文件（网页接入/扩展/CDP 绑定的持久化存储，v0.7.10）
_YUI_COOKIE_DIR = os.path.dirname(os.path.abspath(__file__))  # 可注入目录便于测试
_yui_cookie_file_cache: tuple[float, str] = (0.0, "")  # (mtime, 内容)，避免 watcher 高频轮询反复读盘
_main_cookie_file_cache: tuple[float, str] = (0.0, "")


def _yui_cookie_from_file() -> str:
    """从插件目录 yui_cookie.txt 读取 YUI 小号 Cookie（本地专用，不上传不写日志）。

    复查优化（v0.7.3）：文件 mtime 缓存——自主运营 watcher 每 2.5s 检查一次小号
    配置，文件方式配置时若每次读盘会浪费 IO；mtime 未变则直接用缓存。
    """
    global _yui_cookie_file_cache
    p = os.path.join(_YUI_COOKIE_DIR, _YUI_COOKIE_FILE)
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        _yui_cookie_file_cache = (0.0, "")
        return ""
    cache_ts, cache_val = _yui_cookie_file_cache
    if cache_ts == mtime and cache_val:
        return cache_val
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        content = ""
    _yui_cookie_file_cache = (mtime, content)
    return content


def _main_cookie_from_file() -> str:
    """从插件目录 main_cookie.txt 读取主账号 Cookie（绑定持久化，v0.7.10）。"""
    global _main_cookie_file_cache
    p = os.path.join(_YUI_COOKIE_DIR, _MAIN_COOKIE_FILE)
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        _main_cookie_file_cache = (0.0, "")
        return ""
    cache_ts, cache_val = _main_cookie_file_cache
    if cache_ts == mtime and cache_val:
        return cache_val
    try:
        with open(p, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        content = ""
    _main_cookie_file_cache = (mtime, content)
    return content


def write_main_cookie_file(cookie: str) -> None:
    """持久化主账号 Cookie 到插件目录 main_cookie.txt（绑定流程调用，v0.7.10）。

    不手动清缓存：mtime 已变化，下次读取自然重读文件。
    """
    p = os.path.join(_YUI_COOKIE_DIR, _MAIN_COOKIE_FILE)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(cookie.strip())
    except OSError:
        pass  # 文件写失败不致命（env/配置层仍可生效）


def write_yui_cookie_file(cookie: str) -> None:
    """持久化小号 Cookie 到插件目录 yui_cookie.txt（第二个登录的账号自动归位，v0.7.10）。"""
    global _yui_cookie_file_cache
    _yui_cookie_file_cache = (0.0, "")
    p = os.path.join(_YUI_COOKIE_DIR, _YUI_COOKIE_FILE)
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(cookie.strip())
    except OSError:
        pass


def cookie_uid(cookie: str) -> int | None:
    """从 Cookie 中解析 B站账号 uid（DedeUserID 字段，无需网络）。"""
    import re as _re

    m = _re.search(r"(?:^|;)\s*DedeUserID=(\d+)", str(cookie or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


_ROLE_FILE = "role.json"  # 角色映射：{"uid": "yui"|"main"}——用户明确声明"某 uid 是小号/大号"时生效（v0.7.30）


def _load_roles() -> dict:
    try:
        with open(os.path.join(_YUI_COOKIE_DIR, _ROLE_FILE), "r", encoding="utf-8") as fh:
            data = json.load(fh) or {}
        return {str(k): str(v) for k, v in data.items() if v in ("yui", "main")}
    except (OSError, ValueError):
        return {}


def _save_roles(roles: dict) -> None:
    with open(os.path.join(_YUI_COOKIE_DIR, _ROLE_FILE), "w", encoding="utf-8") as fh:
        json.dump(roles, fh, ensure_ascii=False, indent=2)


def _cookie_for_uid(uid: int) -> str:
    """按 uid 在现有 cookie 文件中匹配登录态（DedeUserID=uid），取修改时间最新的一份。"""
    best, best_mtime = "", 0.0
    for fn in (_MAIN_COOKIE_FILE, _YUI_COOKIE_FILE):
        p = os.path.join(_YUI_COOKIE_DIR, fn)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            if f"DedeUserID={uid}" in content and os.path.getmtime(p) >= best_mtime:
                best, best_mtime = content, os.path.getmtime(p)
        except OSError:
            continue
    return best


def _cookie(account: str = "main") -> str:
    """账号 Cookie：main=主账号（env BILI_COOKIE > 插件目录 main_cookie.txt）；
    yui=YUI 独立小号（env BILI_YUI_COOKIE > 插件目录 yui_cookie.txt）。

    v0.7.10：绑定流程按登录顺序归位——第一个登录的账号持久化为 main_cookie.txt（大号），
    第二个登录的账号自动归为 yui_cookie.txt（小号，默认操作账号）。
    v0.7.30：role.json 角色映射优先——用户声明"某 uid 是小号/大号"后，该角色的
    Cookie 直接从现有 cookie 文件按 uid 匹配，无需重新登录、不覆盖任何文件。
    """
    roles = _load_roles()
    for uid_s, role in roles.items():
        if role == account:
            matched = _cookie_for_uid(int(uid_s))
            if matched:
                return matched
            break
    if account == "yui":
        return os.environ.get("BILI_YUI_COOKIE", "").strip() or _yui_cookie_from_file()
    return os.environ.get("BILI_COOKIE", "").strip() or _main_cookie_from_file()


def _open_with_retry(req, timeout: float, retries: int = 3, jitter: bool = False):
    """urlopen 网络层自动重试（防拦截加固，v0.7.11）。

    - jitter=True（写操作默认）：每次调用前随机等待 0.3~1.2s，让 YUI 自主发起的高频
      写请求（弹幕/评论/动态/投稿）时间分布更自然，降低被 B站 风控识别为机器的概率；
    - 网络错误/超时/HTTP 5xx：指数退避重试 retries 次（基数 0.8/1.8/3.6s ± 随机抖动）；
    - HTTP 4xx 与业务错误码不重试：避免把同一写操作重复提交放大风险。
    """
    if jitter:
        time.sleep(random.uniform(0.3, 1.2))
    last: BaseException | None = None
    for i in range(retries + 1):
        if i:
            time.sleep(random.uniform(0.5, 1.5) * _RETRY_BASE[i - 1])
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 and i < retries:
                last = exc
                continue
            raise BiliError(f"接口 HTTP {exc.code}", "HTTP_ERROR") from exc
        except urllib.error.URLError as exc:
            if i < retries:
                last = exc
                continue
            raise BiliError(f"网络错误：{exc.reason}", "NETWORK") from exc
        except socket.timeout as exc:
            if i < retries:
                last = exc
                continue
            raise BiliError("请求超时", "TIMEOUT") from exc
    raise BiliError(f"网络错误：{last}", "NETWORK") from last


_RETRY_BASE = (0.8, 1.8, 3.6)


def _http_get(url: str, params: dict | None = None, timeout: float = _TIMEOUT, account: str = "main") -> dict:
    """GET JSON 接口；返回解析后的 dict。"""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    })
    if _cookie(account):
        req.add_header("Cookie", _cookie(account))
    try:
        with _open_with_retry(req, timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise BiliError(f"接口 HTTP {exc.code}", "HTTP_ERROR") from exc
    except urllib.error.URLError as exc:
        raise BiliError(f"网络错误：{exc.reason}", "NETWORK") from exc
    except socket.timeout as exc:
        raise BiliError("请求超时", "TIMEOUT") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BiliError("接口返回非 JSON", "BAD_RESPONSE") from exc
    if data.get("code", 0) != 0:
        code = data.get("code", -1)
        if code in (-352, -401, -412):
            raise BiliError("触发风控限制，请稍后再试（或配置 BILI_COOKIE）", "RISK_CONTROL")
        if code == -509:
            # v0.9.25 对照 bili-cli errors.py BILI_CODE_MAP：-509 = 访问过于频繁
            raise BiliError("访问过于频繁，请等待 1-2 分钟后重试（严禁连续重试）", "RISK_CONTROL")
        if code == -101:
            raise BiliError("登录态失效或未登录（请重新配置有效的 BILI_COOKIE）", "AUTH_FAILED")
        if code == -404:
            raise BiliError("内容不存在或已被删除", "NOT_FOUND")
        if code == -403:
            raise BiliError("无权限访问", "FORBIDDEN")
        raise BiliError(data.get("message") or data.get("msg") or "B站接口错误", f"BIZ_{code}")
    return data


# ══════════════ wbi 签名 ══════════════


_mixin_cache: str | None = None
_mixin_cache_ts: float = 0.0


def _get_mixin_key() -> str:
    """从 nav 接口获取 img/sub key 并生成 mixin key（进程内缓存 1 小时）。

    注意：未登录时 nav 返回 code=-101，但 data.wbi_img 仍会给出游客密钥，
    因此这里不能用 _http_get（它会在 code!=0 时抛错）。
    """
    global _mixin_cache, _mixin_cache_ts
    now = time.time()
    if _mixin_cache is not None and now - _mixin_cache_ts < 3600:
        return _mixin_cache
    req = urllib.request.Request(_API + "/x/web-interface/nav", headers={
        "User-Agent": _UA,
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    })
    if _cookie():
        req.add_header("Cookie", _cookie())
    try:
        with _open_with_retry(req, _TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise BiliError(f"接口 HTTP {exc.code}", "HTTP_ERROR") from exc
    except urllib.error.URLError as exc:
        raise BiliError(f"网络错误：{exc.reason}", "NETWORK") from exc
    except socket.timeout as exc:
        raise BiliError("请求超时", "TIMEOUT") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BiliError("接口返回非 JSON", "BAD_RESPONSE") from exc
    wbi = (data.get("data") or {}).get("wbi_img") or {}
    img_url = wbi.get("img_url") or ""
    sub_url = wbi.get("sub_url") or ""
    # 兼容新旧两种格式：img_7cd0....png 与 7cd0....png
    img = re.search(r"/(?:img_)?([0-9a-f]{32})\.", img_url)
    sub = re.search(r"/(?:img_)?([0-9a-f]{32})\.", sub_url)
    if not img or not sub:
        raise BiliError("wbi 密钥解析失败（可配置 BILI_COOKIE 后重试）", "WBI_KEY")
    mixin = "".join((img.group(1) + sub.group(1))[i] for i in _MIXIN_TABLE)[:32]
    _mixin_cache, _mixin_cache_ts = mixin, now
    return mixin


def _wbi_sign(params: dict, mixin_key: str) -> dict:
    """给请求参数附加 wts 与 w_rid 签名。"""
    params = dict(params)
    params["wts"] = int(time.time())
    items = sorted(params.items(), key=lambda kv: str(kv[0]))
    query = urllib.parse.urlencode(items)
    params["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return params


def _wbi_get(url: str, params: dict, timeout: float = _TIMEOUT, account: str = "main") -> dict:
    """带 wbi 签名的 GET。v0.9.6 复查：加 account 参数（默认 main 保持向后兼容）。"""
    mixin = _get_mixin_key()
    signed = _wbi_sign(params, mixin)
    return _http_get(url, signed, timeout=timeout, account=account)


# ══════════════ 解析辅助 ══════════════


def _parse_bvid(text: str) -> str:
    """从输入提取 BV 号；支持完整 URL。"""
    m = _BVID_RE.search(text)
    if not m:
        raise BiliError("未找到 BV 号（请提供形如 BV1xx... 或 https://www.bilibili.com/video/BV1xx... 的链接）", "BAD_BVID")
    return m.group(1)


def _fmt_duration(seconds: int) -> str:
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_time(ts: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except (ValueError, OSError):
        return str(ts)


# ══════════════ 视频 ══════════════


def video_info(bvid: str, account: str = "main") -> dict:
    """视频详情：标题/UP主/时长/分区/互动数据/分P/字幕/标签。

    v0.8.9：支持 account 参数——点赞/投币/收藏等操作链路用当前操作账号的 cookie
    查详情（避免主账号 cookie 失效时整条操作链路报 -403 无权限访问）。
    """
    bvid = _parse_bvid(bvid)
    data = (_http_get(_API + "/x/web-interface/view", {"bvid": bvid}, account=account)).get("data") or {}
    if not data:
        raise BiliError("视频不存在或已被删除", "NOT_FOUND")
    owner = data.get("owner") or {}
    stat = data.get("stat") or {}
    pages = data.get("pages") or []
    subtitle_list = ((data.get("subtitle") or {}).get("list")) or []
    tags = []
    try:
        tags_data = _http_get(_API + "/x/tag/archive/tags", {"bvid": bvid}).get("data") or []
        tags = [t.get("tag_name", "") for t in tags_data if t.get("tag_name")]
    except BiliError:
        tags = []
    return {
        "bvid": bvid,
        "aid": data.get("aid"),
        "title": data.get("title", "").strip(),
        "desc": (data.get("desc") or "").strip(),
        "pic": data.get("pic", ""),
        "owner_mid": owner.get("mid"),
        "owner_name": owner.get("name", ""),
        "pubdate": data.get("pubdate"),
        "duration": data.get("duration", 0),
        "tname": data.get("tname", ""),
        "view": stat.get("view", 0),
        "danmaku": stat.get("danmaku", 0),
        "reply": stat.get("reply", 0),
        "favorite": stat.get("favorite", 0),
        "coin": stat.get("coin", 0),
        "share": stat.get("share", 0),
        "like": stat.get("like", 0),
        "pages": [
            {"cid": p.get("cid"), "page": p.get("page"), "part": (p.get("part") or "").strip(), "duration": p.get("duration", 0)}
            for p in pages
        ],
        "subtitle_urls": [
            {
                "lan": s.get("lan", ""),
                "lan_doc": s.get("lan_doc", ""),
                "url": s.get("subtitle_url", ""),
            }
            for s in subtitle_list
            if s.get("subtitle_url")
        ],
        "tags": tags,
    }


def format_video_info(info: dict) -> str:
    """把 video_info 渲染为给 YUI 的文本。"""
    parts = info.get("pages") or []
    sub = info.get("subtitle_urls") or []
    lines = [
        f"标题：{info['title']}",
        f"UP主：{info['owner_name']}（mid {info['owner_mid']}）",
        f"BV号：{info['bvid']} ｜ 分区：{info['tname']}",
        f"发布时间：{_fmt_time(info['pubdate'])} ｜ 时长：{_fmt_duration(info['duration'])}",
        f"播放 {info['view']} ｜ 弹幕 {info['danmaku']} ｜ 评论 {info['reply']} ｜ "
        f"点赞 {info['like']} ｜ 投币 {info['coin']} ｜ 收藏 {info['favorite']} ｜ 分享 {info['share']}",
    ]
    if parts:
        first = parts[0]
        lines.append(f"分P：共 {len(parts)} 个，当前第 {first.get('page', 1)} 集「{first.get('part') or '正片'}」（cid {first.get('cid')}）")
    if info.get("desc"):
        lines.append("简介：" + (info["desc"][:200] + "…" if len(info["desc"]) > 200 else info["desc"]))
    if info.get("tags"):
        lines.append("标签：" + "、".join(info["tags"][:10]))
    if sub:
        lines.append("字幕：" + "、".join(f"{s['lan_doc']}({s['lan']})" for s in sub))
    return "\n".join(lines)


def _proto_fields(data: bytes) -> list[tuple[int, int, int | None, bytes | None]]:
    """轻量 protobuf wire 解析：返回 [(field_no, wire_type, varint值, 原始bytes)]。"""
    out: list[tuple[int, int, int | None, bytes | None]] = []
    pos = 0
    n = len(data)
    while pos < n:
        key = 0
        shift = 0
        while pos < n:
            b = data[pos]
            pos += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        no, wire = key >> 3, key & 7
        if wire == 0:
            val = 0
            shift = 0
            while pos < n:
                b = data[pos]
                pos += 1
                val |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            out.append((no, wire, val, None))
        elif wire == 1:
            out.append((no, wire, None, data[pos:pos + 8]))
            pos += 8
        elif wire == 2:
            ln = 0
            shift = 0
            while pos < n:
                b = data[pos]
                pos += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not (b & 0x80):
                    break
            out.append((no, wire, None, data[pos:pos + ln]))
            pos += ln
        elif wire == 5:
            out.append((no, wire, None, data[pos:pos + 4]))
            pos += 4
        else:
            break
    return out


def _parse_danmaku_proto(raw: bytes) -> list[tuple[int, str]]:
    """解析 B站弹幕 protobuf（seg.so 新版响应）。

    顶层 = repeated DanmakuElem（field 1 嵌套消息，wire 2）；
    每条弹幕：文本 = field 7（string），进度 = field 15（varint，秒）。
    """
    items: list[tuple[int, str]] = []
    for no, wire, _v, blob in _proto_fields(raw):
        if no != 1 or wire != 2 or not blob:
            continue
        text: str | None = None
        progress = 0
        for sno, swire, sval, sblob in _proto_fields(blob):
            if sno == 7 and swire == 2 and sblob:
                try:
                    text = sblob.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
            elif sno == 15 and swire == 0 and sval is not None:
                progress = int(sval)
        if text:
            items.append((progress, text))
    return items


def _fetch_raw(url: str, params: dict) -> bytes:
    """带 UA/Referer/Cookie 的原始 GET，自动解 gzip。"""
    req = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com/video/"},
    )
    if _cookie():
        req.add_header("Cookie", _cookie())
    try:
        with _open_with_retry(req, _TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise BiliError(f"弹幕接口 HTTP {exc.code}", "HTTP_ERROR") from exc
    except urllib.error.URLError as exc:
        raise BiliError(f"网络错误：{exc.reason}", "NETWORK") from exc
    if raw[:2] == b"\x1f\x8b":  # gzip 压缩响应
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as exc:
            raise BiliError("弹幕数据解压失败", "DANMAKU_PARSE") from exc
    return raw


def video_danmaku(bvid: str, cid: int | None = None, limit: int = 200) -> list[str]:
    """拉取视频弹幕文本（默认第一P的 cid）。

    优先新接口 x/v2/dm/web/seg.so（protobuf，游客可用）；分 P 逐段采集；
    老接口 dm/list.so（XML）保留为兜底。
    """
    bvid = _parse_bvid(bvid)
    if cid is None:
        info = video_info(bvid)
        if not info["pages"]:
            raise BiliError("该视频没有可用的分P信息", "NO_CID")
        cid = info["pages"][0]["cid"]
    danmakus: list[str] = []
    # 新接口 seg.so：protobuf 弹幕，逐段采集直到 limit
    try:
        for seg in range(1, 8):
            raw = _fetch_raw(_API + "/x/v2/dm/web/seg.so", {"type": 1, "oid": cid, "segment_index": seg})
            items = _parse_danmaku_proto(raw)
            if not items:
                break
            for progress, text in items:
                danmakus.append(f"[{_fmt_duration(int(progress))}] {text}")
                if len(danmakus) >= limit:
                    break
            if len(danmakus) >= limit:
                break
    except BiliError:
        pass
    if not danmakus:
        # 老接口兜底：dm/list.so（部分情况仍返回 XML）
        try:
            raw = _fetch_raw(_API + "/x/v1/dm/list.so", {"oid": cid})
            text = raw.decode("utf-8", errors="replace")
            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                root = None
            if root is not None:
                for d in root.findall("d"):
                    p = (d.get("p") or "").split(",")
                    try:
                        ts = float(p[0]) if p and p[0] else 0.0
                    except ValueError:
                        ts = 0.0
                    danmakus.append(f"[{_fmt_duration(int(ts))}] {d.text or ''}".strip())
                    if len(danmakus) >= limit:
                        break
        except BiliError:
            pass
    if not danmakus:
        raise BiliError("该视频暂无弹幕（游客模式弹幕可能被B站加扰；配置 BILI_COOKIE 后重试）", "NO_DANMAKU")
    return danmakus


def video_subtitle(bvid: str, cid: int | None = None, lan: str = "zh-CN") -> str:
    """获取视频 CC 字幕文本（默认第一P、简体中文）。"""
    bvid = _parse_bvid(bvid)
    info = video_info(bvid)
    if cid is None:
        if not info["pages"]:
            raise BiliError("该视频没有分P信息", "NO_CID")
        cid = info["pages"][0]["cid"]
    subs = info["subtitle_urls"]
    if not subs:
        raise BiliError("该视频没有 CC 字幕", "NO_SUBTITLE")
    target = next((s for s in subs if s["lan"] == lan), subs[0])
    url = target["url"]
    if url.startswith("//"):
        url = "https:" + url
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": f"https://www.bilibili.com/video/{bvid}"})
    try:
        with _open_with_retry(req, _TIMEOUT) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, socket.timeout) as exc:
        raise BiliError(f"字幕获取失败：{exc}", "SUBTITLE_FAIL") from exc
    body = payload.get("body") or []
    if not body:
        raise BiliError("字幕内容为空", "NO_SUBTITLE")
    lines = []
    for item in body:
        content = (item.get("content") or "").strip()
        if content:
            lines.append(content)
    return "\n".join(lines)


def video_ai_summary(bvid: str) -> str:
    """B站官方 AI 视频概要（仅部分视频提供，游客不可用，需登录态）。"""
    bvid = _parse_bvid(bvid)
    info = video_info(bvid)
    if not info["pages"]:
        raise BiliError("该视频没有分P信息", "NO_CID")
    cid = info["pages"][0]["cid"]
    try:
        data = _wbi_get(
            _API + "/x/web-interface/view/conclusion/get",
            {"bvid": bvid, "cid": cid},
        ).get("data") or {}
    except BiliError:
        raise BiliError("AI 概要需要登录态（游客不可用；配置 BILI_COOKIE 后重试）", "NO_SUMMARY") from None
    model = data.get("model_result") or {}
    summary = (model.get("summary") or "").strip()
    if not summary:
        raise BiliError("该视频暂不提供 AI 概要", "NO_SUMMARY")
    return summary


def video_comments(bvid: str, mode: str = "hot", page: int = 1, limit: int = 15) -> list[dict]:
    """视频评论：mode=hot 热门 / new 最新；返回 rpid/用户/内容/点赞/回复。"""
    bvid = _parse_bvid(bvid)
    info = video_info(bvid)
    aid = info.get("aid")
    if not aid:
        raise BiliError("视频不存在", "NOT_FOUND")
    mode_code = 3 if mode == "hot" else 2
    data = _wbi_get(
        _API + "/x/v2/reply/main",
        {"type": 1, "oid": aid, "mode": mode_code, "next": max(0, page - 1), "ps": limit},
    ).get("data") or {}
    replies = []
    top = data.get("top_reply")
    if top:
        replies.append(top)
    replies.extend(data.get("replies") or [])
    if not replies:
        raise BiliError("该视频暂无评论", "NO_COMMENTS")
    out = []
    for r in replies[:limit]:
        member = r.get("member") or {}
        content = (r.get("content") or {}).get("message") or ""
        out.append({
            "rpid": r.get("rpid"),
            "user": member.get("uname", ""),
            "content": content.strip(),
            "like": r.get("like", 0),
            "rcount": r.get("rcount", 0),
            "ctime": r.get("ctime", 0),
            "reply_count": len(r.get("replies") or []),
        })
    return out


def popular(limit: int = 3) -> list[dict]:
    """B站热门视频列表（匿名可读），用于 YUI 无跟随目标时自主挑选内容。"""
    try:
        data = _http_get(_API + "/x/web-interface/popular", {"ps": min(max(limit, 1), 10), "pn": 1}, timeout=10)
    except BiliError as exc:
        raise BiliError("热门视频获取失败（" + exc.message + "）", "POPULAR_FAILED") from exc
    vlist = (data.get("data") or {}).get("list") or []
    out = []
    for v in vlist[:limit]:
        out.append({
            "bvid": v.get("bvid", ""),
            "title": (v.get("title") or "").strip(),
            "uname": ((v.get("owner") or {}).get("name") or ""),
            "play": v.get("stat", {}).get("view", 0),
            "desc": (v.get("desc") or "")[:80],
        })
    return out


def search(keyword: str, search_type: str = "video", limit: int = 8) -> list[dict]:
    """搜索视频/直播/用户。search_type: video / user / live。"""
    keyword = keyword.strip()
    if not keyword:
        raise BiliError("请输入搜索关键词", "BAD_KEYWORD")
    type_map = {"video": "video", "user": "bili_user", "live": "live_room"}
    stype = type_map.get(search_type, "video")
    data = _wbi_get(
        _API + "/x/web-interface/wbi/search/type",
        {"keyword": keyword, "search_type": stype, "page": 1},
        timeout=15,
    ).get("data") or {}
    results = data.get("result") or []
    out = []
    for r in results[:limit]:
        if stype == "video":
            out.append({
                "type": "video",
                "bvid": r.get("bvid", ""),
                "title": re.sub(r"<[^>]+>", "", r.get("title", "")),
                "author": r.get("author", ""),
                "play": r.get("play", 0),
                "danmaku": r.get("danmaku", 0),
                "duration": r.get("duration", ""),
                "pubdate": r.get("pubdate", 0),
            })
        elif stype == "bili_user":
            out.append({
                "type": "user",
                "mid": r.get("mid"),
                "uname": r.get("uname", ""),
                "fans": r.get("fans", 0),
                "videos": r.get("videos", 0),
                "sign": r.get("usign", "") or r.get("sign", ""),
            })
        elif stype == "live_room":
            out.append({
                "type": "live",
                "roomid": r.get("roomid"),
                "title": r.get("title", ""),
                "uname": r.get("uname", ""),
                "online": r.get("online", 0),
                "area": r.get("area_name", ""),
                "live_status": r.get("live_status", 0),
            })
    if not out:
        raise BiliError(f"没有找到与「{keyword}」相关的{ {'video': '视频', 'user': '用户', 'live': '直播'}.get(search_type, '内容') }", "NO_RESULT")
    return out


# ══════════════ 直播 ══════════════


def _resolve_room(room: str) -> dict:
    """解析房间号（支持 URL/短号/数字），返回真实 room_id 与直播状态。"""
    room = room.strip()
    m = _ROOM_RE.search(room)
    if m:
        room = m.group(1)
    if not room.isdigit():
        raise BiliError("请输入直播房间号或直播间链接（如 live.bilibili.com/12345）", "BAD_ROOM")
    try:
        data = (_http_get(_LIVE_API + "/room/v1/Room/room_init", {"id": int(room)})).get("data") or {}
    except BiliError as exc:
        raise BiliError("直播间解析失败：" + exc.message, "ROOM_INIT") from exc
    room_id = data.get("room_id")
    if not room_id:
        raise BiliError("直播间不存在", "ROOM_NOT_FOUND")
    return {"room_id": room_id, "live_status": data.get("live_status", 0)}


def _room_online_fallback(room_id: int) -> int:
    """直播中但旧接口 online=0 时，用 getInfoByRoom 的人气值兜底（v0.8.6）。

    B站 页面显示的"在线观众"来自 WS 实时流（无公开 HTTP 接口），接口 online
    是人气值口径——数值会与页面小数字不一致，语义按"人气"对待。
    """
    try:
        data = (_wbi_get(_LIVE_API + "/xlive/web-room/v1/index/getInfoByRoom", {"room_id": room_id})).get("data") or {}
        return int((data.get("room_info") or {}).get("online") or 0)
    except BiliError:
        return 0


def live_info(room: str) -> dict:
    """直播间信息：主播/标题/分区/在线/状态/封面/简介。

    主用旧版 room/v1/Room/get_info（免 wbi，未登录可用）；
    主播名通过 acc/info 补充（uid 已知时）。
    """
    resolved = _resolve_room(room)
    room_id = resolved["room_id"]
    try:
        data = (_http_get("https://api.live.bilibili.com/room/v1/Room/get_info", {"room_id": room_id})).get("data") or {}
    except BiliError:
        # 旧接口风控时回退到 web 接口（需 wbi 签名；配置 BILI_COOKIE 后可用）
        try:
            data = (_wbi_get(_LIVE_API + "/xlive/web-room/v1/index/getInfoByRoom", {"room_id": room_id})).get("data") or {}
            room_info = data.get("room_info") or {}
            anchor = data.get("anchor_info") or {}
            base = anchor.get("base_info") or {}
            live_status = room_info.get("live_status", resolved["live_status"])
            status_text = {0: "未开播", 1: "直播中", 2: "轮播中"}.get(live_status, "未知")
            return {
                "room_id": room_info.get("room_id") or room_id,
                "short_id": room_info.get("short_id"),
                "uid": room_info.get("uid"),
                "uname": room_info.get("uname") or base.get("uname", ""),
                "title": (room_info.get("title") or "").strip(),
                "cover": room_info.get("cover", ""),
                "live_status": live_status,
                "status_text": status_text,
                "online": room_info.get("online", 0),
                "area": room_info.get("area_name", ""),
                "parent_area": room_info.get("parent_area_name", ""),
                "description": (room_info.get("description") or "").strip(),
            }
        except BiliError as exc2:
            raise BiliError("直播间信息获取失败：" + exc2.message, "LIVE_INFO") from exc2
    live_status = data.get("live_status", resolved["live_status"])
    status_text = {0: "未开播", 1: "直播中", 2: "轮播中"}.get(live_status, "未知")
    uid = data.get("uid")
    uname = ""
    if uid:
        try:
            udata = (_http_get(_API + "/x/space/acc/info", {"mid": uid, "jsonp": "jsonp"})).get("data") or {}
            uname = udata.get("name") or ""
        except BiliError:
            uname = ""
    online = data.get("online", 0)
    if not online and live_status == 1:
        online = _room_online_fallback(room_id)  # v0.8.6：直播中在线=0 属接口异常，兜底人气值
    return {
        "room_id": data.get("room_id") or room_id,
        "short_id": data.get("short_id"),
        "uid": uid,
        "uname": uname,
        "title": (data.get("title") or "").strip(),
        "cover": data.get("cover", ""),
        "live_status": live_status,
        "status_text": status_text,
        "online": online,
        "area": data.get("area_name", ""),
        "parent_area": data.get("parent_area_name", ""),
        "description": (data.get("description") or "").strip(),
    }


def format_live_info(info: dict) -> str:
    lines = [
        f"直播间：{info['title']}",
        f"主播：{info['uname']}（uid {info['uid']}）",
        f"状态：{info['status_text']} ｜ 人气：{info['online']}",
        f"分区：{info['parent_area']} / {info['area']}",
        f"房间号：{info['room_id']}（短号 {info['short_id'] or '无'}）",
    ]
    if info.get("description"):
        desc = info["description"]
        lines.append("简介：" + (desc[:150] + "…" if len(desc) > 150 else desc))
    return "\n".join(lines)


# ══════════════ 直播实时弹幕（标准库 WebSocket） ══════════════


def _ws_connect(host: str, port: int, path: str, timeout: float) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    except (ssl.SSLError, OSError):
        sock.close()
        raise BiliError("弹幕服务器 TLS 连接失败", "WS_TLS")
    key = __import__("base64").b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: https://live.bilibili.com\r\n"
        "\r\n"
    )
    sock.sendall(handshake.encode("ascii"))
    sock.settimeout(timeout)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise BiliError("弹幕服务器握手失败", "WS_HANDSHAKE")
        buf += chunk
    head = buf.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
    if " 101 " not in head:
        raise BiliError(f"弹幕服务器拒绝连接：{head}", "WS_HANDSHAKE")
    return sock


def _ws_send(sock: socket.socket, payload: bytes, opcode: int = 0x2) -> None:
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _ws_recv(sock: socket.socket) -> bytes:
    def _read(n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise BiliError("弹幕连接已断开", "WS_CLOSED")
            data += chunk
        return data

    b0, b1 = _read(2)
    fin = b0 & 0x80
    opcode = b0 & 0x0F
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", _read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read(8))[0]
    payload = _read(length)
    if opcode == 0x8:  # close
        raise BiliError("弹幕连接被服务器关闭", "WS_CLOSED")
    if opcode == 0x9:  # ping → pong
        _ws_send(sock, payload, opcode=0xA)
        return b""
    if not fin:
        rest = _ws_recv(sock)
        return payload + rest
    return payload


def _bili_pack(body: bytes, op: int, version: int = 1) -> bytes:
    total = 16 + len(body)
    # 头：包长(4B) + 头长(2B=16) + 协议版本(2B) + 操作码(4B) + 序号(4B)
    return struct.pack(">IHHII", total, 16, version, op, 1) + body


def _bili_unpack(buf: bytes) -> list[tuple[int, int, bytes]]:
    """拆解 B站弹幕协议包：返回 [(op, version, body)]。"""
    out = []
    pos = 0
    while pos + 16 <= len(buf):
        (total, header_len, version, op, _seq) = struct.unpack_from(">IHHII", buf, pos)
        if total < 16 or header_len < 16 or pos + total > len(buf):
            break
        body = buf[pos + header_len:pos + total]
        out.append((op, version, body))
        pos += total
        if total <= 0:
            break
    return out


def _danmaku_messages(raw: bytes) -> list[tuple[int, bytes]]:
    """处理 op=5 消息：返回 [(version, body)]；zlib 压缩版递归解压。"""
    result = []
    for op, version, body in _bili_unpack(raw):
        if op != 5:
            continue
        if version == 2:
            try:
                result.extend(_danmaku_messages(zlib.decompress(body)))
            except zlib.error:
                continue
        else:
            result.append((version, body))
    return result


def live_danmaku(room: str, duration: int = 8, limit: int = 60) -> list[dict]:
    """连接直播弹幕服务器采集 duration 秒内的实时弹幕（匿名游客）。"""
    resolved = _resolve_room(room)
    room_id = resolved["room_id"]
    try:
        # getDanmuInfo 需要 wbi 签名；游客密钥可获取，仍失败通常是网络 IP 被风控（-352）
        data = (_wbi_get(_LIVE_API + "/xlive/web-room/v1/index/getDanmuInfo", {"id": room_id})).get("data") or {}
    except BiliError as exc:
        raise BiliError("弹幕服务器信息获取失败（当前网络可能被B站风控；配置 BILI_COOKIE 或更换网络后重试）：" + exc.message, "DANMU_INFO") from exc
    token = data.get("token") or ""
    hosts = data.get("host_list") or []
    if not hosts:
        raise BiliError("未获取到弹幕服务器地址", "DANMU_INFO")
    host = hosts[0].get("host", "")
    wss_port = hosts[0].get("wss_port") or 443
    if not host:
        raise BiliError("弹幕服务器地址为空", "DANMU_INFO")
    duration = max(3, min(int(duration or 8), 30))
    sock = None
    collected: list[dict] = []
    try:
        sock = _ws_connect(host, wss_port, "/sub", timeout=10)
        auth_body = json.dumps({
            "uid": 0,
            "roomid": room_id,
            "protover": 2,
            "platform": "web",
            "type": 2,
            "key": token,
        }, separators=(",", ":")).encode()
        _ws_send(sock, _bili_pack(auth_body, op=7, version=1))
        _ws_send(sock, _bili_pack(b"[]", op=2, version=1))
        sock.settimeout(2.0)
        deadline = time.time() + duration
        while time.time() < deadline and len(collected) < limit:
            try:
                raw = _ws_recv(sock)
            except socket.timeout:
                # 心跳保活
                try:
                    _ws_send(sock, _bili_pack(b"[]", op=2, version=1))
                except (OSError, BiliError):
                    break
                continue
            if not raw:
                continue
            for version, body in _danmaku_messages(raw):
                if version == 0:
                    try:
                        msg = json.loads(body)
                    except ValueError:
                        continue
                    if msg.get("cmd") == "DANMU_MSG":
                        info = msg.get("info") or []
                        text = ""
                        try:
                            text = (info[1] or "").strip()
                        except (IndexError, TypeError):
                            pass
                        user = ""
                        try:
                            user = (info[2][1] or "").strip()
                        except (IndexError, TypeError):
                            pass
                        if text:
                            collected.append({"user": user, "text": text})
    except BiliError:
        raise
    except (OSError, ssl.SSLError) as exc:
        raise BiliError(f"弹幕连接失败：{exc}", "WS_ERROR") from exc
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    if not collected:
        raise BiliError("采集期间未收到弹幕（直播间可能无人发言或连接被限流）", "NO_DANMAKU")
    return collected


def live_see(room: str, duration: int = 8, danmaku_limit: int = 40) -> dict:
    """直播间实况：房间信息 + 最近实时弹幕（供 LLM 描述直播内容，v0.6.32）。"""
    info = live_info(room)
    recent: list[dict] = []
    try:
        recent = live_danmaku(room, duration=duration, limit=danmaku_limit)
    except BiliError:
        recent = []
    return {
        "kind": "live",
        "room_id": info.get("room_id"),
        "uname": info.get("uname", ""),
        "title": info.get("title", ""),
        "status": info.get("status_text", ""),
        "online": info.get("online", 0),
        "area": info.get("area", ""),
        "parent_area": info.get("parent_area", ""),
        "recent_danmaku": [
            {"user": d.get("user", ""), "text": d.get("text", "")}
            for d in recent[-danmaku_limit:]
        ],
    }


def video_see(bvid: str, danmaku_limit: int = 40, subtitle_chars: int = 600) -> dict:
    """视频实况：视频信息 + 近期弹幕 + CC 字幕开头（供 LLM 描述视频内容，v0.6.32）。"""
    info = video_info(bvid)
    try:
        dms = video_danmaku(bvid, limit=danmaku_limit)
    except BiliError:
        dms = []
    subtitle_head = ""
    try:
        subtitle_head = (video_subtitle(bvid) or "")[:subtitle_chars]
    except BiliError:
        subtitle_head = ""
    return {
        "kind": "video",
        "bvid": bvid,
        "title": info.get("title", ""),
        "uname": info.get("owner_name", ""),
        "duration": info.get("duration", 0),
        "tname": info.get("tname", ""),
        "view": info.get("view", 0),
        "recent_danmaku": dms[-danmaku_limit:],
        "subtitle_head": subtitle_head,
    }


def render_see(see: dict) -> str:
    """把 live_see / video_see 结果渲染成给 LLM 的实况文本（v0.6.32）。"""
    if not see:
        return "暂无实况信息。"
    if see.get("kind") == "live":
        lines = [
            f"直播间实况：{see.get('title') or '（无标题）'}",
            f"房间号：{see.get('room_id') or '未知'}",
            f"主播：{see.get('uname') or '未知'}｜状态：{see.get('status') or '未知'}｜人气：{see.get('online') or 0}",
            f"分区：{see.get('parent_area') or ''}{see.get('area') or ''}",
            "最近弹幕：",
        ]
        dms = see.get("recent_danmaku") or []
        if dms:
            lines.extend(f"  · {d.get('user', '')}：{d.get('text', '')}" for d in dms)
        else:
            lines.append("  （采集期间无人发言）")
        return "\n".join(lines)
    if see.get("kind") == "video":
        dur = see.get("duration") or 0
        mm = f"{dur // 60}:{dur % 60:02d}"
        progress = see.get("progress")
        total = see.get("duration_total")
        prog_txt = ""
        if progress:
            prog_txt = f"｜播放进度：{progress}" + (f"/{total}" if total else "")
        lines = [
            f"视频实况：{see.get('title') or '（无标题）'}",
            f"UP主：{see.get('uname') or '未知'}｜时长：{mm}｜分区：{see.get('tname') or ''}｜播放：{see.get('view') or 0}{prog_txt}",
            "近期弹幕：",
        ]
        dms = see.get("recent_danmaku") or []
        if dms:
            lines.extend(f"  · {d}" for d in dms)
        else:
            lines.append("  （暂无弹幕）")
        sub = (see.get("subtitle_head") or "").strip()
        if sub:
            lines.append(f"字幕开头：{sub}")
        return "\n".join(lines)
    return "暂无实况信息。"


# ══════════════ UP主 ══════════════


def user_info(mid: int) -> dict:
    """UP主信息：昵称/签名/粉丝/关注/视频数/等级。

    用 acc/info + jsonp=jsonp（免 wbi、未登录可用）；当前网络被 B站限流时
    给出明确提示（配置 BILI_COOKIE 可提升）。
    """
    try:
        data = (_http_get(_API + "/x/space/acc/info", {"mid": int(mid), "jsonp": "jsonp"})).get("data") or {}
    except BiliError as exc:
        raise BiliError(
            "用户信息获取失败（当前网络可能被B站限流；配置 BILI_COOKIE 后重试）：" + exc.message,
            "USER_INFO",
        ) from exc
    if not data:
        raise BiliError("用户不存在", "USER_NOT_FOUND")
    return {
        "mid": data.get("mid"),
        "name": data.get("name", ""),
        "sign": (data.get("sign") or "").strip(),
        "face": data.get("face", ""),
        "fans": data.get("fans", 0),
        "attention": data.get("attention", 0),
        "video_count": data.get("video_count", 0),
        "level": data.get("level", 0),
    }


def format_user_info(info: dict) -> str:
    return (
        f"UP主：{info['name']}（mid {info['mid']}）\n"
        f"粉丝 {info['fans']} ｜ 关注 {info['attention']} ｜ 视频 {info['video_count']} ｜ 等级 Lv{info['level']}\n"
        f"签名：{info['sign'] or '无'}"
    )


def user_videos(mid: int, page: int = 1, limit: int = 15) -> list[dict]:
    """UP主的投稿视频列表（按发布时间）。"""
    data = _wbi_get(
        _API + "/x/space/wbi/arc/search",
        {"mid": int(mid), "ps": min(limit, 30), "pn": max(1, page), "order": "pubdate"},
        timeout=15,
    ).get("data") or {}
    vlist = ((data.get("list") or {}).get("vlist")) or []
    if not vlist:
        raise BiliError("该用户暂无投稿或列表为空", "NO_VIDEOS")
    out = []
    for v in vlist[:limit]:
        out.append({
            "bvid": v.get("bvid", ""),
            "title": v.get("title", ""),
            "play": v.get("play", 0),
            "comment": v.get("comment", 0),
            "duration": v.get("length", ""),
            "pubdate": v.get("pubdate", 0),
        })
    return out


# ══════════════ 我的账号 / 稿件管理（需 BILI_COOKIE 登录态） ══════════════


def _csrf(account: str = "main") -> str:
    """从 Cookie 中提取 bili_jct（写操作的 CSRF token）。"""
    m = re.search(r"bili_jct=([0-9a-zA-Z]+)", _cookie(account))
    return m.group(1) if m else ""


def _require_login(account: str = "main") -> str:
    cookie = _cookie(account)
    if not cookie:
        who = "YUI 小号（BILI_YUI_COOKIE 或插件目录 yui_cookie.txt）" if account == "yui" else "主账号（BILI_COOKIE）"
        raise BiliError(f"需要登录态：请配置{who}（含 SESSDATA 与 bili_jct）", "NEED_LOGIN")
    return cookie


def _http_post(url: str, form: dict, timeout: float = _TIMEOUT, account: str = "main", query: dict | None = None) -> dict:
    """POST 表单（带 Cookie）；解析 JSON。

    query：URL query 参数（复查优化 v0.7.6）：vu 系写接口（/x/vu/edit、/x/vu/client/add）
    现行要求 wbi 签名（wts+w_rid）放 URL query，调用方可用
    query={"wts":..., "w_rid":...} 传入；未传保持原行为。
    """
    body = urllib.parse.urlencode(form).encode()
    if query:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": _UA,
        "Referer": "https://www.bilibili.com/",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.bilibili.com",
    })
    if _cookie(account):
        req.add_header("Cookie", _cookie(account))
    try:
        with _open_with_retry(req, timeout, jitter=True) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise BiliError(f"接口 HTTP {exc.code}", "HTTP_ERROR") from exc
    except urllib.error.URLError as exc:
        raise BiliError(f"网络错误：{exc.reason}", "NETWORK") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BiliError("接口返回非 JSON", "BAD_RESPONSE") from exc
    if data.get("code", 0) != 0:
        code = data.get("code", -1)
        if code in (-352, -401, -412, -101):
            raise BiliError("登录态失效或触发风控（请重新配置有效的 BILI_COOKIE）", "AUTH_FAILED")
        raise BiliError(data.get("message") or data.get("msg") or "B站接口错误", f"BIZ_{code}")
    return data


def my_info(account: str = "yui") -> dict:
    """指定账号信息：昵称/uid/等级/硬币/大会员。account=main 主账号 / yui 小号（默认）。"""
    _require_login(account)
    data = (_http_get(_API + "/x/web-interface/nav", account=account)).get("data") or {}
    if not data.get("mid"):
        raise BiliError("无法读取账号信息（登录态无效）", "AUTH_FAILED")
    level_info = data.get("level_info") or {}
    vip = data.get("vipStatus", 0)
    mid = data.get("mid")
    following = data.get("following", 0) or 0
    follower = data.get("follower", 0) or 0
    # nav 的关注/粉丝字段可能缺失或被隐私隐藏返回 0（曾误报"关注和粉丝都是0"）：
    # 用公开 relation/stat 接口补查，仅当返回明确数值时覆盖。
    try:
        stat = (_http_get(_API + "/x/relation/stat", {"vmid": mid}, account=account)).get("data") or {}
        if stat.get("following") is not None:
            following = int(stat.get("following") or 0)
        if stat.get("follower") is not None:
            follower = int(stat.get("follower") or 0)
    except BiliError:
        pass
    return {
        "mid": mid,
        "uname": data.get("uname", ""),
        "level": level_info.get("current_level", 0),
        "coins": data.get("money", 0),
        "vip": bool(vip),
        "following": following,
        "follower": follower,
    }


def format_my_info(info: dict) -> str:
    vip = "大会员" if info["vip"] else "普通用户"
    return (
        f"当前账号：{info['uname']}（uid {info['mid']}）\n"
        f"等级 Lv{info['level']} ｜ {vip} ｜ 硬币 {info['coins']}\n"
        f"关注 {info['following']} ｜ 粉丝 {info['follower']}"
    )


# ══════════════ 关注 / 取消关注（v0.7.26） ══════════════

# 主账号别名：LLM/用户说"关注你的大号/主账号/主人"时，任何工具传这几个词都必须解析为
# 主账号 uid（v0.8.1 修复：此前"owner"被当昵称搜索，YUI 曾关注到昵称真叫 owner 的陌生账号）。
_OWNER_ALIASES = ("owner", "main", "主人", "主账号", "大号")


def _main_uid() -> int:
    """主账号（main）uid；未配置主账号时抛 NEED_MAIN_LOGIN（绝不回落昵称搜索）。

    v0.9.10：新增自指检测——main_cookie.txt 被误写成小号 Cookie（实测 DedeUserID 与
    yui 相同）时，owner 别名会解析成 YUI 自己，导致私信回复 SELF_DM、白名单误判。
    检测到后抛 MAIN_COOKIE_WRONG 明确报错，绝不静默返回小号 uid。
    """
    uid = cookie_uid(_cookie("main"))
    if not uid:
        raise BiliError(
            "还没有配置主账号（main）登录态，无法把 owner/main/主人 解析为主账号；"
            "请先绑定主账号，或改用对方 UID/完整昵称",
            "NEED_MAIN_LOGIN",
        )
    yui_uid = cookie_uid(_cookie("yui"))
    if yui_uid and uid == yui_uid:
        raise BiliError(
            "主账号 Cookie 配置错误：main_cookie.txt 里存的是 YUI 小号的 Cookie（uid 相同），"
            "无法把 owner/主账号 解析为真正的主账号。请重新登录主账号并绑定"
            "（在浏览器登录主账号后重载插件/重新绑定），或用主账号的纯数字 uid 指定目标。",
            "MAIN_COOKIE_WRONG",
        )
    return uid


def _resolve_uid(target: str, account: str = "yui") -> int:
    """把用户输入解析为 B站 uid：纯数字按 uid；owner/main/主人 等别名按主账号；
    否则按昵称搜索并精确匹配（忽略大小写/空格，严禁取搜索结果的近似第一个——
    防关注错人，v0.7.34）。"""
    target = (target or "").strip()
    if not target:
        raise BiliError("目标为空：请提供要关注/取关的用户 uid 或昵称", "EMPTY_TARGET")
    if re.fullmatch(r"\d{1,12}", target):
        return int(target)
    if target in _OWNER_ALIASES:
        return _main_uid()
    try:
        data = _http_get(
            _API + "/x/web-interface/search/type",
            {"search_type": "bili_user", "keyword": target},
            account=account,
        )
    except BiliError as exc:
        raise BiliError("用户搜索失败（当前网络可能被B站风控）：" + exc.message, "SEARCH_FAILED") from exc
    result = ((data.get("data") or {}).get("result")) or []
    if not result:
        raise BiliError(f"未找到昵称「{target}」对应的用户", "USER_NOT_FOUND")
    # v0.7.34：昵称必须精确匹配——搜索结果的第一个可能是相似账号/仿号/同名者，
    # 盲目取第一个会关注错人（用户已实测踩坑）。精确命中优先；其次忽略大小写与空格的
    # 等价匹配；两者都没有时直接报错，严禁选取近似结果自行关注。
    exact = [r for r in result if str(r.get("uname", "")).strip() == target]
    if not exact:
        norm = target.lower().replace(" ", "")
        fuzzy = [r for r in result if str(r.get("uname", "")).strip().lower().replace(" ", "") == norm]
    else:
        fuzzy = []
    pick = (exact or fuzzy or [])[0] if (exact or fuzzy) else None
    if pick is None:
        raise BiliError(
            f"未找到昵称「{target}」完全匹配的用户（为避免关注错人请核对昵称后重试；"
            f"相近结果：{[str(r.get('uname', '')) for r in result[:3]]}）",
            "USER_AMBIGUOUS",
        )
    mid = pick.get("mid")
    if not mid:
        raise BiliError(f"昵称「{target}」解析失败（搜索结果缺少 uid）", "USER_NOT_FOUND")
    return int(mid)


def _resolve_uname(target: str, account: str = "yui") -> str:
    """与 _resolve_uid 配套：返回目标昵称（uid 输入时尽力反查昵称，查不到返回 uid 字符串）。"""
    target = (target or "").strip()
    if target in _OWNER_ALIASES:
        try:
            info = my_info("main")
            if info.get("uname"):
                return str(info["uname"])
        except BiliError:
            pass
        return "主账号"
    if re.fullmatch(r"\d{1,12}", target):
        try:
            data = _http_get(
                _API + "/x/web-interface/card",
                {"mid": int(target)},
                account=account,
            )
            card = ((data.get("data") or {}).get("card")) or {}
            if card.get("name"):
                return str(card["name"])
        except BiliError:
            pass
        return target
    return target


def _account_label(account: str = "yui") -> str:
    """账号标签：主账号 / 小号（附昵称，用于操作结果明确当前作用于哪个账号）。"""
    name = "小号" if account == "yui" else "主账号"
    try:
        info = my_info(account)
        if info.get("uname"):
            name += f"（{info['uname']}）"
    except BiliError:
        pass
    return name


def set_account_role(role: str = "yui", uid: int | None = None, force: bool = False) -> dict:
    """声明账号角色：把指定 uid 标记为小号（yui）或主账号（main）。

    典型场景（v0.7.30）：用户明确说"这个账号是小号"时调用——把该 uid 写入 role.json
    角色映射，此后默认操作账号（小号）真正作用在该账号上。不覆盖、不删除任何 cookie 文件；
    同 uid 换角色（force=true）或已有同角色映射（force=false）时给出明确提示。
    role：yui（标记为小号，默认）/ main（标记为主账号）。uid：可选；不传时取当前
    main_cookie 的 DedeUserID。force：同 uid 已有角色需变更时是否允许覆盖。
    """
    if role not in ("main", "yui"):
        raise BiliError(f"不支持的角色：{role}（仅支持 yui 小号 / main 主账号）", "BAD_ROLE")
    if uid is None:
        raw = _main_cookie_from_file()
        m = re.search(r"DedeUserID=(\d+)", raw) if raw else None
        if not m:
            raise BiliError("无法读取当前登录账号 uid（main cookie 为空或格式异常）", "NO_UID")
        uid = int(m.group(1))
    uid = int(uid)
    if not _cookie_for_uid(uid):
        raise BiliError(f"本机没有 uid {uid} 的登录态（Cookie），无法声明角色；请先在浏览器登录该账号", "NO_COOKIE")
    roles = _load_roles()
    cur = roles.get(str(uid))
    if cur and cur == role:
        raise BiliError(f"uid {uid} 已经标记为{'小号' if role == 'yui' else '主账号'}，无需重复声明", "ALREADY_SET")
    if cur and not force:
        raise BiliError(
            f"uid {uid} 当前标记为{'小号' if cur == 'yui' else '主账号'}，改为{'小号' if role == 'yui' else '主账号'}会改变默认操作账号；确认无误请重传 force=true",
            "ROLE_CONFLICT",
        )
    roles[str(uid)] = role
    _save_roles(roles)
    info = {}
    try:
        info = my_info(role)
    except BiliError:
        pass
    return {
        "role": role,
        "mid": uid,
        "uname": info.get("uname", ""),
        "detail": f"已将 uid {uid}（{info.get('uname', '')}）标记为{'小号' if role == 'yui' else '主账号'}。此后默认操作账号（小号）真正作用在该账号上。",
    }


def _resolve_room_uid(room_id: int, account: str = "yui") -> int | None:
    """直播房间号 → 主播 uid（关注/取关按 uid 操作，纯数字输入可能是房间号而非 uid）。"""
    try:
        data = (_http_get(_LIVE_API + "/room/v1/Room/room_init", {"id": int(room_id)}, account=account)).get("data") or {}
        uid = data.get("uid")
        return int(uid) if uid else None
    except BiliError:
        return None


def modify_relation(target: str, action: str = "follow", account: str = "yui") -> dict:
    """关注/取消关注指定用户（按 uid 或昵称）。action=follow / unfollow。

    account=main 主账号 / yui 小号（默认）；写操作需该账号 Cookie 含 bili_jct。
    数字目标若按 uid 操作报"用户不存在"，自动按直播房间号反查主播 uid 重试一次
    （直播间 9458332 等房间号 ≠ uid，此前会误报"用户不存在"，v0.7.28 修复）。
    """
    _require_login(account)
    act = 1 if action in ("follow", "关注", "subscribe") else 2
    csrf = _csrf(account)
    if not csrf:
        raise BiliError("Cookie 缺少 bili_jct（CSRF token），无法执行关注/取关", "NO_CSRF")
    raw = (target or "").strip()
    mid = _resolve_uid(raw, account)
    uname = _resolve_uname(raw, account)
    form = {"fid": mid, "act": act, "re_src": 11, "csrf": csrf}
    try:
        data = _http_post(_API + "/x/relation/modify", form, account=account)
    except BiliError as exc:
        # 纯数字目标 + 用户不存在：可能把房间号当 uid 了，反查一次主播 uid 重试
        if re.fullmatch(r"\d{1,12}", raw) and ("不存在" in exc.message or exc.code in ("BIZ_-404", "-404", 404)):
            room_uid = _resolve_room_uid(int(raw), account)
            if room_uid and room_uid != mid:
                mid = room_uid
                uname = _resolve_uname(str(mid), account)
                form["fid"] = mid
                try:
                    data = _http_post(_API + "/x/relation/modify", form, account=account)
                except BiliError as exc2:
                    raise BiliError(
                        ("关注失败" if act == 1 else "取关失败") + f"（{uname}，uid {mid}）：" + exc2.message,
                        f"REL_{act}_{exc2.code}",
                    ) from exc2
            else:
                raise BiliError(
                    ("关注失败" if act == 1 else "取关失败") + f"（{uname}，uid {mid}）：" + exc.message,
                    f"REL_{act}_{exc.code}",
                ) from exc
        else:
            raise BiliError(
                ("关注失败" if act == 1 else "取关失败") + f"（{uname}，uid {mid}）：" + exc.message,
                f"REL_{act}_{exc.code}",
            ) from exc
    # 操作后复查（v0.7.28：防"假成功"——接口返回 code=0 但关注关系未实际变化时明确报错）
    try:
        rel = (_http_get(_API + "/x/relation", {"fid": mid}, account=account)).get("data") or {}
        attr = int(rel.get("attribute", 0) or 0)
    except BiliError:
        attr = None  # 复查接口失败不阻断（可能临时风控），以接口结果为准
    if attr is not None:
        if act == 1 and attr == 0:
            raise BiliError("关注未生效（接口返回成功但关注状态未变化，可能被风控延迟，请稍后重试）", "REL_NOT_EFFECTED")
        if act == 2 and attr != 0:
            raise BiliError("取关未生效（接口返回成功但关注状态未变化，可能被风控延迟，请稍后重试）", "REL_NOT_EFFECTED")
    acct = _account_label(account)
    return {
        "mid": mid,
        "uname": uname,
        "action": "follow" if act == 1 else "unfollow",
        "account": acct,
        "following": bool(data.get("data")),
        "message": data.get("message") or "",
    }


def unfollow_all(account: str = "yui", dry_run: bool = False, limit: int = 0) -> dict:
    """把指定账号关注的所有 UP 主全部取消关注（真实执行，逐条调用并复查）。

    逐条调用 /x/relation/modify（B站 无批量取关接口），每条后由 modify_relation
    复查关注状态；风控/网络抖动自动重试（间隔递增）；仍未成功的条目记入 failed
    明细，绝不谎报为成功。limit>0 时单次最多处理 limit 个（默认全量），剩余数量
    通过 remaining 返回——调用方按 remaining 循环调用直到为 0，避免单次调用
    耗时过长触发对话层工具超时（v0.7.33）。
    """
    _require_login(account)
    mid = my_info(account)["mid"]
    followings = []
    pn = 1
    while True:
        try:
            data = (
                _http_get(
                    _API + "/x/relation/followings",
                    {"vmid": int(mid), "pn": pn, "ps": 50, "order": "desc", "order_type": 0},
                    account=account,
                    timeout=15,
                )
            ).get("data") or {}
        except BiliError as exc:
            raise BiliError("关注列表获取失败：" + exc.message, "FOLLOW_LIST") from exc
        page = data.get("list") or []
        if not page:
            break
        followings.extend(page)
        if len(page) < 50:
            break
        pn += 1
    if dry_run:
        return {
            "dry_run": True,
            "account": _account_label(account),
            "total": len(followings),
            "list": [{"fid": f.get("mid") or f.get("fid"), "uname": f.get("uname", "")} for f in followings],
        }
    batch = followings[:limit] if limit and limit > 0 else followings
    remaining = len(followings) - len(batch)
    ok, failed = 0, []
    for f in batch:
        fid = f.get("mid") or f.get("fid")
        uname = f.get("uname", "") or str(fid)
        last = None
        for attempt in range(3):
            try:
                modify_relation(str(fid), "unfollow", account=account)
                ok += 1
                last = None
                break
            except BiliError as exc:
                last = exc
                # 风控/验证码类失败：加长间隔重试；其余（账号状态等）直接放弃该条
                if ("风控" in exc.message or "验证" in exc.message or exc.code in ("REL_2_REL_NOT_EFFECTED", "REL_2_-412", "REL_2_BIZ_-412")):
                    time.sleep(1.5 * (attempt + 1))
                else:
                    break
        if last:
            failed.append({"fid": fid, "uname": uname, "reason": last.message})
        time.sleep(0.3)  # 取关间隔，避免连发触发风控
    return {
        "account": _account_label(account),
        "total": len(followings),
        "ok": ok,
        "failed": failed,
        "remaining": remaining,
    }


def my_videos(page: int = 1, limit: int = 10, account: str = "yui") -> list[dict]:
    """指定账号的投稿列表（nav 取 uid → arc/search）。account=main 主账号 / yui 小号（默认）。

    注：创作中心 /x/vu/web/video/list 接口已失效（404），改用空间投稿接口。
    """
    _require_login(account)
    mid = my_info(account)["mid"]
    try:
        data = _wbi_get(
            _API + "/x/space/wbi/arc/search",
            {"mid": int(mid), "ps": min(limit, 30), "pn": max(1, page), "order": "pubdate"},
            timeout=15,
        ).get("data") or {}
    except BiliError as exc:
        raise BiliError("稿件列表获取失败（当前网络可能被B站风控；请重新配置对应账号的 Cookie）：" + exc.message, "MY_VIDEOS") from exc
    vlist = ((data.get("list") or {}).get("vlist")) or []
    if not vlist:
        raise BiliError("暂无投稿或列表为空", "NO_VIDEOS")
    out = []
    for v in vlist[:limit]:
        state = v.get("state", 0)
        state_text = {0: "公开", -2: "删除", -1: "审核中", 1: "审核中"}.get(state, f"状态{state}")
        out.append({
            "bvid": v.get("bvid", ""),
            "aid": v.get("aid"),
            "title": v.get("title", "").strip(),
            "play": v.get("play", 0),
            "state": state,
            "state_text": state_text,
            "pubdate": v.get("pubdate", 0),
            "length": v.get("length", ""),
        })
    return out


def format_my_videos(videos: list[dict]) -> str:
    lines = []
    for i, v in enumerate(videos, 1):
        lines.append(
            f"{i}. {v['title']}（{v['state_text']}，播放 {v['play']}，时长 {v['length']}）BV号 {v['bvid']}"
        )
    return "\n".join(lines) if lines else "暂无投稿"


def edit_video(bvid: str, title: str | None = None, desc: str | None = None, tag: str | None = None, account: str = "yui") -> dict:
    """修改自己稿件的信息（标题/简介/标签）。account=main 主账号 / yui 小号（默认）。

    只允许修改属于指定账号的稿件；服务端会校验所有权。
    """
    _require_login(account)
    bvid = _parse_bvid(bvid)
    info = video_info(bvid, account=account)
    aid = info.get("aid")
    if not aid:
        raise BiliError("无法获取稿件编号", "NO_AID")
    form = {
        "aid": int(aid),
        "vfe": "1",
        "csrf": _csrf(account),
        "title": (title if title is not None else info["title"]).strip(),
        "desc": (desc if desc is not None else info["desc"]).strip(),
        "tag": (tag if tag is not None else ",".join(info["tags"])).strip(),
    }
    if not form["csrf"]:
        raise BiliError("Cookie 缺少 bili_jct（CSRF token），无法执行写操作", "NO_CSRF")
    # 复查修复（v0.7.6）：/x/vu/edit 现行要求 wbi 签名（wts+w_rid 放 URL query），
    # 缺失会报 -352"请求错误"；签名失败时保持裸请求兜底。
    query = None
    try:
        signed = _wbi_sign(dict(form), _get_mixin_key())
        query = {"wts": signed["wts"], "w_rid": signed["w_rid"]}
    except BiliError:
        pass
    try:
        _http_post(_API + "/x/vu/edit", form, account=account, query=query)
    except BiliError as exc:
        raise BiliError("稿件修改失败：" + exc.message, "EDIT_FAILED") from exc
    return {"bvid": bvid, "title": form["title"], "desc": form["desc"], "tag": form["tag"], "ok": True}


def _live_recent_danmaku(room_id: int, account: str = "yui") -> list:
    """回读直播间最近弹幕（v0.9.18）：ajax/msg 返回 data.admin + data.normal 列表。

    用途：发送弹幕后验证是否真的出现在直播间——B站 /msg/send 会返回 code=0 但静默丢弃
    弹幕（实测 2026-10-23：接口接受、无 dmid，但直播间不显示，疑似防刷屏/屏蔽词过滤）。"""
    d = _http_get(
        "https://api.live.bilibili.com/ajax/msg",
        {"roomid": str(room_id), "token": "", "page": 1},
        timeout=_TIMEOUT,
        account=account,
    )
    data = d.get("data") or {}
    msgs: list = []
    for key in ("admin", "normal"):
        msgs.extend(data.get(key) or [])
    return msgs


def live_send_danmaku(room: str, text: str, account: str = "yui", verify: bool = True) -> dict:
    """向直播间发送一条弹幕。account=yui 小号（默认）/ main 主账号。

    注意：msg/send 的 roomid 必须是页面显示的房间号（URL 上的数字，
    与 B站 Web 端一致），不能用 room_init 解析出的真实 room_id——
    两者不一致时弹幕会发进错误房间（v0.6.30）。

    verify（v0.9.18）：发送后回读直播间最近弹幕验证（默认 True）——接口接受（code=0）≠ 已显示，
    实测 B站 会静默吞弹幕（code=0 无 dmid 但直播间不出现）；回读最多 2 次（各等 3 秒），
    命中自己 uid + 文本才算 confirmed。verify=False 供连发任务使用（不阻塞主循环，由任务下轮回读）。
    """
    _require_login(account)
    text = (text or "").strip()
    if not text:
        raise BiliError("弹幕内容为空", "EMPTY_MSG")
    if len(text) > 30:
        raise BiliError("弹幕最长 30 个字符", "MSG_TOO_LONG")
    csrf = _csrf(account)
    if not csrf:
        raise BiliError("Cookie 缺少 bili_jct（CSRF token），无法发送弹幕", "NO_CSRF")
    page_room = room.strip()
    m = _ROOM_RE.search(page_room)
    if m:
        page_room = m.group(1)
    if not page_room.isdigit():
        raise BiliError("请输入直播房间号或直播间链接（如 live.bilibili.com/12345）", "BAD_ROOM")
    form = {
        "roomid": int(page_room),
        "msg": text,
        "csrf": csrf,
        "csrf_token": csrf,
        "rnd": str(int(time.time())),
        "color": "16777215",
        "fontsize": "25",
        "mode": "1",
        "platform": "web",
        "web_location": "1550101",
    }
    # 直播弹幕走 /msg/send（xlive/web-room/v1/dms/send 已废弃返回 404，v0.6.29）
    try:
        data = _http_post(_LIVE_API + "/msg/send", form, account=account)
    except BiliError as exc:
        raise BiliError("弹幕发送失败：" + exc.message, "SEND_FAILED:" + exc.code) from exc
    resp_data = data.get("data")
    dmid = ""
    if isinstance(resp_data, dict):
        dmid = str(resp_data.get("dmid") or resp_data.get("dmid_str") or "")
    # v0.9.16 用户实测确认：B站直播弹幕接口常态不返回 dmid（回流ID），弹幕通常已正常发出
    # v0.9.18 用户再实测：接口 code=0 无 dmid 时弹幕也可能被 B站 静默吞掉（直播间不显示）——
    # 必须回读验证，confirmed=False 时如实上报，严禁假成功
    confirmed = False
    confirm_note = ""
    if verify:
        try:
            my_uid = _yui_uid(account)
            for _ in range(2):
                time.sleep(3)
                msgs = _live_recent_danmaku(int(page_room), account)
                if any(
                    int(m.get("uid") or 0) == my_uid
                    and (m.get("text") or "").strip() == text
                    for m in msgs
                ):
                    confirmed = True
                    break
            if not confirmed:
                confirm_note = "接口已接受但直播间最近弹幕未出现该条，疑似被 B站 弹幕过滤/风控拦截"
        except Exception:
            confirm_note = "回读验证失败（网络/风控），未确认是否出现在直播间"
    return {
        "ok": True,
        "room_id": int(page_room),
        "text": text,
        "server": data.get("message") or "",
        "resp_code": data.get("code"),
        "dmid": dmid,
        "unverified": not dmid,
        "confirmed": confirmed,
        "confirm_note": confirm_note,
    }


def send_video_danmaku(bvid: str, text: str, progress_ms: int = 0, account: str = "yui") -> dict:
    """向视频投递一条普通弹幕（默认出现在视频开头时间点）。account=main 主账号 / yui 小号。

    参数：
        bvid：视频 BV 号（自动解析到第一P 的 cid）。
        text：弹幕内容（最长 100 字）。
        progress_ms：弹幕出现的时间点（毫秒，0=开头）。
    """
    _require_login(account)
    text = (text or "").strip()
    if not text:
        raise BiliError("弹幕内容为空", "EMPTY_MSG")
    if len(text) > 100:
        raise BiliError("视频弹幕最长 100 个字符", "MSG_TOO_LONG")
    csrf = _csrf(account)
    if not csrf:
        raise BiliError("Cookie 缺少 bili_jct（CSRF token），无法发送弹幕", "NO_CSRF")
    bvid = _parse_bvid(bvid)
    info = video_info(bvid, account=account)
    pages = info.get("pages") or []
    if not pages:
        raise BiliError("该视频没有可用的分P信息", "NO_CID")
    cid = pages[0]["cid"]
    try:
        progress_ms = int(progress_ms)
    except (TypeError, ValueError):
        progress_ms = 0
    progress_ms = max(0, progress_ms)
    form = {
        "type": "1",
        "oid": int(cid),
        "bvid": bvid,
        "msg": text,
        "progress": int(progress_ms),
        "mode": "1",
        "color": "16777215",
        "fontsize": "25",
        "pool": "0",
        "rnd": str(int(time.time()) * 1000000),
        "csrf": csrf,
        "csrf_token": csrf,
        "platform": "web",
    }
    # 新版发送接口要求 wbi 签名（缺失会触发 -352 风控"请求错误"）
    try:
        form = _wbi_sign(form, _get_mixin_key())
    except BiliError:
        pass  # 签名失败不致命，回退裸请求
    try:
        data = _http_post(_API + "/x/v2/dm/post", form, account=account)
    except BiliError as exc:
        raise BiliError("视频弹幕发送失败：" + exc.message, "SEND_FAILED:" + exc.code) from exc
    dm = data.get("data") or {}
    # v0.9.25 对照 bili-cli danmaku.py：send_danmaku 返回的弹幕 ID 字段名是 dmid_str（大数以字符串返回）
    # 或 dmid，两者都取；原只取 dmid 会导致接口返回 dmid_str 时误判"没拿到 ID"假失败。
    dmid = str(dm.get("dmid_str") or dm.get("dmid") or "")
    if not dmid:
        raise BiliError(
            "弹幕接口已接受（code=0）但未返回弹幕 ID（dmid），可能被风控拦截——不能确认已发送，请勿声称已发出。",
            "DANMAKU_UNVERIFIED",
        )
    return {"ok": True, "bvid": bvid, "cid": int(cid), "text": text, "progress_ms": int(progress_ms), "dmid": dmid}


# ══════════════ YUI 独立小号写操作（评论/动态/投稿） ══════════════
# 全部走 account="yui"（BILI_YUI_COOKIE 或插件目录 yui_cookie.txt），
# 与用户主账号 BILI_COOKIE 严格隔离；内容由 YUI 生成，插件只做长度/格式硬校验。


def _http_post_json(
    url: str,
    body: dict,
    csrf: str,
    account: str = "yui",
    timeout: float = _TIMEOUT,
    query: dict | None = None,
) -> dict:
    """POST JSON body（带 Cookie 与 csrf）；用于动态发布等 JSON 接口。

    query：URL query 参数（默认 {"platform":"web","csrf":csrf}）。
    复查优化（v0.7.3）：B站动态接口现行要求 wbi 签名（wts+w_rid），
    调用方可用 query=_wbi_sign({...}) 传入签名结果；未传时保持原行为兜底。
    """
    q = dict(query or {"platform": "web", "csrf": csrf})
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": _UA,
        "Referer": "https://t.bilibili.com/",
        "Content-Type": "application/json; charset=utf-8",
        "Origin": "https://t.bilibili.com",
    })
    if _cookie(account):
        req.add_header("Cookie", _cookie(account))
    try:
        with _open_with_retry(req, timeout, jitter=True) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise BiliError(f"接口 HTTP {exc.code}", "HTTP_ERROR") from exc
    except urllib.error.URLError as exc:
        raise BiliError(f"网络错误：{exc.reason}", "NETWORK") from exc
    try:
        resp_data = json.loads(raw)
    except ValueError as exc:
        raise BiliError("接口返回非 JSON", "BAD_RESPONSE") from exc
    if resp_data.get("code", 0) != 0:
        code = resp_data.get("code", -1)
        if code in (-352, -401, -412, -101):
            raise BiliError("登录态失效或触发风控（请重新配置 YUI 小号 Cookie）", "AUTH_FAILED")
        raise BiliError(resp_data.get("message") or resp_data.get("msg") or "B站接口错误", f"BIZ_{code}")
    return resp_data


def add_reply(oid: int, type_id: int, text: str, account: str = "yui", verify: bool = True) -> dict:
    """用指定账号发一条评论。type_id：1=视频，2=动态，3=专栏，11=直播。

    参数：
        oid：目标对象的 id（视频=aid、动态=动态id、直播=房间号、专栏=cvid）。
        type_id：评论区类型（1 视频 / 2 动态 / 3 专栏 / 11 直播）。
        text：评论内容（最长 1000 字）。
        verify：发送后回读评论区验证自己的评论真实可见（v0.7.50 防假成功）。

    注意：B站 评论接口对无效目标/风控命中也可能返回 code=0（假成功），
    仅凭 code==0 判定成功不可靠；verify=True 时会回读评论区，
    找不到自己的评论即抛 REPLAY_UNVERIFIED，绝不静默声称已发送。
    """
    _require_login(account)
    text = (text or "").strip()
    if not text:
        raise BiliError("评论内容为空", "EMPTY_MSG")
    if len(text) > 1000:
        raise BiliError("评论最长 1000 个字符", "MSG_TOO_LONG")
    csrf = _csrf(account)
    if not csrf:
        raise BiliError("Cookie 缺少 bili_jct（CSRF token），无法发送评论", "NO_CSRF")
    form = {
        "oid": int(oid),
        "type": int(type_id),
        "message": text,
        "csrf": csrf,
        "csrf_token": csrf,
        "jsonp": "jsonp",
        # v0.9.25 对照 bilibili-api comment.send 定义：plat/statistics/gaia_source 是 B站 新版评论
        # 接口的必带字段，缺失时新号评论更易被风控/折叠（bili-cli comment.py 同款 payload）。
        "plat": 1,
        "statistics": {"appId": 100, "platform": 5},
        "gaia_source": "main_web",
    }
    try:
        _http_post(_API + "/x/v2/reply/add", form, account=account)
    except BiliError as exc:
        raise BiliError("评论发送失败：" + exc.message, "REPLY_FAILED:" + exc.code) from exc
    if verify:
        try:
            uid = str((my_info(account) or {}).get("mid") or "")
        except BiliError:
            uid = ""
        if uid:
            visible, folded, hidden = _reply_visible(int(oid), int(type_id), uid, text, account=account)
            if not visible:
                raise BiliError(
                    "评论接口已接受（code=0），但未在评论区回读到这条评论——可能被风控/审核拦截、"
                    "目标无效或入池延迟。不能确认已发送，请勿向用户声称'已发送'；可稍后自行到评论区核实。",
                    "REPLY_UNVERIFIED",
                )
            if folded or hidden:
                # v0.9.25 对照 bili-cli comment check：评论发送成功但被折叠/审核（别人通常看不到，
                # @ 也可能不发提醒）——如实上报，让 YUI 告知用户"已发出但可能不可见"，严禁自称正常发出
                return {
                    "ok": True,
                    "oid": int(oid),
                    "type": int(type_id),
                    "text": text,
                    "folded": True,
                    "note": "评论已发出，但回读检测到该评论被折叠/隐藏（新号或敏感内容常被折叠，"
                    "别人可能看不到，@ 也可能不会发提醒）。如实告知用户，严禁声称评论正常可见。",
                }
    return {"ok": True, "oid": int(oid), "type": int(type_id), "text": text, "folded": False}


def _reply_visible(oid: int, type_id: int, uid: str, text: str, tries: int = 2, account: str = "main") -> tuple[bool, bool, bool]:
    """回读评论区（最新一页）确认 uid 的评论真实可见（v0.7.50）。

    返回 (visible, folded, hidden)：visible=是否命中；命中后按 B站 附加的
    folder.is_folded / invisible / state 字段判断是否被折叠/隐藏（v0.9.25，
    对照 bili-cli comment check——折叠的评论在发布者视角仍"正常显示"，
    必须读这些状态字段才能识别"发出去了但别人看不到"）。
    评论入池有秒级时滞，未命中时短等重试一次；内容用包含匹配容忍格式差异。
    v0.9.6 复查：加 account 参数，回读用发评论的账号 cookie（原默认 main，main 失效会假失败）。
    """
    text = (text or "").strip()
    for attempt in range(max(1, tries)):
        try:
            data = _wbi_get(
                _API + "/x/v2/reply/main",
                {"type": type_id, "oid": oid, "mode": 2, "next": 0, "ps": 20},
                account=account,
            ).get("data") or {}
        except BiliError:
            data = {}
        for reply in data.get("replies") or []:
            member = reply.get("member") or {}
            content = reply.get("content") or {}
            message = content.get("message") or ""
            if str(member.get("mid")) == str(uid) and message and (
                text in message or message in text
            ):
                folder = reply.get("folder") or {}
                folded = bool(folder.get("is_folded"))
                hidden = bool(reply.get("invisible")) or reply.get("state") not in (0, None)
                return True, folded, hidden
        if attempt < max(1, tries) - 1:
            time.sleep(1.5)
    return False, False, False


def post_dynamic(text: str, account: str = "yui") -> dict:
    """用指定账号发一条文字动态。需小号 Cookie（含 bili_jct）。

    v0.8.8 接口修复：原 /x/dynamic/web/create/v1/dyn 已被 B站 下线（HTTP 404），
    改用旧版稳定接口 POST api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/create
    （body: type=4, content=JSON字符串, from=create.client.web, api=req），
    返回 dynamic_id 供删除/回读验证。
    """
    _require_login(account)
    text = (text or "").strip()
    if not text:
        raise BiliError("动态内容为空", "EMPTY_MSG")
    if len(text) > 2000:
        raise BiliError("动态最长 2000 个字符", "MSG_TOO_LONG")
    # v0.9.22 复盘：同内容短窗去重——接口接受即算已发布，重试一律被挡
    _dup_key = (account, text)
    _now_ts = time.time()
    _last_ts = _dyn_last_posts.get(_dup_key)
    if _last_ts and _now_ts - _last_ts < _DYN_DUP_WINDOW:
        raise BiliError(
            f"同一内容 {int(_DYN_DUP_WINDOW)} 秒内已发布过（疑似重复触发），严禁重复发布；"
            "如需再次发布请换新内容或稍后重试。",
            "DYN_DUP",
        )
    csrf = _csrf(account)
    if not csrf:
        raise BiliError("Cookie 缺少 bili_jct（CSRF token），无法发布动态", "NO_CSRF")
    body = {
        "type": 4,
        # v0.9.3 修复：content 必须是纯文本，不能包成 json.dumps({"content": text})
        # 原写法会导致动态显示为 {"content": "..."} 而非纯文本（接口返回 code=0 但内容错误=假成功）
        "content": text,
        "from": "create.client.web",
        "api": "req",
    }
    try:
        resp = _http_post_json(
            "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/create",
            body, csrf, account=account,
        )
    except BiliError as exc:
        raise BiliError("动态发布失败：" + exc.message, "DYN_FAILED:" + exc.code) from exc
    # 接口接受=已发布过，立即记录（无论后续回读结果，60s 内重试都被挡）
    _dyn_last_posts[_dup_key] = time.time()
    data = resp.get("data") or {}
    dyn_id = str(data.get("dynamic_id_str") or data.get("dynamic_id") or "")
    # v0.9.3 回读验证：发布后调动态详情接口确认内容正确（防假成功）
    if dyn_id:
        verified, actual_content = _dynamic_verified(account, dyn_id, text)
        if not verified:
            # v0.9.3 复查优化：只在内容明显是 JSON 编码错误（以 {"content": 开头）时自动删除，
            # 否则只报错不删除——避免 B站 接口格式变化导致误删正确动态
            looks_like_json_err = isinstance(actual_content, str) and actual_content.lstrip().startswith("{") and '"content"' in actual_content
            if looks_like_json_err:
                try:
                    delete_dynamic(dyn_id, account=account)
                except BiliError:
                    pass
            raise BiliError(
                "动态发布后回读验证失败（内容与预期不符"
                + ("，已自动删除误发动态" if looks_like_json_err else "，请在 B站 动态页面确认")
                + "）。【重要】动态可能已成功发出，严禁重复发送！"
                + "如需补发请先到 B站 动态页面确认当前状态，再决定是否删除重发。",
                "DYN_UNVERIFIED",
            )
        # v0.9.9：对外可见验证——详情接口能查到 ≠ 对外可见
        # （被风控/审核拦截时详情对发布者可见，但动态不进对外可见动态流，用户看不到）
        if not _dynamic_in_feed_verified(account, dyn_id):
            raise BiliError(
                "动态详情能查到但未进入对外可见的动态流（可能被风控/审核拦截，仅自己可见）——"
                "严禁声称已发布；请到 B站 动态页面确认状态，必要时稍后重发，严禁连续盲发。",
                "DYN_INVISIBLE",
            )
    return {"ok": True, "text": text[:100], "dynamic_id": dyn_id}


def _dynamic_verified(account: str, dynamic_id: str, text: str, tries: int = 3) -> tuple[bool, str | None]:
    """发布后回读验证：调动态详情接口，确认动态内容与发送的 text 一致。

    v0.9.3 新增：防假成功（接口返回 code=0 但内容被错误编码，如包成 JSON 字符串）。
    v0.9.3 复查优化：同时检查 card.item.content 和 card.item.description（不同动态类型字段不同）；
    返回 (verified, actual_content)——actual_content 用于判断是否为 JSON 编码错误以决定是否自动删除。
    v0.9.5 修复假失败：tries 2→3，间隔 1.0→1.5s（B站动态详情接口同步有延迟，2次不够）；
    内容不匹配时也重试一次（可能是接口延迟返回旧缓存），不是直接判定失败。
    """
    last_actual: str | None = None
    for i in range(tries):
        try:
            data = _http_get(
                "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
                {"dynamic_id": dynamic_id},
                account=account,
            )
        except BiliError:
            if i < tries - 1:
                time.sleep(1.5)
            continue
        card_raw = ((data.get("data") or {}).get("card")) or ""
        if not card_raw:
            if i < tries - 1:
                time.sleep(1.5)
            continue
        try:
            card = json.loads(card_raw) if isinstance(card_raw, str) else card_raw
        except (json.JSONDecodeError, TypeError):
            return False, str(card_raw)[:200]
        # v0.9.3 复查优化：同时检查 content（文字动态）和 description（图片/视频动态）
        item = card.get("item") or {}
        actual = (item.get("content") or item.get("description") or "").strip()
        last_actual = actual
        if actual == text.strip():
            return True, actual
        # v0.9.5：内容不匹配时也重试一次（可能是接口延迟返回旧缓存），不是直接判定失败
        if i < tries - 1:
            time.sleep(1.5)
    return False, last_actual


def _yui_uid(account: str) -> int:
    """获取指定账号的 uid（nav 接口），供动态流验证使用。"""
    data = _http_get(_API + "/x/web-interface/nav", account=account)
    mid = (data.get("data") or {}).get("mid")
    if not mid:
        raise BiliError("无法获取账号 uid（nav 接口），动态流验证不可用", "NO_UID")
    return int(mid)


def _dynamic_in_feed_verified(account: str, dynamic_id: str, tries: int = 3) -> bool:
    """动态流验证（v0.9.9）：调 space 对外可见动态流，确认动态 id_str 命中且 visible=True。

    背景：get_dynamic_detail 详情接口对发布者可见 ≠ 对外可见——被风控/审核拦截时
    详情能查到（内容验证通过），但动态不进对外可见动态流，其他人（包括用户）看不到。
    实测（2026-10-23）：feed/space 返回 items[].id_str 与 visible 字段；
    tries=3 间隔 1.5s（动态流同步有延迟，与 _dynamic_verified 同模式）。
    """
    uid = _yui_uid(account)
    for i in range(tries):
        try:
            feed = _http_get(
                "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                {"host_mid": uid, "offset": ""},
                account=account,
            )
        except BiliError:
            if i < tries - 1:
                time.sleep(1.5)
            continue
        items = ((feed.get("data") or {}).get("items")) or []
        for it in items:
            if str(it.get("id_str") or "") == str(dynamic_id):
                return bool(it.get("visible", True))  # 在流里但 visible=False 视为未对外可见
        if i < tries - 1:
            time.sleep(1.5)
    return False


def _dynamic_deleted_verified(account: str, dynamic_id: str, tries: int = 3) -> bool:
    """删除后回读验证（v0.9.8）：动态详情查不到（code=-2 或 data.card 为空）即确认删除生效。

    与 _dynamic_verified 同模式：B站动态详情接口同步有延迟，tries=3 间隔 1.5s；
    防假成功——rm_dynamic 接口返回 code=0 但动态未实际删除（风控延迟/接口假成功）。
    """
    for i in range(tries):
        try:
            data = _http_get(
                "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail",
                {"dynamic_id": dynamic_id},
                account=account,
            )
        except BiliError:
            if i < tries - 1:
                time.sleep(1.5)
            continue
        code = data.get("code", 0)
        card_raw = ((data.get("data") or {}).get("card")) or ""
        if code == -2 or not card_raw:
            return True  # 动态已不存在，删除生效
        if i < tries - 1:
            time.sleep(1.5)
    return False


def delete_dynamic(dynamic_id, account: str = "yui") -> dict:
    """删除指定动态（v0.8.8 新增，供发布后回读验证/误发撤回）。

    接口 POST api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/rm_dynamic。
    v0.9.8 加删除后回读验证：详情查不到才确认删除（防假成功，与 post_dynamic 一致）。
    """
    _require_login(account)
    csrf = _csrf(account)
    if not csrf:
        raise BiliError("Cookie 缺少 bili_jct", "NO_CSRF")
    try:
        did = int(dynamic_id)
    except (TypeError, ValueError):
        raise BiliError("dynamic_id 必须是数字", "BAD_ID")
    try:
        _http_post_json(
            "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/rm_dynamic",
            {"dynamic_id": did}, csrf, account=account,
        )
    except BiliError as exc:
        raise BiliError("动态删除失败：" + exc.message, "DYN_DEL_FAILED:" + exc.code) from exc
    if not _dynamic_deleted_verified(account, str(did)):
        raise BiliError(
            "动态删除接口已接受（code=0）但回读仍能查到该动态——不能确认已删除，请勿声称已删除；"
            "若重试需先确认动态是否已删（避免重复删除他人新动态）。",
            "DYN_DEL_UNVERIFIED",
        )
    return {"ok": True, "dynamic_id": str(did)}


def _safe_multipart_name(name: str) -> str:
    """清洗 multipart 文件名：去掉会破坏 boundary 的引号/换行/反斜杠（复查优化 v0.7.3）。"""
    return re.sub(r'[\r\n"\\]', "_", str(name))


def _http_multipart(url: str, fields: dict, files: dict, account: str = "yui", timeout: float = 60.0) -> dict:
    """手写 multipart/form-data POST（无第三方依赖），用于投稿分片上传。"""
    boundary = "----neko" + str(int(time.time() * 1000000))
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    for key, (filename, data) in files.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{_safe_multipart_name(filename)}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8") + data + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": _UA,
        "Referer": "https://member.bilibili.com/",
        "Origin": "https://member.bilibili.com",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    })
    if _cookie(account):
        req.add_header("Cookie", _cookie(account))
    try:
        with _open_with_retry(req, timeout, jitter=True) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise BiliError(f"接口 HTTP {exc.code}", "HTTP_ERROR") from exc
    except urllib.error.URLError as exc:
        raise BiliError(f"网络错误：{exc.reason}", "NETWORK") from exc
    try:
        resp_data = json.loads(raw)
    except ValueError as exc:
        raise BiliError("接口返回非 JSON", "BAD_RESPONSE") from exc
    if resp_data.get("code", 0) != 0:
        code = resp_data.get("code", -1)
        if code in (-352, -401, -412, -101):
            raise BiliError("登录态失效或触发风控（请重新配置 YUI 小号 Cookie）", "AUTH_FAILED")
        raise BiliError(resp_data.get("message") or resp_data.get("msg") or "B站接口错误", f"BIZ_{code}")
    return resp_data


def submit_video(file_path: str, title: str, desc: str = "", tags: str = "", tid: int = 171, account: str = "yui") -> dict:
    """用指定账号投稿一个本地视频文件（web 端分片上传 + 提交）。

    依赖：小号 Cookie 需有投稿权限；视频文件必须已存在；分区 tid 默认 171（电子竞技）。
    流程：init（获取上传凭证）→ 分片 upload → complete（合并）→ add（提交稿件，进审核）。
    说明：B站 web 投稿接口字段偶有变动，若报 BIZ_xxx 需按当时接口实测调整。
    """
    _require_login(account)
    csrf = _csrf(account)
    if not csrf:
        raise BiliError("Cookie 缺少 bili_jct（CSRF token），无法投稿", "NO_CSRF")
    if not os.path.isfile(file_path):
        raise BiliError(f"视频文件不存在：{file_path}", "NO_FILE")
    size = os.path.getsize(file_path)
    if size <= 0:
        raise BiliError("视频文件为空", "EMPTY_FILE")
    title = (title or "").strip()
    if not title:
        raise BiliError("投稿标题为空", "NO_TITLE")
    meta = json.dumps({"title": title, "desc": (desc or "").strip(), "cover": ""}, ensure_ascii=False)
    try:
        init = _http_post(_API + "/x/vu/client/init", {
            "name": os.path.basename(file_path),
            "size": int(size),
            "meta": meta,
            "copyright": "2",
            "csrf": csrf,
        }, account=account, timeout=30.0)
    except BiliError as exc:
        raise BiliError("投稿初始化失败：" + exc.message, "UPLOAD_INIT_FAILED") from exc
    init_data = init.get("data") or {}
    up_uid = init_data.get("up_uid") or 0
    upload_id = init_data.get("upload_id") or ""
    chunk_size = int(init_data.get("chunk_size") or (4 * 1024 * 1024))
    if not upload_id:
        raise BiliError("投稿初始化未返回 upload_id", "UPLOAD_NO_ID")
    total_chunks = max(1, (size + chunk_size - 1) // chunk_size)
    with open(file_path, "rb") as f:
        for i in range(total_chunks):
            chunk = f.read(chunk_size)
            if not chunk:
                break
            try:
                _http_multipart(_API + "/x/vu/client/upload", {
                    "upload_id": upload_id,
                    "chunk": str(i),
                    "chunks": str(total_chunks),
                    "size": str(len(chunk)),
                    "partSize": str(chunk_size),
                    "total": str(size),
                    "filename": os.path.basename(file_path),
                    "up_uid": str(up_uid),
                    "csrf": csrf,
                }, {"file": (os.path.basename(file_path), chunk)}, account=account, timeout=120.0)
            except BiliError as exc:
                raise BiliError(f"上传分片 {i + 1}/{total_chunks} 失败：" + exc.message, "UPLOAD_CHUNK_FAILED") from exc
            # 后台化：分片间小延迟，避免占满玩家带宽/拖慢其正在进行的游戏或直播（v0.7.1）
            if i + 1 < total_chunks:
                time.sleep(0.15)
    try:
        _http_post(_API + "/x/vu/client/complete", {
            "upload_id": upload_id,
            "up_uid": str(up_uid),
            "csrf": csrf,
        }, account=account, timeout=60.0)
    except BiliError as exc:
        raise BiliError("上传合并失败：" + exc.message, "UPLOAD_COMPLETE_FAILED") from exc
    # 复查修复（v0.7.6）：/x/vu/client/add（稿件提交）现行要求 wbi 签名，缺失 -352。
    add_query = None
    try:
        signed = _wbi_sign({
            "copyright": "2",
            "source": "",
            "tid": int(tid),
            "title": title,
            "desc": (desc or "").strip(),
            "tag": (tags or "").strip(),
            "cover": "",
            "up_uid": str(up_uid),
            "csrf": csrf,
        }, _get_mixin_key())
        add_query = {"wts": signed["wts"], "w_rid": signed["w_rid"]}
    except BiliError:
        pass
    try:
        _http_post(_API + "/x/vu/client/add", {
            "copyright": "2",
            "source": "",
            "tid": int(tid),
            "title": title,
            "desc": (desc or "").strip(),
            "tag": (tags or "").strip(),
            "cover": "",
            "up_uid": str(up_uid),
            "csrf": csrf,
        }, account=account, timeout=60.0, query=add_query)
    except BiliError as exc:
        raise BiliError("稿件提交失败：" + exc.message, "SUBMIT_FAILED") from exc
    return {"ok": True, "title": title, "file": os.path.basename(file_path), "tid": int(tid), "size": int(size)}


# ══════════════ 当前观看感知（前台窗口标题，通用） ══════════════

_BROWSER_MARKERS = ("chrome", "edge", "firefox", "opera", "brave", "browser", "safari", "360")
_SITE_KEYWORDS = [
    ("bilibili", ("哔哩哔哩", "bilibili")),
    ("youtube", ("youtube",)),
    ("zhihu", ("知乎", "zhihu")),
    ("weibo", ("微博", "weibo")),
    ("xiaohongshu", ("小红书", "xiaohongshu")),
    ("douyin", ("抖音", "douyin")),
    ("baidu", ("百度", "baidu")),
    ("taobao", ("淘宝", "taobao")),
    ("jd", ("京东", "jd")),
    ("tieba", ("贴吧", "tieba")),
    ("github", ("github",)),
    ("douban", ("豆瓣", "douban")),
    ("steam", ("steam",)),
    ("netease", ("网易", "163.com")),
    ("tencent", ("腾讯", "qq.com")),
    ("dingtalk", ("钉钉", "dingtalk")),
    ("feishu", ("飞书", "feishu", "lark")),
    ("wechat", ("微信", "weixin", "wechat")),
]
_BILI_TITLE_SUFFIX_RE = re.compile(r"^(.*?)[_\-—]哔哩哔哩")
# B站新版视频页标题常见格式「视频标题_分区名」（不带"哔哩哔哩"字样），用于兜底识别
_BILI_SECTION_MARKERS = (
    "游戏杂谈", "游戏热门视频", "单机游戏", "电子竞技", "手机游戏", "网络游戏", "桌游棋牌",
    "动画", "番剧", "影视", "电影", "纪录片", "音乐", "舞蹈", "知识", "科技", "数码",
    "生活", "美食", "动物圈", "时尚", "运动", "汽车", "鬼畜", "娱乐", "综艺", "资讯",
    "Vlog", "专栏", "公开课", "搞笑", "日常", "手工", "绘画", "历史", "军事", "游戏",
)
_BILI_SECTION_SUFFIX_RE = re.compile(r"[_\-—](?:" + "|".join(map(re.escape, _BILI_SECTION_MARKERS)) + r")$")
_BROWSER_TITLE_SUFFIX_RE = re.compile(
    r"\s*[-—_]\s*(?:Google Chrome|Chrome|Microsoft Edge|Edge|Firefox|Opera|Brave|Safari|浏览器)$",
    re.IGNORECASE,
)


def _strip_browser_suffix(title: str) -> str:
    """去掉浏览器窗口标题末尾的浏览器名，还原页面标题。"""
    return _BROWSER_TITLE_SUFFIX_RE.sub("", title).strip()


_URL_BVID_RE = re.compile(r"/video/(BV[a-zA-Z0-9]+)")
_URL_AV_RE = re.compile(r"/video/av(\d+)")
_URL_LIVE_RE = re.compile(r"live\.bilibili\.com/(\d+)")
_URL_EP_RE = re.compile(r"bangumi/play/ep(\d+)")
_URL_SS_RE = re.compile(r"bangumi/play/ss(\d+)")


def parse_bili_url(url: str, title: str = "") -> dict | None:
    """从浏览器扩展推送的活动标签页 URL 解析 B站内容。纯函数，可测试。

    返回与 parse_window_title 兼容的 dict（is_bili=True），并附带 bvid / room_id /
    ep / ss 精确标识；非 B站 URL 返回 None。
    """
    url = (url or "").strip()
    if not url:
        return None
    low = url.lower()
    if "bilibili.com" not in low and "b23.tv" not in low:
        return None
    subject = (title or "").strip()
    m = _BILI_TITLE_SUFFIX_RE.match(subject)
    if m and m.group(1).strip():
        subject = m.group(1).strip()
    base = {"app": "browser-ext", "window": title, "is_browser": True, "site": "bilibili", "subject": subject}
    m = _URL_LIVE_RE.search(low)
    if m:
        return {**base, "kind": "live", "is_bili": True, "room_id": m.group(1)}
    m = _URL_BVID_RE.search(url)  # BV 号区分大小写，需在原始 URL 上匹配
    if m:
        return {**base, "kind": "video", "is_bili": True, "bvid": m.group(1)}
    m = _URL_AV_RE.search(low)
    if m:
        return {**base, "kind": "video", "is_bili": True, "aid": m.group(1)}
    m = _URL_EP_RE.search(low)
    if m:
        return {**base, "kind": "bangumi", "is_bili": True, "ep": m.group(1)}
    m = _URL_SS_RE.search(low)
    if m:
        return {**base, "kind": "bangumi", "is_bili": True, "ss": m.group(1)}
    if "live" in low:
        return {**base, "kind": "live", "is_bili": True}
    return {**base, "kind": "web", "is_bili": True}


def parse_window_title(title: str, exe: str = "") -> dict:
    """解析前台窗口标题与进程名，识别应用/站点/页面主题。纯函数，可测试。

    返回：app（进程名）、window（原始标题）、is_browser、site（站点标识）、
    subject（页面/窗口主题）、kind（video/live/web/app/other/unknown）、is_bili。
    """
    title = (title or "").strip()
    exe_name = os.path.basename((exe or "").replace("\\", "/")).lower()
    if not title and not exe_name:
        return {"app": "", "window": "", "is_browser": False, "site": "", "subject": "", "kind": "unknown", "is_bili": False}
    low = title.lower()
    is_browser = any(m in exe_name for m in _BROWSER_MARKERS) or any(
        m in low for m in ("google chrome", "microsoft edge", "firefox", "opera", "brave")
    )
    # 站点关键词只在标题中匹配（不匹配进程名，避免 steam++.exe/Watt Toolkit 这类误判为 steam）
    site = ""
    for name, kws in _SITE_KEYWORDS:
        if any(k in low for k in kws):
            site = name
            break
    # B站：视频页/直播页特殊解析
    if "哔哩哔哩" in title or "bilibili" in low:
        if "直播" in title:
            subject = re.split(r"[_\-—]", title, maxsplit=1)[0].strip()
            return {
                "app": exe_name, "window": title, "is_browser": is_browser, "site": "bilibili",
                "subject": subject, "kind": "live", "is_bili": True,
            }
        m = _BILI_TITLE_SUFFIX_RE.match(title)
        if m and m.group(1).strip():
            return {
                "app": exe_name, "window": title, "is_browser": is_browser, "site": "bilibili",
                "subject": m.group(1).strip(), "kind": "video", "is_bili": True,
            }
        return {
            "app": exe_name, "window": title, "is_browser": is_browser, "site": "bilibili",
            "subject": "", "kind": "other", "is_bili": True,
        }
    # B站兜底：浏览器标题形如「视频标题_分区名」（新版页面不带"哔哩哔哩"字样）
    if is_browser and not site:
        stripped = _strip_browser_suffix(title)
        m = _BILI_SECTION_SUFFIX_RE.search(stripped)
        if m:
            subject = stripped[: m.start()].strip(" _-")
            if subject:
                return {
                    "app": exe_name, "window": title, "is_browser": is_browser, "site": "bilibili",
                    "subject": subject, "kind": "video", "is_bili": True,
                }
    subject = _strip_browser_suffix(title) if is_browser else title
    return {
        "app": exe_name, "window": title, "is_browser": is_browser, "site": site,
        "subject": subject, "kind": "web" if is_browser else "app", "is_bili": False,
    }


def _foreground_window() -> dict:
    """读取当前前台窗口的标题与进程路径（仅 Windows 可用）。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, ImportError, OSError):
        raise BiliError("当前平台不支持读取前台窗口", "PLATFORM")
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return {"title": "", "exe": ""}
    buf = ctypes.create_unicode_buffer(512)
    n = user32.GetWindowTextW(hwnd, buf, 512)
    title = buf.value[: max(0, n)]
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            size = wintypes.DWORD(512)
            path = ctypes.create_unicode_buffer(512)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                exe = path.value
        finally:
            kernel32.CloseHandle(handle)
    return {"title": title, "exe": exe}


# ══════════ 多窗口感知（实时同步 / bili_now_watching 共用） ══════════
#
# 为什么不用"前台窗口"：用户和 YUI 聊天时，聊天窗口（N.E.K.O 客户端）必然在前台，
# 但用户真正在看的内容（B站视频/网页/游戏）往往在后台。因此改为枚举所有可见窗口，
# 排除聊天客户端/终端/空标题，再按内容优先级选出"最可能在看"的窗口。

# 开发终端/命令行：多为调试或 AI 操作，不代表用户在看的内容
_TERMINAL_EXES = (
    "windowsterminal", "cmd.exe", "powershell", "pwsh",
    "python", "py.exe", "bash", "sh.exe", "wt.exe", "conhost",
)
# N.E.K.O 客户端自身（用户与 YUI 聊天的窗口）：不作为感知对象
_NEKO_CLIENT_EXES = ("n.e.k.o", "neko", "launcher")


def _is_terminal_window(parsed: dict) -> bool:
    """是否开发终端/命令行窗口（不参与感知）。"""
    app = (parsed.get("app") or "").lower()
    return any(m in app for m in _TERMINAL_EXES)


def _is_neko_client(parsed: dict) -> bool:
    """是否 N.E.K.O 客户端聊天窗口（不参与感知）。"""
    app = (parsed.get("app") or "").lower()
    return any(m in app for m in _NEKO_CLIENT_EXES)


def _skip_window(parsed: dict) -> bool:
    """窗口是否应被排除（未知/空标题/终端/聊天客户端）。"""
    if parsed.get("kind") in ("", "unknown"):
        return True
    if not parsed.get("window"):
        return True
    if _is_terminal_window(parsed) or _is_neko_client(parsed):
        return True
    return False


def _window_score(parsed: dict) -> int:
    """内容优先级：B站视频/直播 > 已知站点 > 网页 > 其他应用。"""
    if parsed.get("is_bili"):
        return 100
    if parsed.get("site"):
        return 90
    if parsed.get("kind") == "web":
        return 80
    if parsed.get("kind") == "app":
        return 70
    return 60


def _visible_windows() -> list[dict]:
    """枚举所有可见顶层窗口（仅 Windows），返回 [{"title": str, "exe": str}]。"""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, ImportError, OSError):
        raise BiliError("当前平台不支持读取窗口", "PLATFORM")

    windows: list[dict] = []

    def _exe_of(pid: int) -> str:
        exe = ""
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                size = wintypes.DWORD(512)
                path = ctypes.create_unicode_buffer(512)
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    exe = path.value
            finally:
                kernel32.CloseHandle(handle)
        return exe

    wnd_enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        n = user32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value[: max(0, n)].strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        windows.append({"title": title, "exe": _exe_of(pid.value)})
        return True

    user32.EnumWindows(wnd_enum_proc(_enum_proc), 0)
    return windows


def pick_now_watching() -> dict | None:
    """选出最可能'用户正在看'的窗口，返回 parse_window_title 结果；无可感知窗口返回 None。

    排除：N.E.K.O 聊天客户端、开发终端、空标题/系统窗口。
    优先级：B站视频/直播 > 已知站点 > 网页 > 其他应用（游戏等）。
    """
    best = None
    best_score = -1
    try:
        windows = _visible_windows()
    except BiliError:
        return None
    for win in windows:
        parsed = parse_window_title(win["title"], win["exe"])
        if _skip_window(parsed):
            continue
        score = _window_score(parsed)
        if score > best_score:
            best_score, best = score, parsed
    return best


def now_watching(tab: dict | None = None) -> dict:
    """感知用户当前在看什么：扩展推送的活动标签页（精确）优先，否则枚举可见窗口。

    tab：浏览器扩展推送的 {url, title, at}。B站标签页按 URL 精确解析（bvid/room_id
    直接匹配详情，无需搜索）；非 B站标签页按标题通用解析。无扩展数据时回退窗口感知。
    """
    parsed: dict | None = None
    if tab and isinstance(tab, dict):
        url = str(tab.get("url") or "")
        title = str(tab.get("title") or "")
        parsed = parse_bili_url(url, title)
        if isinstance(parsed, dict):
            parsed["from"] = "extension-tab"
        else:
            parsed = parse_window_title(title, "chrome.exe")
            parsed["from"] = "extension-tab"
    if parsed is None:
        parsed = pick_now_watching()
    if parsed is None:
        fg = _foreground_window()
        parsed = parse_window_title(fg["title"], fg["exe"])
    out = dict(parsed)
    # 精确匹配：扩展 tab 带 bvid → 直接取视频详情；live → 取直播间信息
    bvid = parsed.get("bvid")
    if bvid:
        try:
            info = video_info(bvid)
            out["video"] = {
                "bvid": bvid,
                "title": info["title"],
                "owner_name": info["owner_name"],
                "view": info["view"],
                "danmaku": info["danmaku"],
                "duration": _fmt_duration(info["duration"]),
                "pubdate": _fmt_time(info["pubdate"]),
                "desc": (info["desc"] or "")[:200],
            }
        except BiliError:
            out["video"] = None
    elif parsed.get("kind") == "video" and parsed.get("subject"):
        try:
            results = search(parsed["subject"], "video", limit=3)
        except BiliError:
            results = []
        if results:
            best = results[0]
            out["match"] = best
            try:
                info = video_info(best["bvid"])
                out["video"] = {
                    "bvid": best["bvid"],
                    "title": info["title"],
                    "owner_name": info["owner_name"],
                    "view": info["view"],
                    "danmaku": info["danmaku"],
                    "duration": _fmt_duration(info["duration"]),
                    "pubdate": _fmt_time(info["pubdate"]),
                    "desc": (info["desc"] or "")[:200],
                }
            except BiliError:
                out["video"] = None
    if parsed.get("kind") == "live":
        room_id = parsed.get("room_id")
        if room_id:
            try:
                info = live_info(room_id)
                out["live"] = {
                    "room_id": room_id,
                    "title": info.get("title") or "",
                    "uname": info.get("uname") or "",
                    "status_text": info.get("status_text") or "",
                    "online": info.get("online") or 0,
                    "area": info.get("area") or "",
                    "parent_area": info.get("parent_area") or "",
                }
            except BiliError:
                out["live"] = None
    return out


def format_now_watching(info: dict) -> str:
    kind = info.get("kind")
    if kind == "unknown":
        return "没有检测到前台窗口，无法感知当前内容。"
    if info.get("is_bili"):
        head = "用户正在看 B站" + ("直播" if kind == "live" else "视频") + "："
        if kind == "live" and info.get("live"):
            li = info["live"]
            lines = [
                head + f"「{li['title'] or info.get('subject') or '未知直播间'}」",
                f"主播：{li.get('uname') or '未知'} ｜ 状态：{li.get('status_text')} ｜ 人气：{li.get('online')}",
                f"分区：{li.get('parent_area') or ''} / {li.get('area') or ''} ｜ 房间号 {li.get('room_id')}",
            ]
            return "\n".join(lines)
        if kind == "live":
            return head + "「" + (info.get("subject") or "未知直播间") + "」（窗口：%s）" % info.get("window")
        if info.get("video"):
            v = info["video"]
            lines = [
                head + f"「{v['title']}」",
                f"UP主：{v['owner_name']} ｜ 播放 {v['view']} ｜ 弹幕 {v['danmaku']} ｜ 时长 {v['duration']}",
                f"发布时间：{v['pubdate']} ｜ BV号 {v['bvid']}",
            ]
            if v.get("desc"):
                lines.append("简介：" + v["desc"])
            return "\n".join(lines)
        if info.get("match"):
            m = info["match"]
            return head + f"「{m['title']}」（作者 {m['author']}）BV号 {m['bvid']}"
        return head + "「" + (info.get("subject") or "未知") + "」（未能精确匹配，可让用户提供视频链接）"
    app = info.get("app") or ""
    subject = info.get("subject") or ""
    if info.get("site"):
        return f"用户正在浏览 {info['site']}：{subject or '（首页/未知页面）'}（{app}）"
    if kind == "web":
        return f"用户正在浏览网页：{subject or '（未知页面）'}（{app}）"
    return f"用户正在使用应用：{app or '未知'}（窗口：{info.get('window') or '无标题'}）"
