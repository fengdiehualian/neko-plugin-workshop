"""`sticker_send` 的常驻面（v0.17.0）：能力句 + 许可 + 激活区的分类全表。

**为什么目录挂在这里而不是挂在注入里**：工具列表每一轮都随请求喂给模型，而且插件
重挂一次就推到活着的会话上（宿主 `main_logic/core/tool_calling.py:67` 的
`register_tool_and_sync` → `_sync_tools_to_active_session` → realtime 的
`session.update {tools}`；唯一例外是 Gemini 方言不支持中途换工具表）。
`push_message(ai_behavior="read")` 不是这一类东西：它是**排干即弃的一次性 cue**——
文字模式等下一个用户话轮，语音模式要等下一次自然热切换（宿主
`main_logic/core/lifecycle.py:2749-2778` 明写"哪怕那隔好几个话轮"，且是 owner 决定）。
实机账本对得上：10 次注入只换 2 次发图。

**参考侧的机制**（本轮搬的是它频率高的前两条，第三条"标记协议"插件碰不到她的
输出流所以搬不成）：① 每轮把 `分类键 - 用途` 全表缝进常驻面；② 那上面**只有许可、
没有闸**——概率 100%、条数不限，全部做成模型看不见的事后过滤。它整套提示词里找不到
一句"你必须配图"。所以这里也不写祈使句，闸一律留在 `services/sender.py` 硬拦。

纪律：本模块是纯函数，输入只有"分类概览串 + 档位"，不碰库也不碰 SDK；
档位许可句仍然只有 `core/eagerness.py` 一个来源（陷阱 26），这里调它而不是抄它。
"""

from __future__ import annotations

from .catalog import Sticker, format_group_overview
from .eagerness import INTERNAL_SELECTION_NOTE, decision_checkpoint, normalize_tier, send_tool_description
from .quick_candidates import QUICK_CANDIDATE_TITLE

# 能力句：说清"这是什么"，并把三种形态都摆成合法选项（参考侧 prompt.head 的结构）。
_HEAD = "发一张你收藏间里的表情包，配合你正在回的这句话。纯文字、正常回复配图、只发图都行。"
_NATURAL_REPLY_EXAMPLE = (
    "例（只示意流程，不照搬台词）：问今天心情→先正常回答，再从符合自述态度的真实候选选 ID 发图；"
    "要求只答数字→纯文字。\n"
)

# 读表指引 + 最低摩擦路径。刻意不提冷却/去重/概率这些闸：她们在拒绝的那一刻
# 由 `_SEND_HINTS` 就地说明就够了，常驻面上写禁令只会教她别发。
# 普通回复留在她自己的回复通道；可选 text 仅作额外配文，与图片分条，署名仍是插件。
# Full worked examples remain on lookup receipts, rather than both standing tools.
STANDING_MATCH_RULE = (
    "按自己回复的态度读逐图说明，分清安慰对方和说自己；动作、对象、阶段和语气需贴合。"
    "可选符合当前态度的单纯表情，不必复现正文每个动作；"
    "图片不得新增未发生的核心情节，等待不选已收礼或已见面，求摸头不选戳鼻尖，抱抱不选舔人。"
    "不为配图改写回复态度。"
)

_SELECTION_PATH = (
    "需要更多候选时先调用 sticker_list(query=正向意图短词)，如「自己被摸头 舒服」；"
    "也可用 sticker_list(group=分类名) 浏览。"
    "选中后调用 sticker_send(sticker_id=候选ID)；只有 group 或 query 时只查候选，不发送。"
    "没有贴切候选就纯文字。"
    "正常文字回复照常说，text 留空即可，图片独立发送；"
    "只有额外配文才填 text，配文与图片分条发送，配文不要在正文重复。"
)
_TAIL = STANDING_MATCH_RULE + _SELECTION_PATH

_LOOKUP_USAGE = (
    "为本轮回复查表情候选，不发送。query 用空格或逗号分隔的正向短词，写清动作、对象及阶段，"
    "如「自己被摸头 舒服」「给对方泡茶 已完成 开心」「等待收到 期待」。"
    "可填 group 浏览分类；同时给时限定分类。情绪词不会补入不同动作，分类只缩小范围。"
    "按回复意图核对逐图说明的对象、阶段和语气，具体匹配规则见候选结果；"
    "有贴切图片再用 sticker_send(sticker_id=候选ID)，不贴切就纯文字。"
)
# 空库/空区时也得有一句实话——没有可发的分类还鼓励她发，只会换来一堆失败调用。
_EMPTY = "（这个区暂时还没有可发的分类，先纯文字回，等主人往里收图。）"

CATALOG_TITLE = "【你的分类】"

# 「未分组」是我们面板侧的记账词，不是她能填进 group 的值（填了必回 group_not_found）。
# 概览尺会带上这一行，所以进常驻面前先摘掉。
_UNGROUPED_MARK = "・未分组"


def catalog_for_tool(stickers: list[Sticker], groups: dict[str, str] | None = None) -> str:
    """激活区 → 分类概览串（每行一个分类）。

    尺复用 `format_group_overview`：只有**有未禁用的图**的分类才出——空分类对她
    隐形（陷阱 21），否则她选中一个空分类就是目录里有它、一发回 `group_not_found`
    的鬼打墙。
    """
    overview = format_group_overview(stickers, groups or {})
    lines = [line for line in overview.splitlines() if not line.startswith(_UNGROUPED_MARK)]
    return "\n".join(lines)


def build_send_tool_description(catalog: str, tier: str = "natural", *, quick_candidates: str = "") -> str:
    """Lead with normal speech, then image selection and tier willingness."""
    normalized = normalize_tier(tier)
    body = f"\n{CATALOG_TITLE}\n{catalog}\n" if catalog else ""
    if not catalog and not quick_candidates:
        body = f"\n{_EMPTY}\n"
    quick = (
        "快捷候选已附逐图说明；有贴切的直接调用 sticker_send(sticker_id=候选ID)，不用先查图。"
        "只选符合本轮态度、对象和阶段的图片；无贴切候选可再检索，也可纯文字。\n"
        f"{QUICK_CANDIDATE_TITLE}\n{quick_candidates}\n"
        if quick_candidates else ""
    )
    example = _NATURAL_REPLY_EXAMPLE if quick_candidates and normalized in ("natural", "eager") else ""
    base = f"{decision_checkpoint(tier)}\n{INTERNAL_SELECTION_NOTE}\n{_HEAD}\n{quick}{example}{_TAIL}{body}"
    return send_tool_description(base, normalized)


def build_list_tool_description(tier: str = "natural") -> str:
    """Give the first tool in the selection path the same tier and speech order."""
    return (
        f"{decision_checkpoint(tier)}\n{INTERNAL_SELECTION_NOTE}\n"
        "发送工具里已有贴切的快捷候选时，可直接选其 ID，不必再查。\n"
        f"{_LOOKUP_USAGE}"
    )
