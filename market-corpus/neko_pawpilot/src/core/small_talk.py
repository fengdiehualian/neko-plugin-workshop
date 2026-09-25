"""氛围闲聊池：按场景分话题 + 事件+随机双触发，低频不打扰。"""

from __future__ import annotations

import random
import time
from typing import Optional

TOPICS = {
    "default": [
        "看到那片山了吗喵？听说翻过去就是瑞士了~",
        "公路边的风景真好看，我替你看风景，你专注开车喵",
        "这单跑完能赚不少，稳稳开就行喵",
        "长途驾驶最容易犯困，我隔一会儿就陪你聊两句",
        "跟着导航走没错，岔路口我会提醒你的喵",
    ],
    "highway": [
        "高速巡航最舒服了，定速一开，剩下的交给我盯着喵",
        "开大车的司机都辛苦，你也是其中一员喵",
        "高速上开久了容易犯困，需要就吱一声，我陪你说话",
        "保持车距慢慢开，这条高速我很熟喵",
    ],
    "urban": [
        "进城了喵，留意行人自行车，我会帮你盯着",
        "市区红绿灯多，正好歇口气喵",
        "城市送货最怕堵，不过慢慢开总比刮蹭强喵",
    ],
    "night": [
        "深夜的高速，星星特别好看喵⭐",
        "夜路视线差，我会更注意提醒你的喵",
        "困了就说，我们找休息区，安全第一喵",
    ],
    "service": [
        "休息区泡杯咖啡，感觉又活过来了喵~",
        "趁休息我帮你看看油量和轮胎喵",
        "歇够了再出发，路上我继续陪你喵",
    ],
}

MILESTONE_TOPICS = {
    100: "到 100km 了喵！第一段旅程起步！",
    500: "500km 咯，你是公路新星喵！",
    1000: "1000km！纪念一下！我是见证者喵 🎉",
    5000: "5000km 大关！这条路都认识你了喵！",
}


class SmallTalk:
    """L4 闲聊：场景话题池 + 里程碑 + 低频保底。"""

    def __init__(self, persona: object = None) -> None:
        self._persona = persona
        self._rng = random.Random()
        self._last_talk = 0.0
        self._last_km = 0.0
        self._fired_milestones: set = set()

    def update(self, snap) -> None:
        """跟踪里程触发里程碑。"""
        if snap.odometer_km > self._last_km:
            self._last_km = snap.odometer_km

    def random_topic(self, scenario: str = "default", now: float | None = None) -> Optional[str]:
        """按场景取话题（间隔随口吻：话痨短/冰山长，不打扰驾驶）。"""
        now = now or time.time()
        interval = 900.0
        if self._persona is not None:
            interval = getattr(self._persona, "talk_interval", 900.0)
        if now - self._last_talk < interval:
            return None
        self._last_talk = now
        pool = TOPICS.get(scenario, TOPICS["default"])
        return self._rng.choice(pool)

    def milestone_topic(self) -> Optional[str]:
        """里程里程碑话题。"""
        for km, topic in sorted(MILESTONE_TOPICS.items()):
            if self._last_km >= km and km not in self._fired_milestones:
                self._fired_milestones.add(km)
                return topic
        return None
