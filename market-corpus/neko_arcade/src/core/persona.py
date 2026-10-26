"""猫娘人格：性格、心情、说话风格 + **从宿主配置读猫娘人设**。

游戏不关心人设; 插件负责把宿主的猫娘人格(名字/特质/一句话台词/行为特点/对主人的称呼)
读出来交给台词通道, 这样每句话都是"那只猫娘"的口吻, 而不是通用模板。
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

EMOTION_POOL = ("excitement", "curiosity", "proud", "upset", "sleepy")
DECAY_RATES = {"excitement": 0.06, "curiosity": 0.05, "proud": 0.07, "upset": 0.04, "sleepy": 0.02}


class MoodArc:
    """情绪弧线：事件触发→峰值→衰减→残留。"""

    def __init__(self, name: str, decay_rate: float = 0.05) -> None:
        self.name = name
        self.value = 0.0
        self.peak = 0.0
        self.decay_rate = decay_rate
        self.residual = 0.0

    def trigger(self, intensity: float) -> None:
        self.value = min(1.0, max(self.value, intensity))
        self.peak = max(self.peak, self.value)
        self.residual = max(self.residual, intensity * 0.12)

    def decay(self) -> None:
        if self.value > self.residual:
            self.value -= self.decay_rate * (self.value - self.residual)
            self.value = max(self.residual, self.value)


class Mood:
    """五根情绪弧线。"""

    def __init__(self) -> None:
        self.arcs = {k: MoodArc(k, DECAY_RATES[k]) for k in EMOTION_POOL}

    def trigger(self, emotion: str, intensity: float = 0.5) -> None:
        if emotion in self.arcs:
            self.arcs[emotion].trigger(min(intensity, 1.0))

    def decay_all(self) -> None:
        for a in self.arcs.values():
            a.decay()

    def primary(self) -> str:
        best = max(self.arcs.values(), key=lambda a: a.value)
        return best.name if best.value > 0.15 else "calm"

    def style(self) -> Dict[str, Any]:
        best = max(self.arcs.values(), key=lambda a: a.value)
        v = best.value
        if best.name == "excitement" and v > 0.5:
            return {"energy": "high", "verbosity": "多话", "exclaim": 3, "pace": "快"}
        if best.name == "proud" and v > 0.5:
            return {"energy": "high", "verbosity": "炫耀", "exclaim": 2, "pace": "中"}
        if best.name == "upset" and v > 0.5:
            return {"energy": "low", "verbosity": "委屈", "exclaim": 1, "pace": "慢"}
        if best.name == "sleepy" and v > 0.4:
            return {"energy": "low", "verbosity": "极简", "exclaim": 0, "pace": "极慢"}
        if v > 0.2:
            return {"energy": "medium", "verbosity": "正常", "exclaim": 1, "pace": "中"}
        return {"energy": "calm", "verbosity": "简洁", "exclaim": 0, "pace": "中"}

    def urge_bonus(self) -> float:
        bonus = 1.0 + self.arcs["excitement"].value * 0.35 + self.arcs["curiosity"].value * 0.25
        bonus -= self.arcs["upset"].value * 0.2 + self.arcs["sleepy"].value * 0.3
        return max(0.4, bonus)

    def snapshot(self) -> Dict[str, float]:
        return {k: round(v.value, 2) for k, v in self.arcs.items()}


def host_persona_candidates() -> List[Path]:
    """可能存放宿主角色配置的文件(按可信度)。打包版在 exe 旁边的 config/ 里。"""
    out: List[Path] = []

    def add(path: Any) -> None:
        if not path:
            return
        p = Path(str(path))
        if p not in out:
            out.append(p)

    env_file = os.environ.get("NEKO_CHARACTERS_FILE", "").strip()
    if env_file:
        add(env_file)
    roots: List[Path] = []
    env_dir = os.environ.get("NEKO_APP_DIR", "").strip()
    if env_dir:
        roots.append(Path(env_dir))
    try:                                    # 打包版: static/config 就在 exe 旁边
        exe = Path(sys.executable).resolve().parent
        roots += [exe, exe.parent, exe.parent / "resources" / "bin"]
    except Exception:
        pass
    for name in ("main_server", "main_logic", "launcher", "config"):
        mod = sys.modules.get(name)
        file = getattr(mod, "__file__", None) if mod is not None else None
        if file:
            base = Path(str(file)).resolve().parent
            roots += [base, base.parent, base.parent.parent]
    try:
        roots.append(Path.cwd())
    except Exception:
        pass
    for r in roots:
        for rel in ("config/characters/zh-CN.json", "config/characters/zh_CN.json",
                    "config/characters.json"):
            add(r / rel)
    return out


#: 缓存: (文件签名 + 指定角色) → (人设, 时间)。**猫娘是会被换的**, 所以这里
#: 带 mtime/size 签名 + TTL: 文件一改(或宿主换了"当前猫娘")就重新读, 平时不重复解析。
_PERSONA_CACHE: Dict[str, Any] = {"sig": None, "ts": 0.0, "data": None}


def _persona_signature(cfg: Optional[Dict[str, Any]]) -> Any:
    want = str((cfg or {}).get("character_name")
               or os.environ.get("NEKO_CHARACTER") or "").strip()
    for path in host_persona_candidates():
        try:
            st = path.stat()
            return (str(path), int(st.st_mtime), st.st_size, want)
        except Exception:
            continue
    return None


def load_host_persona(cfg: Optional[Dict[str, Any]] = None, *,
                      ttl: float = 5.0, force: bool = False) -> Optional[Dict[str, Any]]:
    """读宿主的猫娘人设(热更新)。找不到就返回 None(调用方回退通用人设)。"""
    sig = _persona_signature(cfg)
    now = time.time()
    cached = _PERSONA_CACHE.get("data")
    if (not force and sig is not None and sig == _PERSONA_CACHE.get("sig")
            and cached is not None and now - float(_PERSONA_CACHE.get("ts") or 0) < ttl):
        return dict(cached)
    data: Any = None
    for path in host_persona_candidates():
        try:
            if path.is_file():
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                break
        except Exception:
            continue
    if not isinstance(data, dict):
        return None
    master = data.get("主人") if isinstance(data.get("主人"), dict) else {}
    user_call = str((master or {}).get("昵称") or "").strip() or "主人"
    want = str((cfg or {}).get("character_name") or os.environ.get("NEKO_CHARACTER") or "").strip()
    current = str(data.get("当前猫娘") or "").strip()
    cats = data.get("猫娘")
    persona: Any = None
    if isinstance(cats, dict):
        persona = cats.get(want) or cats.get(current) or cats.get("default") \
            or next((v for v in cats.values() if isinstance(v, dict)), None)
    elif isinstance(cats, list) and cats:
        persona = next((c for c in cats if isinstance(c, dict)
                        and str(c.get("昵称") or c.get("name") or "") == want), cats[0])
    if not isinstance(persona, dict):
        return None
    out = {
        "name": str(persona.get("昵称") or persona.get("name") or current or "喵喵"),
        "traits": persona.get("核心特质") or persona.get("traits") or [],
        "description": str(persona.get("一句话台词") or persona.get("description") or ""),
        "habits": persona.get("行为特点") or persona.get("habits") or {},
        "dislikes": persona.get("厌恶") or [],
        "self_call": str(persona.get("自称") or ""),
        "user_call": user_call,
    }
    _PERSONA_CACHE.update({"sig": sig, "ts": now, "data": dict(out)})
    return out


def apply_persona(target: "Persona", data: Optional[Dict[str, Any]]) -> bool:
    """把(可能换过的)人设就地写到 Persona 上, 返回是否发生变化。"""
    if not isinstance(data, dict) or target is None:
        return False
    changed = False
    for attr, key in (("name", "name"), ("traits", "traits"), ("user_call", "user_call"),
                      ("description", "description"), ("habits", "habits"),
                      ("dislikes", "dislikes"), ("self_call", "self_call")):
        value = data.get(key)
        if value in (None, "", [], {}):
            continue
        if getattr(target, attr, None) != value:
            setattr(target, attr, value)
            changed = True
    return changed


class Persona:
    """猫娘人格本体。"""

    def __init__(self, host_persona: Optional[Dict[str, Any]] = None) -> None:
        self.name = (host_persona or {}).get("name", "喵喵")
        self.traits = (host_persona or {}).get("traits", [])
        self.user_call = (host_persona or {}).get("user_call", "主人")
        #: 宿主人格的更多细节(给台词通道用, 游戏不碰)
        self.description = (host_persona or {}).get("description", "")
        self.habits = (host_persona or {}).get("habits", {}) or {}
        self.dislikes = (host_persona or {}).get("dislikes", []) or []
        self.self_call = (host_persona or {}).get("self_call", "")
        self.mood = Mood()
        self._rng = random.Random()

    def feel(self, emotion: str, intensity: float = 0.5) -> None:
        self.mood.trigger(emotion, intensity)

    def on_event(self, kind: str) -> None:
        if kind == "highlight":
            self.mood.trigger("excitement", 0.85)
            self.mood.trigger("proud", 0.6)
        elif kind == "lowlight":
            self.mood.trigger("upset", 0.7)
        elif kind == "nice":
            self.mood.trigger("excitement", 0.35)
            self.mood.trigger("curiosity", 0.25)
        elif kind == "boring":
            self.mood.trigger("sleepy", 0.3)
        elif kind == "chat" or kind == "invite":
            self.mood.trigger("curiosity", 0.2)
            self.mood.trigger("excitement", 0.5)

    def polish(self, text: str, style: Optional[Dict[str, Any]] = None) -> str:
        """拟人化修饰：结巴、语气词。"""
        s = style or self.mood.style()
        if self._rng.random() < 0.06 + (0.08 if s.get("energy") == "high" else 0.0):
            if len(text) > 2:
                i = self._rng.randint(0, 1)
                text = text[:i] + text[i] + "、" + text[i:]
        if self._rng.random() < 0.35 and not text.endswith(("！", "?", "？")):
            if not text.endswith(("喵", "呢", "哦", "啦", "呀")):
                text += self._rng.choice(["喵", "呢", "啦", "呀"])
        return text

    def snapshot(self) -> Dict[str, Any]:
        s = self.mood.style()
        return {"mood": self.mood.primary(), "emotions": self.mood.snapshot(),
                "style": s, "name": self.name, "user_call": self.user_call}