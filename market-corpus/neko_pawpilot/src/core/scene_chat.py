"""画面畅聊：OCR 识别屏幕内容 → 场景话题（看见什么聊什么）。"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

# 关键词 → (话题文本, 优先级)
SCENE_KEYWORDS: List[Tuple[Tuple[str, ...], str, int]] = [
    (("rest area", "服务区", "休息区"), "前面有服务区喵，正好歇口气再走~", 3),
    (("gas", "fuel", "加油站", "油站"), "加油站！顺路补个油喵~", 3),
    (("police", "警车", "police car"), "诶？前面有警车！千万别超速喵！🚔", 5),
    (("accident", "事故", "crash"), "前面好像有事故喵，绕一下吧，安全第一！", 5),
    (("toll", "收费", "收费站"), "要过收费站了喵，准备好零钱~", 3),
    (("city", "城市", "town"), "进城了喵，注意限速和行人！", 2),
    (("highway", "高速", "motorway"), "上高速了喵！定速巡航开起来~", 2),
    (("rain", "雨", "wet"), "下雨了喵！雨刮开起来，慢点开~", 4),
    (("snow", "雪", "winter"), "下雪了喵！路面滑，千万别急刹！", 5),
    (("tunnel", "隧道"), "进隧道了喵，记得开灯！", 2),
    (("bridge", "桥", "brücke"), "过桥了喵，这儿的风景真不错~", 2),
    (("border", "边境", "海关"), "到边境了喵，注意证件和海关检查~", 2),
    (("night", "night"), "天黑了喵，我陪你跑夜路~", 2),
]


class SceneChat:
    """OCR 屏幕文本 → 场景话题（L4 通道）。"""

    def __init__(self) -> None:
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
