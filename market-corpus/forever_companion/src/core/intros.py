"""功能介绍的静态文案与原理流程（1.2.7 · 面板「功能介绍」卡片的唯一文案源）。

定位：这是**数据层**，不是渲染层——只声明"每个功能对外介绍时说什么"，
怎么排版、怎么画图全部在前端（ui/capintro.tsx）。能力中心新增功能时，
在 CAP_INTROS 补一条即可自动获得介绍卡片（缺条目时面板会如实显示「介绍
暂缺」而不是瞎编）。

i18n 契约（与 core/capabilities.py 的 label 兜底同一套思路）：

- 这里存的是**中文原文**；面板协议里的字符串统一以 ``{"$i18n": key,
  "default": 中文}`` 引用下发，前端 ``t(key, {defaultValue})`` 按宿主语言
  解析（zh 走 default、en 及以后的小语种进 i18n/*.json 补 key 即可，
  不用动本文件）。
- key 命名由 cap id 派生、规则唯一（见 ``purpose_key`` 等小函数）：
  本文件是文案的**事实源**，i18n JSON 只是它的翻译登记表。

流程演示图（flow）：3~6 个节点串成一条「输入 → 处理 → 输出」的链，
每个节点 = ``FlowNode(kind, 中文标签)``（无图标字段，1.2.7 去 emoji）：

- kind 只影响配色（前端不认识新 kind 时回落中性色，向前兼容）：
  ``src`` 输入源 / ``proc`` 处理 / ``gate`` 判定闸门（虚线框）/ ``out`` 输出
- 节点为纯文字胶囊，无图标（1.2.7 用户反馈去 emoji，配色即类型语言）

字符串纪律：正文里的引用一律用「」角括号——ASCII 双引号会截断 Python
字符串，全角弯引号在部分工具链里会被规范化掉（都踩过）。

依赖链 / LLM 触点 / 占用工具等结构化事实**不在这里重复维护**——
前端从既有 capabilities 数据（deps/llm/tools/blocked_by）现场拼装，
保证介绍卡片和功能状态永远是同一口径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

JsonObject = dict[str, Any]

# 流程节点 kind 词表（稳定字符串，前端按此上色；新增 kind 需同步样式层）
FLOW_KINDS = ("src", "proc", "gate", "out")


@dataclass(frozen=True)
class FlowNode:
    """原理图上的一个节点：kind 决定配色，label 中文原文。

    （1.2.7 用户反馈：不要 emoji 图标——类型区分全靠配色胶囊与虚线框，
    节点退化为纯文字胶囊；新增字段前先想清楚，别把图标需求又加回来。）
    """

    kind: str
    label: str


@dataclass(frozen=True)
class CapIntro:
    """一项功能的介绍内容（全部字段为中文原文，经 key 引用下发）。

    purpose:   这个功能做什么（一段话）
    scenarios: 主要场景（2~4 条「你会看到什么」）
    limits:    限制与注意事项（2~5 条，如实说边界，不粉饰）
    flow:      原理演示链（3~6 节点）
    actions:   可选的情绪动作说明（标识、显示名、含义与时限）
    """

    purpose: str
    scenarios: tuple[str, ...]
    limits: tuple[str, ...]
    flow: tuple[FlowNode, ...]
    actions: tuple[tuple[str, str, str], ...] = ()


# ---- i18n key 派生（唯一规则，改不得：i18n JSON 按此登记翻译）----

def _base(cap_id: str) -> str:
    return f"panel.capintro.{cap_id}"


def purpose_key(cap_id: str) -> str:
    return f"{_base(cap_id)}.purpose"


def scenario_key(cap_id: str, idx: int) -> str:
    """idx 从 1 起（JSON key 用 scene1/scene2…，避免 0 起始的歧义）。"""
    return f"{_base(cap_id)}.scene{idx}"


def limit_key(cap_id: str, idx: int) -> str:
    return f"{_base(cap_id)}.limit{idx}"


def flow_key(cap_id: str, idx: int) -> str:
    return f"{_base(cap_id)}.flow{idx}"


CAP_INTROS: dict[str, CapIntro] = {
    # ---- 状态与提醒（rhythm）----
    "whisper": CapIntro(
        purpose=(
            "将当前阶段的状态提示加入对话上下文，供角色回复时参考。"
            "提示只在后台传递，不单独显示消息。"
        ),
        scenarios=(
            "日常聊天中，角色可参考状态调整回应语气",
            "询问近况时，角色可参考当前阶段回答",
            "相同提示可随角色设定呈现不同表达",
        ),
        limits=(
            "活动感知依赖此功能；关闭后停止添加状态与活动提示",
            "默认每 3 条用户消息检查一次；内容未变化时不重复添加，频率可在设置中调整",
            "会过滤部分不适合日常聊天的词语；实际回复由角色模型决定，不能保证措辞",
            "模拟状态仅用于角色互动，不代表现实健康判断",
        ),
        flow=(
            FlowNode("src", "用户消息"),
            FlowNode("proc", "检查新消息"),
            FlowNode("gate", "检查提示频率"),
            FlowNode("proc", "计算当前阶段"),
            FlowNode("out", "添加后台状态提示"),
        ),
    ),
    "phase_openers": CapIntro(
        purpose=(
            "进入新阶段时，发送一次问候请求，让角色有机会主动介绍近况。"
            "具体内容由角色模型生成。"
        ),
        scenarios=(
            "阶段变化时，角色可能主动说一句近况",
            "同一阶段跨天或重启，不重复发起问候",
        ),
        limits=(
            "需要开启模拟总开关；暂停回复的情绪状态下，不发送问候提示",
            "每次阶段变化最多请求一次；再次进入同名阶段时可重新请求",
            "问候不是固定台词；是否回应和如何表达取决于角色模型",
        ),
        flow=(
            FlowNode("src", "阶段变化"),
            FlowNode("gate", "本阶段尚未提示"),
            FlowNode("out", "发送问候提示"),
        ),
    ),
    "activity_sense": CapIntro(
        purpose=(
            "读取 N.E.K.O 提供的在场与忙碌状态，供角色参考你的活动节奏。"
            "不使用摄像头，也不读取屏幕内容。"
        ),
        scenarios=(
            "离开电脑后，角色可参考离开状态接续聊天",
            "专注工作时，角色可参考忙碌状态简短回应",
        ),
        limits=(
            "需要开启状态提示；活动信息随状态提示加入对话上下文",
            "隐私模式或活动信号不可用时，不添加活动提示",
            "只使用在场、忙碌等概括状态；提示可能随对话发送给 N.E.K.O 配置的模型服务",
        ),
        flow=(
            FlowNode("src", "宿主活动状态"),
            FlowNode("gate", "检查隐私设置"),
            FlowNode("proc", "整理在场与忙碌状态"),
            FlowNode("out", "加入状态提示"),
        ),
    ),
    "anniversary": CapIntro(
        purpose=(
            "在相伴 30、60、90、365 天等节点，向角色发送后台提醒。"
            "角色可在聊天中提起纪念日，不额外弹出庆祝消息。"
        ),
        scenarios=(
            "纪念节点当天，角色可能在聊天中提起相伴天数",
            "在相处统计中查看相伴天数与纪念节点",
        ),
        limits=(
            "纪念日期根据本地相处统计计算，关闭提醒不会清除统计",
            "仅在纪念节点当天提醒一次，不补发错过的日期",
            "提醒只作聊天参考，不保证角色一定提起；可单独关闭本功能",
        ),
        flow=(
            FlowNode("src", "每日日期检查"),
            FlowNode("gate", "到达纪念节点"),
            FlowNode("out", "添加后台纪念日提醒"),
        ),
    ),
    "birthday": CapIntro(
        purpose=(
            "生日当天首次聊天时，向角色发送后台提醒。"
            "可选择在时光日记保存生日记录；提醒不保证角色一定祝福。"
        ),
        scenarios=(
            "生日当天聊天时，角色可能向你送上祝福",
            "开启记录选项后，时光日记会保存一条生日记录",
            "未填写生日时，不发送生日提醒",
        ),
        limits=(
            "生日为 2 月 29 日时，非闰年在 2 月 28 日提醒",
            "暂停回复期间不发送提醒；当天恢复后的下一条消息可再次触发检查",
            "每天最多提醒一次；年份仅校验日期，不计算或提供年龄",
            "生日记录使用插件的固定文案，并非角色亲笔；可在时光日记中删除",
            "需要填写有效生日，并开启当前角色的模拟总开关",
        ),
        flow=(
            FlowNode("src", "设置的生日日期"),
            FlowNode("gate", "生日当天且尚未提醒"),
            FlowNode("proc", "添加提醒与可选记录"),
            FlowNode("out", "由角色决定如何祝福"),
        ),
    ),
    # ---- 情绪与感知（mood）----
    "mood_engine": CapIntro(
        purpose=(
            "为角色提供情绪工具，记录状态与心情变化。"
            "工具记录只作参考，回应与感受由角色决定；到期不代表释怀。"
        ),
        scenarios=(
            "在面板查看角色通过工具表达的情绪状态",
            "不同情绪下，回应语气或主动消息节奏可能变化",
            "查看自主选择、用户要求与记录到期的变化",
        ),
        limits=(
            "语气感知、对话片段、个人日记和我的日记依赖此功能，关闭后会一并停用",
            "部分情绪状态会临时暂停宿主主动消息，结束后恢复原设置",
            "手动演示与角色自主触发的情绪分开记录，不混入自主情绪统计",
            "每个角色同时保留一个当前情绪状态，新选择会替换旧状态；她也可以不调用工具直接调整回应",
            "限时状态可设 1～720 分钟；到期只结束记录，不代表已经释怀，也没有必须依次冷战、爆发、和好的顺序",
        ),
        flow=(
            FlowNode("src", "互动信号"),
            FlowNode("gate", "角色调用情绪工具"),
            FlowNode("proc", "更新情绪状态与时限"),
            FlowNode("out", "记录状态与心情变化"),
        ),
        actions=(
            ("ebb_tide", "冷战沉默", "暂时想保持距离，是否沉默、解释或回应由她决定。限时记录，默认 10 分钟，可在设置中调整；期间暂停主动消息。"),
            ("sea_fog", "已读不回", "暂时不想接当前话题，可简短回应、换话题或稍后接回，不代表真实已读回执。限时记录，默认 10 分钟，可在设置中调整；期间暂停主动消息。"),
            ("shallow_reef", "敷衍应付", "不太想展开交流，但必要的问题仍可认真回答，不强制使用短句。限时记录，默认 10 分钟，可在设置中调整；期间暂停主动消息。"),
            ("storm_surge", "情绪风暴", "想表达当前对话里的强烈感受，表达方式由她决定，不要求持续爆发。限时记录，默认 10 分钟，可在设置中调整；期间暂停主动消息。"),
            ("seek_harbor", "想要被安抚", "希望被关心或理解，可以直接表达需要，也可以暂时不说。由后续选择或手动结束，另有默认 120 分钟的兜底解除，可在设置中调整或关闭；期间暂停主动消息。"),
            ("ripple", "心有涟漪", "一点在意或别扭，不等于强烈冲突。默认记录 15 分钟，不暂停主动消息；是否释怀由她决定。"),
            ("warm_current", "暖流涌动", "想亲近或陪伴，表达沿用自身性格，不强制撒娇。默认记录 30 分钟，不暂停主动消息；与低落时想被安抚不同。"),
            ("spring_tide", "满潮欢喜", "记录当下的开心或兴奋，可以分享好事，也可安静享受。默认记录 30 分钟，不暂停主动消息；与结束旧状态不同。"),
            ("rising_tide", "心情转晴", "也就是「心情好了」：她自主结束之前的情绪记录，恢复因情绪暂停的主动消息。它不是新的持续状态，也不要求立刻亲近或完全没有余绪。"),
        ),
    ),
    "tone_sense": CapIntro(
        purpose=(
            "回复结束后，后台模型分析互动语气并更新心情记录。"
            "需要时提醒角色记录或调整情绪；不会直接替角色选择情绪动作。"
        ),
        scenarios=(
            "语气缓和时，角色可能收到调整情绪状态的提醒",
            "情绪明显时，角色可能收到记录心情的邀请",
        ),
        limits=(
            "需要开启情绪系统；分析可能有误，提醒不保证角色一定采用",
            "默认使用 N.E.K.O 的情感模型；切换模型槽位时读取本地配置直连，分析文本可能发送至外部服务",
            "宿主接口或模型服务不可用时，暂停语气分析，不影响其他功能",
            "分析受调用间隔与筛选阈值限制；部分阶段会提高灵敏度，可在设置中调整",
        ),
        flow=(
            FlowNode("src", "近期互动文本"),
            FlowNode("proc", "后台分析语气"),
            FlowNode("gate", "检查提醒条件"),
            FlowNode("out", "提醒记录或调整状态"),
        ),
    ),
    # ---- 三本日记（diary）----
    "fragments": CapIntro(
        purpose=(
            "后台模型提取消息中的明确喜好与重要表达，连同原话保存到时光日记。"
            "角色可通过检索工具回顾已保存的片段。"
        ),
        scenarios=(
            "明确提到喜欢的食物或活动时，相关原话可能被保存",
            "以后聊到相关话题时，角色可以检索已保存的片段",
            "在面板中查看、搜索或删除保存的对话片段",
        ),
        limits=(
            "置信度不足时不保存；仍可能遗漏或误解，可在面板核对、删除",
            "默认使用 N.E.K.O 的 agent 模型槽位，必要时读取本地配置直连；消息可能发送至外部服务",
            "不会把全部片段自动加入聊天；角色可自主检索，部分情绪下会低频收到参考提醒",
            "需要开启情绪系统，并受模型可用性、调用间隔和每日预算限制",
        ),
        flow=(
            FlowNode("src", "用户消息"),
            FlowNode("proc", "后台提取重要表达"),
            FlowNode("gate", "检查置信度"),
            FlowNode("out", "保存原话，供角色检索"),
        ),
    ),
    "journal": CapIntro(
        purpose=(
            "默认每隔 7 天邀请角色记录近期感受。"
            "点击写作按钮时，后台模型按角色口吻直接生成一篇新日记，完成后保存在面板。"
        ),
        scenarios=(
            "在日记页阅读角色写下的近期感受与心情变化",
            "角色可以续写已有书页，也可以开始新的一页",
        ),
        limits=(
            "邀请可附带心情和片段素材；日记不自动加入后续聊天或 N.E.K.O 记忆",
            "自动邀请由角色决定是否采用；手动写作直接由后台模型生成一篇新日记，不再等待角色决定",
            "手动写作复用我的日记模型通道，需近期对话或新手记素材；受模型可用性和调用预算限制",
            "关闭本功能主要停止邀请；若写作工具仍可用，角色仍可能自主写作",
        ),
        flow=(
            FlowNode("src", "达到写作间隔"),
            FlowNode("proc", "发送邀请与参考素材"),
            FlowNode("gate", "角色决定是否写作"),
            FlowNode("out", "保存书页，供你翻阅"),
        ),
    ),
    "review": CapIntro(
        purpose=(
            "后台模型根据互动记录、语气、心情和片段生成相处总结。"
            "不是角色亲笔，也不是对你的评分；内容可能有偏差，仅供面板回顾。"
        ),
        scenarios=(
            "在日记页阅读一篇后台整理的相处总结",
            "回看近期互动和心情变化，了解这段时间的相处情况",
        ),
        limits=(
            "仅在面板展示，不自动加入聊天或 N.E.K.O 记忆，也不提供角色访问此日记的工具",
            "默认累计 50 次互动，或满 7 天且期间有聊天后生成；手动生成也需足够素材",
            "默认使用 N.E.K.O 的 agent 模型槽位，必要时读取本地配置直连；素材可能发送至外部服务，受调用预算限制",
            "手动演示与角色自主触发的情绪分开标注，避免混淆",
        ),
        flow=(
            FlowNode("src", "累计相处素材"),
            FlowNode("gate", "达到次数或天数条件"),
            FlowNode("proc", "后台整理相处总结"),
            FlowNode("out", "保存到我的日记"),
        ),
    ),
}


def intro_for(cap_id: str) -> CapIntro | None:
    """按能力 id 取介绍条目（未登记返回 None，面板据此显示「介绍暂缺」）。"""
    return CAP_INTROS.get(cap_id)


def build_intro_payload(spec: Any, ref: Callable[[str, str], JsonObject]) -> JsonObject:
    """把声明表组装成面板协议 payload（纯函数、零 IO、不认识 SDK）。

    spec: core.capabilities.CapabilitySpec（鸭子类型，测试可用简单对象）
    ref:  (key, default_zh) -> i18n 引用 的构造函数（mixin 层注入 SDK 的 tr）

    依赖链 / LLM 触点 / 占用工具等结构化事实从 spec 现场取，保证介绍卡片
    与功能状态永远同一口径；这里只携带文案引用。未登记介绍的能力返回
    ``{"id", "found": False}``，前端如实展示「介绍暂缺」。
    """
    intro = CAP_INTROS.get(spec.id)
    if intro is None:
        return {"id": spec.id, "found": False}
    # strip("[]") 防御：声明表里段名本不带括号（"mood"），但手滑写成
    # "[mood]" 也不会渲染出 "[[mood]]"（TOML 里那是数组表）
    config_keys = [] if spec.config is None else [f"[{str(spec.config[0]).strip('[]')}].{spec.config[1]}"]
    return {
        "id": spec.id,
        "found": True,
        "purpose": ref(purpose_key(spec.id), intro.purpose),
        "scenarios": [ref(scenario_key(spec.id, i), s) for i, s in enumerate(intro.scenarios, 1)],
        "limits": [ref(limit_key(spec.id, i), s) for i, s in enumerate(intro.limits, 1)],
        "flow": [
            {"kind": n.kind, "label": ref(flow_key(spec.id, i), n.label)}
            for i, n in enumerate(intro.flow, 1)
        ],
        "actions": [
            {
                "id": action_id,
                "label": ref(f"mood.{action_id}", label),
                "description": ref(f"{_base(spec.id)}.action.{action_id}", description),
            }
            for action_id, label, description in intro.actions
        ],
        "deps": list(spec.depends),
        "config_keys": config_keys,
        "llm": spec.llm,
        "tools": list(spec.tools),
    }
