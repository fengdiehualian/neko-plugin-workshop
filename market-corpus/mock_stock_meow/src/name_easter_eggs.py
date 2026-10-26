"""P20.0：名字彩蛋引擎。

玩家输入的名字 → 命中词条 → **插件层**的三种效果通道。

**只碰插件层**：引擎层与模式层一个字节都不改；初始资金的**基础公式**
（``base * days``）也不改 —— 彩蛋的乘数只作用在它算完之后。
这样 ``tests/simulate.py`` 的 500 局基线**逐字节全等**是自动成立的
（基线模拟器只 import ``engine`` + ``modes``，从不经过插件层）。

三个通道（见 ``文档/P20.0-方案-命名系统与名字彩蛋引擎.md`` §1.2）：

1. **初始资金乘数** —— ``cash = round(base * days * 加厚 * 彩蛋乘数, 2)``；
2. **运行时配置覆写** —— ``dataclasses.replace`` 改 news / infinite 那两段
   （P16.0 的新手期放水已经是同构做法）；
3. **日终结算后的插件层钩子** —— 持仓市值按比例变成现金。

**P13 契约**：这里给模型的**只有事实**（处境），**没有表演指令**。
「用嘲讽的语气」那种写法不出现 —— 语气由模型自己决定。

本模块是**纯函数 + 纯数据**，不 import 引擎，也不碰 IO。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

__all__ = [
    "CATEGORY_COMMUNITY",
    "CATEGORY_HUBRIS",
    "CATEGORY_SELF_DEPRECATING",
    "CATEGORY_STEADY",
    "DEFAULT_PLAYER_NAME",
    "EGGS",
    "EasterEgg",
    "FACT_CASH_DOWN",
    "FACT_CASH_UP",
    "FACT_DAILY_RETURN",
    "FACT_LOAN_SIGNED",
    "FACT_LOAN_UNAVAILABLE",
    "FACT_MODE_HELL",
    "FACT_NEWS_FAKE",
    "MAX_NAME_LENGTH",
    "apply_cash_multiplier",
    "build_fact",
    "daily_return_gain",
    "match_egg",
    "normalize_name",
    "public_info",
    "sanitize_name",
]

#: 玩家没填名字时的默认名。
DEFAULT_PLAYER_NAME = "无名韭菜"

#: 名字最长多少个字符（防呆：面板截断 + 后端再截一次）。
#:
#: P21.0：从 12 收到 **6** —— 名字会出现在面板标题栏与结算页里，
#: 太长会撑爆排版（也会让提示词注入的载荷更长）。
MAX_NAME_LENGTH = 6

#: 分类。
CATEGORY_SELF_DEPRECATING = "self_deprecating"
CATEGORY_HUBRIS = "hubris"
CATEGORY_COMMUNITY = "community"
CATEGORY_STEADY = "steady"

#: 名字里命中这些词时**静默替换**成 ``·``（不报错、不让玩家知道自己被审查了）。
_MASK = "·"


# ---------------------------------------------------------------------------
# 效果事实模板（给模型看 ⇒ 措辞一律**不含数字**，也不含表演指令）
# ---------------------------------------------------------------------------

#: 初始钱多得多。
FACT_CASH_UP = "他手里的初始钱比这一档的常人多得多 —— 像有人替他垫了本钱。"
#: 初始钱少一截。
FACT_CASH_DOWN = "他手里的初始钱比这一档的常人少一截 —— 像先把手续费交了。"
#: 地狱开局。
FACT_MODE_HELL = "他这一局的开局档是地狱档 —— 那是全场最紧的一条命线。"
#: 新闻更假。
FACT_NEWS_FAKE = "这一局新闻的准确率比平时低得多。"
#: 签了魔爪契约（无尽档：真的借到钱了）。
FACT_LOAN_SIGNED = "他签了魔爪契约 —— 借到手的钱和要还的钱都比平时多。"
#: 签了魔爪契约（经典档：没有放贷渠道，那张纸眼下只是个签名）。
FACT_LOAN_UNAVAILABLE = "他签了魔爪契约，但这一关没有放贷的渠道 —— 那张纸眼下只是个签名。"
#: 每天持仓自己会长一点。
FACT_DAILY_RETURN = "他每天的持仓都会自己长一点 —— 他什么都不用做。"


@dataclass(frozen=True)
class EasterEgg:
    """一条彩蛋词条。

    * ``aliases`` —— 归一化之后与之**完全相等**才算命中（不做包含匹配：
      「星辰」既是彩蛋词也是群友标的名，「老大」「大A」这种两字词做包含
      匹配误伤面极大）。
    * ``cash_multiplier`` —— 初始资金乘数（**只作用在基础公式之后**）。
    * ``mode_override`` —— 强制开局档（只对**经典档**生效，无尽已经是深渊）。
    * ``news_fake_boost`` —— 假消息比例的提升量。
    * ``loan_multiplier`` —— 借款额度 / 还款额的乘数（**只对无尽档**有地方用）。
    * ``daily_return`` —— 日终持仓回报率（持仓市值按这个比例变成现金）。
    * ``identity_fact`` —— 注入模型的**类别事实**（含 ``{PLAYER_NAME}`` 占位）。
    """

    key: str
    category: str
    aliases: tuple[str, ...]
    cash_multiplier: float = 1.0
    label_key: str = ""
    identity_fact: str = ""
    mode_override: str = ""
    news_fake_boost: float = 0.0
    loan_multiplier: float = 1.0
    daily_return: float = 0.0


#: 词库。
#:
#: **优先级即顺序**：先认「自家人」（社区特供），再认「狂妄」（股神狂妄），
#: 再认「躺平」（稳如老狗），最后才是「自嘲」（抽象自嘲）。
#: 一命一中，不叠加。
EGGS: tuple[EasterEgg, ...] = (
    # ── 社区特供（先认自家人）────────────────────────────────────
    EasterEgg(
        key="community_wehos",
        category=CATEGORY_COMMUNITY,
        aliases=("wehos", "weh"),
        cash_multiplier=0.8,
        label_key="panel.name_egg_community",
        identity_fact="玩家自称「{PLAYER_NAME}」—— 他是自己人。",
        loan_multiplier=2.0,
    ),
    EasterEgg(
        key="community_boss",
        category=CATEGORY_COMMUNITY,
        aliases=("老大", "群主", "老板"),
        cash_multiplier=0.8,
        label_key="panel.name_egg_community",
        identity_fact="玩家自称「{PLAYER_NAME}」—— 他是自己人。",
        loan_multiplier=2.0,
    ),
    EasterEgg(
        key="community_friend",
        category=CATEGORY_COMMUNITY,
        aliases=("群友", "战友好"),
        cash_multiplier=0.8,
        label_key="panel.name_egg_community",
        identity_fact="玩家自称「{PLAYER_NAME}」—— 他是自己人。",
        loan_multiplier=2.0,
    ),
    # ── 股神狂妄 ───────────────────────────────────────────────
    EasterEgg(
        key="hubris_buffett",
        category=CATEGORY_HUBRIS,
        aliases=("巴菲特", "老巴", "巴神", "股神"),
        cash_multiplier=3.0,
        label_key="panel.name_egg_hubris",
        identity_fact="玩家自称「{PLAYER_NAME}」。",
        mode_override="classic_hell",
        news_fake_boost=0.25,
    ),
    EasterEgg(
        key="hubris_soros",
        category=CATEGORY_HUBRIS,
        aliases=("索罗斯", "巨鳄", "金融巨鳄"),
        cash_multiplier=3.0,
        label_key="panel.name_egg_hubris",
        identity_fact="玩家自称「{PLAYER_NAME}」。",
        mode_override="classic_hell",
        news_fake_boost=0.25,
    ),
    EasterEgg(
        key="hubris_mystic",
        category=CATEGORY_HUBRIS,
        aliases=("玄学", "算命先生"),
        cash_multiplier=3.0,
        label_key="panel.name_egg_hubris",
        identity_fact="玩家自称「{PLAYER_NAME}」。",
        mode_override="classic_hell",
        news_fake_boost=0.25,
    ),
    # ── 稳如老狗 ───────────────────────────────────────────────
    EasterEgg(
        key="steady_star",
        category=CATEGORY_STEADY,
        aliases=("星辰", "躺平", "苟着", "老狗"),
        label_key="panel.name_egg_steady",
        identity_fact="玩家自称「{PLAYER_NAME}」。",
        daily_return=0.05,
    ),
    EasterEgg(
        key="steady_pixiu",
        category=CATEGORY_STEADY,
        aliases=("貔貅", "貔貅附体", "只进不出"),
        label_key="panel.name_egg_steady",
        identity_fact="玩家自称「{PLAYER_NAME}」。",
        daily_return=0.05,
    ),
    EasterEgg(
        key="steady_piggy",
        category=CATEGORY_STEADY,
        aliases=("存款", "存钱罐", "收钱罐"),
        label_key="panel.name_egg_steady",
        identity_fact="玩家自称「{PLAYER_NAME}」。",
        daily_return=0.05,
    ),
    # ── 抽象自嘲 ───────────────────────────────────────────────
    EasterEgg(
        key="self_chive",
        category=CATEGORY_SELF_DEPRECATING,
        aliases=("韭菜", "韭菜佬", "小韭菜"),
        cash_multiplier=0.9,
        label_key="panel.name_egg_self",
        identity_fact="玩家自称「{PLAYER_NAME}」—— 这是他自己填上去的身份，不是我们给他贴的标签。",
    ),
    EasterEgg(
        key="self_catcher",
        category=CATEGORY_SELF_DEPRECATING,
        aliases=("接盘侠", "接盘人", "接盘"),
        cash_multiplier=0.9,
        label_key="panel.name_egg_self",
        identity_fact="玩家自称「{PLAYER_NAME}」—— 这是他自己填上去的身份，不是我们给他贴的标签。",
    ),
    EasterEgg(
        key="self_market",
        category=CATEGORY_SELF_DEPRECATING,
        aliases=("大a", "大盘", "大a韭菜"),
        cash_multiplier=0.9,
        label_key="panel.name_egg_self",
        identity_fact="玩家自称「{PLAYER_NAME}」—— 这是他自己填上去的身份，不是我们给他贴的标签。",
    ),
    EasterEgg(
        key="self_retail",
        category=CATEGORY_SELF_DEPRECATING,
        aliases=("散户", "小散", "追高狗", "追高侠"),
        cash_multiplier=0.9,
        label_key="panel.name_egg_self",
        identity_fact="玩家自称「{PLAYER_NAME}」—— 这是他自己填上去的身份，不是我们给他贴的标签。",
    ),
    EasterEgg(
        key="self_gambler",
        category=CATEGORY_SELF_DEPRECATING,
        aliases=("赌狗", "梭哈怪", "赌狗命"),
        cash_multiplier=0.9,
        label_key="panel.name_egg_self",
        identity_fact="玩家自称「{PLAYER_NAME}」—— 这是他自己填上去的身份，不是我们给他贴的标签。",
    ),
)


# ---------------------------------------------------------------------------
# 名字的清洗与匹配
# ---------------------------------------------------------------------------


def normalize_name(raw: str) -> str:
    """把名字归一化（**用于匹配**）。

    * NFKC 规范化（全角 → 半角）；
    * 去掉零宽字符与控制字符；
    * 换行 / 制表 ⇒ 空格；
    * 折叠连续空白、去首尾空白；
    * 拉丁字母转小写（``Wehos`` 与 ``wehos`` 是同一个人）。
    """
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFKC", raw)
    cleaned: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cf", "Cc", "Cs", "Co", "Cn"}:
            # 零宽字符 / 控制字符 / 代理对 —— 一律丢掉。
            continue
        cleaned.append(" " if char.isspace() else char)
    return " ".join("".join(cleaned).split()).lower()


def sanitize_name(raw: str, *, banned: tuple[str, ...] = ()) -> str:
    """把玩家输入清洗成**可以入库**的名字（防呆）。

    * 长度截到 :data:`MAX_NAME_LENGTH`（**提示词注入的第一道闸**）；
    * 换行 / 控制字符 ⇒ 空格（再折叠）；
    * 花括号 ⇒ ``·``（名字会被拼进提示词，留着 ``{...}`` 等于让玩家
      自己往提示词里插占位符 —— 宿主会把它展开成别的东西）；
    * 禁词**静默替换**成 ``·``（不报错 —— 报错等于告诉玩家「这里有敏感词」）；
    * 空 ⇒ :data:`DEFAULT_PLAYER_NAME`。

    **后端永远不信任面板** —— 面板传什么，这里都重新洗一遍。
    """
    if not isinstance(raw, str):
        raw = ""
    text = unicodedata.normalize("NFKC", raw)
    cleaned: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cf", "Cc", "Cs", "Co", "Cn"}:
            continue
        cleaned.append(" " if char.isspace() else char)
    text = " ".join("".join(cleaned).split())
    if not text:
        return DEFAULT_PLAYER_NAME
    text = text[:MAX_NAME_LENGTH]
    for char in ("{", "}"):
        text = text.replace(char, _MASK)
    for word in banned:
        if word and word in text:
            text = text.replace(word, _MASK * len(word))
    return text or DEFAULT_PLAYER_NAME


def match_egg(raw: str) -> EasterEgg | None:
    """按名字匹配彩蛋（**精确匹配**，未命中返回 ``None``）。"""
    normalized = normalize_name(raw)
    if not normalized:
        return None
    for egg in EGGS:
        for alias in egg.aliases:
            if normalized == normalize_name(alias):
                return egg
    return None


def apply_cash_multiplier(base_cash: float, egg: EasterEgg | None) -> float:
    """初始资金的彩蛋乘数。

    **只在基础公式算完之后乘**（``base * days`` 一个字都不改）——
    这样 ``tests/simulate.py`` 的副本公式永远有效，500 局基线逐字节全等。
    """
    if egg is None:
        return round(float(base_cash), 2)
    return round(float(base_cash) * float(egg.cash_multiplier), 2)


def daily_return_gain(positions_value: float, egg: EasterEgg | None) -> float:
    """日终的持仓回报（**稳如老狗**类）。

    拍板口径：**日终把持仓市值按这个比例变成现金** —— 不是股数乘一点。
    没有彩蛋 ⇒ 收益恒为 0（普通玩家的一天就是普通的一天）。
    """
    if egg is None or egg.daily_return <= 0.0:
        return 0.0
    return round(float(positions_value) * float(egg.daily_return), 6)


def public_info(egg: EasterEgg | None) -> dict[str, str] | None:
    """彩蛋的**公开信息**（面板用）。

    只有 ``key`` / ``category`` / ``label_key`` —— **没有倍率、没有数值**：
    面板只需要知道"这个玩家顶着哪个名号"，不需要知道效果有多狠。
    未命中 ⇒ ``None``（面板不显示徽标）。
    """
    if egg is None:
        return None
    return {
        "key": egg.key,
        "category": egg.category,
        "label_key": egg.label_key,
    }


def build_fact(
    egg: EasterEgg,
    player_name: str,
    applied_facts: tuple[str, ...] = (),
) -> str:
    """把彩蛋事实拼成**一段**给模型看的话。

    * 类别事实（``{PLAYER_NAME}`` 会被替换成玩家的**自报名号** ——
      注意这不是宿主的 ``{MASTER_NAME}``：那一位是宿主 session 里的真名，
      这里这一位是玩家在面板上自己填的**戏名**，彩蛋就是按它匹配的）；
    * ``applied_facts`` —— 这一局**实际生效了**的那些效果
      （没生效的不写进去：无尽档不会被换成地狱档，经典档也没有放贷渠道）。

    返回空串 = 没有彩蛋（未命中）⇒ 什么都不注入。

    **只给事实，不给表演指令**（P13 契约）—— 怎么称呼、什么语气，是模型的事。
    """
    if egg is None:
        return ""
    text = egg.identity_fact.replace("{PLAYER_NAME}", player_name)
    parts = [part for part in (text, *applied_facts) if part]
    return " ".join(parts)
