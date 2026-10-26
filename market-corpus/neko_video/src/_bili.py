"""B站数据获取（纯标准库，零依赖、零登录）。

关键约束（已验证）：
  - 搜索 API `search/type` 无需登录即可 code=0 返回结果（不触发 -412 风控）
  - 但**搜索结果第一条不一定是你正在看的那个**，所以必须做相似度匹配
  - 弹幕 .so 接口返回 deflate 裸流，必须 zlib.decompress(raw, -15)

接口（均已实测可用）：
  标题 → BV:  /x/web-interface/search/type?search_type=video&keyword=...
  BV → 信息:  /x/web-interface/view?bvid=...
  弹幕:       /x/v1/dm/list.so?oid=<cid>   （XML，一次拿全，带时间戳）
  热评:       /x/v2/reply?type=1&oid=<aid>&sort=2
"""

from __future__ import annotations

import difflib
import gzip
import http.cookiejar
import json
import re
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib

__all__ = [
    "BiliError",
    "danmaku",
    "hot_comments",
    "resolve_bv_by_title",
    "strip_site_suffix",
    "video_info",
]

_CTX = ssl.create_default_context()
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_JAR),
    urllib.request.HTTPSHandler(context=_CTX),
)
_OPENER.addheaders = [
    ("User-Agent", _UA),
    ("Referer", "https://www.bilibili.com/"),
    ("Accept", "application/json, text/plain, */*"),
    ("Accept-Language", "zh-CN,zh;q=0.9"),
]

_warmed_up = False

class BiliError(RuntimeError):
    """B站接口不可用 / 返回错误码。"""


def _warmup() -> None:
    """访问主站拿 buvid3 等 cookie，降低风控概率。只做一次。"""
    global _warmed_up
    if _warmed_up:
        return
    _warmed_up = True
    try:
        _OPENER.open("https://www.bilibili.com/", timeout=10).read(1024)
    except Exception:  # noqa: BLE001 - warmup 失败不影响后续
        pass


def _get(url: str, *, binary: bool = False, timeout: int = 15):
    _warmup()
    req = urllib.request.Request(url)
    with _OPENER.open(req, timeout=timeout) as r:
        raw = r.read()
        enc = r.headers.get("Content-Encoding") or ""
    if enc == "gzip":
        raw = gzip.decompress(raw)
    if binary:
        return raw
    return json.loads(raw.decode("utf-8", "replace"))


def strip_site_suffix(title: str) -> str:
    """从浏览器窗口标题里剥出真正的视频标题。

    B站窗口标题的固定形状是「视频标题_哔哩哔哩_bilibili<浏览器后缀>」，
    所以最稳的做法不是列举浏览器名（列不全、各家格式还不同），而是
    **按站名标记切**：取标记之前的部分就是视频标题。

    "机械迷城_哔哩哔哩_bilibili - Google Chrome"       → "机械迷城"
    "...【自制伪纪录片电影】_哔哩哔哩_bilibili -联想浏览器" → "...【自制伪纪录片电影】"
    """
    t = (title or "").strip()

    # ① 首选：按站名标记切（标记必须在中间，否则可能是标题自带"哔哩哔哩"）
    m = re.search(r"[_\-\s]*哔哩哔哩", t)
    if m and m.start() > 0:
        t = t[: m.start()]

    # ② 兜底：去掉常见浏览器后缀（标题里没有站名标记时）
    t = re.sub(r"\s*[-–—]\s*(Google Chrome|Microsoft Edge|Mozilla Firefox|Brave|Opera|Vivaldi)\s*$", "", t)
    t = re.sub(r"\s*[-–—]\s*\S*浏览器\s*$", "", t)  # 联想/QQ/360 等各类浏览器

    return t.strip(" _-|")


def _clean_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _norm(s: str) -> str:
    """归一化用于相似度比较：去掉标点/空白/全角符号，转小写。"""
    s = re.sub(r"[\s　]+", "", s or "")
    s = re.sub(r"[《》【】\[\]（）()、，。！？!?,.:;：；\"'“”‘’~～\-—_|/\\]", "", s)
    return s.lower()


