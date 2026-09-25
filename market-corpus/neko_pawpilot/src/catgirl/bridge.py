"""人设桥接：宿主导入人设 + 存在感话术。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

EXISTENCE_LINES = {
    "refuel": [
        "加完油啦喵~ 我下车活动了下筋骨！",
        "趁加油，我去看看轮胎有没有问题喵~",
    ],
    "job_delivered": [
        "到站了喵！我帮你检查下车辆损伤报告…",
        "卸货的时候我绕车看了一圈，一切正常喵！",
    ],
    "crash": [
        "撞得重不重喵？我检查下伤情… 驾驶室损伤 {parts}，要修车了",
        "呜… 我看看你伤到哪了喵，先靠边停！",
    ],
    "game_end": [
        "今天辛苦啦喵！我帮你把车停好了，晚安~ 💤",
        "呼… 坐了一天副驾驶，我也困了喵，晚安！",
    ],
}


def _load_mode_config() -> Dict[str, Any]:
    p = Path(__file__).resolve().parent.parent / "data" / "config" / "mode.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class CatgirlBridge:
    """人设桥接：存在感话术（人设本体由宿主导入到 Persona）。"""

    def __init__(self) -> None:
        self._existence = _load_mode_config().get("existence", EXISTENCE_LINES)

    def existence_line(self, event_name: str, **kw: Any) -> str:
        """存在感话术（加油下车/到站检查/事故安慰）。"""
        lines = self._existence.get(event_name)
        if not lines or not isinstance(lines, (list, tuple)):
            return ""
        text = lines[0] if lines else ""
        if "{parts}" in text:
            text = text.replace("{parts}", kw.get("parts", ""))
        return text
