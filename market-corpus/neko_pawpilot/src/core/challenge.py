"""每单小挑战：出发立目标，到货结算输赢。"""

from __future__ import annotations

import random
from typing import Any, Dict, Optional

CHALLENGE_TYPES = ("fuel", "speeding", "brake")

CHALLENGE_LINES = {
    "fuel": "我跟你赌：这趟百公里油耗 < {target:.0f}L 就到站给你唱歌喵~",
    "speeding": "这趟全程不超速的话，我请你吃冰淇淋！🍦（话术奖励）",
    "brake": "一个急刹都不许有！做得到吗？做不到要给我讲故事哦~",
}

CHALLENGE_WIN = {
    "fuel": "百公里 {actual:.1f}L！你赢了喵！……咳咳，♪ 我是小司机，开着小卡车 ♪",
    "speeding": "全程没超速！说到做到，冰淇淋记上了喵 🍦",
    "brake": "一个急刹都没有！完美驾驶喵！",
}

CHALLENGE_LOSE = {
    "fuel": "百公里 {actual:.1f}L，超了目标喵… 欠我一个故事，记小本本上了~",
    "speeding": "超速了 {count} 次喵，赌约输啦，故事欠上了~",
    "brake": "急刹了 {count} 次喵… 说好的不刹呢！",
}


class Challenge:
    """出发时随机立目标，到货结算。"""

    def __init__(self, memory) -> None:
        self.memory = memory
        self._active: Optional[Dict[str, Any]] = None
        self._rng = random.Random()

    def start(self, kind: Optional[str] = None) -> str:
        """接单时随机立一个目标；kind 可指定类型（测试用）。"""
        if kind is None:
            kind = self._rng.choice(CHALLENGE_TYPES)
        target = None
        if kind == "fuel":
            target = round(self._rng.uniform(28, 34), 1)
        self._active = {"kind": kind, "target": target}
        line = CHALLENGE_LINES[kind].format(target=target) if target else CHALLENGE_LINES[kind]
        return line

    def settle(self, stats: Dict[str, Any]) -> str:
        """到货结算；stats 含 fuel_avg/speedings/hard_brakes。"""
        if not self._active:
            return ""
        kind = self._active["kind"]
        target = self._active.get("target")
        win = False
        if kind == "fuel":
            actual = float(stats.get("fuel_avg", 99))
            win = target is not None and actual <= target
            line = (CHALLENGE_WIN if win else CHALLENGE_LOSE)["fuel"].format(
                actual=actual, target=target or 0)
        elif kind == "speeding":
            count = int(stats.get("speedings", 0))
            win = count == 0
            line = (CHALLENGE_WIN if win else CHALLENGE_LOSE)["speeding"].format(count=count)
        else:
            count = int(stats.get("hard_brakes", 0))
            win = count == 0
            line = (CHALLENGE_WIN if win else CHALLENGE_LOSE)["brake"].format(count=count)
        # 输赢记录进关系记忆
        key = "challenge_wins" if win else "challenge_losses"
        entry = self.memory.query("relationship", key) or {"count": 0}
        self.memory.remember("relationship", key, {"count": int(entry.get("count", 0)) + 1},
                             importance=0.6)
        self._active = None
        return line
