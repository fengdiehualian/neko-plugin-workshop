"""新闻系统：真新闻、假新闻、模糊新闻、雷新闻、噪音新闻。

教育目的说明
------------
新闻是"诱导你做决策"的工具。本系统刻意实现真实世界里最容易被收割的两种
操作方式：**信假新闻追涨**（heat +10）与**雷新闻后补仓**（heat +20），并用
反转日把"你昨天相信的东西今天全反了"变成可复现的机制，让玩家在零成本环境
里体会被反转碾过的全过程。

所有新闻内容均为虚构，与任何真实公司、真实事件无关。
"""

from __future__ import annotations

from typing import Container, Sequence

from .config import STREAM_NEWS, NewsConfig
from .models import NewsItem
from .rng import derive_stream_rng

__all__ = [
    "generate_news",
    "is_reversal_day",
    "plan_reversal_days",
]

_PHASE_HONEYMOON = "honeymoon"
_PHASE_TRAP = "trap"
_PHASE_SLAUGHTER = "slaughter"

_TRUE_BULLISH = (
    "{name}宣布产线升级，预计产能提升一成。",
    "{name}获得一笔长期订单，营收预期改善。",
    "{name}新产线通过验收，交付能力提升。",
)
_TRUE_BEARISH = (
    "{name}原材料成本上升，毛利承压。",
    "{name}主要客户缩减采购，订单量下滑。",
    "{name}新项目进度低于预期。",
)
_FAKE_BULLISH = (
    "{name}传出大额资金即将入场，盘面看涨。",
    "{name}被曝即将获得独家授权，市场沸腾。",
    "{name}传闻机构调研席位大增。",
)
_FAKE_BEARISH = (
    "{name}被曝存在交付问题，短线承压。",
    "{name}传闻将面临订单调整。",
    "{name}有声音称其现金流紧张。",
)
_VAGUE = (
    "市场传闻部分标的公司近期将有动作。",
    "有分析师提示关注行业景气度变化。",
    "盘中出现多笔异常大单，资金动向不明。",
)
_THUNDER = (
    "{name}被曝出资质问题，股价已连续下挫。",
    "{name}公告称将暂停部分业务，市场哗然。",
    "{name}出现重大客户流失传闻，股价承压。",
)
_NOISE = (
    "今日整体成交清淡，情绪平稳。",
    "盘面窄幅震荡，多空分歧不大。",
    "午后出现一轮短线资金进出。",
)


def plan_reversal_days(
    seed: int,
    *,
    days: int,
    min_days: int = 1,
    max_days: int = 3,
) -> set[int]:
    """由主种子决定本局**保证发生**的反转日集合（纯函数）。"""
    if days <= 0:
        return set()
    rng = derive_stream_rng(seed, STREAM_NEWS, 2)
    low = max(1, min(min_days, days))
    high = max(low, min(max_days, days))
    count = rng.randint(low, high)
    return set(rng.sample(range(1, days + 1), count))


def is_reversal_day(
    seed: int,
    day: int,
    *,
    reversal_days: Container[int],
    config: NewsConfig,
) -> bool:
    """判断某天是否是反转日（纯函数）。

    计划内的反转日保证发生；其余日期有 ``reversal_prob_min ~ reversal_prob_max``
    的概率额外成为反转日。
    """
    if day in reversal_days:
        return True
    rng = derive_stream_rng(seed, STREAM_NEWS, day, 3)
    probability = rng.uniform(config.reversal_prob_min, config.reversal_prob_max)
    return rng.random() < probability


def _pick_kind(rng, phase: str, config: NewsConfig) -> str:
    roll = rng.random()
    if phase == _PHASE_TRAP:
        if roll < config.fake_ratio_trap:
            return "fake"
        if roll < config.fake_ratio_trap + config.noise_ratio:
            return "noise"
        return "vague"
    if phase == _PHASE_HONEYMOON:
        if roll < config.true_ratio_honeymoon:
            return "true"
        if roll < config.true_ratio_honeymoon + config.noise_ratio:
            return "noise"
        return "vague"
    if roll < config.thunder_ratio_slaughter:
        return "thunder"
    if roll < config.thunder_ratio_slaughter + config.noise_ratio:
        return "noise"
    return "vague"


def _render(
    rng, kind: str, name: str, *, reversal: bool
) -> tuple[str, float | None, str | None]:
    """渲染新闻文案；反转日会把多空方向整体翻转。

    返回 ``(content, accuracy, direction)``。``direction`` 取值
    ``bullish`` / ``bearish`` / ``None``（模糊新闻与噪音没有方向）。
    """
    if kind == "true":
        bullish = rng.random() < 0.5
        if reversal:
            bullish = not bullish
        template = rng.choice(_TRUE_BULLISH if bullish else _TRUE_BEARISH)
        return template.format(name=name), None, ("bullish" if bullish else "bearish")
    if kind == "fake":
        bullish = rng.random() < 0.5
        if reversal:
            bullish = not bullish
        template = rng.choice(_FAKE_BULLISH if bullish else _FAKE_BEARISH)
        return template.format(name=name), None, ("bullish" if bullish else "bearish")
    if kind == "vague":
        return rng.choice(_VAGUE), None, None
    if kind == "thunder":
        # 雷新闻永远向下；反转日也不翻（它是"实锤利空"，不是待验证的消息）
        return rng.choice(_THUNDER).format(name=name), None, "bearish"
    return rng.choice(_NOISE), None, None


def generate_news(
    seed: int,
    day: int,
    *,
    phase: str,
    symbols: Sequence[str],
    reversal_days: Container[int],
    config: NewsConfig,
) -> list[NewsItem]:
    """生成某一天的新闻集合（纯函数）。

    同一 ``(seed, day)`` 必然得到同一结果；输入的 ``phase`` 决定真/假/雷新闻
    的配比；``reversal_days`` 命中的日期会把新闻方向整体翻转。
    """
    rng = derive_stream_rng(seed, STREAM_NEWS, day)
    reversal = day in reversal_days
    items: list[NewsItem] = []
    for index in range(max(0, config.news_per_day)):
        kind = _pick_kind(rng, phase, config)
        stock_id = rng.choice(list(symbols)) if symbols else None
        name = stock_id or "该标的"
        content, accuracy, direction = _render(rng, kind, name, reversal=reversal)
        if kind == "true":
            accuracy = (
                config.truth_ratio_honeymoon
                if phase == _PHASE_HONEYMOON
                else 1.0
            )
        items.append(
            NewsItem(
                id=f"news-{day}-{index + 1}",
                day=day,
                type=kind,
                stock_id=stock_id,
                content=content,
                accuracy=accuracy,
                direction=direction,
            )
        )
    return items


def news_kind_from(news_type: str, direction: str | None) -> str:
    """把新闻类型与方向映射成**局中可见**的中性分类。

    只暴露四种中性分类：``bullish`` / ``bearish`` / ``vague`` / ``noise``。
    真新闻、假新闻、雷新闻的区分**绝不**在这里泄露 —— 真假只出现在结算报告里。
    """
    if direction == "bullish":
        return "bullish"
    if direction == "bearish":
        return "bearish"
    if news_type == "vague":
        return "vague"
    return "noise"


def news_kind(item: NewsItem) -> str:
    """把一条新闻映射成**局中可见**的中性分类。

    见 :func:`news_kind_from`。
    """
    return news_kind_from(item.type, item.direction)
