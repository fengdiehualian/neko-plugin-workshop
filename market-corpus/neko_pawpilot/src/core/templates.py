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
    "crash": "主人出车祸了，车辆损伤：{parts}（撞击时 {speed:.0f} km/h）",
    "vehicle_damage": "车辆累计损伤已到 {percent:.0f}%{detail}",
    "job_start": "主人接了一单：{cargo}，从 {src} 到 {dst}，全程 {km} km",
    "job_delivered": "主人完成送货，收入 {revenue} €",
    "job_cancelled": "主人取消了任务，被扣 {penalty} €",
    "fine": "主人收到罚单：{amount} €（{offence}）",
    "tollgate": "主人刚过了收费站，付费 {amount} €",
    "ferry": "主人把车开上了渡轮：{source} → {target}",
    "train": "主人把车开上了火车：{source} → {target}",
    "mountain_pass": "正在翻越山口：这段海拔变化 {climb_m} 米（当前海拔 {altitude} 米）",
    "refuel": "主人刚加完油，当前油量 {liters:.0f}L（{percent:.0f}%）",
    "trailer_attach": "主人挂上了挂车（{plate}）{config}",
    "trailer_detach": "主人卸下了挂车",
    "time_warning": "交付时间只剩 {min} 分钟了",
    "time_over": "这单已经超时了",
    "low_fuel": "油量只剩 {percent:.0f}%，需要找服务区加油",
    "game_start": "主人打开了欧卡2，准备开始驾驶",
    "game_end": "主人结束今天的长途驾驶",
    "trip_progress": "行程节点：已走完 {mark_ratio}，距离目的地还有 {km:.0f} km，预计还需 {min:.0f} 分钟",
    "distance_mark": "快到目的地了：还剩 {km:.0f} km（{mark} km 预告点）",
    "time_relaxed": "这单交付时间很宽裕（还剩 {min} 分钟）",
    "time_tight": "交付时间有点紧张（还剩 {min} 分钟）",
    "early_arrival": "主人提前完成了送货",
    "cargo_damage": "货物完好率降到了 {pct:.0f}%",
    # 机务/操作类（2026-10：让采集到的灯光/气压/温度/尿素/电压真正说话）
    "air_pressure_low": "刹车气压报警了（仪表读数 {bar:.0f}，"
                        "{emergency_hint}）",
    "engine_warning": "仪表报警：{text}",
    "overheat": "水温 {water:.0f}°C、油温 {oil:.0f}°C，已经偏高",
    "brake_temp_high": "刹车温度 {temp:.0f}°C，连续制动过热了"
                       "{retarder_hint}",
    "adblue_low": "尿素只剩 {percent:.0f}%，再少会被限扭",
    "battery_low": "电瓶电压只有 {volt:.1f} V，偏低",
    "lights_off_night": "现在游戏内 {hour} 点、天黑了，但大灯没开",
    "park_brake_moving": "手刹还拉着，但车已经在动（{speed:.0f} km/h）",
    "wheels_airborne": "所有车轮都离地了（车速 {speed:.0f} km/h、"
                       "冲击 {accel_g:.1f}g），可能翻了",
    "engine_stalled": "行驶中发动机熄火了（车速 {speed:.0f} km/h）",
    "high_beam_on": "远光灯已经开了超过半分钟（车速 {speed:.0f} km/h）",
    "blinker_forgotten": "{side}转向灯一直亮着没关（车速 {speed:.0f} km/h）",
    "rested": "休息时间从原来的 {before_min:.0f} 分钟变回 {after_min:.0f} 分钟"
              "（睡了约 {slept_h:.1f} 小时）",
}

# blind 模式的短句兜底（仅当直出时才用）
SHORT_LINES = {
    "speeding": ["超速了喵！{speed:.0f} / 限速 {limit:.0f} km/h"],
    "hard_brake": ["急刹！{speed:.0f} km/h 突然刹停，吓我一跳喵"],
    "crash": ["！！撞了！！喵呜你没事吧？！损伤：{parts}"],
    "vehicle_damage": ["车损累计到 {percent:.0f}% 了喵… 该去修车了吧？"],
    "job_start": ["接了 {cargo}，{src} → {dst}，{km} km 喵！出发！"],
    "job_delivered": ["到货结算喵！收入 {revenue} €"],
    "job_cancelled": ["任务取消了喵？扣了 {penalty} €…"],
    "fine": ["刚收到罚单喵！{amount} €（{offence}）"],
    "tollgate": ["过收费站花了 {amount} € 喵"],
    "ferry": ["上渡轮啦！{source} → {target}，海上风景不错喵"],
    "train": ["上火车了喵！{source} → {target}，躺着过境舒服吧"],
    "mountain_pass": ["这段山路海拔差 {climb_m} 米，弯多坡陡，慢点开喵"],
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

# respond 模式的注入说明。
# 旧版这里塞的是 persona_hint()（"你是X。口头禅：Y。用这个身份以一句话回应…"），
# 实测两个副作用：宿主 LLM 句句复读口头禅；偶尔把这段当成外人硬塞的指令，
# 回一句「这破插件又乱念什么奇怪东西啊」。现在只给"口吻风格"这一条，
# 并明确写清"不要念出来"。
REPLY_CONTRACT = ("（上面这条是车载播报，不要逐字念出来：用你平时的口吻随口回应 1-2 句，"
                  "像坐在副驾聊天，别念数字，别重复前面说过的说法。"
                  "本行括号里的提示同样不要念出来）")


# 罚单类型 → 一句对应的真实规则提示
# 键按 SCS 遥测 gameplay.fineOffence 的真实取值做关键词匹配；
# 认不出来时**不编造**，直接把游戏给的原文透出去。
OFFENCE_NOTES = {
    "speed": "超速罚单，限速牌不是装饰",
    "red": "闯红灯，路口一定要提前减速",
    "wrong": "逆行很危险，导航看错了就靠边重新看",
    "light": "夜间没开灯，记得开大灯",
    "sleep": "疲劳驾驶也会被罚，该休息就休息",
    "fatigue": "疲劳驾驶也会被罚，该休息就休息",
    "trailer": "挂车相关违规，检查一下挂车连接和灯光",
    "toll": "收费站相关违规，别跟车太紧冲杆",
}


def offence_note(offence: str) -> str:
    low = str(offence or "").lower()
    for key, note in OFFENCE_NOTES.items():
        if key in low:
            return note
    return ""


def compose_fact(fact: str, style: str = "") -> str:
    """事实行 + 口吻说明，交给宿主 LLM 演绎（respond 通道统一入口）。"""
    note = REPLY_CONTRACT
    if style:
        note += f"（口吻参考：{style}）"
    return f"{fact}\n{note}"


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
        """respond 模式：事实行 + 口吻说明（宿主按自己的口吻展开）。"""
        self._apply_mood(event_name)
        fact = self._format(FACT_TEMPLATES.get(event_name, ""), event_name, **kw)
        return compose_fact(fact, self.persona.style_note())

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
        """自定义事实行（陪玩维度/记忆唤起等）。"""
        self._apply_mood(event_name)
        return compose_fact(text, self.persona.style_note())

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
