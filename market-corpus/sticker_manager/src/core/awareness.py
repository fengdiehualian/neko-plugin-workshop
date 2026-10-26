"""存在感注入（v0.2.0）的纯函数层：选"注什么"，拼"注成什么样"。

**为什么需要它**：`sticker_list` / `sticker_send` 两个工具一直在，但它们考的是
"她想不想得起来用"。注入就是那句"想起来"。

**v0.17.0 把职责收窄了**：目录（分类全表）不再由注入带——它搬进了 `sticker_send` 的
工具描述（`core/tool_surface.py`），那里每轮都在场；注入是排干即弃的一次性 cue，
当目录载体不合格。本模块现在只管两件事：

1. **挑时机**：`injection_due_for_turn` 把"新一轮到了"翻成"这轮注不注"（纯判定）。
2. **拼文案**：库大小 + 指向常驻面的一句 + 按档位的意愿段。空库回空串=不该注。
   给模型看的中文不走 i18n（our_life 同一纪律），面板/入口的用户文案才走。

节奏（间隔多少秒、注入给谁）不在这层：那是 services/awareness.py 与时钟的事。
"""

from __future__ import annotations

import re
from typing import Any

from .catalog import Sticker
from .configuration import INJECT_MODE_DEFAULT, INJECT_MODES
from .eagerness import injection_guidance, injection_pointer


def normalize_mode(mode: Any) -> str:
    """不在册的档位退到默认——与 eagerness 档位同一条尺：宁可退档也不炸拍。"""
    value = str(mode or "").strip()
    return value if value in INJECT_MODES else INJECT_MODE_DEFAULT


# 情绪信号词表（v0.20.0）。为什么是词表而不是模型：陷阱 30 明令禁止插件从子进程
# 直连模型通道，所以这条只能便宜、必须在本地算完。
# 收词纪律：**只收"这句里有一个人在反应"的词**。不收名词、不收事务用语（"报告/帮我算/
# 几点"），也不收孤立的程度词——误报的代价是她连着甩图，比漏报更难看。
_EMOTION_WORDS = (
    "开心", "高兴", "笑死", "哈哈", "可爱", "喜欢", "爱了", "心动", "期待", "激动",
    "感动", "暖", "谢啦", "谢谢", "辛苦", "想你了", "想你", "陪陪", "抱抱", "安慰",
    "无语", "服了", "裂开", "崩溃", "破防", "emo", "难受", "难过", "委屈", "心疼",
    "焦虑", "烦", "生气", "气死", "生我气", "讨厌", "嫌弃", "困", "累", "累瘫", "摆烂", "emo了",
    "好耶", "冲鸭", "牛啊", "绝了", "笑不出来", "蚌埠住", "绷不住",
)
# 副信号：成串的标点是"这句有反应"的形状证据（单个问号是提问、单个句号是陈述，都不算）。
_REACTION_RE = re.compile(r"…{2,}|[!！]+|[~～]{2,}|[？?]{2,}|哈{3,}")
# Match conversational actions as short utterances, not words inside editing/search requests.
_INTERACTION_RE = re.compile(
    r"(?:来|再|给你|给我|要|想|求|让我|我来|可以|继续)?"
    r"(?:摸摸(?:头|你的头|你)?|摸头|不摸了|别摸了|贴贴|蹭蹭|亲亲|撒娇)"
    r"(?:一下下|一下|好不好|你|我|了|啦|呀|嘛|吧|哦|喵|吗){0,3}"
)
_INTERACTION_TRIM = " \t\r\n，,。.!！?？~～…（）()「」『』“”\"'、"


def emotional_signal(text: str) -> bool:
    """这句用户话里有没有情绪反应空间（v0.20.0 事件门控点名的判据）。

    纯信息问答（"现在几点""帮我算 1+1""with 怎么用？"）回 False——参考那边和同门的
    判据都是"办事的时候纯文字"，点名点错比不点名更糟。
    """
    body = str(text or "")
    if not body.strip():
        return False
    lowered = body.lower()
    if any(word in lowered for word in _EMOTION_WORDS):
        return True
    if _INTERACTION_RE.fullmatch(body.strip(_INTERACTION_TRIM)):
        return True
    return bool(_REACTION_RE.search(body))


