"""画面畅聊（钓鱼版）：OCR 识别屏幕文本 → 场景话题 / 鱼种互动。

与 pawpilot 的 SceneChat 同构：关键词表兜底 + 文本变化去重 + 冷却。
额外：可注入 KnowledgeBase，OCR 文本里出现知识库鱼种/饵名时主动聊。
"""

from __future__ import annotations

import time
from typing import Any, List, Optional, Tuple

# 关键词 → (话题文本, 优先级)
SCENE_KEYWORDS: List[Tuple[Tuple[str, ...], str, int]] = [
    (("boss", "boss 血", "boss战", "首领"), "Boss 出来了喵！准备好最强鱼竿！⚔️", 5),
    (("rare", "稀有", "legendary", "传奇"), "稀有鱼！这种可不常见喵，专心收线！✨", 5),
    (("drip", "滴液", "彩虹"), "滴液鱼/彩虹皮肤喵！这颜色也太好看了~", 4),
    (("endangered", "濒危"), "濒危鱼种喵… 拍个照再放回去？", 3),
    (("grill", "烤", "烧烤"), "烤架飘香喵，钓上来直接加餐！", 3),
    (("seagull", "海鸥"), "海鸥又在边上转悠喵… 看好鱼篓！", 4),
    (("shark", "鲨鱼"), "鲨鱼！这种劲道十足，别被拖下水喵！", 4),
    (("crab", "螃蟹"), "螃蟹上钩了喵，夹子当心！", 2),
    (("squid", "鱿鱼", "章鱼"), "触手系喵？小心喷墨！", 3),
    (("turtle", "海龟"), "海龟慢悠悠的，但咬钩很凶喵！", 2),
    (("new", "新鱼", "discovered", "发现"), "图鉴新鱼喵！收集进度+1！📖", 4),
    (("island", "岛", "水域"), "这片水域看起来鱼种不太一样喵，多试试不同饵？", 2),
    (("night", "night", "夜晚"), "夜钓了喵，月光下的鱼更活跃？", 2),
    (("money", "金币", "卖鱼", "价格"), "这鱼值不少金币喵，卖了换个好饵！", 3),
]


class SceneChat:
    """OCR 屏幕文本 → 钓鱼场景话题（L4 通道）。"""

    def __init__(self, knowledge: Any = None) -> None:
        self.knowledge = knowledge  # 可注入 KnowledgeBase 做鱼种匹配
        self._last_topic = 0.0
        self._last_text = ""

    def topic_from_ocr(self, ocr_text: str, now: float | None = None) -> Optional[str]:
        """OCR 文本 → 场景话题；10 分钟冷却避免刷屏。"""
        now = now or time.time()
        if not ocr_text or now - self._last_topic < 600:
            return None
        text = ocr_text.lower()
        # 文本变化才可能触发新话题
        if text == self._last_text:
            return None
        # 知识库鱼种/饵名优先匹配（OCR 看到 UI 里的鱼名）
        kb_line = self._knowledge_line(text)
        if kb_line:
            self._last_topic = now
            self._last_text = text
            return kb_line
        best: Optional[Tuple[str, int]] = None
        for keywords, topic, priority in SCENE_KEYWORDS:
            if any(k in text for k in keywords):
                if best is None or priority > best[1]:
                    best = (topic, priority)
        if best:
            self._last_topic = now
            self._last_text = text
            return best[0]
        return None

    def _knowledge_line(self, text: str) -> Optional[str]:
        """OCR 文本命中知识库鱼种/饵名 → 聊鱼。"""
        kb = self.knowledge
        if kb is None or not text:
            return None
        try:
            for name in kb.creature_names():
                if name and name.lower() in text:
                    worth = 0
                    info = kb.creature_info(name)
                    if info and "值" in info:
                        try:
                            worth = int(info.split("值")[1].split(" 金币")[0])
                        except (ValueError, IndexError):
                            worth = 0
                    hint = f"（值 {worth} 金币）" if worth else ""
                    return f"诶，屏幕上是 {name} 喵{hint}！这种鱼我认识，手感不错~"
            for bait in kb.bait_names():
                if bait and bait.lower() in text:
                    return f"在用 {bait} 饵喵？这饵对不少鱼都有效~"
        except Exception:
            return None
        return None
