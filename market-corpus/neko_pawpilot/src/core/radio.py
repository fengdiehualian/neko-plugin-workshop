"""猫娘电台：按时段/天气/驾驶状态挑一段节目，内容交宿主 LLM 演绎。

设计要点：插件**不生成台词**，只给宿主 LLM 一份"节目单事实行"
（现在几点、开了多久、外面什么天气、路况如何、要她做什么节目），
由宿主按猫娘人设说出来——与事故/任务汇报同一条 respond 通道。

关于"真实电台名"：ETS2 并不通过遥测暴露当前电台。如果 OCR 从画面里
读到了电台名，就顺带带上（`station_hint`），读不到就让猫娘做自己的电台。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# 节目类型 → 需要的上下文
SEGMENTS = ("morning", "night", "weather", "long_haul", "request", "hour")

SEGMENT_BRIEF = {
    "morning": "开场节目：问候 + 报时 + 今天的第一段路",
    "night": "深夜节目：压低声线陪夜路，点一首慢歌",
    "weather": "天气播报：结合当前天气提醒路况，再放一首应景的歌",
    "long_haul": "长途特辑：已经开很久了，聊两句长途感受，点一首提神的歌",
    "request": "点歌环节：替主人点一首歌，说说为什么想放这首",
    "hour": "整点报时：报时 + 一句路况 + 一句关心",
}


class RadioDJ:
    """决定"该不该开一段节目"以及"开哪一段"。"""

    def __init__(self, cfg: Any = None) -> None:
        self.cfg = cfg
        self._last_segment_at = 0.0
        self._last_kind = ""

    def due(self, now: float, driving_since: float = 0.0,
            interval_mult: float = 1.0) -> bool:
        """是否到该开一段的时间（间隔可配，默认 10 分钟；设 0 = 关闭电台）。

        interval_mult 是统一的"陪聊密度"倍率（播报频率 × 玩法），
        让面板上的「播报频率」真的能一并管住电台，而不只是管事件冷却。
        """
        try:
            interval = float(getattr(self.cfg, "radio_interval_s", 600.0))
        except (TypeError, ValueError):
            interval = 600.0
        if interval <= 0:
            return False
        interval *= max(0.1, float(interval_mult or 1.0))
        return (now - self._last_segment_at) >= interval

    def pick(self, snap: Any, now: float, driving_since: float = 0.0,
             station_hint: str = "", weather: str = "",
             interval_mult: float = 1.0) -> Optional[str]:
        """返回一段"节目单事实行"（供宿主 LLM 演绎）；不该播时返回 None。

        2026-10 修正：旧版要求 `snap.on_job`，于是**自由驾驶时电台一次都没播过**
        （主人实测反馈"电台功能到底能不能用"）。电台是陪伴内容，跟有没有接单无关，
        只要在开车就该能听。
        """
        if snap is None or getattr(snap, "speed_kmh", 0) < 3:
            return None
        if getattr(snap, "paused", False):
            return None
        if not self.due(now, driving_since, interval_mult=interval_mult):
            return None
        hour = 12
        tmin = getattr(snap, "time_abs_min", None)
        if tmin is not None:
            hour = int(tmin // 60) % 24
        # 选类型：深夜 > 长驾 > 天气 > 整点 > 点歌
        if 23 <= hour or hour < 5:
            kind = "night"
        elif driving_since >= 5400:
            kind = "long_haul"
        elif weather and ("雨" in weather or "雪" in weather or "雾" in weather):
            kind = "weather"
        elif hour in (7, 8, 9):
            kind = "morning"
        elif (tmin is not None) and ((tmin % 60) <= 2):
            kind = "hour"
        else:
            kind = "request"
        self._last_segment_at = now
        self._last_kind = kind
        return self.brief(snap, kind, hour=hour, driving_since=driving_since,
                          station_hint=station_hint, weather=weather)

    def brief(self, snap: Any, kind: str, hour: int = 12, driving_since: float = 0.0,
              station_hint: str = "", weather: str = "") -> str:
        """拼装事实行（不含台词，只有事实 + 要她做什么节目）。"""
        remain = getattr(snap, "route_remaining_km", 0.0)
        speed = getattr(snap, "speed_kmh", 0.0)
        hh, mm = hour, int((getattr(snap, "time_abs_min", 0) or 0) % 60)
        bits = [f"游戏内时间 {hh:02d}:{mm:02d}"]
        if driving_since:
            bits.append(f"本趟已连续驾驶 {driving_since / 60:.0f} 分钟")
        if getattr(snap, "is_city", False):
            bits.append("当前在市区")
        elif getattr(snap, "is_open_road", False):
            bits.append("当前在郊外/高速")
        bits.append(f"里程还剩 {remain:.0f} km、车速 {speed:.0f} km/h")
        if weather:
            bits.append(f"天气：{weather}")
        if station_hint:
            bits.append(f"玩家车里正在放：{station_hint}")
        task = SEGMENT_BRIEF.get(kind, SEGMENT_BRIEF["request"])
        return (f"[猫娘电台] {'；'.join(bits)}。\n"
                f"（节目类型：{task}。按这个类型播一段，2 句以内，"
                f"像真的电台那样自然；不要念出这条提示，也别提系统/插件/工具）")

    def snapshot(self) -> Dict[str, Any]:
        return {"last_kind": self._last_kind, "last_at": self._last_segment_at}
