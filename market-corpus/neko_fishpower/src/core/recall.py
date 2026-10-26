"""记忆唤起：钓到重复鱼/到达岛屿时"想起来"，低权重记忆用模糊措辞。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .memory import MemoryStore


class Recall:
    """场景唤起：根据当前事件查记忆，生成一句"想起来"的话。"""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    def on_caught(self, msg: Dict[str, Any], journal_count: int) -> Optional[str]:
        """钓到鱼：查图鉴足迹（是否重复/新鱼）。低权重记忆用"好像/大概"。"""
        fish = str(msg.get("fish", ""))
        if not fish:
            return None
        prev = self.memory.query("journal", fish)
        if prev and not msg.get("new"):
            hedge = "好像" if self.memory.weight_of("journal", fish) < 0.4 else ""
            ts = prev.get("ts", 0)
            if ts:
                from datetime import datetime
                day = datetime.fromtimestamp(ts).strftime("%m月%d日")
                return f"又是{fish}喵！{hedge}上次钓到它还是 {day} 呢~"
            return f"这条{fish}{hedge}见过好多次了喵，老熟鱼~"
        if msg.get("new"):
            return None  # 新鱼由 discovered/caught 播报
        return None

    def on_island(self, island: str) -> Optional[str]:
        """到达岛屿：查足迹（低权重模糊化）。"""
        if not island:
            return None
        prev = self.memory.query("islands", island)
        if prev:
            hedge = "好像" if self.memory.weight_of("islands", island) < 0.4 else ""
            name = island if island.endswith("岛") else f"{island}岛"
            return (f"{hedge}又回{name}了喵，上次来是 "
                    f"{time.strftime('%m月%d日', time.localtime(prev.get('ts', 0)))}~")
        return None
