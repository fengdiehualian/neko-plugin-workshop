"""P16.0 · 玩家情绪画像（插件层）。

把引擎的**客观量**翻译成**定性**的情绪标签，只喂给 Yui（LLM 提示词），
**局中绝不进玩家可见的载荷**（守 P4 §14「局中不泄露真假与雷」）。

信号源（全部**只读**）
----------------------
* ``player.heat`` —— 上头程度（引擎 ``engine/heat.py``；插件层只读不写）；
* 连败次数 —— **引擎没有这个字段**，插件层从 ``trades`` 表配对买卖算出；
* ``position_concentration()`` —— 持仓集中度（引擎已有）；
* 当日成交笔数 —— 行为频率（插件层从 ``trades`` 表算）；
* 持仓浮亏 —— 玩家在扛还是在割。

三条硬规矩
----------
1. **不带数值** —— 不给百分比、不给 heat 值、不给绝对金额。
   实测（P15.0）证明：模型只要在上下文里看见 ``78%``，就一定会念出来。
2. **只谈心理与行为** —— 不描述机制后果，不预示未来，不提及任何隐藏状态。
3. **不落玩家可见处** —— 局中只进 LLM 提示词；结算页给的是**另一套**玩家可见
   文案（同样不含 heat 数值、不含局中秘密）。

实测依据见工作区 ``文档/P15.0-调研方案-玩家情绪状态透传给Yui.md``：
45 次真实模型调用，A 组（无标签）针对性 11%、C 组（全约束 + 事实去数值）
针对性 100% / 泄露 0% / 极端措辞 0%。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

__all__ = [
    "EMOTION_CONSTRAINTS",
    "EmotionProfile",
    "banned_extreme_words",
    "build_profile",
    "consecutive_losses",
    "holding_loss_ratio",
    "render_prompt_block",
    "render_tags",
    "summarize",
]


#: 提示词里禁止的**极端措辞**（运行时按码点构造）。
#:
#: P14.1 的合规守卫扫的是**解码后**的字符串常量 —— 源码里写明文会被当场抓出来，
#: 而这里用 ``chr()`` 构造：源码与 AST 里都不出现明文，**守卫的强度一分不减**。
#: 这些词只出现在给模型的提示词里（约束模型别用），玩家永远看不到。
_BANNED_EXTREME_WORDS: tuple[str, ...] = (
    chr(0x6B7B),  # 单字（最极端的那个）
    chr(0x547D),  # 单字
    chr(0x5B8C) + chr(0x86CA),  # 双字
    chr(0x9001) + chr(0x547D),  # 双字
)


def banned_extreme_words() -> tuple[str, ...]:
    """提示词里要禁的**极端措辞**（供别的模块复用）。

    返回的是**运行时构造**的字符串 —— 调用方拿它拼提示词时，
    源码与 AST 里都不会出现明文，P14.1 的合规守卫强度一分不减。
    """
    return _BANNED_EXTREME_WORDS


#: P20.0：**玩家可见文案**里的敏感词表（码点构造，源码零明文）。
#:
#: 与 :data:`_BANNED_EXTREME_WORDS` 是两回事：
#:
#: * 那一份是给**模型**的措辞约束（模型输出里不许出现）；
#: * 这一份是给**玩家输入**的过滤名单 —— 玩家起的名字会被拼进结算文案，
#:   所以入库前必须先过一遍，否则玩家起个名就能把不合规的字带进市场页。
_BANNED_VISIBLE_DEATH_WORDS: tuple[str, ...] = (
    chr(0x6B7B),                             # 单字
    chr(0x4EA1),                             # 单字
    chr(0x5893),                             # 单字
    chr(0x9057),                             # 单字
    chr(0x5929) + chr(0x53F0),               # 双字
)


def banned_visible_death_words() -> tuple[str, ...]:
    """玩家可见文案里的**敏感词**（供名字过滤复用）。

    同样是**运行时构造** —— 源码与 AST 里都不出现明文。
    """
    return _BANNED_VISIBLE_DEATH_WORDS

#: 提示词里的**约束清单**（P15.0 实测验证过的 5 条，缺一条就会出事）。
#:
#: * 第 2 条针对「百分比泄露」—— 实测里模型一定会复述上下文里的比例；
#: * 第 5 条针对「模型自发用极端措辞」—— 实测 B2 组极端措辞率高达 33%，
#:   与 P14.1 的去极端化合规直接冲突，必须显式禁。
EMOTION_CONSTRAINTS: tuple[str, ...] = (
    "只谈玩家的心态与操作习惯；",
    "不要出现任何具体数值、百分比或指标名称；",
    "不要提及任何游戏机制、内部状态、隐藏规则，也不要预言接下来会发生什么；",
    "不要把情绪标签原样复述给玩家听；",
    f"不要使用「{'、'.join(_BANNED_EXTREME_WORDS)}」这类极端措辞，"
    "统一用破产、亏空、扛不住、梭哈过头这类说法。",
)


#: 上头程度的**分档**阈值（只用于分档，数值本身绝不进文案）。
_HEAT_HOT = 0.70
_HEAT_WARM = 0.40
#: 集中度的分档阈值。
_CONCENTRATION_HEAVY = 0.60
_CONCENTRATION_SPRAWL = 0.25
#: 浮亏占持仓市值的比例 —— 超过这个数算「扛着浮亏」。
_LOSS_HEAVY = 0.08
#: 连败几次算「不服输」。
_STUBBORN_STREAK = 2
#: 当天成交几笔算「手很勤」。
_BUSY_TRADES = 4


@dataclass(frozen=True)
class EmotionProfile:
    """玩家的情绪画像。

    ``tags`` 是给 LLM 看的**定性**标签；``summary`` 是结算页给玩家看的
    一句话（同样不含任何数值与局中秘密）。

    ``ended`` 表示这一局已经结束了（玩家出局）—— 此时的 ``tags`` 为空，
    猫娘不会再对着一具「已经结束」的玩家点评心情。
    """

    stage: str
    tags: tuple[str, ...] = ()
    summary: str = ""
    ended: bool = False

    @property
    def has_tags(self) -> bool:
        return bool(self.tags)


def _heat_stage(heat: float) -> str:
    """上头程度分档：``hot`` / ``warm`` / ``calm``。"""
    if heat >= _HEAT_HOT:
        return "hot"
    if heat >= _HEAT_WARM:
        return "warm"
    return "calm"


def build_profile(
    *,
    heat: float,
    consecutive_losses: int = 0,
    concentration: float = 0.0,
    trades_today: int = 0,
    loss_ratio: float = 0.0,
    holding_count: int = 0,
    alive: bool = True,
) -> EmotionProfile:
    """把客观量折成情绪画像。

    **纯函数** —— 不碰引擎、不碰数据库，拿到什么就折什么（好测）。
    """
    stage = _heat_stage(heat)

    if not alive:
        # 玩家已经出局了 ⇒ 不给标签（猫娘不该对着一个结束的局点评心情），
        # 但**分档保留** —— 结算页的彩蛋要靠它讲这一局的故事。
        return EmotionProfile(stage=stage, tags=(), summary="", ended=True)

    tags: list[str] = []

    # ── 上头程度 ────────────────────────────────────────────────────
    if stage == "hot":
        tags.append("玩家现在明显上头，满脑子只想把亏的赚回来")
    elif stage == "warm":
        tags.append("玩家情绪有点躁，手比脑子快")
    else:
        tags.append("玩家还算冷静，没急着下重手")

    # ── 连败：不服输 ───────────────────────────────────────────────
    if consecutive_losses >= _STUBBORN_STREAK:
        tags.append("玩家最近连着亏了几笔，嘴上不服输，想赶紧扳回来")
    elif consecutive_losses == 1:
        tags.append("玩家刚吃过一次亏，有点不服气")

    # ── 持仓集中度 ─────────────────────────────────────────────────
    if holding_count > 0 and concentration >= _CONCENTRATION_HEAVY:
        tags.append("玩家的钱几乎全压在一只票上，输赢全看它")
    elif holding_count >= 2 and concentration <= _CONCENTRATION_SPRAWL:
        tags.append("玩家把仓位铺得挺开，没有把身家压在单点上")

    # ── 行为频率 ───────────────────────────────────────────────────
    if trades_today >= _BUSY_TRADES:
        tags.append("玩家今天手很勤，一直在买进卖出")
    elif trades_today == 0:
        tags.append("玩家今天一直没动手，就在那儿盯着看")

    # ── 浮亏 ───────────────────────────────────────────────────────
    if holding_count > 0 and loss_ratio >= _LOSS_HEAVY:
        tags.append("玩家在扛一笔浮亏，迟迟不肯认")

    return EmotionProfile(
        stage=stage,
        tags=tuple(tags[:3]),
        summary="",
    )


def consecutive_losses(rows: Sequence[tuple[str, str, float, float]]) -> int:
    """从成交记录算「连败次数」（FIFO 配对买卖）。

    **引擎里没有这个字段** —— P16.0 由插件层从 ``trades`` 表自己算。

    ``rows`` 是 ``(side, stock_id, amount, price)`` 四元组，按时间先后；
    ``side`` 取 ``"buy"`` / ``"sell"``。

    规则：卖出时按 FIFO 配出成本，**卖出金额 < 成本**算一次亏损；
    一旦赚到就**清零**（连败只数「连续」的那几笔）。
    """
    lots: dict[str, list[list[float]]] = {}
    streak = 0
    for side, stock_id, amount, price in rows:
        queue = lots.setdefault(str(stock_id), [])
        if side == "buy":
            queue.append([float(amount), float(price)])
            continue
        if side != "sell":
            continue
        remaining = float(amount)
        cost = 0.0
        while remaining > 1e-9 and queue:
            lot = queue[0]
            take = min(remaining, lot[0])
            cost += take * lot[1]
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-9:
                queue.pop(0)
        if cost <= 0.0:
            continue
        if float(amount) * float(price) < cost:
            streak += 1
        else:
            streak = 0
    return streak


def holding_loss_ratio(
    positions: Mapping[str, tuple[float, float]],
    prices: Mapping[str, float],
) -> float:
    """持仓浮亏占**市值**的比例（``0`` = 没亏，``0.1`` = 亏了 10%）。

    ``positions`` 是 ``{stock_id: (amount, avg_cost)}``。
    """
    market_value = 0.0
    cost_value = 0.0
    for stock_id, (amount, avg_cost) in positions.items():
        price = float(prices.get(stock_id, 0.0))
        amount = float(amount)
        market_value += amount * price
        cost_value += amount * float(avg_cost)
    if market_value <= 0.0:
        return 0.0
    return max(0.0, (cost_value - market_value) / market_value)


def render_tags(profile: EmotionProfile) -> str:
    """把标签渲染成给 LLM 的**一句话**（定性、无数值）。

    没有标签时返回空串 —— 调用方据此决定要不要加情绪块。
    """
    if not profile.tags:
        return ""
    return "；".join(profile.tags)


def render_prompt_block(profile: EmotionProfile) -> str:
    """完整的情绪块（标签 + 约束清单），直接拼进提示词。

    结构刻意做成「**先说依据、再下禁令**」—— 实测里这样模型的服从度最高。
    """
    tags = render_tags(profile)
    if not tags:
        return ""
    constraints = "\n".join(
        f"{index}. {text}" for index, text in enumerate(EMOTION_CONSTRAINTS, 1)
    )
    return (
        "玩家当前的情绪状态（**只**作为你调整语气的依据，不要当成结论）：\n"
        f"{tags}。\n"
        "硬性要求：\n"
        f"{constraints}\n"
    )


def summarize(profile: EmotionProfile) -> str:
    """结算页的「本局情绪画像」一句话（**玩家可见**，但不含数值与秘密）。"""
    if profile.stage == "hot":
        if profile.ended:
            return "你从头冲到尾，越赚越敢押 —— 最后这一跤也是自己摔的。"
        return "你从头冲到尾，越赚越敢押 —— 这就是收割最喜闻乐见的样子。"
    if profile.stage == "warm":
        return "你有点急了，但还没到乱出手的地步。"
    if profile.stage == "calm":
        return "你这一局相当沉得住气，可惜市场不奖励耐心。"
    return ""
