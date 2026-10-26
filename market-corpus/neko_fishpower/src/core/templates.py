"""情感渲染：事件事实 → 事实行 prompt（respond）或短句（blind 兜底）。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, Optional

from .mood import Persona

# respond 模式的事实行模板：只陈述事实，措辞由宿主按当前人设展开
FACT_TEMPLATES = {
    "cast": "主人抛竿了，用了 {bait} 饵",
    "bite": "浮漂动了，有鱼在咬钩",
    "caught": "主人钓到一条{fish}{extra}，重 {weight} kg，价值 {worth} 金币",
    "miss": "主人提竿晚了，鱼跑掉了",
    "sold": "主人卖了鱼，入账 {amount} 金币（当前共 {total} 金币）",
    "bet": "主人在赌场押了 {color}，赌注价值 {worth} 金币",
    "roulette_result": "赌场轮盘开奖：{color}，{won_desc}",
    "slot_spin": "主人在玩老虎机（下注 {cost} 金币）",
    "slot_result": "老虎机结果揭晓，{won_desc}",
    "grill_start": "主人把鱼放上烤架开始烤",
    "grill_done": "主人的鱼烤好了",
    "boss_spawn": "Boss 出现了",
    "boss_hp": "Boss 血量 {hp}/{max_hp}",
    "boss_death": "主人和队友击败了 Boss",
    "discovered": "主人解锁了图鉴新鱼：{fish}（{count}/{total}）",
    "kill": "主人击杀了 {target}",
    "seagull": "一只海鸥叼走了主人的鱼",
    "game_start": "主人打开了渔力全开，准备钓鱼",
    "game_end": "主人结束今天的垂钓",
}

# blind 模式的短句兜底（仅当直出时才用）
SHORT_LINES = {
    "cast": ["抛竿啦喵！用了{bait}，等鱼上钩~"],
    "bite": ["浮漂动了！来了来了喵！"],
    "caught": ["钓到{fish}了喵！{weight}kg，值{worth}金币{extra}"],
    "miss": ["跑掉了喵… 下次提竿再快一点！"],
    "sold": ["卖鱼入账 {amount} 金币喵，现在共 {total} 金币"],
    "bet": ["在赌场押了{color}！{worth}金币的鱼，够大胆喵！"],
    "roulette_result": ["轮盘开奖：{color}！{won_desc}"],
    "slot_spin": ["老虎机转起来喵！"],
    "slot_result": ["老虎机结果喵！{won_desc}"],
    "grill_start": ["鱼放上烤架了喵，等它滋滋响~"],
    "grill_done": ["鱼烤好啦喵！香气扑鼻！"],
    "boss_spawn": ["Boss 出现了喵！大家集火！"],
    "boss_hp": ["Boss 还有 {hp}/{max_hp} 血，坚持住喵！"],
    "boss_death": ["Boss 被击败了喵！传奇战利品！🎉"],
    "discovered": ["图鉴新鱼：{fish}！{count}/{total} 了喵！"],
    "kill": ["击杀了 {target} 喵，厉害！"],
    "seagull": ["海鸥叼走了你的鱼！！气死喵了！"],
    "game_start": ["欢迎回来喵！今天钓点什么？"],
    "game_end": ["今天辛苦了喵，收竿晚安~ 💤"],
}

# respond 模式的要求行：交给宿主，按当前人设决定措辞
REPLY_CONTRACT = (
    "以当前人设的口吻，用一句话回应上面的钓鱼情况。"
    "你是钓鱼陪玩猫娘，语气自然，不超过 30 字，可以带语气词。"
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
