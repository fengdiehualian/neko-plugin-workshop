"""「配表情积极度」三档的语义（v0.15.0 收成一处的两个受众）。

档位只有一个含义——**她有多想配图**——但要喂给两个地方：

1. `injection_guidance(tier)`：存在感注入里的意愿段。低频、会随对话沉底；
2. `send_tool_description(base, tier)`：`sticker_send` 的工具描述。工具列表**每一轮都在场**，
   所以想让她"更想发"，这根杠杆比调注入频率有效得多（v0.15.0 的来由：实机日志显示
   6 次注入只换来 2 次发图，且两次都紧跟在注入后 30~60 秒内——三道闸一次都没拦）。

两段的**许可强度**必须同向（否则她读到的是自相矛盾的话），但**措辞分工**不同：
注入段可以长（讲道理），工具描述段必须短（工具描述是常驻提示，写长是在挤她的注意力）。

发送层的四把尺（冷却 / 最近不重复 / 概率闸 / 复用窗口）一律不吃档位——见 DESIGN 陷阱 26。
"""

from __future__ import annotations

from .configuration import EAGERNESS_DEFAULT, EAGERNESS_LEVELS

# Normal speech precedes optional tool round trips; image-only replies stay valid.
REPLY_FIRST_NOTE = "先在正常回复通道说完正文，再选 ID 配图；只发图可直接选图。"

# Applies to every receipt, including empty candidates and transport failures.
INTERNAL_SELECTION_NOTE = (
    "选图不进正文或括号动作，不照读图片说明；空候选或未发送也不解释原因。"
    "正文只说一次；若已说完就结束，仅续接未说完的话，不复述原话或配文。"
)

# Freeze 0.9.10 independently; later eager tuning must not change natural.
NATURAL_FREQUENCY_NOTE = (
    "自然档：轻松聊天默认配一张贴切图，直接选快捷候选 ID，不等强烈情绪或要图。"
    "日常问答也可用单纯表情回应态度。"
    "用户要求纯文字、无贴切图片或严肃任务就纯文字，单回复最多一张。"
)

# Extend the full natural preference instead of replacing it with shorter guidance.
EAGER_EXTRA_NOTE = "上一轮发过，本轮有贴切的新图仍优先配一张。"
EAGER_FREQUENCY_NOTE = NATURAL_FREQUENCY_NOTE.replace("自然档", "爱发档") + EAGER_EXTRA_NOTE

# Freeze the 0.9.8 eager strategy independently of future eager tuning.
RESERVED_FREQUENCY_NOTE = (
    "矜持档：普通聊天参考每 2～3 条完整回复配一张，不固定轮次；"
    "近期几轮未配图时优先从快捷候选选 ID，不等要图。"
    "用户要求纯文字或无贴切图片就纯文字，单回复最多一张。"
)

# Reserved and natural preserve independently observed strategies.
WILL: dict[str, str] = {
    "reserved": RESERVED_FREQUENCY_NOTE + "分清安慰与自述。",
    "natural": NATURAL_FREQUENCY_NOTE + "分清安慰与自述。",
    "eager": EAGER_FREQUENCY_NOTE + "分清安慰与自述。",
}

# Keep tier willingness, but never put candidate lookup ahead of normal speech.
DECISION_CHECKPOINT: dict[str, str] = {
    "reserved": (
        f"{REPLY_FIRST_NOTE}按本轮表达的态度判断是否配图。"
        f"{RESERVED_FREQUENCY_NOTE}"
    ),
    "natural": (
        f"{REPLY_FIRST_NOTE}按本轮表达的态度判断哪张贴切，直接选 ID 配图。"
        f"{NATURAL_FREQUENCY_NOTE}"
    ),
}
DECISION_CHECKPOINT["eager"] = DECISION_CHECKPOINT["natural"].replace("自然档", "爱发档") + EAGER_EXTRA_NOTE

# 工具描述追加段（短）。同样不许写"干脆不发"这种退路——那是上一版 eager 失效的原因之一。
TOOL_NOTE: dict[str, str] = {
    "reserved": (
        " 矜持档：有贴切图片的轻松互动优先配一张，不用等主人开口，文字说清也别省掉；"
        "按回复意图读候选再选 ID，无贴切图片、信息问答或正经办事纯文字，单回复最多一张。"
    ),
    "natural": (
        " 自然档：日常互动有贴切图片就主动配一张，不用等主人开口；"
        "单纯表情表达回复态度也可以，不必复现正文每个动作。"
        "读说明后选 ID；用户要求纯文字、无贴切图片或严肃任务就纯文字，单回复最多一张。"
    ),
}
TOOL_NOTE["eager"] = TOOL_NOTE["natural"].replace("自然档", "爱发档")

