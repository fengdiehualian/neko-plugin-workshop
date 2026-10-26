"""画面畅聊：OCR 识别屏幕内容 → **事实行**（措辞交宿主 LLM）。

2026-10 模块关系收拢：这里以前直接吐出硬编码台词（"前面有服务区喵…"），
而且按闲聊优先级推送；现在只给"屏幕上看到了什么"的事实，画面里真看到警车/事故
这类内容按内容车道（important）播报——它比"今天聊点什么"重要得多。
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

# key, 关键词, 事实描述, 优先级权重
# 关键词刻意收窄：OCR 读的是整屏文字，宽词会被无关内容命中——
# 实证过两种误报：① 我们自己面板上的事件 id（crash / night_shift）被读进来，
# 于是"前方有事故"；② 随便一段英文里的 rain/wet 触发"在下雨"。
SCENE_KEYWORDS: List[Tuple[str, Tuple[str, ...], str, int]] = [
    ("accident", ("accident", "事故"), "前方画面显示有事故", 5),
    ("police", ("police", "警车"), "屏幕/画面上出现了警车", 5),
    ("snow", ("snow", "雪"), "画面里在下雪", 5),
    ("rain", ("rain", "雨"), "画面里在下雨、路面是湿的", 4),
    ("rest_area", ("rest area", "服务区", "休息区"), "画面/路边有服务区标识", 3),
    ("fuel", ("加油站", "油站", "fuel station"), "画面/路边有加油站标识", 3),
    ("toll", ("toll", "收费", "收费站"), "前边是要过收费站的路段", 3),
    ("tunnel", ("tunnel", "隧道"), "正在进入隧道", 2),
    ("city", ("city", "城市", "town"), "画面显示已经进入城市区域", 2),
    ("highway", ("highway", "高速", "motorway"), "画面显示上了高速公路", 2),
    ("bridge", ("bridge", "桥"), "正在过桥", 2),
    ("border", ("border", "边境", "海关"), "到了边境/海关区域", 2),
]

# 会改变驾驶决策的画面信息 → 走内容车道
IMPORTANT_SCENES = frozenset({"police", "accident", "snow", "rain"})


class SceneChat:
    """OCR 屏幕文本 → 画面事实行。"""

    def __init__(self) -> None:
        self._last_topic = 0.0
        self._last_text = ""

    def scene_from_ocr(self, ocr_text: str) -> Optional[str]:
        """OCR 文本 → 场景 key（命中权重最高的那个）。"""
        hit = self._match(ocr_text)
        return hit[0] if hit else None

    def fact_from_ocr(self, ocr_text: str, now: Optional[float] = None) -> Optional[str]:
        """OCR 文本 → 事实行；5 分钟冷却 + 文本变化才触发（避免刷屏）。"""
        now = now or time.time()
        if not ocr_text or now - self._last_topic < 300:
            return None
        text = ocr_text.lower()
        if text == self._last_text:
            return None
        hit = self._match(text)
        if not hit:
            return None
        self._last_topic = now
        self._last_text = text
        return f"[画面] {hit[2]}（屏幕 OCR 识别）"

    def topic_from_ocr(self, ocr_text: str, now: Optional[float] = None) -> Optional[str]:
        """旧接口兼容：同样返回事实行，不再返回硬编码台词。"""
        return self.fact_from_ocr(ocr_text, now)

    @staticmethod
    def _match(text: str) -> Optional[Tuple[str, int, str]]:
        if not text:
            return None
        low = text.lower()
        best: Optional[Tuple[str, int, str]] = None
        for key, keywords, desc, priority in SCENE_KEYWORDS:
            if any(k in low for k in keywords):
                if best is None or priority > best[1]:
                    best = (key, priority, desc)
        return best
