"""瑕疵注入：微小抖动、不完美停顿、回复文本结巴/手滑/忘词。

v2.1: 新增 jitter_text() 对 LLM 回复文本做不完美后处理——
模拟真实玩家的打字节奏：偶尔打错→重打、结巴、多打语气词。
"""

import random
from dataclasses import dataclass

# ── jitter_text 效果常量（无实例状态依赖的方法提取为模块层） ──

STUTTER_CHANCE: float = 0.10
FILLER_CHANCE: float = 0.15
TYPO_CHANCE: float = 0.05
FORGET_CHANCE: float = 0.08

FILLERS = ["嗯…", "唔…", "啊…", "诶…"]
FORGET_NOTES = ["*挠头*", "*歪头*", "*想了想*", "*小声嘀咕*"]


@dataclass
class ImperfectionInjector:
    """不完美效果注入器。

    intensity: 不完美强度 0~1，越高越"不完美"。
    pause_chance / jitter_chance: 由 intensity 派生。
    """

    intensity: float = 0.5
    pause_chance: float = 0.0
    jitter_chance: float = 0.0

    def __post_init__(self) -> None:
        self.intensity = max(0.0, min(1.0, self.intensity))
        self.pause_chance = 0.12 * (1.0 + self.intensity)
        self.jitter_chance = 0.08 * (1.0 + self.intensity)

    # ── 节奏层（交互引擎 tick 用） ──

    def maybe_jitter(self) -> bool:
        """是否跳过本 tick（犹豫/发呆）。"""
        return random.random() < self.jitter_chance

    def maybe_pause(self) -> bool:
        """是否需要额外停顿（说话前的小延迟）。"""
        return random.random() < self.pause_chance

    # ── 文本后处理层（LLM 回复加工） ──

    @staticmethod
    def jitter_text(text: str, intensity: float = 0.5) -> str:
        """对 LLM 回复文本做不完美后处理。"""
        
        if intensity < 0.3 or not text or len(text) < 3:
            return text

        effects: list[str] = []

        if intensity > 0.4 and random.random() < STUTTER_CHANCE * intensity:
            effects.append("stutter")

        if random.random() < FILLER_CHANCE * intensity:
            effects.append("filler")

        if intensity > 0.6 and random.random() < TYPO_CHANCE * intensity:
            effects.append("typo")

        if intensity > 0.3 and random.random() < FORGET_CHANCE * intensity:
            effects.append("forget")

        if not effects:
            return text

        return ImperfectionInjector._apply_effects(text, effects)

    @staticmethod
    def _apply_effects(text: str, effects: list[str]) -> str:
        """依次应用文本效果。"""
        result = text
        for effect in effects:
            if effect == "stutter":
                if len(result) > 1 and result[0] not in ("。", "！", "，", "…", "~"):
                    result = "…" + result

            elif effect == "filler":
                pick = random.choice(FILLERS)
                pos = int(len(result) * (0.1 + random.random() * 0.2))
                if pos < len(result) and result[pos] not in ("。", "！", "，"):
                    result = result[:pos] + pick + result[pos:]

            elif effect == "typo":
                if not result.endswith("~"):
                    result = result.rstrip("。！~") + "~"

            elif effect == "forget":
                note = random.choice(FORGET_NOTES)
                has_punct = result[-1] in ("。", "！", "？", "~")
                result = result + (" " if has_punct else "") + note

        return result

    # ── 停顿 ────────────────────────────────────

    def sleep_ms(self, base_ms: int = 0) -> float:
        """返回建议的停顿 ms 数。用在说话前等待。"""
        if random.random() < self.pause_chance:
            extra = random.uniform(0.2, 0.6) * self.intensity * base_ms
            return float(base_ms) + extra
        return float(base_ms)
