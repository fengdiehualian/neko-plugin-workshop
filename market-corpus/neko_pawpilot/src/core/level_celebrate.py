"""司机等级庆祝：等级变化检测 + 庆祝话术。"""

from __future__ import annotations

from typing import Dict, Optional

LEVEL_KEY = "driver_level"


class LevelCelebrate:
    """检测司机等级变化并播报。"""

    def __init__(self, memory) -> None:
        self.memory = memory
        self._last_level: Optional[int] = None

    def load(self) -> None:
        entry = self.memory.query(LEVEL_KEY, "level")
        self._last_level = int(entry.get("value", 0)) if entry else 0

    def update(self, level: Optional[int]) -> Optional[str]:
        """新等级 → **事实行**（措辞交宿主 LLM）；首次读到不算升级。"""
        if level is None or level <= 0:
            return None
        if self._last_level and level > self._last_level:
            self._last_level = level
            self.memory.remember(LEVEL_KEY, "level", {"value": level}, importance=0.8)
            return f"[升级] 司机等级升到 {level} 级，值得庆祝一下"
        self._last_level = level or self._last_level
        return None

    def snapshot(self) -> Dict:
        return {"level": self._last_level or 0}
