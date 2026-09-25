"""空闲主动闲聊：没有敌人/低威胁时，猫娘偶尔主动说一句轻松短句。

用途：让插件在没有态势警报的“安静”时段也能陪聊，而不是一直沉默。
设计上把 chatter 事件优先级压到最低（见 EVENT_CATALOG），因此只要有更重要的
警觉/对局事件，它就永远不会抢戏；只有真正空闲时才轮得到它。
"""

from __future__ import annotations

import time

from ..core.contracts import CbqConfig, CompanionEvent, SceneState

_FIGHT_THREATS = frozenset({"near", "critical"})


class ChatterDetector:
    id = "chatter"

    def __init__(self, cfg: CbqConfig | None = None) -> None:
        self.cfg = cfg or CbqConfig()
        self._last_chat_at = 0.0  # 上一次真正说出一句闲聊的时间（wall-clock）
        self._last_fight_at = 0.0  # 最后一次“有敌人/高威胁”的时间

    def reset(self) -> None:
        self._last_chat_at = 0.0
        self._last_fight_at = 0.0

    def _in_fight(self, cur: SceneState) -> bool:
        awareness = cur.awareness if isinstance(cur.awareness, dict) else {}
        threat = str(awareness.get("threat_level") or "none").strip().lower()
        return bool(cur.flags.get("target_in_view")) or threat in _FIGHT_THREATS

    def feed(self, prev: SceneState, cur: SceneState) -> list[CompanionEvent]:
        now = time.time()
        if not cur.game_running:
            self.reset()
            return []
        in_fight = self._in_fight(cur)
        if in_fight:
            # 正在交火/有敌人：既不说闲聊，也把“战斗结束后的冷静期”起点刷新到这里
            self._last_chat_at = 0.0
            self._last_fight_at = now
            return []

        # 空闲状态
        min_interval = max(
            0.0, float(getattr(self.cfg, "chatter_min_interval_seconds", 45.0) or 0.0)
        )
        fight_cooldown = max(
            0.0, float(getattr(self.cfg, "chatter_fight_cooldown_seconds", 20.0) or 0.0)
        )
        # 刚打完仗，先过一段“冷静期”再搭话
        if self._last_fight_at and now - self._last_fight_at < fight_cooldown:
            return []
        if self._last_chat_at == 0.0:
            # 刚进入空闲：从这一秒开始计时，不立即说话，避免一开局/刚静下来就插话
            self._last_chat_at = now
            return []
        if now - self._last_chat_at < min_interval:
            return []
        # 已达到间隔：说一句，并重新开始下一段计时
        self._last_chat_at = now
        return [CompanionEvent(event_id="chatter", level="info", edge="enter", created_at=now)]


def build_chatter_detectors(cfg: CbqConfig | None = None) -> list[ChatterDetector]:
    return [ChatterDetector(cfg)]
