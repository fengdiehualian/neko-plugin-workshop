"""情绪层：驾驶场景的情绪弧线。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, Optional

MOOD_DEFAULTS = {
    "mood_pool": ["excitement", "worry", "sleepy", "sympathy", "proud", "calm"],
    "decay_rates": {
        "excitement": 0.06, "worry": 0.04, "sleepy": 0.02,
        "sympathy": 0.05, "proud": 0.07, "calm": 0.03,
    },
}


def _load_mood_config() -> Dict[str, Any]:
    p = Path(__file__).resolve().parent.parent / "data" / "config" / "mood.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(MOOD_DEFAULTS)


class MoodArc:
    """单条情绪弧线：触发→峰值→衰减→残留。"""

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
    """多情绪弧线：兴奋/担忧/困倦/心疼/得意。"""

    def __init__(self) -> None:
        data = _load_mood_config()
        pool = data.get("mood_pool", MOOD_DEFAULTS["mood_pool"])
        rates = data.get("decay_rates", MOOD_DEFAULTS["decay_rates"])
        self.arcs = {k: MoodArc(k, rates.get(k, 0.05)) for k in pool}

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
        """当前主导情绪 → 说话风格。"""
        best = max(self.arcs.values(), key=lambda a: a.value)
        v = best.value
        if best.name == "excitement" and v > 0.5:
            return {"energy": "high", "verbosity": "多话", "exclaim": 3}
        if best.name == "worry" and v > 0.5:
            return {"energy": "medium", "verbosity": "碎碎念", "exclaim": 1}
        if best.name == "sleepy" and v > 0.4:
            return {"energy": "low", "verbosity": "极简", "exclaim": 0}
        if best.name == "sympathy" and v > 0.5:
            return {"energy": "low", "verbosity": "温柔", "exclaim": 1}
        if best.name == "proud" and v > 0.5:
            return {"energy": "high", "verbosity": "炫耀", "exclaim": 2}
        if v > 0.2:
            return {"energy": "medium", "verbosity": "正常", "exclaim": 1}
        return {"energy": "calm", "verbosity": "简洁", "exclaim": 0}

    def snapshot(self) -> Dict[str, float]:
        return {k: round(v.value, 2) for k, v in self.arcs.items()}


def _load_host_persona() -> Optional[Dict[str, Any]]:
    """从宿主 config/characters.json 导入当前猫娘人设。"""
    try:
        import json as _json
        import os as _os
        candidates = []
        # 1) 显式环境变量（开发/测试覆盖）
        env = _os.environ.get("NEKO_HOST_DIR", "")
        if env:
            candidates.append(_os.path.join(env, "config", "characters.json"))
        # 2) 宿主 AppData（实机安装路径）
        appdata = _os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(
                _os.path.join(appdata, "N.E.K.O", "config", "characters.json"))
        local = _os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.append(
                _os.path.join(local, "N.E.K.O", "config", "characters.json"))
        # 3) 相对插件根逐级向上（arcade 同款，装进宿主后生效）
        fdir = _os.path.dirname(_os.path.abspath(__file__))
        host_root = fdir
        for _ in range(8):
            candidates += [
                _os.path.join(host_root, "config", "characters.json"),
                _os.path.join(host_root, "config", "characters", "zh-CN.json"),
                _os.path.join(host_root, "config", "characters", "zh_CN.json"),
            ]
            host_root = _os.path.dirname(host_root)
        cfg_path = next((p for p in candidates if _os.path.exists(p)), None)
        if not cfg_path:
            return None
        with open(cfg_path, encoding="utf-8") as f:
            characters = _json.load(f)
        if not isinstance(characters, dict):
            return None
        master = characters.get("主人", {}) or {}
        user_call = (master.get("昵称") or "").strip() or "主人"
        cats = characters.get("猫娘")
        if not isinstance(cats, dict):
            return None
        persona = (cats.get("default") or next(iter(cats.values()), None))
        if not persona:
            return None
        return {
            "traits": persona.get("核心特质") or persona.get("traits") or [],
            "description": persona.get("一句话台词") or persona.get("description") or "",
            "habits": persona.get("行为特点") or persona.get("habits") or {},
            "name": persona.get("名称") or persona.get("name") or "喵喵",
            "user_call": user_call,
        }
    except Exception:
        return None


def _load_voice_styles() -> Dict[str, Dict[str, Any]]:
    """读口吻风格配置（voice_styles.json）。"""
    try:
        import json as _json
        p = Path(__file__).resolve().parent.parent / "data" / "config" / "voice_styles.json"
        data = _json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class Persona:
    """猫娘人格：宿主导入人设 + 多口吻融合 + 情绪驱动。"""

    def __init__(self, host_persona: Any = None, voice_styles=None) -> None:
        hp = getattr(host_persona, "snapshot", None)
        data = hp() if callable(hp) else (host_persona or {})
        if not data:
            data = _load_host_persona() or {}
        self.name = data.get("name", "喵喵")
        self.user_call = data.get("user_call", "主人")
        self.traits: list = data.get("traits", [])
        self.description = data.get("description", "")
        self.habits: dict = data.get("habits", {})
        self._voice_styles = _load_voice_styles()
        # 支持多语气：voice_styles 为列表（如 ["tsundere","chatty"]）
        self.voice_styles = list(voice_styles) if voice_styles else ["default"]
        self.voice_styles = [s for s in self.voice_styles if s in self._voice_styles] \
            or ["default"]
        self.mood = Mood()
        self._rng = random.Random()

    @property
    def voice_style(self) -> str:
        """兼容旧字段：返回主语气（第一个）。"""
        return self.voice_styles[0]

    def set_voice_style(self, style: str) -> bool:
        """切换口吻风格（旧接口：单语气）。"""
        if style not in self._voice_styles:
            return False
        self.voice_styles = [style]
        return True

    def set_voice_styles(self, styles) -> bool:
        """多语气融合：接受列表，无效项过滤，至少保留一个。"""
        valid = [s for s in (styles or []) if s in self._voice_styles]
        if not valid:
            return False
        self.voice_styles = valid
        return True

    @property
    def talk_interval(self) -> float:
        """闲聊间隔（秒）：多语气取最小值（最活跃的语气主导频率）。"""
        vals = [float(self._voice_styles.get(s, {}).get("talk_interval", 900))
                for s in self.voice_styles]
        return min(vals) if vals else 900.0

    @property
    def strict_mode(self) -> bool:
        """严厉督导模式：任一语气开启即生效。"""
        return any(bool(self._voice_styles.get(s, {}).get("strict_mode", False))
                   for s in self.voice_styles)

    def feel(self, emotion: str, intensity: float = 0.5) -> None:
        self.mood.trigger(emotion, intensity)

    def on_event(self, event_name: str, mood_map: Dict[str, Any]) -> None:
        """事件 → 情绪触发（映射来自 data/config/mood.json）。"""
        for emo, intensity in (mood_map or {}).items():
            self.feel(emo, intensity)

    def polish(self, text: str) -> str:
        """拟人化修饰：偶尔结巴、补语气词。"""
        style = self.mood.style()
        if self._rng.random() < 0.06 + (0.08 if style.get("energy") == "high" else 0.0):
            if len(text) > 2:
                i = self._rng.randint(0, 1)
                text = text[:i] + text[i] + "、" + text[i:]
        if self._rng.random() < 0.35 and not text.endswith(("！", "?", "？")):
            if not text.endswith(("喵", "呢", "哦", "啦", "呀")):
                text += self._rng.choice(["喵", "呢", "啦", "呀"])
        return text

    def persona_hint(self) -> str:
        """人设提示：主人设 + 多口吻融合，注入 LLM 让宿主按此演绎。"""
        parts = []
        if self.name:
            parts.append(f"你是{self.name}")
        if self.traits:
            parts.append("、".join(self.traits))
        if self.description:
            parts.append(f"口头禅：{self.description}")
        base = "，".join(parts) if parts else "你是猫娘"
        # 多语气 prompt 融合
        prompts = [self._voice_styles.get(s, {}).get("prompt", "")
                   for s in self.voice_styles]
        prompts = [p for p in prompts if p]
        style_prompt = " ".join(prompts) if prompts else ""
        return (f"{base}。称呼{self.user_call}为「{self.user_call}」"
                f"（当前情绪：{self.mood.style().get('verbosity', '正常')}）。"
                f"{style_prompt} 用这个身份以一句话回应驾驶情况，30字内，自然带语气词。")

    def snapshot(self) -> Dict[str, Any]:
        labels = [self._voice_styles.get(s, {}).get("label", s)
                  for s in self.voice_styles]
        return {"name": self.name, "user_call": self.user_call,
                "traits": self.traits, "description": self.description,
                "voice_style": self.voice_styles[0],
                "voice_label": labels[0],
                "voice_styles": self.voice_styles,
                "voice_labels": labels,
                "mood": self.mood.primary(), "emotions": self.mood.snapshot(),
                "style": self.mood.style()}
