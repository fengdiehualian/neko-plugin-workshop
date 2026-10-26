"""统一陪伴层: 台词 / 心情 / 闲聊回应 / 局中主动搭话。

**这一层归插件本体, 游戏不参与**(见 docs/rules.md 的「游戏适配插件」约定):
  · 游戏只报「事件键 + 局面描述 + 可选的主人原话」;
  · 提示词、LLM 调用、缓存、模板兜底、心情弧线、静默催prompt, 全部在这里。

三级台词: LRU 缓存 → LLM 生成 → data/config/<game>/emotion.json 模板。
心情用 core/persona.py 的 Mood(五条情绪弧), 不再让各游戏自己存 mood 字段。
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("neko_arcade.companion")

#: 事件键 → 心情(情绪弧由事件驱动, 游戏不用管)
#: ⚠️ 这里必须用 **persona.EMOTION_POOL 的规范 id**(excitement/curiosity/proud/upset/sleepy),
#: 写成 excited/curious 会被 Mood.trigger 静默忽略(心情永远不动, 提示词也取不到标签)。
MOOD_BY_EVENT: Dict[str, str] = {
    "highlight": "excitement", "win": "excitement", "caught_legendary": "excitement",
    "lowlight": "upset", "lose": "upset", "blocked": "upset", "captured": "upset",
    "player_good": "curiosity", "chat": "curiosity", "nudge": "curiosity",
    "hint": "calm",
}
#: 语气档(喂给 LLM 的"你此刻的心情"), 键 = 规范 id
MOOD_DESC = {"excitement": "兴奋", "curiosity": "好奇", "proud": "得意",
             "upset": "委屈", "sleepy": "困", "calm": "平静"}
#: 别名表: 台词模型/配置/历史数据可能写 excited、curious 或中文, 统一归一到规范 id
MOOD_ALIASES: Dict[str, str] = {
    "excited": "excitement", "excitement": "excitement", "兴奋": "excitement",
    "happy": "excitement", "开心": "excitement",
    "curious": "curiosity", "curiosity": "curiosity", "好奇": "curiosity",
    "proud": "proud", "得意": "proud",
    "upset": "upset", "sad": "upset", "委屈": "upset", "难过": "upset",
    "sleepy": "sleepy", "困": "sleepy", "困倦": "sleepy",
    "calm": "calm", "平静": "calm",
}


def mood_id(name: Any) -> str:
    """把各种写法的心情名归一到 persona 的规范 id; 认不出返回空串。"""
    key = str(name or "").strip().lower()
    return MOOD_ALIASES.get(key, "")


def _split_control(text: str) -> tuple:
    """把台词尾部的控制指令 JSON 剥出来, 返回 (干净台词, control)。

    台词模型可以这样控制她的情绪延续:
        哼，这条不算喵！\n{"mood": "proud", "intensity": 0.8}
    剥掉的 JSON 不给用户看, mood/intensity 应用到人格上(后续台词口吻跟着变)。
    """
    raw = str(text or "").strip()
    idx = raw.rfind("{")
    if idx < 0:
        return raw, {}
    tail = raw[idx:].strip()
    if not tail.endswith("}"):
        return raw, {}
    try:
        import json as _json
        obj = _json.loads(tail)
    except Exception:
        return raw, {}
    if not isinstance(obj, dict):
        return raw, {}
    return raw[:idx].strip(), {str(k): v for k, v in obj.items() if isinstance(k, str)}


class Companion:
    """本体唯一的陪伴实现。"""

    def __init__(self, persona: Any = None, llm: Any = None,
                 config_manager: Any = None, cfg: Optional[Dict[str, Any]] = None,
                 rng: Optional[random.Random] = None) -> None:
        self.persona = persona
        self.llm = llm
        self.cfg_mgr = config_manager
        self.cfg = cfg or {}
        self._rng = rng or random.Random()
        self._cache: Dict[str, str] = {}
        self._cache_cap = int(self.cfg.get("line_cache", 48))
        self._last_ts = 0.0

    # ── 台词 ────────────────────────────────
    def templates(self, game_id: str, event: str) -> List[str]:
        """事件模板: 优先读该游戏的 emotion.json(本体已统一加载)。"""
        pool: List[str] = []
        try:
            data = self.cfg_mgr.load(game_id).emotion_templates if self.cfg_mgr else {}
            for key in (event, event.split("_")[0], "chat"):
                got = (data or {}).get(key)
                if isinstance(got, list) and got:
                    pool = [str(x) for x in got]
                    break
        except Exception as exc:
            log.debug("读 %s 模板失败: %s", game_id, exc)
        return pool

    async def line(self, game_id: str, event: str, *, ctx: Optional[Dict[str, Any]] = None,
                   user_text: str = "", situation: str = "", cache: bool = True,
                   style_hint: str = "") -> str:
        """一句台词: 缓存 → LLM → 模板。任何一步失败都退到模板, 不抛异常。"""
        table = {k: str(v) for k, v in (ctx or {}).items() if v is not None}
        pool = self.templates(game_id, event)
        fallback = ""
        if pool:
            fallback = self._rng.choice(pool)
            try:
                fallback = fallback.format(**table)
            except Exception:
                pass
        if not self._llm_ready() or not self.cfg.get("enable_llm_lines", True):
            return fallback
        ck = f"{game_id}|{event}|{self.mood()}|{table.get('bucket', '')}"
        if cache and ck in self._cache:
            return self._cache[ck]
        prompt = self._prompt(event, table, user_text, situation, style_hint)
        try:
            out = await self.llm.call(prompt)
        except Exception as exc:
            log.debug("台词 LLM 失败(%s/%s): %s", game_id, event, exc)
            out = None
        text = self._clean(out)
        if text:
            # 控制通道: 尾部 JSON 剥掉(不给用户看), mood/intensity 应用到人格
            text, control = _split_control(text)
            if control:
                self.apply_control(control)
        if not text:
            return fallback
        if cache:
            if len(self._cache) >= self._cache_cap:
                self._cache.pop(next(iter(self._cache)))
            self._cache[ck] = text
        return text

    def persona_block(self, style_hint: str = "") -> str:
        """把宿主猫娘的人设拼成一段(游戏不参与; 没读到就用配置提示/通用兜底)。"""
        p = self.persona
        bits: List[str] = []
        if p is not None:
            name = getattr(p, "name", "") or ""
            if name:
                bits.append(f"你是「{name}」")
            self_call = getattr(p, "self_call", "")
            if self_call:
                bits.append(f"自称「{self_call}」")
            traits = getattr(p, "traits", None) or []
            if traits:
                bits.append("核心特质：" + "、".join(str(x) for x in traits[:5]))
            desc = getattr(p, "description", "")
            if desc:
                bits.append(f"你常挂嘴边的一句话：「{desc}」")
            habits = getattr(p, "habits", None) or {}
            if isinstance(habits, dict) and habits:
                bits.append("行为特点：" + "；".join(str(x) for x in list(habits)[:4]))
            elif isinstance(habits, list) and habits:
                bits.append("行为特点：" + "；".join(str(x) for x in habits[:4]))
            dislikes = getattr(p, "dislikes", None) or []
            if dislikes:
                bits.append("你讨厌：" + "、".join(str(x) for x in dislikes[:3]))
        hint = (style_hint or self.cfg.get("persona_hint", "") or "").strip()
        if hint:
            bits.append(f"补充：{hint}")
        if not bits:
            bits.append("你是一只嘴硬心软的猫娘，会撒娇也会吐槽")
        return "；".join(bits)

    def apply_control(self, control: Dict[str, Any]) -> None:
        """把台词里的控制指令应用到人格(情绪延续); 未知键静默忽略。

        心情名支持别名(excited/excitement、curious/curiosity、中文…),
        统一归一到 persona 的规范 id; 认不出或应用失败都不抛异常。
        """
        mid = mood_id(control.get("mood"))
        if not mid or mid == "calm" or self.persona is None:
            return
        if not hasattr(self.persona, "feel"):
            return
        try:
            power = float(control.get("intensity", 0.6))
        except (TypeError, ValueError):
            power = 0.6
        try:
            self.persona.feel(mid, max(0.0, min(1.0, power)))
        except Exception as exc:            # 人格实现异常不该打断台词
            log.debug("应用心情控制失败(%s): %s", mid, exc)

    def _prompt(self, event: str, table: Dict[str, str], user_text: str,
                situation: str, style_hint: str) -> str:
        who = getattr(self.persona, "name", "") or "喵喵"
        call = getattr(self.persona, "user_call", "主人")
        parts = [
            f"你在演一只叫「{who}」的猫娘，正在陪「{call}」玩。",
            f"人设（必须贴合，这是主人的猫娘本体）：{self.persona_block(style_hint)}",
            f"称呼对方：{call}（按她的习惯叫，不要叫别的）",
            f"你此刻的心情：{self.mood_label()}",
            f"刚刚发生：{table.get('event_desc') or event}",
        ]
        if situation:
            parts.append(f"当前局面：{situation}")
        if table.get("move"):
            parts.append(f"涉及的一步/一着：{table['move']}")
        if user_text:
            parts.append(f"{call}刚跟你说：「{user_text}」")
            parts.append("先回应他这句话（吐槽/撒娇/嘴硬/搭话都行），不要答非所问、不要只回一个「嗯」。")
        parts.append("要求：1~2 句话，合计不超过 40 个字；不要引号；不要解释规则；"
                     "不要复述数据/坐标清单；可以有语气词。只输出台词本身。")
        return "\n".join(parts)

    @staticmethod
    def _clean(out: Any) -> str:
        if not out:
            return ""
        text = " ".join(x.strip() for x in str(out).splitlines() if x.strip())
        text = text.strip().strip('"“”「」').replace("\n", " ").strip()
        return text if 0 < len(text) <= 80 else ""

    def _llm_ready(self) -> bool:
        llm = self.llm
        if llm is None:
            return False
        return (getattr(llm, "_client", None) is not None
                or getattr(llm, "_host_call", None) is not None)

    async def chat_reply(self, game_id: str, user_text: str, *, situation: str = "",
                         style_hint: str = "") -> str:
        """局中闲聊: 一定带主人原话, 且不缓存(每次都要新鲜)。"""
        return await self.line(game_id, "chat", ctx={"event_desc": "主人在玩的中间跟你说话"},
                               user_text=user_text, situation=situation,
                               cache=False, style_hint=style_hint)

    # ── 心情(走本体现成的 persona.Mood) ──────
    def feel(self, event: str, strength: float = 0.6) -> None:
        """按事件键推动心情(highlight/lose/chat…); 未知事件静默忽略。"""
        mid = mood_id(MOOD_BY_EVENT.get(str(event or "").strip(), ""))
        if not mid or mid == "calm" or self.persona is None:
            return
        if not hasattr(self.persona, "feel"):
            return
        try:
            self.persona.feel(mid, max(0.0, min(1.0, float(strength))))
        except Exception as exc:
            log.debug("心情推动失败(%s/%s): %s", event, mid, exc)

    def mood(self) -> str:
        """当前主心情的**规范 id**(没有情绪时返回 calm)。"""
        try:
            if self.persona is None:
                return "calm"
            return mood_id(self.persona.mood.primary()) or "calm"
        except Exception:
            return "calm"

    def mood_label(self) -> str:
        """当前心情的中文标签(给提示词用)。"""
        return MOOD_DESC.get(self.mood(), "平静")

    # ── 局中主动搭话(策略只在这里) ───────────
    def should_nudge(self, last_active: float, *, nudges: int = 0,
                     now: Optional[float] = None) -> bool:
        """静默阈值/次数上限/节流全在本体: 游戏只报"我在等他动作 + 上次活动时间"。"""
        now = now or time.time()
        if not last_active or now - last_active < float(self.cfg.get("nudge_after", 75.0)):
            return False
        if nudges >= int(self.cfg.get("nudge_max", 3)):
            return False
        if now - self._last_ts < float(self.cfg.get("nudge_gap", 150.0)):
            return False
        self._last_ts = now
        return True