def _similarity(query: str, candidate: str) -> float:
    """查询串与候选标题的相似度，兼顾"完全一致"和"包含关系"。"""
    q, c = _norm(query), _norm(candidate)
    if not q or not c:
        return 0.0
    ratio = difflib.SequenceMatcher(None, q, c).ratio()
    # 包含关系加成：
    #   q ⊂ c（查询是候选的子串）——用户看的多是短标题，结果带前缀后缀，强信号。
    #   c ⊂ q（候选是查询的子串）——危险：像《妹妹》这种超短泛化标题会偶然成为
    #   长查询的子串，若给高加成会错配到完全无关的视频（实测踩过：匹配到 31 小时
    #   的直播录像）。所以候选必须覆盖查询的相当一部分（>=60%）才给加成。
    if q in c:
        coverage = len(q) / max(len(c), 1)
        ratio = max(ratio, 0.7 + 0.3 * coverage)
    elif c in q:
        coverage = len(c) / max(len(q), 1)
        if coverage >= 0.6:
            ratio = max(ratio, 0.7 + 0.3 * coverage)
    return min(1.0, ratio)  # 相似度上限就是 1.0


def _search_queries(keyword: str) -> list[str]:
    """生成多级回退查询串（按优先级）。

    B站搜索对省略号、书名号等符号处理很差：实测完整标题
    「我记录下了妹妹的最后7天...【自制伪纪录片电影】」一条都搜不到，
    但砍掉省略号后的部分（「我记录下了妹妹的最后7天」）第一条就是正确视频。
    所以按"完整 → 砍省略号 → 主标题 → 书名号内关键词"依次尝试。
    """
    out: list[str] = []

    def _add(s: str) -> None:
        s = s.strip()
        if s and s not in out:
            out.append(s)

    _add(keyword)
    _add(re.sub(r"[.．。·]{2,}", " ", keyword))          # 省略号 → 空格
    _add(re.sub(r"[《》【】\[\]（）()]", " ", keyword))   # 去书名号类符号
    head = re.split(r"[.．。·]{2,}|【|《|\(", keyword)[0]  # 省略号/书名号前的主标题
    if len(head) >= 4:
        _add(head)
    inner = re.findall(r"[【《]([^】》]{2,40})[】》]", keyword)  # 书名号内的关键词
    for it in inner[:2]:
        _add(it)
    return out


def _search_once(query: str, order: str = "") -> list[dict]:
    """一次搜索。order 是**关键**：见 resolve_bv_by_title 的说明。"""
    url = (
        "https://api.bilibili.com/x/web-interface/search/type"
        f"?search_type=video&keyword={urllib.parse.quote(query)}&page=1"
    )
    if order:
        url += f"&order={order}"
    try:
        d = _get(url)
    except Exception as e:  # noqa: BLE001
        raise BiliError(f"搜索请求失败: {type(e).__name__}") from e
    if d.get("code") != 0:
        raise BiliError(f"搜索接口 code={d.get('code')} msg={d.get('message')!r}")
    return (d.get("data") or {}).get("result") or []


