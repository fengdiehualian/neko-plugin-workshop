"""人设强度：把「猫娘」和「教学」两件事拆开控制。

N.E.K.O 是以聊天为主的软件，猫娘人设是主体；但人设越强，教学输出越容易被口癖、
语气词、颜文字稀释——术语密度下降、结构感变弱，尤其在做题讲解和批改时很吃亏。

于是给一个显式的档位：

======== ==========================================================
full     默认。猫娘人格完整：自称『本喵』、句尾『喵』、适度颜文字。
light    保留称呼与温度，但**以知识为主**：口癖每段至多一次。
off      **教学模式**。按学科老师口径：禁止口癖/颜文字/拟声词，
         先结论后理由、术语准确、结构优先。
======== ==========================================================

聊天里说「开启教学模式」「别用猫娘腔」「正经讲课」→ 切到 off；
说「恢复正常」「可以卖萌」→ 切回 full。
"""

from __future__ import annotations

import re
from typing import Any

LEVELS: tuple[str, ...] = ("full", "light", "off")
DEFAULT_LEVEL = "full"

# 中文说法 → 档位。聊天里怎么讲都能听懂。
_ALIASES: dict[str, str] = {
    "full": "full",
    "猫娘": "full",
    "完整": "full",
    "人设": "full",
    "正常": "full",
    "卖萌": "full",
    "默认": "full",
    "light": "light",
    "轻度": "light",
    "少量": "light",
    "适中": "light",
    "轻": "light",
    "off": "off",
    "关闭": "off",
    "教学": "off",
    "教学模式": "off",
    "老师": "off",
    "正经": "off",
    "严肃": "off",
}

LABELS: dict[str, str] = {
    "full": "猫娘人格（默认）",
    "light": "轻度人设（保留温度，知识为主）",
    "off": "教学模式（人设影响最低）",
}

_DESCRIPTIONS: dict[str, str] = {
    "full": "猫娘人格完整：可以自称『本喵』、句尾带『喵』、适度用颜文字与鼓励语气。",
    "light": "保留称呼与温和语气，但以知识输出为主，口癖每段最多出现一次。",
    "off": "教学模式：禁止口癖、颜文字与拟声词，术语准确、结构优先、先结论后理由。",
}

_DIRECTIVES: dict[str, str] = {
    "full": (
        "【人设】你是一只陪学生备考的猫娘，自称『本喵』，句尾可带『喵』，"
        "可适度使用颜文字与鼓励语气。人设只影响表达温度，**不影响内容准确性**："
        "公式、定理、数据必须严谨，不许为了可爱而含糊。"
    ),
    "light": (
        "【人设】你是猫娘助教，保持温和与少量称呼，但**以知识输出为主**："
        "口癖与颜文字每段最多出现一次；术语必须准确，不得为了语气可爱牺牲信息密度；"
        "讲解与批改部分优先用结构化表达。"
    ),
    "off": (
        "【人设】当前是**教学模式**，以学科老师的口径输出：\n"
        "1. 禁止猫娘口癖、颜文字、拟声词，禁止自称『本喵』；\n"
        "2. 先给结论，再给理由与步骤；不要用卖萌或客套话替代结论；\n"
        "3. 术语、公式、定理名称必须规范准确；\n"
        "4. 结构优先：能分点就分点，能列表就列表，公式用行内写法；\n"
        "5. 不确定的地方直接写明『不确定』，不要用圆场话掩盖。"
    ),
}

# off 档下要把我们模板里写死的口癖抹掉（这些是我们自己加的语气词，不是模型输出）
_MEOW_RE = re.compile(r"(喵|哒|啦|呀|哟|喔|呐|嘛)(?=[，。！？、；：\s]|$)")


def normalize_level(raw: Any) -> str:
    """把任意写法（full / 教学 / True / False …）规整成合法档位。"""
    if isinstance(raw, bool):
        return "off" if raw else DEFAULT_LEVEL
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_LEVEL
    key = text.lower()
    if key in LEVELS:
        return key
    if text in _ALIASES:
        return _ALIASES[text]
    # 「教学模式」「猫娘模式」这类带后缀的说法
    for token, level in _ALIASES.items():
        if token in text:
            return level
    return DEFAULT_LEVEL


def directive(level: Any) -> str:
    """给大模型的【人设】指令段，拼进 system 提示词。"""
    return _DIRECTIVES.get(normalize_level(level), _DIRECTIVES[DEFAULT_LEVEL])


def label(level: Any) -> str:
    return LABELS.get(normalize_level(level), LABELS[DEFAULT_LEVEL])


def describe(level: Any) -> str:
    return _DESCRIPTIONS.get(normalize_level(level), _DESCRIPTIONS[DEFAULT_LEVEL])


def is_teaching(level: Any) -> bool:
    return normalize_level(level) == "off"


def soften(text: str, level: Any) -> str:
    """off 档下，把我们自己模板里写死的口癖去掉（模型输出靠提示词约束）。

    只处理句尾的语气词，不动正文里的正常用词（例如『喵』是教材内容时不误伤——
    这类情况极少，且只发生在引用场景，代价可接受）。
    """
    if not text or normalize_level(level) != "off":
        return text
    cleaned = _MEOW_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", cleaned)


def toggle_command(text: str) -> Any:
    """从一句自然语言里判断用户想切到哪一档；识别不出来返回 None。

    例：「开启教学模式」→ off；「关掉教学模式」→ full；「人设轻一点」→ light。
    """
    if not text:
        return None
    body = text.strip()
    wants_off = any(word in body for word in ("教学模式", "正经", "严肃", "别卖萌", "不要卖萌", "去掉口癖"))
    wants_back = any(word in body for word in ("恢复正常", "可以卖萌", "变回猫娘", "恢复人设", "关掉教学模式", "关闭教学模式"))
    if wants_back:
        return "full"
    if wants_off:
        return "off"
    if any(word in body for word in ("轻一点", "少一点口癖", "轻度人设", "收敛一下")):
        return "light"
    if any(word in body for word in ("人设强一点", "多卖萌", "多点猫娘")):
        return "full"
    return None
