"""永远的陪伴 —— 连续心情（valence/arousal）数学：惰性衰减、冲量叠加、语气信号积分。

纯函数模块：直接操作 state.py 的 _MoodState，不持有宿主上下文、不做 IO；
主类 ForeverCompanionPlugin 上的同名方法是薄委托（签名带 shard，取 shard.mood 转调这里，
并从 [mood].arousal_baseline 读静息基线传入——纯函数层不读配置，基线默认 0.0
即旧语义"回归到完全平静"）。

arousal 静息基线语义（0.6.5 起）：衰减目标从 0 改为 arousal_baseline（默认
0.35，配置层给出）——arousal = baseline + (arousal - baseline) × exp(-Δt/τ)，
高于基线回落、低于基线回升（homeostasis）；从未喂过信号时当前值即 (0.0, 基线)，
安装就显示静息水平。valence 完全不变（0 基线、向 0 衰减）。

猴补丁兼容性（硬约束，同 state.py）：必须 ``import time`` 后调 ``time.time()``——
测试 monkeypatch 改的是 stdlib time 模块对象的属性；不得 ``from time import time``。
"""

from __future__ import annotations

import math
import time

from .state import (
    _AFFECT_AROUSAL_TAU_SEC,
    _AFFECT_VALENCE_TAU_SEC,
    _TONE_AFFECT_AROUSAL_STEP,
    _TONE_AFFECT_DIRECTIONS,
    _TONE_AFFECT_VALENCE_STEP,
    _MoodState,
)

# 动作 → 连续心情冲量 (Δvalence, Δarousal)：动作生效瞬间推一次二维心情，
# 之后靠惰性衰减自然回落（arousal τ≈30 分钟先平复、valence τ≈4 小时慢释怀）。
# 动作到期不加反向冲量——余波自己散去才像真的情绪；
# rising_tide 不在表里（不落动作状态），由工具单独给缓解冲量
_MOOD_AFFECT_IMPULSES = {
    "ebb_tide": (-0.45, 0.25),
    "sea_fog": (-0.35, 0.10),
    "shallow_reef": (-0.20, -0.05),
    "storm_surge": (-0.55, 0.60),
    "seek_harbor": (-0.30, 0.35),
    "ripple": (0.15, 0.20),
    "warm_current": (0.35, 0.15),
    "spring_tide": (0.55, 0.45),
}


def _current_affect(
    mood: _MoodState, now: float | None = None, arousal_baseline: float = 0.0
) -> tuple[float, float]:
    """连续心情的当前值：把存的 (valence, arousal) 按 affect_updated_at 惰性衰减到此刻。

    与 is_active 同一套惰性语义——不回调度器（插件无常驻事件循环），
    只在读取时按 exp(-Δt/τ) 折算；写入（动作冲量/语气信号）时才把折算值落盘。
    arousal 的衰减目标是静息基线：高于基线回落、低于基线回升（homeostasis）；
    从未喂过信号（affect_updated_at <= 0）时返回 (0.0, 基线)——安装即显示静息水平。
    """
    baseline = max(0.0, min(1.0, arousal_baseline))  # 防御性 clamp：衰减目标必须在域内
    if mood.affect_updated_at <= 0:
        return 0.0, baseline
    current = now if now is not None else time.time()
    dt = max(0.0, current - mood.affect_updated_at)
    valence = mood.valence * math.exp(-dt / _AFFECT_VALENCE_TAU_SEC)
    arousal = baseline + (mood.arousal - baseline) * math.exp(-dt / _AFFECT_AROUSAL_TAU_SEC)
    return valence, arousal


def _apply_affect_impulse(
    mood: _MoodState, dv: float, da: float, now: float | None = None, arousal_baseline: float = 0.0
) -> None:
    """先惰性衰减到当前值，再叠加冲量并 clamp 到域内（valence [-1,1]、arousal [0,1]）。"""
    current = now if now is not None else time.time()
    valence, arousal = _current_affect(mood, now=current, arousal_baseline=arousal_baseline)
    mood.valence = max(-1.0, min(1.0, valence + dv))
    mood.arousal = max(0.0, min(1.0, arousal + da))
    mood.affect_updated_at = current


def _feed_tone_affect(
    mood: _MoodState, label: str, confidence: float, now: float | None = None,
    arousal_baseline: float = 0.0, weight: float = 1.0,
) -> None:
    """语气信号积分进连续心情（筛选/校正两种模式都喂）。

    步长 = confidence × weight × 上限（valence ±0.10 / arousal ±0.12）——单轮限幅，
    借"单事件最多移动一格"的思路防一句话把心情打满；neutral 微拉一格：
    valence 向 0 拉，arousal 向静息基线拉（双向，不越过目标）。
    weight 是来源权重：她的回复 1.0，用户消息 0.25（互动氛围同向传导、占比更低）。
    """
    conf = max(0.0, min(1.0, confidence))
    w = max(0.0, min(1.0, weight))
    if conf <= 0.0 or w <= 0.0:
        return
    current = now if now is not None else time.time()
    baseline = max(0.0, min(1.0, arousal_baseline))
    valence, arousal = _current_affect(mood, now=current, arousal_baseline=baseline)
    dv_step = conf * w * _TONE_AFFECT_VALENCE_STEP
    da_step = conf * w * _TONE_AFFECT_AROUSAL_STEP
    if label == "neutral":
        # 各向自己的目标微拉：最多移动一格，不越过（valence → 0，arousal → 基线）
        dv = -math.copysign(min(abs(valence), dv_step), valence) if valence else 0.0
        gap = arousal - baseline
        da = -math.copysign(min(abs(gap), da_step), gap) if gap else 0.0
    else:
        direction = _TONE_AFFECT_DIRECTIONS.get(label)
        if direction is None:
            return  # 未知 label 不动心情
        dv, da = direction[0] * dv_step, direction[1] * da_step
    mood.valence = max(-1.0, min(1.0, valence + dv))
    mood.arousal = max(0.0, min(1.0, arousal + da))
    mood.affect_updated_at = current
