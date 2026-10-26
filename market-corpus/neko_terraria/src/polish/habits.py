"""个人习惯：性格种子与行为偏好。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

# ── 默认猫娘角色习惯（当主项目人设不可用时） ──
FALLBACK_TRAITS: Dict[str, float] = {
    "talkative": 0.7,
    "brave": 0.6,
    "curious": 0.8,
    "playful": 0.7,
    "loyal": 0.9,
}

# trait → urge 倍率映射（>1 加速说话，<1 减速）
TRAIT_MODIFIER: Dict[str, float] = {
    "talkative": 1.3,
    "quiet": 0.6,
    "curious": 1.15,
    "apathetic": 0.8,
    "playful": 1.1,
    "serious": 0.9,
    "brave": 1.05,
    "timid": 0.85,
    "lazy": 0.7,
    "energetic": 1.2,
    "loyal": 1.0,
    "tsundere": 0.85,
    "dere": 1.2,
    "kuudere": 0.7,
    "yandere": 1.4,
}

# 风格映射：trait → (匹配阈值, 风格描述)
STYLE_MAP: List[Tuple[str, float, str]] = [
    ("talkative", 0.6, "多话吐槽"),
    ("tsundere",  0.5, "傲娇"),
    ("dere",      0.6, "撒娇"),
    ("kuudere",   0.5, "冷淡"),
    ("yandere",   0.5, "病娇"),
    ("playful",   0.6, "活泼闹腾"),
    ("serious",   0.6, "严肃认真"),
    ("curious",   0.6, "好奇探索"),
]


@dataclass
class PersonalHabits:
    """角色的说话习惯。影响交互引擎的 urge 增量倍率 + 说话风格。"""

    seed: str = "neko"
    prefs: Dict[str, float] = field(default_factory=lambda: dict(FALLBACK_TRAITS))
    _urge_modifier: float = 1.0

    # ── 工厂 / 构造 ──────────────────────────────

    @staticmethod
    def from_persona(persona: Optional[Dict[str, Any]]) -> "PersonalHabits":
        """从主项目 persona 构建实例。"""
        return PersonalHabits(prefs=PersonalHabits._build_from_persona(persona))

    @staticmethod
    def _build_from_persona(persona: Optional[Dict[str, Any]]) -> Dict[str, float]:
        """从主项目人设提取 traits 数值（纯函数，不依赖实例状态）。"""
        if not persona:
            return dict(FALLBACK_TRAITS)

        traits = dict(FALLBACK_TRAITS)

        # 从 persona.traits 列表映射
        host_traits: Union[List[str], List[dict]] = persona.get("traits", [])
        for t in host_traits:
            name = t if isinstance(t, str) else str(t.get("name", ""))
            tlower = name.lower()
            matched = False
            for key in TRAIT_MODIFIER:
                if key in tlower:
                    traits[key] = max(traits.get(key, 0.5), 0.6)
                    matched = True
                    break
            if not matched and tlower:
                traits[tlower] = 0.6

        # 从 persona.habits 提取数值
        host_habits: dict = persona.get("habits", {})
        for key, val in host_habits.items():
            try:
                traits[key.lower()] = float(val)
            except (ValueError, TypeError):
                pass

        return traits

    def __post_init__(self) -> None:
        """dataclass 生成后计算 urge 修正因子。"""
        self._urge_modifier = self._calc_urge_modifier()

    # ── urge 计算 ────────────────────────────────

    def _calc_urge_modifier(self) -> float:
        """基于 prefs 计算 urge 总体倍率因子。"""
        modifier = 1.0
        for tname, tval in self.prefs.items():
            if tname in TRAIT_MODIFIER:
                weight = tval * TRAIT_MODIFIER[tname] + (1.0 - tval) * 1.0
                modifier *= weight
        return round(modifier, 4)

    @property
    def urge_modifier(self) -> float:
        """交互引擎每 tick 的 urge 增量乘这个因子。"""
        return self._urge_modifier

    # ── trait 查询 ────────────────────────────────

    def trait(self, name: str, default: float = 0.5) -> float:
        """获取特定 trait 的数值（0~1）。"""
        return self.prefs.get(name.lower(), default)

    def affect_style(self, base_style: str = "") -> str:
        """根据 traits 计算说话风格关键词，用于注入 LLM prompt。"""
        styles = [desc for key, threshold, desc in STYLE_MAP
                  if self.trait(key, 0) > threshold]

        if not styles:
            return base_style or "普通猫娘"
        result = "、".join(styles)
        return f"{base_style}（{result}）" if base_style else result