# **触发判据**（v0.19.0）：回答"什么时候该想到它"，不是"怎么调"。
# 为什么单独一层：v0.17.0 把目录搬进常驻面、v0.18.0 把动作压成一次调用，实机仍然
# "不提醒她就不发"。同门那批她真会调的工具（forever_companion 的 mood_*）每一条都带
# 判据或跨工具路由（`mixins/mood_actions.py:638,667,695`），没有一条只摆选项——
# 因为它们的动作和我们要一次工具往返，而参考项目"只给许可"能成立是因为它写个标记就完事、
# 零往返。许可式措辞换到往返型动作上就是错配，这轮改的就是这个。
TRIGGER_CRITERIA: dict[str, str] = {
    "reserved": (
        "什么时候该想到它：轻松接话、开心庆祝、撒娇贴贴、关心安慰、玩笑吐槽、犯困或道晚安，"
        "有贴切图片就直接选 ID 配一张，需要更多再查候选；"
        "不用等用户点名要图，也别因文字已经说清楚就省掉。"
        "按自己回复要表达的态度选具体图片，分清安慰对方和说自己；"
        "无贴切图片、信息问答或正经办事就纯文字，单回复最多一张。"
    ),
    "natural": (
        "什么时候该想到它：轻松接话、开心庆祝、撒娇贴贴、关心安慰、玩笑吐槽、犯困或道晚安，"
        "日常问答、询问喜好和关心近况也算聊天，不因带问号就跳过。"
        "有贴切图片就直接选 ID 配一张，需要更多再查候选；"
        "不用等用户点名要图，也别因文字已经说清楚就省掉。"
        "按自己回复要表达的态度选具体图片，分清安慰对方和说自己；"
        "用户要求纯文字、无贴切图片或严肃任务就纯文字，单回复最多一张。"
    ),
}
TRIGGER_CRITERIA["eager"] = TRIGGER_CRITERIA["natural"]

# 点名节奏（v0.19.0）：**积极度档位统一驱动三面**——判据措辞、注入点名句、几天几轮注一次。
# Reserved preserves 0.9.8; natural preserves 0.9.10's one-turn cadence.
# 主人要"能自定义"：文件里把 `inject_interval_n` 写成具体数字就覆盖档位（0 = 跟随档位）。
INJECT_INTERVAL_BY_TIER: dict[str, int] = {"reserved": 3, "natural": 1, "eager": 1}
# 配置里这个值表示"跟随档位"，不是"每 0 轮"。
INJECT_INTERVAL_FOLLOW_TIER = 0


def effective_inject_interval_n(tier: str, configured: int) -> int:
    """这一档实际每几轮点名一次：配置写死优先，0/负数才跟随档位。

    不许拿档位去改发送层的任何一把尺（陷阱 26 没动）；这里只裁"提醒的密度"。
    """
    if isinstance(configured, int) and configured > 0:
        return configured
    return INJECT_INTERVAL_BY_TIER[normalize_tier(tier)]


# 返回值里的下一步指引（v0.19.0）。同门每个情绪工具成功后都用 `note` 指挥下一步
# （"被哄好时记得调用 mood_rising_tide"），我们以前只回 `{ok, sent, desc}`——
# 她做完一件事后拿不到任何"接下来呢"，那一面是白扔的。
# 这条**不提任何闸**（冷却/去重/概率在拒绝那一刻由 `_SEND_HINTS` 就地说，陷阱 29）。
NEXT_STEP_NOTE = (
    "表情包已提交到聊天，本轮选图已完成。只续接尚未说完的回复；"
    "若已说完就结束，不为配图另起一段，不复述原话、额外配文或选图过程。"
)


def injection_pointer(tier: str) -> str:
    """Give speech order and the candidate/ID path without blocking the reply."""
    return (
        f"{REPLY_FIRST_NOTE}"
        "读快捷候选逐图说明，贴切就用 sticker_send(sticker_id=候选ID)；需更多再用 sticker_list，"
        "text 留空即可，图片独立发送；没有合适的就纯文字。"
        + INTERNAL_SELECTION_NOTE
    )


def tool_criteria(tier: str) -> str:
    """工具描述里的判据段（和许可句一道，两个受众同一方向）。"""
    return TRIGGER_CRITERIA[normalize_tier(tier)]


def decision_checkpoint(tier: str) -> str:
    """Lead the tool description with speech before optional image selection."""
    return DECISION_CHECKPOINT[normalize_tier(tier)]


def normalize_tier(tier: object) -> str:
    """不在册的档位退到默认档（与配置读入 `_as_choice` 同一条尺）。"""
    if isinstance(tier, str) and tier.strip() in EAGERNESS_LEVELS:
        return tier.strip()
    return EAGERNESS_DEFAULT


def injection_guidance(tier: str) -> str:
    """Reminder willingness only; refusal guidance belongs to actual failed calls."""
    return WILL[normalize_tier(tier)]


def send_tool_description(base: str, tier: str) -> str:
    """`sticker_send` 的完整描述：静态正文 + 这一档的触发判据 + 档位许可句。

    判据排在许可句之前：先回答"什么时候该想到它"，再说"这一档有多主动"——
    往返型动作上光给许可不够（v0.19.0 的实机教训，见 `TRIGGER_CRITERIA` 上方注释）。
    """
    normalized = normalize_tier(tier)
    return f"{base}{TRIGGER_CRITERIA[normalized]}\n{TOOL_NOTE[normalized]}"
