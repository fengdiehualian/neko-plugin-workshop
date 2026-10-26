"""视频"梗 / 看点"素材提取（纯标准库）。

目的：让她吐槽时**用得上这个视频的梗**，而不是泛泛而谈。

数据来源（都已实测可用）：
  1. **视频标签**（`view/detail` 的 `Tags`）—— UP 主自己打的分类，最能说明
     "这是什么梗/什么圈子"。实测《今天你吃"紫米汤圆"了吗【OW恶搞动画】》返回
     `['搞笑','音乐','二次元手游','攻略','游戏','AI动画','OW']` —— 一眼看出是
     OW（守望先锋）+ 恶搞动画。
  2. **弹幕**（已拉取）—— 观众在具体时间点刷的梗（"笑容消失术""魂游说是"）。
  3. **热评**（已拉取）—— 观众沉淀下来的高赞梗/共识。

为什么不直接"抄梗台词"：
  插件红线是**不写台词**（用户人设千差万别，硬编码台词会 OOC）。这里只提供
  "标签 + 观众原话"这类**事实**，让她用自己的语气组织。

★ 标签接口会限流（实测 `search/type` 曾返回 412），所以：
  - 只在新视频时拉一次
  - 失败静默降级（没有标签也能聊，只是少一层梗）
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request

__all__ = ["extract_meme_hints", "fetch_tags", "is_meme_like"]

_CTX = ssl.create_default_context()
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# 明显是"梗/玩梗"的标签或文本特征（用于从标签里挑出最有梗味的几个）
_MEME_MARKERS = (
    "梗", "恶搞", "搞笑", "沙雕", "整活", "鬼畜", "抽象", "名场面", "爆笑",
    "生草", "魔性", "表情包", "meme", "二创", "同人",
)


def is_meme_like(text: str) -> bool:
    """这段文本是否带"梗味"（用于给弹幕/评论排序）。"""
    t = (text or "").lower()
    return any(m in t for m in _MEME_MARKERS)


def fetch_tags(bvid: str, *, timeout: int = 12) -> list[str]:
    """拉视频标签（UP 主打的分类）。失败返回 []。

    ★ 用 `view/detail`（不是 `view`）：只有它带 `Tags` 字段。
    """
    if not bvid:
        return []
    url = (
        "https://api.bilibili.com/x/web-interface/view/detail"
        f"?bvid={urllib.parse.quote(bvid)}"
    )
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        })
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - 拿不到标签不影响主流程
        return []
    if d.get("code") != 0:
        return []
    tags = ((d.get("data") or {}).get("Tags")) or []
    out: list[str] = []
    for t in tags:
        name = str((t or {}).get("tag_name") or "").strip()
        if name and name not in out:
            out.append(name)
    return out[:10]


def extract_meme_hints(
    *,
    tags: list[str] | None = None,
    danmaku: list[dict] | None = None,
    comments: list[dict] | None = None,
    limit: int = 6,
) -> list[str]:
    """从标签/弹幕/评论里挑出最有"梗味"的几条素材。

    返回一个短列表（默认最多 6 条），直接拼进推送文案。**只挑事实，不编台词。**

    ★ 挑选优先级（重要）：**标签优先于弹幕**。
      实测教训：《紫米汤圆【OW恶搞动画】》的标签里有 `搞笑/恶搞/沙雕`（梗味）
      和 `守望先锋/OW/哈蒙德`（**圈子身份**）。后者才是"这是什么圈子的梗"的
      关键 —— 早期版本让弹幕梗把标签挤掉了，她就不认识 OW 圈。
      所以顺序是：梗味标签 → 其余标签（圈子）→ 弹幕梗 → 评论梗。
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(s: str, *, limit_len: int = 24) -> None:
        s = " ".join(str(s or "").split()).strip()
        if not s or s in seen:
            return
        # 太长的不要（那是句子，不是梗）
        if len(s) > limit_len:
            return
        seen.add(s)
        out.append(s)

    tags = [str(t).strip() for t in (tags or []) if str(t).strip()]

    # ① 带梗味的标签（搞笑/恶搞/鬼畜…）—— 最准，说明视频就是玩梗的
    for t in tags:
        if len(out) >= limit:
            break
        if is_meme_like(t):
            _add(t)

    # ② 其余标签（圈子身份：OW / 守望先锋 / 原神 / 魂游…）—— 优先于弹幕
    for t in tags:
        if len(out) >= limit:
            break
        _add(t)

    # ③ 弹幕里的梗（弹幕梗都很短；长句一律不算梗）
    for d in (danmaku or []):
        if len(out) >= limit:
            break
        txt = str((d or {}).get("text") or "")
        if is_meme_like(txt):
            _add(txt, limit_len=16)

    # ④ 热评里的梗（同样要求短）
    for c in (comments or []):
        if len(out) >= limit:
            break
        txt = str((c or {}).get("text") or "")
        if is_meme_like(txt):
            _add(txt, limit_len=16)

    return out[:limit]
