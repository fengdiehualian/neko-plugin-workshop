"""每钓小挑战：出钓时立目标，结算时判定（纯话术，靠记忆记录输赢）。"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, Optional

from .memory import MemoryStore

GOALS = [
    ("weight", "我跟你赌：下一竿钓到 3kg 以上的鱼喵！赢了给我唱首歌~", 3.0),
    ("streak", "连钓 3 条不跑鱼的话，我请你吃冰淇淋！🍦", 3),
    ("rare", "赌这条是稀有货！输了要给我讲故事哦~", None),
]


class Challenge:
    """每钓小挑战：开局立目标，caught/miss 事件结算。"""

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory
        self._active: Optional[Dict[str, Any]] = None
        self._streak = 0

    def start(self) -> Optional[str]:
        """出钓开始（game_start）随机立一个目标（冷却 3 局一次）。"""
        wins = int((self.memory.query("challenge", "wins") or {}).get("count", 0))
        losses = int((self.memory.query("challenge", "losses") or {}).get("count", 0))
        if (wins + losses) % 3 != 0:
            return None
        goal, line, param = random.choice(GOALS)
        self._active = {"goal": goal, "param": param, "start": time.time()}
        return line

    def on_caught(self, msg: Dict[str, Any]) -> Optional[str]:
        """上钩时判定目标。"""
        if not self._active:
            return None
        goal = self._active["goal"]
        if goal == "weight":
            w = float(msg.get("weight", 0) or 0)
            if w >= float(self._active.get("param", 0)):
                return self._win(f"{w}kg！目标达成！")
        elif goal == "streak":
            self._streak += 1
            if self._streak >= int(self._active.get("param", 3)):
                return self._win(f"连钓 {self._streak} 条！稳如老狗喵！")
        elif goal == "rare":
            if msg.get("rarity") in ("rare", "legendary") or msg.get("shiny"):
                return self._win("稀有货到手！眼睛真尖喵！")
        return None

    def on_miss(self) -> Optional[str]:
        """跑鱼：连钓中断/目标失败。"""
        if self._active and self._active.get("goal") == "streak":
            self._streak = 0
            return "哎呀连钓断了喵… 挑战失败，欠我一个故事~"
        if self._active:
            self._active = None
        return None

    def _win(self, text: str) -> str:
        self.memory.bump("challenge", "wins", "count")
        self._active = None
        self._streak = 0
        return f"{text} 你赢了喵！……♪ 我是小钓手，钓条大鱼 ♪"
