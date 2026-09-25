"""司机等级庆祝：等级变化检测 + 庆祝话术。"""

from __future__ import annotations

from typing import Dict, Optional

LEVEL_KEY = "driver_level"

LEVEL_LINES = [
    "升到 {level} 级了喵！恭喜！🎉",
    "新等级！{level} 级！距离老司机又近了一步喵！",
    "升级啦！{level} 级，驾驶技术越来越好了喵 ⭐",
]


class LevelCelebrate:
    """检测司机等级变化并庆祝。"""

    def __init__(self, memory) -> None:
        self.memory = memory
        self._last_level: Optional[int] = None

    def load(self) -> None:
        entry = self.memory.query(LEVEL_KEY, "level")
        self._last_level = int(entry.get("value", 0)) if entry else 0

    def update(self, level: Optional[int]) -> Optional[str]:
        """新等级 → 庆祝话术；首次不算升级。"""
        if level is None or level <= 0:
            return None
        if self._last_level and level > self._last_level:
            import random
            line = random.choice(LEVEL_LINES).format(level=level)
            self._last_level = level
            self.memory.remember(LEVEL_KEY, "level", {"value": level}, importance=0.8)
            return line
        self._last_level = level or self._last_level
        return None

    def set_level(self, level: int) -> None:
        """面板/OCR 设置当前等级。"""
        self._last_level = level
        self.memory.remember(LEVEL_KEY, "level", {"value": level}, importance=0.8)

    def snapshot(self) -> Dict:
        return {"level": self._last_level or 0}
