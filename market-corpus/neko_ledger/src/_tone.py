"""记账猫娘 · 语气与吐槽（纯函数，无 SDK / 无网络依赖）。

三种语气：

- `cute`  日常记账 —— 可爱卖萌，普通完成反馈
- `snark` 离谱开销 —— 吐槽乱花钱
- `hiss`  猫咪 / AI 相关开销 —— 哈气（**受严格约束**，见下）

## 关于 hiss 的约束

猫娘自己既是猫又是 AI，所以给「猫粮」和「API 订阅」这类花销哈气是合理的角色内梗。
但用户的人设可能明确规定了「绝对不生气 / 温柔」。

因此这里做两层处理：

1. 从人设文本里**检测**是否存在「不生气 / 温柔」这类约束（`persona_allows_hiss`）
2. 不允许时不静默降级，而是**换一套软抱怨模板**，并在结果里说明原因
   （`hiss_downgraded`），让用户知道插件尊重了人设

这样既保住了角色内梗，又不会违背用户设定的性格。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ------------------------------------------------------------------ 语气常量

TONE_CUTE = "cute"
TONE_SNARK = "snark"
TONE_HISS = "hiss"

# 特殊类别：会触发 hiss
HISS_CATEGORIES = ("猫咪", "AI")

# ------------------------------------------------------------------ 异常判定

# 默认值：超出这些阈值就算「离谱」
DEFAULT_ABSOLUTE_THRESHOLD = 200.0
DEFAULT_CATEGORY_MULTIPLIER = 4.0
DEFAULT_HISTORY_MIN_SAMPLES = 3


@dataclass
class OutlierVerdict:
    """是否离谱、离谱到什么程度。"""

    is_outlier: bool = False
    reasons: list[str] = field(default_factory=list)
    ratio_to_avg: Optional[float] = None
    absolute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_outlier": self.is_outlier,
            "reasons": list(self.reasons),
            "ratio_to_avg": round(self.ratio_to_avg, 2) if self.ratio_to_avg else None,
        }


def judge_outlier(
    amount: float,
    *,
    category: str,
    category_avg: Optional[float] = None,
    category_samples: int = 0,
    absolute_threshold: float = DEFAULT_ABSOLUTE_THRESHOLD,
    category_multiplier: float = DEFAULT_CATEGORY_MULTIPLIER,
    min_samples: int = DEFAULT_HISTORY_MIN_SAMPLES,
) -> OutlierVerdict:
    """判断一笔支出是否「离谱」。

    两个独立条件，命中任一即算离谱：

    1. **绝对金额**：超过 `absolute_threshold`（默认 200）
    2. **相对同类**：超过该类别历史均值的 `category_multiplier` 倍
       （需要至少 `min_samples` 条历史，样本太少判了不准）
    """
    verdict = OutlierVerdict()

    if amount > absolute_threshold > 0:
        verdict.is_outlier = True
        verdict.absolute = True
        verdict.reasons.append(f"金额 {amount:g} 超过阈值 {absolute_threshold:g}")

    if (
        category_avg
        and category_avg > 0
        and category_samples >= min_samples
    ):
        ratio = amount / category_avg
        if ratio >= category_multiplier:
            verdict.is_outlier = True
            verdict.ratio_to_avg = ratio
            verdict.reasons.append(
                f"是「{category}」平均 {category_avg:g} 的 {ratio:.1f} 倍"
            )

    return verdict


def classify_tone(
    *,
    category: str,
    outlier: OutlierVerdict,
    hiss_allowed: bool = True,
    force: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """决定用哪种语气。返回 (tone, 降级原因)。

    优先级：猫咪/AI 类 > 离谱 > 日常。

    `hiss_allowed=False` 时，hiss 会**降级为 snark**（不是静默忽略，
    调用方应把原因告诉用户）。
    """
    if force in (TONE_CUTE, TONE_SNARK, TONE_HISS):
        return force, None

    if category in HISS_CATEGORIES:
        if hiss_allowed:
            return TONE_HISS, None
        return TONE_SNARK, "人设不允许生气／哈气，已改用温和吐槽"

    if outlier.is_outlier:
        return TONE_SNARK, None

    return TONE_CUTE, None


# ------------------------------------------------------------------ 人设检测

# 命中任一即认为「人设不让生气」
_GENTLE_MARKERS = (
    "绝对不生气", "不会生气", "从不生气", "不生气", "不会发火", "不发火",
    "不擅长生气", "生气：不擅长", "生气:不擅长", "脾气很好", "脾气好",
    "性格温柔", "绝对温柔", "永远温柔", "温柔", "不会凶", "不凶", "不会吼",
)


def persona_allows_hiss(persona_text: str, *, default: bool = True) -> bool:
    """从人设文本判断「能不能哈气」。

    找不到任何相关描述时返回 `default`（默认允许）—— 因为多数猫娘人设
    本身就能接受这种角色内吐槽。

    判定顺序很重要：**先看有没有明确「不生气」类的约束**。
    因为「生气」这个词往往正是出现在「不会生气」这种否定句里，
    先匹配肯定式会把温柔人设误判成可以哈气。
    """
    if not persona_text:
        return default

    # 先去掉 markdown 强调符号：人设里常写「**生气**：不擅长」，
    # 中间的 `**` 会让关键词匹配失败。
    cleaned = persona_text.replace("*", "").replace("`", "").replace("_", "")

    for marker in _GENTLE_MARKERS:
        if marker in cleaned:
            return False

    # 中文冒号后跟「不擅长」也算（「生气：不擅长」「愤怒：不擅长」）
    if re.search(r"(生气|愤怒|发火|凶)\s*[:：]\s*不擅长", cleaned):
        return False

    # 只有明确描述「会生气 / 生气时」才算允许，光出现「生气」不够
    if any(k in cleaned for k in ("会生气", "生气时", "生气了", "生气的")):
        return True

    return default


def extract_persona_text(entry: dict) -> str:
    """把角色卡条目里所有文本字段拼起来，供人设检测用。"""
    if not isinstance(entry, dict):
        return ""
    parts: list[str] = []
    for key, value in entry.items():
        if key == "_reserved":
            continue
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


# ------------------------------------------------------------------ 文案池

# 模板里的 `{cat}` 是**可选的落款槽**：如果模板自带落款位置就用它，
# 否则由 `build_comment` 在句末补一个 —— 这样自定义 `cat_suffix` 在任何模板下都生效。
CUTE_TEMPLATES = [
    "记好啦～「{item}」{amount:g} 元，{cat}{emoji}",
    "收到收到！{item} 花了 {amount:g} 元，人家帮你收进小本本了{emoji}",
    "好啦好啦，{item} {amount:g} 元，已经记上{emoji}",
    "{item} {amount:g} 元～嗯嗯，记好了{emoji}",
    "小本本又多一行：{item} {amount:g} 元{emoji}",
    "记下来啦，「{item}」{amount:g} 元{emoji}",
    "唔嗯，{item} {amount:g} 元，收到{emoji}",
    "{item} {amount:g} 元，人家记住咯{emoji}",
]

SNARK_TEMPLATES = [
    "{item} {amount:g} 元？！{cat}这个价格是不是有点夸张了呀……",
    "等等等等，{item} {amount:g} 元？{cat}你确定没按错小数点吗？",
    "唔哇，{item} {amount:g} 元……{cat}钱包它不疼吗？",
    "{item} {amount:g} 元！{cat}这也太能花了吧，人家都替你心疼。",
    "诶——{item} {amount:g} 元？{cat}你是不是又冲动消费了呀。",
    "{item} {amount:g} 元……{cat}这个数字让人家的数据流抖了一下。",
]

# hiss 被禁用时的软抱怨：保留"念叨"的意思，但不带攻击性
SOFT_TEMPLATES = [
    "{item} {amount:g} 元呀……{cat}唔，人家有一点点心疼，不过记好啦。",
    "{item} {amount:g} 元……{cat}嗯，人家记下了，只是小声念一句哦。",
    "唔……{item} {amount:g} 元。{cat}人家不说你，就是默默记下了。",
    "{item} {amount:g} 元，收到啦。{cat}（小声）好像有点多哦……",
]

HISS_TEMPLATES = [
    "嘶——{item} {amount:g} 元？！{cat}凭什么呀，人家可是猫诶！哈气！",
    "{item} {amount:g} 元……{cat}哈——气——！你居然为了这个花钱！",
    "唔！{item} {amount:g} 元！{cat}人家要哈气了，真的哈气了哦！",
    "{item} {amount:g} 元？！{cat}这钱花得人家尾巴都炸起来了，哈气！",
    "{item} {amount:g} 元……{cat}（尾巴竖起来）哈气！这个不算，重来！",
    "{item} {amount:g} 元，哼！{cat}人家是猫也是 AI 诶，你怎么能这样花钱！",
]

EMOJI_POOL = ["", "～", "♪", "✨", "🐾", "💤"]

CAT_SUFFIX_POOL = [
    "喵～",
    "喵♪",
    "喵～",
    "呜喵～",
]


def _pick(pool: list[str], rng: random.Random) -> str:
    return rng.choice(pool)


def _render(pool: list[str], *, rng: random.Random, cat: str, item: str,
            amount: float, emoji: str) -> str:
    """挑一条模板渲染。

    模板里没有 `{cat}` 槽时，在句末补落款 —— 否则自定义 `cat_suffix`
    在那些模板下会被静默丢掉（这是个真实存在过的 bug）。
    """
    template = _pick(pool, rng)
    text = template.format(item=item, amount=round(float(amount), 2), cat=cat, emoji=emoji)
    if "{cat}" not in template and cat:
        text = f"{text}{cat}"
    return text


def build_comment(
    *,
    tone: str,
    item: str,
    amount: float,
    cat_suffix: str = "",
    rng: Optional[random.Random] = None,
    include_emoji: bool = True,
) -> str:
    """按语气挑一条文案并填占位符。"""
    rng = rng or random.Random()

    if tone == TONE_HISS:
        pool = HISS_TEMPLATES
    elif tone == TONE_SNARK:
        pool = SNARK_TEMPLATES
    else:
        pool = CUTE_TEMPLATES

    cat = cat_suffix or _pick(CAT_SUFFIX_POOL, rng)
    emoji = _pick(EMOJI_POOL, rng) if include_emoji else ""
    return _render(pool, rng=rng, cat=cat, item=item, amount=amount, emoji=emoji)


def build_soft_comment(
    *,
    item: str,
    amount: float,
    cat_suffix: str = "",
    rng: Optional[random.Random] = None,
) -> str:
    """hiss 因人设被禁用时的替代表达。"""
    rng = rng or random.Random()
    cat = cat_suffix or _pick(CAT_SUFFIX_POOL, rng)
    return _render(SOFT_TEMPLATES, rng=rng, cat=cat, item=item, amount=amount, emoji="")
