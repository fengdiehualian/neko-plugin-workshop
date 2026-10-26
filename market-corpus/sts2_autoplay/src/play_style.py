"""游玩模式（"认真 / 捣蛋 / 笨蛋"）。

插件现在是唯一的决策者，所以"玩得不认真"这件事由插件自己实现：规划器照常算出最优操作，
这一层再按档位概率把它换成**次优**或**最差**的**合法**选择。

三条硬约束：

* ``serious``（默认）**零影响**——扰动层在 serious 下直接原样返回规划器的结果，连候选排序都不算。
* 换出来的操作**永远合法**：候选只能来自规划器给出的合法下标集合，且要能通过
  ``action_engine`` 校验；拿不到合法替代就放弃扰动、退回最优。
* 概率与选择都是**纯函数 + 可注入随机源**（随机数由调用方掷好再传进来），所以可以确定性地测试。

本模块不 import 任何游戏侧模块，保持可独立测试。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

PLAY_STYLE_SERIOUS = "serious"
PLAY_STYLE_MISCHIEVOUS = "mischievous"
PLAY_STYLE_SILLY = "silly"

PLAY_STYLES: tuple[str, ...] = (PLAY_STYLE_SERIOUS, PLAY_STYLE_MISCHIEVOUS, PLAY_STYLE_SILLY)

#: 每个档位"把最优换成别的"的概率。serious 恒为 0，这也是默认档必须是它的原因。
PERTURBATION_PROBABILITY: dict[str, float] = {
    PLAY_STYLE_SERIOUS: 0.0,
    PLAY_STYLE_MISCHIEVOUS: 0.2,
    PLAY_STYLE_SILLY: 0.6,
}

# NEKO 会把玩家原话解析成 style 参数，这里也接受中文说法，便于直接口头切换。
_ALIASES: dict[str, str] = {
    # 认真（默认）
    "serious": PLAY_STYLE_SERIOUS,
    "normal": PLAY_STYLE_SERIOUS,
    "standard": PLAY_STYLE_SERIOUS,
    "认真": PLAY_STYLE_SERIOUS,
    "认真打": PLAY_STYLE_SERIOUS,
    "好好打": PLAY_STYLE_SERIOUS,
    "我要通关": PLAY_STYLE_SERIOUS,
    "正常": PLAY_STYLE_SERIOUS,
    "正经": PLAY_STYLE_SERIOUS,
    # 捣蛋
    "mischievous": PLAY_STYLE_MISCHIEVOUS,
    "mischief": PLAY_STYLE_MISCHIEVOUS,
    "捣蛋": PLAY_STYLE_MISCHIEVOUS,
    "调皮": PLAY_STYLE_MISCHIEVOUS,
    "闹着玩": PLAY_STYLE_MISCHIEVOUS,
    "别太认真": PLAY_STYLE_MISCHIEVOUS,
    "恶作剧": PLAY_STYLE_MISCHIEVOUS,
    # 笨蛋
    "silly": PLAY_STYLE_SILLY,
    "笨蛋": PLAY_STYLE_SILLY,
    "你傻啦": PLAY_STYLE_SILLY,
    "随便打打": PLAY_STYLE_SILLY,
    "犯傻": PLAY_STYLE_SILLY,
    "傻瓜": PLAY_STYLE_SILLY,
    "犯蠢": PLAY_STYLE_SILLY,
}


def normalize_play_style(value: Any) -> str:
    """把配置/语音里的说法归一成三档之一。

    **未知取值一律退化为 ``serious``**：配置写错不能让它莫名其妙地变傻。
    """
    text = str(value or "").strip().lower().replace(" ", "_")
    return _ALIASES.get(text, PLAY_STYLE_SERIOUS)


def perturbation_probability(style: Any) -> float:
    """该档位"这一次要不要扰动"的概率。"""
    return PERTURBATION_PROBABILITY.get(normalize_play_style(style), 0.0)


T = TypeVar("T")


def choose_candidate(ordered: Sequence[T], *, style: Any, roll: float) -> tuple[T | None, str]:
    """从"最优在前"的候选列表里按档位挑一个，返回 ``(候选, 原因)``。

    ``roll`` 是调用方掷好的 [0, 1) 随机数，传进来而不是在这里掷，是为了让整个选择过程可确定性测试。

    * serious → 第 0 个（永远）
    * 掷出的 roll ≥ 该档概率 → 保持第 0 个
    * mischievous → 第 1 个（没有就退回第 0 个）
    * silly → 最后一个（没有就退回第 0 个）

    候选列表为空时返回 ``(None, "no_candidates")``，调用方据此放弃扰动。
    """
    items = list(ordered)
    if not items:
        return None, "no_candidates"

    normalized = normalize_play_style(style)
    if normalized == PLAY_STYLE_SERIOUS:
        return items[0], "serious"
    if len(items) == 1:
        return items[0], "no_alternative"
    if roll >= perturbation_probability(normalized):
        return items[0], "kept_best"
    if normalized == PLAY_STYLE_SILLY:
        return items[-1], "silly_worst"
    return items[1], "mischievous_second"


__all__ = [
    "PLAY_STYLE_SERIOUS",
    "PLAY_STYLE_MISCHIEVOUS",
    "PLAY_STYLE_SILLY",
    "PLAY_STYLES",
    "PERTURBATION_PROBABILITY",
    "normalize_play_style",
    "perturbation_probability",
    "choose_candidate",
]
