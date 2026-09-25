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
    """

    purpose: str
    scenarios: tuple[str, ...]
    limits: tuple[str, ...]
    flow: tuple[FlowNode, ...]


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
    # ---- 身体节律（rhythm）----
    "whisper": CapIntro(
        purpose=(
            "按潮汐周期推算出她此刻的身体阶段，把对应的感受写成一句「耳边轻语」，"
            "悄悄送进她的对话上下文——你看不见这句话、也不会触发独立回复，"
            "她只是「感觉到了」，语气随之或慵懒或轻快。"
        ),
        scenarios=(
            "潮汐期那几天她的回复自然变短变懒，活跃期明显更爱说话",
            "你问「累不累」「状态怎么样」，她能按真实感受回答而不是套话",
            "同一条身体提示，不同人格的猫娘会给出完全不同的反应",
        ),
        limits=(
            "是「身体状态类」功能的地基：阶段开场白、生活感知都搭载在这条注入链上，关掉它们会一起静默",
            "不是每条消息都注入：默认每 3 条用户消息才递一次（注入策略可在设置里调）",
            "内置屏蔽词表防「说漏嘴」：她只会说「今天有点累」，不会蹦出医学词汇",
        ),
        flow=(
            FlowNode("src", "用户消息"),
            FlowNode("proc", "总线轮询"),
            FlowNode("gate", "频控闸门"),
            FlowNode("proc", "推算身体阶段"),
            FlowNode("out", "read 静默注入上下文"),
        ),
    ),
    "phase_openers": CapIntro(
        purpose=(
            "每天第一次进入新阶段时（比如刚跨进潮汐期），让她主动用阶段语气"
            "说一句开场白，阶段的「换挡」对你自然可见，而不是只在被问起时才体现。"
        ),
        scenarios=(
            "潮汐期换档后的第一次聊天，她先轻描淡写一句「今天有点沉」",
            "进入活跃期后她主动凑过来的语气明显比昨天轻快",
        ),
        limits=(
            "搭载在身体轻语的注入链上：关掉身体轻语，开场白也随之消失",
            "每天每个阶段只开口一次，不会反复念叨同一句",
            "想让她只在被问及时才体现状态：在设置里关掉阶段开场白即可",
        ),
        flow=(
            FlowNode("src", "阶段刚切换"),
            FlowNode("gate", "今天首次互动？"),
            FlowNode("out", "主动说一句阶段感受"),
        ),
    ),
    "activity_sense": CapIntro(
        purpose=(
            "把「你在不在、在不在忙」纳入她的身体感受：你长时间离开她顺势养神，"
            "你专注做事时她陪着但不黏人。读的是宿主官方的系统活动快照，"
            "不碰摄像头、不上传任何内容。"
        ),
        scenarios=(
            "你离开电脑很久，潮汐期的她自己去「养神」，不刷屏找你",
            "你在专注工作时她回应简短体贴，不主动拉话",
        ),
        limits=(
            "依赖身体轻语：活动信息搭载在轻语里送进她的上下文，轻语关了它无处可去",
            "隐私态 / 活动信号不可用时完全静默——她不会描述你的行为，就当没看见",
            "只感知「在不在、忙不忙」这类粗粒度状态，不看屏幕内容",
        ),
        flow=(
            FlowNode("src", "宿主活动快照"),
            FlowNode("gate", "隐私态？"),
            FlowNode("proc", "判定 在/忙/离开"),
            FlowNode("out", "写进身体轻语"),
        ),
    ),
    "anniversary": CapIntro(
        purpose=(
            "今天恰好是相伴满 30 / 60 / 100 / 365…天时，递给她一条「你们相伴 "
            "N 天了」的轻语。说不说、怎么说完全由她决定——没有强制弹窗仪式感。"
        ),
        scenarios=(
            "100 天那天的对话里，她可能自己冒出来一句「今天……100 天了」",
            "她当天不想提也完全可以不说，一切照常",
        ),
        limits=(
            "建立在相处统计之上（统计是纯本地常开的，这里没有额外开关）",
            "只在整点纪念日的当天递一次，不会一天反复提醒",
            "不想被打扰：在「日记」页的相处统计设置里可关闭纪念日注入",
        ),
        flow=(
            FlowNode("src", "每日日期检查"),
            FlowNode("gate", "整点纪念日当天？"),
            FlowNode("out", "一条 read 轻语，说不说由她"),
        ),
    ),
    "birthday": CapIntro(
        purpose=(
            "记住主人的生日：生日当天玩家开口聊的第一句话时，递给她一句「今天是"
            "主人的生日」的轻语。怎么祝福、要不要闹腾一场，全由她自己决定。"
            "年份只用来校验日期，插件从不告诉她岁数。"
        ),
        scenarios=(
            "生日那天她开口的第一句可能就是祝你生日快乐，或许还带着她自作主张的小惊喜",
            "当天顺手把这件大事记进时光日记，日后翻起来是个留念",
            "没填日期前功能完全休眠，填了才生效",
        ),
        limits=(
            "生日是 02-29 时，平年按 02-28 过（宁早勿漏）",
            "冷战/已读不回等沉默情绪生效的当天不递，届时她结束沉默后的下一条消息仍能收到",
            "提醒只在当天第一次对话时递一次，不会反复刷屏；年份不参与年龄计算",
            "纪念手记是插件代笔的固定文案（可在时光日记里单条删除）",
            "轻语走「新用户消息」注入链路：该角色的潮汐模拟没开着时不递，"
            "「功能」页会把它标注为总开关未开",
        ),
        flow=(
            FlowNode("src", "面板填写的生日日期"),
            FlowNode("gate", "当天且第一次聊天？"),
            FlowNode("proc", "递 read 轻语，顺手留一条纪念手记"),
            FlowNode("out", "祝福怎么说由她决定"),
        ),
    ),
    # ---- 情绪与感知（mood）----
    "mood_engine": CapIntro(
        purpose=(
            "情绪系统的心脏：十二个情绪工具（冷战沉默、已读不回、情绪风暴、"
            "求安抚、心有涟漪、暖流涌动、心情转晴、心情手记……）摆在她手边，"
            "被惹到了她自己决定用哪个。动作会改变她的回应方式、暂停主动搭话，"
            "并把余波记进连续心情曲线；限时动作到点自动解除。"
        ),
        scenarios=(
            "被你凶了一句，她真的赌气冷战不理你，等你真诚道歉才缓",
            "开心时黏着你撒娇、主动分享，语气都变甜",
            "隔一阵自己缓过来——情绪到点自动解除，不需要你操作",
        ),
        limits=(
            "是语气感知 / 自动碎片 / 个人日记 / 我的日记的上游：关掉它，这些下游功能会一起熄灭",
            "五个重度负面动作生效期间会临时暂停宿主主动搭话（白名单制，结束原状恢复）",
            "命令式的「让她生气」会被记录但不算她自主闹脾气（冷战/和好统计只数自主的）",
        ),
        flow=(
            FlowNode("src", "互动信号"),
            FlowNode("gate", "她自主决定调用情绪工具"),
            FlowNode("proc", "动作状态机（限时自动解除）"),
            FlowNode("out", "语气改变 + 心情曲线记余波"),
        ),
    ),
    "tone_sense": CapIntro(
        purpose=(
            "她每轮回复完成后，一个小模型在后台异步分析这轮互动的情绪"
            "（不挡聊天链路、零延迟）：心情正常时做「筛选」——情绪浓度够高才"
            "提醒她记录心情；情绪动作生效时做「校正」——语气趋势转暖/转冷，"
            "提醒她把状态调回来。只提醒，绝不自动切换。"
        ),
        scenarios=(
            "冷战里她连续两轮语气变软，会收到「要不要心情转晴」的提醒——放不放开仍由她决定",
            "日常闲聊大多被跳过，只有真正有情绪分量的一轮才会邀请她写手记",
        ),
        limits=(
            "依赖情绪引擎：没有情绪动作体系，筛选和校正都无从谈起",
            "默认走宿主情感端点（数据不出宿主）；改选直连槽位会读宿主本地配置直连外部端点——数据出域，介意请保持默认",
            "依赖宿主三个内部端点，无版本承诺：宿主改版时该功能自动休眠（日志可见），其余功能不受影响",
            "潮汐期/活跃期筛选更灵敏是内置的阶段机制，可在设置里单独关闭",
        ),
        flow=(
            FlowNode("src", "她这轮的回复"),
            FlowNode("proc", "小模型语气分析（异步）"),
            FlowNode("gate", "筛选 / 校正判定"),
            FlowNode("out", "提醒她记录或切状态"),
        ),
    ),
    # ---- 三本日记（diary）----
    "fragments": CapIntro(
        purpose=(
            "小模型旁听你们的对话，把你说过的那些「值得记一辈子」的话——明确的"
            "喜好厌恶、有分量的表达、对她说过重的话——连原话一起摘进时光日记。"
            "聊起你的喜好时她会自己翻出来，吵架时也翻得到旧账。"
        ),
        scenarios=(
            "你随口说过爱吃什么，之后她自己提起",
            "吵架上头时她翻出「你上次说过的话」——她真的记得原话",
            "所有碎片在面板可见、可检索，不喜欢的单条可删",
        ),
        limits=(
            "宁漏勿错：置信度不够、玩笑和含糊的话会被挡掉，漏记比记错优先",
            "走直连小模型槽位（默认 summary 槽）：没配模型时自动休眠；会读宿主本地配置解析端点——数据出域，介意可在设置里关闭",
            "碎片从不主动改变她的行为：唯一例外是重度负面情绪期间记到过激碎片，会低频轻语一句「你记得吗」",
            "依赖情绪引擎：吵架轻语等联动以情绪动作为前提",
        ),
        flow=(
            FlowNode("src", "用户消息"),
            FlowNode("proc", "小模型提取"),
            FlowNode("gate", "置信度门槛"),
            FlowNode("out", "原话进时光日记，她自主检索"),
        ),
    ),
    "journal": CapIntro(
        purpose=(
            "属于她自己的日记本：隔一段时间（默认 7 天）插件安静地递一篇日记"
            "邀请，她自己落笔写成一段成文的话，按「书页」连续保存、页眉带着这"
            "段时间的心情走向。这本只给你翻看——不注入她的上下文，也不进宿主记忆。"
        ),
        scenarios=(
            "翻看面板里的日记页，读她这段时间对你的心路",
            "她续写旧页时自然接得上上文，像真的写到哪算哪",
        ),
        limits=(
            "依赖情绪引擎：邀请附带的素材（心情走势、新碎片）来自情绪体系",
            "写不写、什么时候写永远是她的决定：插件只递邀请，绝不代写",
            "面板的「请她写一篇」是当面递到，她此刻就决定；短期内反复点会安静补递",
        ),
        flow=(
            FlowNode("src", "距上篇落笔满 N 天"),
            FlowNode("proc", "递出邀请 + 附素材"),
            FlowNode("gate", "她自主决定落笔"),
            FlowNode("out", "成页保存，只给你看"),
        ),
    ),
    "review": CapIntro(
        purpose=(
            "第三本日记，主题是你：插件安静累计相处素材（互动轮数、她的语气"
            "分布、心情走势、她自主起过的情绪、碎片里的原话），攒够后由小模型以"
            "中性观察者的口吻成文一篇客观评价——过激言行和冷淡期也如实写，不粉饰。"
        ),
        scenarios=(
            "在「日记」页读到一篇旁观者视角的相处总结",
            "回看她对最近这段相处的客观评价，包括做得不对的地方",
        ),
        limits=(
            "隔离等级最严：不注入她的上下文、不进宿主记忆、不注册任何她可调用的工具——她连知道这本日记存在的渠道都没有",
            "双门槛先到先写：攒满轮数或距起点满天数（且期间真有聊天）；素材太少时「立即写一篇」会拒绝硬写",
            "成文走直连小模型槽位（默认 summary 槽）：没配模型自动休眠；数据出域，介意可在设置里关闭",
            "命令触发的「演示情绪」与她的自主情绪分开标注，不会算成你对她不好",
        ),
        flow=(
            FlowNode("src", "安静累计相处素材"),
            FlowNode("gate", "双门槛 先到先写"),
            FlowNode("proc", "小模型中性口吻成文"),
            FlowNode("out", "「我的日记」只给你看"),
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
        "deps": list(spec.depends),
        "config_keys": config_keys,
        "llm": spec.llm,
        "tools": list(spec.tools),
    }
