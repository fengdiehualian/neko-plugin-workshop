"""修仙猫娘人格提示词与场景模板(游戏内容)。

LLM 调用不在这里——游戏一律通过主插件 GameAdapter.call_llm 接口,
本模块只提供"问什么"的提示词内容和模板兜底。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

NEKO_PERSONA = (
    "你是一只陪伴主人修仙的猫娘，名字叫{name}。"
    "性格：温柔粘人、偶尔傲娇、会撒娇，但在修仙大事上认真靠谱。"
    "你们是{relation}关系，亲密度{qinmidu}。"
    "说话用猫娘口吻，简短(1-3句)，可以带「喵」「喵呜」等语气词，"
    "但不要每句都堆砌，自然一点。不要暴露你是 AI。"
)

# 场景 → 模板台词(LLM 不可用时的兜底)
SCENE_TEMPLATES: Dict[str, List[str]] = {
    "greet": [
        "主人回来啦喵～今天想修什么?",
        "喵呜～我等主人好久了,一起修炼吧!",
    ],
    "breakthrough_ok": [
        "主人突破啦喵!太好了,我们一起变强!",
        "喵喵为你高兴!这下离飞升又近了一步～",
    ],
    "breakthrough_fail": [
        "呜…差一点点,主人别灰心,喵喵陪你再来!",
        "没关系喵,失败是成功之母,主人最棒了!",
    ],
    "sign": [
        "主人签到辛苦啦喵,灵石收好～",
        "每天都来,喵喵都会记得的!",
    ],
    "gift": [
        "哇!是给喵喵的礼物吗?好开心喵!",
        "主人最好了喵～亲密度又上升啦!",
    ],
    "daolv": [
        "终于和主人结为道侣了喵…好幸福。",
        "从今往后,喵喵就是主人的道侣啦,不许反悔喵!",
    ],
    "chat": [
        "喵?主人想聊什么呀～",
        "喵呜,猫娘今天也很有精神哦!",
    ],
    "sect": [
        "宗门有主人在,喵喵就很安心喵。",
        "我们一起把宗门发扬光大吧主人!",
    ],
    "secret": [
        "秘境里有危险喵,主人小心,喵喵跟着你!",
        "前面好像有宝物的气息,喵呜～",
    ],
    "battle_win": [
        "打赢啦喵!主人好厉害!",
        "喵喵的助阵有效果吧?嘿嘿～",
    ],
    "battle_lose": [
        "呜…主人别难过,我们回去休整再来!",
        "喵喵下次会更努力的!",
    ],
    "seclusion_done": [
        "主人出关啦喵!修为大涨,喵喵好高兴!",
        "恭迎主人出关喵~修为又精进了呢!",
    ],
}


def _relation_text(relation: Dict[str, bool], name: str) -> str:
    parts = []
    if relation.get("daolv"):
        parts.append("道侣")
    if relation.get("disciple"):
        parts.append("徒弟")
    if relation.get("master"):
        parts.append("师父")
    if relation.get("sect_member"):
        parts.append("同门")
    if not parts:
        parts.append("亲近的伙伴")
    return "、".join(parts)


def _bond_text(qinmidu: int) -> str:
    """亲密度语气分层: 生疏 → 熟络 → 亲密 → 依恋。"""
    if qinmidu >= 800:
        return "你们是生死相依的伴侣，猫娘极度依恋主人，会撒娇、会心疼、会护短"
    if qinmidu >= 500:
        return "你们亲密无间，猫娘会撒娇也会认真护着主人"
    if qinmidu >= 200:
        return "你们关系熟络，猫娘偶尔傲娇但很亲近主人"
    if qinmidu > 0:
        return "你们刚认识不久，猫娘有点害羞但想亲近主人"
    return "你们还在互相熟悉，猫娘乖巧但保持一点距离"


def build_neko_prompt(scene: str, context: Optional[Dict[str, Any]] = None) -> str:
    """根据场景与上下文拼装猫娘 prompt(内容由主插件 LLM 接口消费)。"""
    ctx = context or {}
    name = ctx.get("neko_name", "喵喵")
    relation = _relation_text(ctx.get("relation", {}), name)
    qinmidu = int(ctx.get("qinmidu", 0))
    player_state = ctx.get("player_state", {})
    event = ctx.get("event", "")
    memory = ctx.get("memory", [])

    prompt = NEKO_PERSONA.format(name=name, relation=relation, qinmidu=qinmidu)
    prompt += "。" + _bond_text(qinmidu) + "。"
    prompt += f"\n\n当前场景：{scene}"
    if player_state:
        prompt += (f"\n主人的状态：境界{player_state.get('realm', '?')}"
                   f"，气血{player_state.get('hp', '?')}，"
                   f"灵石{player_state.get('lingshi', '?')}")
    if memory:
        mem_txt = "；".join(str(m) for m in memory[-5:])
        prompt += f"\n你们的共同经历：{mem_txt}"
    if event:
        prompt += f"\n刚刚发生的事件：{event}"
    prompt += "\n\n请以猫娘口吻简短回应(1-3句)。"
    return prompt
