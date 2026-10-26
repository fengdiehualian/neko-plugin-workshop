"""氛围闲聊池：按场景的话题（抛竿等待/收线/赌场/烤鱼/Boss/岛屿/海鸥）。"""

from __future__ import annotations

import random
from typing import Dict, Optional

TOPICS = {
    "waiting": [
        "浮漂安安静静的喵… 这条水域的鱼都在睡午觉吗？",
        "钓鱼就是要耐得住性子喵~ 我看好这一竿！",
        "听说这片水域最近来了不少新鱼，耐心等它们上钩喵！",
    ],
    "reeling": [
        "这条挣扎得挺凶，估计不小喵！",
        "稳住稳住喵！别让它把线扯断了！",
        "手感不错喵！这种挣扎说明是条好鱼！",
    ],
    "casino": [
        "赌场那边又热闹了喵，要不要去玩一把？（小赌怡情~）",
        "听那边开奖的动静，有人赢大发了喵！",
    ],
    "grill": [
        "烤架那边飘香味了喵，烤鱼配海风绝了~",
        "钓累了可以去烤条鱼吃，补充体力喵！",
    ],
    "boss": [
        "Boss 出没的传闻越来越多了喵，全岛都在备战！",
        "要是能钓到 Boss 级的，那可是传奇战利品喵！",
    ],
    "island": [
        "这座岛的风景真不错喵，海风都带着咸味~",
        "别的岛听说鱼种不太一样，想收集图鉴得多跑跑喵！",
    ],
    "seagull": [
        "那边有只海鸥盯着咱们的鱼篓看半天了喵… 小心！",
        "海鸥又来巡逻了喵，看好手上的鱼！",
    ],
    "night": [
        "夜钓别有味道喵，月光下水面银闪闪的~",
        "这么晚还在钓鱼，是真爱了喵！我陪你！",
    ],
    "default": [
        "今天钓得怎么样喵？手感顺不顺？",
        "这片海的鱼脾气我都摸清了喵，稳的！",
    ],
}


class SmallTalk:
    """L4 闲聊：场景话题 + 低频随机触发。"""

    def __init__(self) -> None:
        self._last: Dict[str, float] = {}
        self._interval = 900.0  # 15 分钟保底

    def random_topic(self, scenario_key: str, now: float) -> Optional[str]:
        """按场景抽一条（同一场景有冷却）。"""
        if now - self._last.get(scenario_key, 0) < self._interval:
            return None
        self._last[scenario_key] = now
        pool = TOPICS.get(scenario_key) or TOPICS["default"]
        return random.choice(pool)
