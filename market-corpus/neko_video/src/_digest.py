"""把 B站数据压成一条**短、可去重、可安全塞进上下文**的视频摘要。

设计红线（来自黑夜君临的教训）：
  - 内容必须短：弹幕/评论全文几万字，绝不能整段推 —— 会刷爆她的上下文。
  - 必须硬去重：同一视频只推一次（时间戳/内容指纹），物理上杜绝复读。
  - 只给事实，不给台词：她用自己的语气吐槽，插件不写一句台词（防 OOC）。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict

__all__ = [
    "build_digest",
    "build_hotspot_text",
    "build_intro_text",
    "content_fingerprint",
    "rhythm_hotspots",
]
# 情绪关键词（零成本分类，实测可用）
_EMOTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "笑": ("哈哈", "笑死", "233", "hhh", "哈哈哈", "乐", "草", "绷不住", "笑不活", "离谱"),
    "泪": ("泪目", "哭了", "感动", "破防", "心疼", "泪", "绷不住了", "好哭"),
    "惊": ("我去", "卧槽", "我超", "震惊", "惊了", "天呐", "牛逼", "吓一跳", "太强了"),
    "期待": ("期待", "蹲", "终于", "更新", "催更", "来了来了", "等到"),
}

_BUCKET_SECONDS = 5


def _fmt_ts(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 60}:{s % 60:02d}"


def _emotion_of(text: str) -> str | None:
    for label, kws in _EMOTION_KEYWORDS.items():
        if any(k in text for k in kws):
            return label
    return None


def _has_content(text: str) -> bool:
    """样本是否含实质内容（至少 2 个中文/字母数字字符）。

    弹幕里大量 "?"、"啊？"、"6" 之类纯符号/单字，拿它们当样本等于没给信息。
    """
    core = re.sub(r"[^一-鿿A-Za-z0-9]", "", text or "")
    return len(core) >= 2


def rhythm_hotspots(
    messages: list[dict], *, top: int = 5, min_count: int = 3
) -> list[dict]:
    """弹幕密度 → 情绪高点（就是进度条上那团"山峰"）。

    返回 [{at, at_label, count, emotion, sample, samples:[...]}, ...]，按时间升序。

    ★ 挑的是**局部峰值**而不是"密度最高的几个桶"：如果相邻两个桶都很高，
      只报最高的那个，否则同一波弹幕会被报成两个高光点。

    ★ `min_count`（2026-10-11 实测加的门槛）：单个峰值至少要有这么多条弹幕
      才算"高光"。弹幕稀疏的视频（实测 21 条弹幕的视频），按密度算出来的
      "峰值"只有 1~2 条弹幕，那是**噪音不是高光** —— 拿它当"观众反应最集中处"
      会让她满嘴弹幕。这种视频应该**干脆不报高光点**，让她按自己的理解聊内容
      （内容层有简介/热评/梗撑着）。
    """
    if not messages:
        return []
    buckets: dict[int, list[dict]] = defaultdict(list)
    for m in messages:
        try:
            t = float(m.get("t", 0))
        except (TypeError, ValueError):
            continue
        buckets[int(t // _BUCKET_SECONDS)].append(m)
    if not buckets:
        return []

    # 只保留"局部峰值"：比左右邻居都不低的桶
    peaks: list[int] = []
    for bucket in buckets:
        cur = len(buckets[bucket])
        left = len(buckets.get(bucket - 1, []))
        right = len(buckets.get(bucket + 1, []))
        if cur >= left and cur >= right:
            peaks.append(bucket)
    # 峰值太少的短视频（弹幕均匀）就退回全部桶
    candidates = peaks or list(buckets)

    ranked = sorted(candidates, key=lambda b: len(buckets[b]), reverse=True)[:top]
    # ★ 自适应门槛（2026-10-11）：弹幕越多，峰值要求越高（避免零星噪音成为"高光"）。
    #   弹幕本来就稀疏的视频（如 20~30 条），峰值至少 3 条才算；
    #   弹幕多的视频（几百条），1~2 条的峰值明显是噪音，门槛抬到 ~5 条。
    total = len(messages)
    if total >= 300:
        floor = max(min_count, 5)
    elif total >= 100:
        floor = max(min_count, 4)
    else:
        floor = min_count
    out: list[dict] = []
    for bucket in sorted(ranked):
        items = buckets[bucket]
        if len(items) < floor:
            continue          # 太稀疏 → 不是高光，别报（见 docstring）
        texts = [str(i.get("text", "")) for i in items]
        emotions = Counter(e for e in (_emotion_of(t) for t in texts) if e)
        emotion = emotions.most_common(1)[0][0] if emotions else "吐槽"
        # 优先取"有实质内容且最短"的一条（最像一句话吐槽）；
        # 全是纯符号/单字时退回最短原文，绝不放弃这个高点。
        meaningful = [t for t in texts if _has_content(t)]
        sample = min(meaningful, key=len, default="")
        if not sample:
            sample = min((t for t in texts if t), key=len, default="")
        # 另外几条有实质内容的弹幕，给猫娘更多"有话可说"的料
        extras = sorted({t for t in meaningful if t != sample}, key=len)[:3]
        at = bucket * _BUCKET_SECONDS
        out.append({
            "at": at,
            "at_label": _fmt_ts(at),
            "count": len(items),
            "emotion": emotion,
            "sample": sample[:40],
            "samples": extras,
        })
    return out


def build_digest(
    info: dict,
    danmaku: list[dict],
    comments: list[dict],
    *,
    max_hotspots: int = 4,
    max_comments: int = 3,
) -> str:
    """拼一条紧凑的视频摘要文本（目标 < 500 字）。"""
    lines: list[str] = []
    title = (info.get("title") or "").strip()
    author = (info.get("author") or "").strip()
    tname = (info.get("tname") or "").strip()
    dur = info.get("duration") or 0
    head = f"《{title}》"
    meta = []
    if author:
        meta.append(f"UP主 {author}")
    if tname:
        meta.append(f"分区 {tname}")
    if dur:
        meta.append(f"时长 {_fmt_ts(dur)}")
    if meta:
        head += "（" + "，".join(meta) + "）"
    lines.append(head)

    desc = re.sub(r"\s+", " ", (info.get("desc") or "").strip())
    if desc:
        lines.append(f"简介：{desc[:160]}")

    hotspots = rhythm_hotspots(danmaku, top=max_hotspots)
    if hotspots:
        pts = "；".join(
            f"{h['at_label']} {h['emotion']}点×{h['count']}"
            + (f"（“{h['sample']}”）" if h["sample"] else "")
            for h in hotspots
        )
        lines.append(f"观众反应高峰：{pts}")
    elif danmaku:
        lines.append(f"弹幕 {len(danmaku)} 条，无明显集中高点")
    else:
        lines.append("弹幕稀少，暂无观众反应数据")

    if comments:
        cs = "；".join(f"“{c['text'][:60]}”（赞{c.get('like', 0)}）" for c in comments[:max_comments])
        lines.append(f"高赞评论：{cs}")

    return "\n".join(lines)


def content_fingerprint(info: dict, danmaku: list[dict], comments: list[dict]) -> str:
    """内容指纹：用于"同一视频只推一次"的硬去重。"""
    parts = [
        str(info.get("bvid") or ""),
        str(info.get("title") or ""),
        str(len(danmaku)),
        str(len(comments)),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8", "replace")).hexdigest()[:16]


def build_intro_text(
    info: dict,
    danmaku: list[dict],
    comments: list[dict],
    *,
    meme_hints: list[str] | None = None,
) -> str:
    """第一轮：「这是什么视频」。

    给她的料要能支撑"创业式吐槽"——标题/简介讲清主题，节奏图指出观众在
    哪几个点集中反应，高赞评论让她知道大家的态度。深度话题时她也能顺着聊。

    `meme_hints`：这个视频的"梗/圈子"素材（标签 + 观众原话，见 _memes.py）。
    有梗时她能顺着圈子玩梗，而不是泛泛而谈。
    """
    lines: list[str] = []
    title = (info.get("title") or "").strip()
    author = (info.get("author") or "").strip()
    tname = (info.get("tname") or "").strip()
    dur = info.get("duration") or 0
    head = f"《{title}》"
    meta = []
    if author:
        meta.append(f"UP主 {author}")
    if tname:
        meta.append(f"分区 {tname}")
    if dur:
        meta.append(f"时长 {_fmt_ts(dur)}")
    if meta:
        head += "（" + "，".join(meta) + "）"
    lines.append(head)

    # ★ 梗/圈子：让她知道"这是什么梗、哪个圈子的"，吐槽才有梗味
    if meme_hints:
        lines.append("梗/圈子线索：" + "、".join(meme_hints[:6]))

    # ★ 简介是"这个视频讲什么"的最好来源（尤其弹幕少的视频）。
    #   弹幕稀疏时放宽到 260 字：让简介顶上来当主料，而不是硬凑弹幕高光。
    desc = re.sub(r"\s+", " ", (info.get("desc") or "").strip())
    if desc:
        limit = 260 if len(danmaku) < 100 else 180
        lines.append(f"简介：{desc[:limit]}")

    spots = rhythm_hotspots(danmaku, top=6)
    if spots:
        pts = "；".join(
            f"{h['at_label']}（{h['count']}条）" for h in spots
        )
        # ★ 呈现方式（2026-10-11 关键修正）：**不能写成"观众反应高峰"** ——
        #   实测她会直接复述"这个片子的观众反应高峰好多呀"，满嘴弹幕。
        #   改成中性标注 + 明确声明"只是参考、别当主题讲"。
        lines.append(
            f"【参考·观众弹幕最密的时间点，不是主题】{pts}\n"
            "（这些只是观众刷弹幕密的位置，**不是**这个视频的重点；"
            "别去讲「观众反应高峰」这种话。）"
        )

    # ★ 热评的"观点"往往比弹幕的"口号"有料（实测："让好汉查好汉，让ai审ai"
    #   这种金句全在评论里）。弹幕稀疏时把热评提到高光前面，优先喂她。
    comments_first = bool(comments) and len(danmaku) < 100
    cs = ""
    if comments:
        cs = "；".join(
            f"“{c['text'][:70]}”（赞{c.get('like', 0)}）" for c in comments[:4]
        )
    if cs:
        if comments_first:
            lines.append(f"高赞评论（观众的核心观点在这）：{cs}")
        else:
            lines.append(f"高赞评论：{cs}")

    if not spots:
        if danmaku:
            lines.append(f"（弹幕只有 {len(danmaku)} 条，没形成明显高峰——别硬提弹幕，"
                         "就按上面的简介和评论，用你自己的理解聊这个视频。）")
        else:
            lines.append("（这个视频几乎没有弹幕。别编观众反应，"
                         "就按上面的简介/标题，用你自己的理解聊。）")

    return "\n".join(lines)


def build_hotspot_text(info: dict, hotspot: dict) -> str:
    """高光点提醒：视频播到某个峰值的这一刻。

    ★ 设计（2026-10-11 二次修正）：**不描述画面**（她自己看得见屏幕），
      也**不把弹幕当主题**（实测她会满嘴"这里弹幕好多"）。

      正确的呈现：只告诉她"**这一刻是看点**"，弹幕作为**极小字号的附注**
      （"观众在刷：…"），并把"情绪标签"（笑点/泪点）去掉——那是我的分类，
      不是她的感受，写出来她就会念。

    例：「《…》播到 3:20 了——**这里是值得留意的一刻**。
         （观众也在刷：笑容消失术 / 太好笑了）」
    """
    title = (info.get("title") or "").strip()
    label = hotspot.get("at_label") or "?"
    sample = hotspot.get("sample") or ""
    extras = hotspot.get("samples") or []

    quote = " / ".join(t for t in ([sample] + list(extras)) if t)
    line = f"《{title}》播到 {label} 了——**这里是值得留意的一刻**。"
    if quote:
        line += f"\n（观众这时也在刷这些：{quote}）"
    return line