def injection_due_for_turn(
    mode: Any,
    *,
    turns_since_inject: int,
    interval_n: int,
    floor_remaining_sec: float,
    signal: bool = False,
    event_gated: bool = True,
) -> bool:
    """新一轮到了，这一轮注不注（纯判定，时钟与计数都在调用方手里）。

    地板未过一律 False——`min_interval_sec` 是连珠炮防刷屏用的，两种模式都吃它。
    计数只在注入成功后清零（见 services/turns.py 纪律），所以这里被闸拦下时
    不消耗计数：攒够 N 轮而这轮被地板挡住时，下一轮仍然立刻命中。

    v0.20.0 加 `signal`：这句用户话有情绪反应时**不等计数攒满就点名**。依据是同门
    `forever_companion` 的 `emotion_sense.py:480-520`——她真会照着调的那类提醒，触发源
    都是"刚读完的这句用户话"（语气标签 + 阈值），而不是轮次计数；载体同样是 `read`。
    计数节奏退居兜底（信号一直没来时提醒仍会按档点名），`event_gated=false` 可整条关掉。
    """
    if floor_remaining_sec > 0.0:
        return False
    if event_gated and signal:
        return True
    if normalize_mode(mode) == "every_user_message":
        return True
    return turns_since_inject >= max(1, int(interval_n))


# 注入文本的骨架。刻意不提"系统提示"这类元话语。
# v0.20.0 更正：这里原本写着"也不下命令"，那是许可式措辞的残留——陷阱 33/34 已经把
# "要求她每轮真的做一次配图判断"定成该下的命令（要她**判断**，不是要她**发**）。
# 她是自愿用表情的主人，不是被执行分支的脚本；给的是"有什么 + 现在判断 + 怎么发"。
# 轮 C 起中段多一条**使用规则**（学习外部系统 提示词里"区分安慰与自述、不贴切就不发"
# 与数量软提示）：硬闸在发送层的冷却，软提示进这段文案——两层分离，各管各的。
_HEADER = "【表情包】你的收藏间里有 {count} 张表情包。"
# 事件门控点名的落点句（v0.20.0）：形状照同门 `emotion_sense` 的 `（语气感知提醒）`——
# 先陈述刚发生的事（这句有情绪），再点名工具，最后留退路（不贴切就别配）。
_EVENT_LEDE = "（表情包点名）最近的对话有互动或情绪反应，回复时想一下配不配图。"
# v0.17.0：**目录与常货行从注入里退场**。它们搬去了常驻面（`core/tool_surface.py`
# 拼进 `sticker_send` 的工具描述，每轮都在场），注入只剩"想起来"这一件事。
# 理由不是省字——是载体错了：`ai_behavior="read"` 是排干即弃的一次性 cue，文字模式
# 等下一个用户话轮、语音模式要等下一次自然热切换（宿主 lifecycle.py 明写"哪怕隔好几个
# 话轮"）。把目录写进这种 cue，等于把地图塞进一张会过期的便条。
# 意愿段在 `core/eagerness.py`：那里同时管"注入怎么说"和
# "工具描述怎么说"，两处许可强度必须同向，所以不许在这边再抄一份。
# v0.19.0：这句从"去看说明"改成**点名工具 + 说清现在就能做**。同门的经验是三面叠加
# （描述给判据 + 事件门控的点名提醒 + 返回值指挥下一步），只靠常驻面那一条推不动她
# ——见 `core/eagerness.TRIGGER_CRITERIA` 上方注释。尺在 `core/eagerness.injection_pointer`。
def build_awareness_text(
    stickers: list[Sticker],
    *,
    eagerness: str = "natural",
    event: bool = False,
) -> str:
    """拼一条注入文本。空库回空串——调用方拿空串当"这拍不该注"。

    v0.20.0 的 `event=True` 是**事件门控点名**那一支：开头先给她一句"就是现在这句"
    的落点（同门 `emotion_sense` 的 `（语气感知提醒）` 结构：先陈述刚发生的事实，
    再点名工具，最后留退路）。计数兜底那一支不带这句 lede，免得把"每 6 轮例行点名"
    伪装成"这句有情绪"。

    v0.20.11 起只保留库大小、group 路径与档位意愿；拒绝规则留在实际失败回执。
    轮 F 那版还会附套图分类概览与最近常用前 N 行——那是把一次性 cue 当目录载体用，
    实机账本（10 次注入 / 2 次发图）之后换成了常驻面。
    v0.14.0：意愿段按「配表情积极度」选档，不在册的档位退到 natural（与配置读入同一条尺）。
    """
    total = sum(1 for s in stickers if not s.disabled)
    if total <= 0:
        return ""
    lines = [_HEADER.format(count=total), injection_pointer(eagerness), injection_guidance(eagerness)]
    if event:
        lines.insert(0, _EVENT_LEDE)
    return "\n".join(lines)