def resolve_bv_by_title(
    title: str, *, min_score: float = 0.55, duration_hint: float | None = None
) -> dict | None:
    """窗口标题 → 最匹配的视频。

    返回 {bvid, title, author, duration, score}；匹配度不足返回 None。

    ★ 五个坑（都实测踩过）：
      1. 不能取第一条：搜"机械迷城"第一条是 77 分钟全流程，用户可能在刷短视频。
         → 必须按标题相似度挑最像的。
      2. 完整标题常搜不到（B站对省略号/书名号处理差）。→ 多级回退查询。
      3. **默认"相关性"排序会把精确视频挤出去**（实测：搜完整标题，默认排序
         20 条里没有那个视频，但 order=pubdate 第 1 条就是它）。
         → 三种排序都搜，**合并候选**再挑（2026-10-11 改：逐 order 一命中就返回
         会拿到次优的那个，因为不同 order 返回的是不同子集）。
      4. 搜索接口会限流：连续高频搜会退化。→ 失败交给上层 5 秒轮询重试，天然退避。
      5. ★★ **同名视频极多**（2026-10-11 实测）：「我的行为VS奶奶看到的：」这类
         模板标题在 B站有 **40+ 个**同名视频，光靠标题+播放量**必然选错**
         （实测错配到 27 秒的同名短片 → 弹幕/评论读 0）。
         → 用 **duration_hint**（来自系统媒体会话的真实时长）过滤：时长对不上的
         直接排除。这是唯一能可靠区分同名视频的信号。

    `duration_hint`：当前播放视频的真实时长（秒）。给了它就按"时长最接近"优先。
    """
    keyword = strip_site_suffix(title)
    if not keyword:
        return None

    for query in _search_queries(keyword):
        # ★ 合并该查询下**所有排序**的候选再挑（见坑 3）
        pool: list[dict] = []
        seen: set[str] = set()
        for order in ("pubdate", "click", ""):
            results = _search_once(query, order)   # 失败抛 BiliError，交给上层
            for item in results or []:
                bv = str(item.get("bvid") or "")
                if not bv or bv in seen:
                    continue
                seen.add(bv)
                pool.append(item)
        if not pool:
            continue

        best, best_key = None, None
        for item in pool:
            cand = _clean_html(item.get("title", ""))
            score = _similarity(keyword, cand)
            play = _as_int(item.get("play"))
            dur = _parse_duration_seconds(item.get("duration"))
            # ★ 排序键 = (相似度, 时长是否对上, 播放量)：
            #   ① 相似度更高者优先；
            #   ② 有 duration_hint 时，**时长更接近**者优先 —— 同名视频靠这个区分
            #      （标题一样、播放量也接近，只有时长能分出你看的是哪一个）；
            #   ③ 再同分时取播放量更高的。
            dur_gap = (
                abs(dur - duration_hint)
                if (duration_hint and dur is not None)
                else 1e9      # 没有时长信息 → 不参与时长比较
            )
            key = (score, -dur_gap, play)
            if best_key is None or key > best_key:
                best, best_key = item, key

        if best is None:
            continue
        best_score = best_key[0]
        # 有 duration_hint 但**最佳候选的时长也差得太远** → 这个 query 的结果里
        # 没有你要看的那个视频，换下一级回退 query 再试（别直接放弃）。
        # 注意：best_key[1] 存的是 **-gap**（gap=时长差），所以"差得远"= 更小的负数，
        # 判据是 `best_key[1] < -容差`。
        if duration_hint and best_key[1] < -_DURATION_TOLERANCE:
            continue
        if best_score >= min_score:
            return {
                "bvid": best.get("bvid"),
                "title": _clean_html(best.get("title", "")),
                "author": best.get("author"),
                "duration": best.get("duration"),
                "duration_seconds": _parse_duration_seconds(best.get("duration")),
                "play": _as_int(best.get("play")),
                "score": round(best_score, 3),
                "query": query,
            }
    return None


# 同名视频的时长容差（秒）：SMTC 报的时长与搜索结果的时长都是整数秒，
# 差几秒很常见（片头/片尾/四舍五入），给 8 秒余量。
_DURATION_TOLERANCE = 8.0


