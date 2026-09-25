"""情感渲染：事件事实 → 事实行 prompt（respond）或短句（blind 兜底）。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, Optional

from .mood import Persona

# respond 模式的事实行模板：只陈述事实，措辞由宿主按当前人设展开
FACT_TEMPLATES = {
    "speeding": "主人正在超速行驶，当前 {speed:.0f} km/h，限速 {limit:.0f} km/h",
    "hard_brake": "主人刚才急刹车了（{speed:.0f} km/h 突然刹停）",
    "crash": "主人出车祸了，车辆损伤：{parts}",
    "job_start": "主人接了一单：{cargo}，从 {src} 到 {dst}，全程 {km} km",
    "job_delivered": "主人完成送货，收入 {revenue} €",
    "job_cancelled": "主人取消了任务，被扣 {penalty} €",
    "fine": "主人收到罚单：{amount} €（{offence}）",
    "tollgate": "主人刚过了收费站，付费 {amount} €",
    "refuel": "主人刚加完油，当前油量 {liters:.0f}L（{percent:.0f}%）",
    "trailer_attach": "主人挂上了挂车（{plate}）",
    "trailer_detach": "主人卸下了挂车",
    "time_warning": "交付时间只剩 {min} 分钟了",
    "time_over": "这单已经超时了",
    "low_fuel": "油量只剩 {percent:.0f}%，需要找服务区加油",
    "game_start": "主人打开了欧卡2，准备开始驾驶",
    "game_end": "主人结束今天的长途驾驶",
    "trip_progress": "太好啦！我们已经走了{mark_ratio}了喵，距离目的地还有 {km:.0f} km~",
    "distance_mark": "快到啦！还剩 {km:.0f} km 就到目的地了（{mark} km 预告）",
    "time_relaxed": "这单交付时间很宽裕（还剩 {min} 分钟）",
    "time_tight": "交付时间有点紧张（还剩 {min} 分钟）",
    "early_arrival": "主人提前完成了送货",
    "cargo_damage": "货物完好率降到了 {pct:.0f}%",
}

# blind 模式的短句兜底（仅当直出时才用）
SHORT_LINES = {
    "speeding": ["超速了喵！{speed:.0f} / 限速 {limit:.0f} km/h"],
    "hard_brake": ["急刹！{speed:.0f} km/h 突然刹停，吓我一跳喵"],
    "crash": ["！！撞了！！喵呜你没事吧？！损伤：{parts}"],
    "job_start": ["接了 {cargo}，{src} → {dst}，{km} km 喵！出发！"],
    "job_delivered": ["到货结算喵！收入 {revenue} €"],
    "job_cancelled": ["任务取消了喵？扣了 {penalty} €…"],
    "fine": ["刚收到罚单喵！{amount} €（{offence}）"],
    "tollgate": ["过收费站花了 {amount} € 喵"],
    "refuel": ["加油了喵！现在 {liters:.0f}L（{percent:.0f}%）"],
    "trailer_attach": ["挂车挂上了喵！（{plate}）"],
    "trailer_detach": ["挂车卸下了喵，去接新活？"],
    "time_warning": ["只剩 {min} 分钟交付时间了喵！抓紧！"],
    "time_over": ["超时了喵… 这单要扣钱了 😿"],
    "low_fuel": ["油量只剩 {percent:.0f}% 了喵，记得找服务区！"],
    "game_start": ["欢迎回来喵！今天跑哪条线？"],
    "game_end": ["今天辛苦了喵，晚安~ 💤"],
    "trip_progress": ["已经走了{mark_ratio}了喵！还剩 {km:.0f} km~"],
    "distance_mark": ["快到了喵！还剩 {km:.0f} km~"],
    "time_relaxed": ["时间很宽裕喵~ 慢慢开，欣赏下风景！"],
    "time_tight": ["时间有点紧喵… 要抓紧了！不过安全第一！"],
    "early_arrival": ["提前到了喵！效率满分！雇主肯定开心！⭐"],
    "cargo_damage": ["货物完好率只剩 {pct:.0f}% 了喵… 刚才是不是蹭到了？"],
}

# respond 模式的要求行：交给宿主，按当前人设决定措辞
REPLY_CONTRACT = (
    "以当前人设的口吻，用一句话回应上面的驾驶情况。"
    "你是副驾驶伙伴，语气自然，不超过 30 字，可以带语气词。"
)


def _load_templates() -> Dict[str, str]:
    p = Path(__file__).resolve().parent.parent / "data" / "config" / "templates.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


class EmotionRenderer:
    """事件 → 事实行 prompt 或短句（LLM 优先，模板降级）。"""

    def __init__(self, persona: Persona, llm: Any = None) -> None:
        self.persona = persona
        self._llm = llm
        self._short_lines = _load_templates()
        mood_path = Path(__file__).resolve().parent.parent / "data" / "config" / "mood.json"
        try:
            mood_data = json.loads(mood_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            mood_data = {}
        self._mood_map: Dict[str, Dict[str, float]] = mood_data.get("event_mood", {})
        self._rng = random.Random()

    async def fact_prompt_llm(self, event_name: str, **kw: Any) -> Optional[str]:
        """LLM 渲染（配置了才调用），失败返回 None 由模板兜底。"""
        if self._llm is None:
            return None
        fact = self._format(FACT_TEMPLATES.get(event_name, ""), event_name, **kw)
        hint = self.persona.persona_hint()
        return await self._llm.call(f"{fact}\n{hint}")

    def fact_prompt(self, event_name: str, **kw: Any) -> str:
        """respond 模式：事实行 + 人设要求行（宿主按当前人设展开）。"""
        self._apply_mood(event_name)
        fact = self._format(FACT_TEMPLATES.get(event_name, ""), event_name, **kw)
        hint = self.persona.persona_hint()
        return f"{fact}\n{hint}"

    async def short_line_llm(self, event_name: str, **kw: Any) -> Optional[str]:
        """blind 短句：LLM 优先（配置了才调），失败返回 None 走模板。"""
        if self._llm is None:
            return None
        fact = self._format(FACT_TEMPLATES.get(event_name, ""), event_name, **kw)
        hint = self.persona.persona_hint()
        text = await self._llm.call(f"{fact}\n{hint}（一句话，短，直出）")
        return text if text else None

    def short_line(self, event_name: str, **kw: Any) -> str:
        """blind 模式：直出短句（模板）。"""
        self._apply_mood(event_name)
        text = self._format(self._short_lines.get(event_name, ""), event_name, **kw)
        return self.persona.polish(text)

    def custom_fact(self, text: str, event_name: str = "game_start") -> str:
        """自定义事实行（记忆唤起等）。"""
        self._apply_mood(event_name)
        return f"{text}\n{self.persona.persona_hint()}"

    def _format(self, tmpl: str, event_name: str, **kw: Any) -> str:
        if tmpl:
            try:
                return tmpl.format(**kw)
            except (KeyError, ValueError):
                pass
        return f"[{event_name}] {kw}"

    def _apply_mood(self, event_name: str) -> None:
        mapping = self._mood_map.get(event_name)
        if mapping:
            self.persona.on_event(event_name, mapping)
