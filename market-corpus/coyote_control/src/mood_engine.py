"""
心情引擎 - 生成控制参数
"""
import random
from typing import Any, Dict

# AI 可选择的控制模式
CONTROL_MODES = {
    "reward_gentle": {
        "name": "温柔奖励",
        "intensity_range": (8, 18),
        "waveforms": ["BREATH", "BUBBLE"],
        "duration_ms_range": (2000, 4000),
        "channel": "A",
        "description": "轻柔的刺激，用于鼓励和奖励",
    },
    "reward_playful": {
        "name": "活泼奖励",
        "intensity_range": (15, 28),
        "waveforms": ["PULSE", "TIDE", "FROG"],
        "duration_ms_range": (2000, 4000),
        "channel": "both",
        "description": "活泼有趣的刺激，让主人开心",
    },
    "tease_light": {
        "name": "轻度调戏",
        "intensity_range": (20, 32),
        "waveforms": ["CLIMB", "WAVE"],
        "duration_ms_range": (2500, 4000),
        "channel": "both",
        "description": "稍强的刺激，用于调戏",
    },
    "punish_mild": {
        "name": "轻度惩罚",
        "intensity_range": (28, 40),
        "waveforms": ["PULSE", "FROG"],
        "duration_ms_range": (2000, 3500),
        "channel": "both",
        "description": "中等强度惩罚",
    },
    "punish_strong": {
        "name": "强力惩罚",
        "intensity_range": (35, 50),
        "waveforms": ["PULSE", "FROG"],
        "duration_ms_range": (2500, 4000),
        "channel": "both",
        "description": "强烈惩罚，慎用",
    },
}


class MoodEngine:
    """心情引擎 - 生成控制参数"""

    def __init__(self, logger):
        self.logger = logger

    def generate_params(self, mode: str, custom_intensity: int = None, custom_duration_ms: int = None) -> Dict[str, Any]:
        """
        根据模式生成控制参数
        mode: CONTROL_MODES 中的键
        custom_intensity: 自定义强度（可选）
        custom_duration_ms: 自定义持续时间（可选）
        """
        if mode not in CONTROL_MODES:
            self.logger.warning("Invalid mode: %s, fallback to reward_gentle", mode)
            mode = "reward_gentle"

        profile = CONTROL_MODES[mode]
        
        if custom_intensity is not None:
            # 郊狼设备上限 200；0 表示归零，不要再抬到 5
            intensity = max(0, min(200, custom_intensity))
        else:
            intensity_min, intensity_max = profile["intensity_range"]
            intensity = random.randint(intensity_min, intensity_max)

        if custom_duration_ms is not None:
            duration_ms = max(1000, min(10000, custom_duration_ms))
        else:
            duration_min, duration_max = profile["duration_ms_range"]
            duration_ms = random.randint(duration_min, duration_max)

        params = {
            "mode": mode,
            "intensity": intensity,
            "waveform": random.choice(profile["waveforms"]),
            "duration_ms": duration_ms,
            "channel": profile["channel"],
            "description": profile["description"],
        }
        
        self.logger.info("Generated params for mode '%s': intensity=%d, duration=%dms, waveform=%s", 
                        mode, intensity, duration_ms, params["waveform"])
        return params

    def list_modes(self) -> list:
        """列出所有可用模式（供 AI 参考）"""
        return [
            {
                "mode": mode_id,
                "name": profile["name"],
                "description": profile["description"],
                "intensity_range": profile["intensity_range"],
            }
            for mode_id, profile in CONTROL_MODES.items()
        ]