def _parse_duration_seconds(value: object) -> int | None:
    """把搜索结果的时长（'2:12' / '1:02:03' / 132）解析成秒。失败 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value or "").strip()
    if not s:
        return None
    try:
        parts = [int(x) for x in s.split(":")]
    except (TypeError, ValueError):
        return None
    if not parts:
        return None
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def _as_int(value: object) -> int:
    """把搜索接口里的播放量（可能是 int / str / '12.3万'）转成 int。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value or "").strip()
    if not s:
        return 0
    try:
        if s.endswith("万"):
            return int(float(s[:-1]) * 10000)
        if s.endswith("亿"):
            return int(float(s[:-1]) * 100000000)
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def video_info(bvid: str) -> dict:
    """BV 号 → 标题/UP/时长/cid/aid/分区/简介。"""
    d = _get(f"https://api.bilibili.com/x/web-interface/view?bvid={urllib.parse.quote(bvid)}")
    if d.get("code") != 0:
        raise BiliError(f"view 接口 code={d.get('code')} msg={d.get('message')!r}")
    data = d.get("data") or {}
    if not data:
        raise BiliError("view 接口无数据")
    return {
        "bvid": data.get("bvid"),
        "aid": data.get("aid"),
        "cid": data.get("cid"),
        "title": data.get("title"),
        "author": (data.get("owner") or {}).get("name"),
        "duration": data.get("duration"),          # 秒
        "tname": data.get("tname"),                # 分区
        "desc": (data.get("desc") or "")[:500],
    }


def danmaku(cid: int, *, limit: int = 600) -> list[dict]:
    """弹幕（带时间戳）。返回 [{t:秒, text:str}, ...]，按时间排序。

    ★ .so 返回 deflate 裸流，必须 zlib.decompress(raw, -15)。
    """
    url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={int(cid)}"
    raw = _get(url, binary=True, timeout=20)
    if not raw:
        return []
    text = None
    for attempt in (lambda b: zlib.decompress(b, -15),
                    lambda b: zlib.decompress(b),
                    lambda b: b):
        try:
            text = attempt(raw).decode("utf-8", "replace")
            break
        except Exception:  # noqa: BLE001
            continue
    if not text or "<" not in text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    out: list[dict] = []
    for d in root.iter("d"):
        try:
            p = (d.get("p") or "").split(",")
            t = float(p[0]) if p and p[0] else 0.0
        except (ValueError, IndexError):
            t = 0.0
        txt = (d.text or "").strip()
        if txt:
            out.append({"t": t, "text": txt})
        if len(out) >= limit:
            break
    out.sort(key=lambda x: x["t"])
    return out


def hot_comments(aid: int, *, limit: int = 5) -> list[dict]:
    """高赞热评。返回 [{text, like, images:[url,...]}, ...]。

    ★ 评论**带图**（实测可用）：B站评论区大量是"图 + 一句话"的梗图
      （如"地球运转的底层逻辑"+ 猫抱地球的图）。图片 URL 在
      `content.pictures[].img_src`，实测能直接下载（PIL 也能处理）。
      把图给猫娘"看"，她才能真正懂评论区的笑点（这是用户明确要的）。
    """
    url = (
        "https://api.bilibili.com/x/v2/reply"
        f"?type=1&oid={int(aid)}&sort=2&ps={max(1, min(limit, 20))}&pn=1"
    )
    try:
        d = _get(url, timeout=15)
    except Exception:  # noqa: BLE001 - 评论拿不到不影响主流程
        return []
    if d.get("code") != 0:
        return []
    replies = ((d.get("data") or {}).get("replies")) or []
    out = []
    for r in replies[:limit]:
        content = (r.get("content") or {}).get("message", "")
        # ★ 评论图片（实测字段：content.pictures[].img_src）
        pics = (r.get("content") or {}).get("pictures") or []
        images: list[str] = []
        for p in pics[:2]:      # 一条评论最多取 2 张，防止体积失控
            u = str((p or {}).get("img_src") or "").strip()
            if u:
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("http://"):
                    u = "https://" + u[len("http://"):]   # 统一 https
                images.append(u)
        if content or images:
            out.append({
                "text": content.strip()[:180],
                "like": r.get("like", 0),
                "images": images,
            })
    return out


def download_image(url: str, *, timeout: int = 15, max_bytes: int = 8 * 1024 * 1024) -> bytes | None:
    """下载一张图片（评论图/预览图通用）。失败或超限返回 None。"""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Referer": "https://www.bilibili.com/",
        })
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read(max_bytes + 1)
        if not raw or len(raw) > max_bytes:
            return None
        return raw
    except Exception:  # noqa: BLE001
        return None