from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(slots=True)
class QQPipelineStageTrace:
    stage: str
    status: str
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ── source_kind 的单一真相表 ─────────────────────────────────────────
#
# 这些常量是**全部真实写者**（`source_kind=` 的赋值点）。任何一处新增来源都必须
# 同时决定它属于下面哪一类，否则就会重演本次修掉的两个洞。
#
#: 该轮是**真实的人在说话**：sender 就是发言人本人。
KIND_INCOMING = "incoming"
KIND_INCOMING_PRIVATE = "incoming_private"
KIND_INCOMING_GROUP = "incoming_group"
KIND_RAPID_FIRE = "rapid_fire_flush"

#: 该轮的 sender 只是**名义发言人**，prompt 文本不是这个人说的话。
#: 主动搭话的控制指令、缓冲合并、回溯补回、入群通知、禁言反应都属此类。
#:
#: 原来还有一个 `KIND_PROACTIVE_SPEECH = "proactive_speech"`（**冷场破冰**那一轮）。
#: 主动破冰整套已于 2026-09-29 删除（使用者口径「干脆不要这个先」），它随之成为
#: **零生产者**的死常量；看门狗 `test_no_declared_kind_is_dead` 会当场报出来，故一并删除。
KIND_PROACTIVE_PRIVATE = "proactive_private"
KIND_PROACTIVE_GROUP = "proactive_group"
KIND_RETROACTIVE_REVIEW = "retroactive_review"
KIND_GROUP_JOIN_NOTICE = "group_join_notice"
#: 群里**别人**被禁言/解禁 → 她按"正在和她对话的人"的判断说一句（2026-09-29 使用者口径：
#: 「先用『他不是正在和她对话的人 → 不反应』判断，再生成」）。sender 同样是名义发言人：
#: `_synthetic_source` 里放的这个值，`user_id` 是事件主角（被禁言的人），prompt 文本是
#: 我们写的事件描述，**不是他这一轮说的话**。
KIND_GROUP_BAN_NOTICE = "group_ban_notice"
#: 异步插件任务的结果回投（她先前答应过"结果出来告诉你"，后台轮询到结果后由
#: `plugin_tool_followup_service` 合成的那一轮）。sender 同样是**名义发言人**：
#: 群聊里借管理员的身份，私聊里就是收件人本人。
KIND_PLUGIN_TOOL_RESULT = "plugin_tool_result"

#: 合成来源：其 sender 是名义发言人而非真实说话者。
#:
#: 写侧（不入 participant bucket）、读侧（不召回该成员的 scoped 记忆）、mention
#: 计数必须用**同一份**判据，否则一处漏掉就等于用别人的私人事实去生成公开发言。
#:
#: ⚠️ 本集合曾经漏掉 `proactive_private` / `proactive_group`（主动发言把 admin
#: 当 sender，于是**管理员在本群的成员域画像被注入一条公开发到全群的回复**，
#: 见 `runtime_ops_service.py` 的两个 send 入口），同时含一个**零生产者**的
#: `buffer_delayed`。两类漂移（漏真实生产者 / 留死常量）都是靠人工同步集合与
#: 赋值点造成的，现在由 `tests/test_qq_source_kind_sets.py` 的看门狗盯着。
SYNTHETIC_SOURCE_KINDS = frozenset({
    KIND_PROACTIVE_PRIVATE,
    KIND_PROACTIVE_GROUP,
    KIND_RAPID_FIRE,
    KIND_RETROACTIVE_REVIEW,
    KIND_GROUP_JOIN_NOTICE,
    KIND_GROUP_BAN_NOTICE,
    KIND_PLUGIN_TOOL_RESULT,
})

#: 这些来源**本身已经是缓冲投递链路的一环**，不得再被投进 reply_buffer
#: （否则会自我延迟/自我合并）。与 `SYNTHETIC_SOURCE_KINDS` 是**不同的判据**：
#: 入群通知是合成轮，但它走正常投递、不过缓冲；而主动发言两者都是。
#: 曾经这里是 `reply_pipeline` 里内联的第三个元组，于是同样漏了 proactive_group。
BUFFER_INTERNAL_SOURCE_KINDS = frozenset({
    KIND_RAPID_FIRE,
    KIND_PROACTIVE_PRIVATE,
    KIND_PROACTIVE_GROUP,
    # 结果回投是一条**到点就该发**的通知：它已经在后台等过一轮了，再进缓冲
    # 就等于"等结果"之后又随机延迟并可能和别的草稿合并。
    KIND_PLUGIN_TOOL_RESULT,
})


def delivered_blocks_text(blocks) -> str:
    """All user-visible text of a plan, not just the first block.

    A `<msg>` reply can span several blocks; postprocess keeps only the
    first one in `reply_text`. Anything disclosed in a later text/voice
    block would otherwise never bump its scoped mention counter and never
    reach anti-repeat suppression."""
    parts: list[str] = []
    for block in blocks or []:
        # 与投递侧同口径：同块的 record 与 text **两者都发**（先文字、再语音），
        # 所以两段都要记进记忆与 mention 计数。以前只记 record，是因为那会儿
        # 文字根本发不出去（投递侧 record 分支直接 continue）。
        record = str(getattr(block, "record", "") or "").strip()
        for value in (str(getattr(block, "text", "") or "").strip(), record):
            if value:
                parts.append(value)
        # 选项文案在**文本块**上才会送到用户面前（开放平台渲染成按钮，
        # NapCat/私聊把它并进正文，语音把它念出来）。record 块投递时不把
        # keyboard 交给文本分支（见 reply_delivery_node 的 record 分支），
        # 所以它根本不会渲染——记下来等于让没人看过的选项进记忆与 mention 计数。
        if not record:
            labels = " / ".join(
                part.strip()
                for part in str(getattr(block, "keyboard", "") or "").split("|")
                if part.strip()
            )
            if labels:
                parts.append(labels)
    return "\n".join(parts)


def is_synthetic_source(source_kind: str | None) -> bool:
    """True when the turn's nominal sender did not actually say anything."""
    return str(source_kind or "") in SYNTHETIC_SOURCE_KINDS


def backlog_sender_label(item: dict, *, default: str = "群友") -> str:
    """一条 backlog 记录里发言人的显示名。

    **键名只能写在这里**。backlog 落盘的键是 ``sender_name``（见
    ``QQBacklogMessage.to_dict`` 与真实 ``backlog_state.json``），而另外几条路径
    （喂给插件的消息字典）用的是 ``sender_nickname``。三处读点各自硬编码键名时，
    两处写成了 ``sender_nickname`` —— 对 backlog 记录永远取不到，于是**全部回落到
    QQ 号**：转发卡片、回溯补回摘要里显示的都是数字而不是昵称。
    """
    for key in ("sender_name", "sender_nickname"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return str(item.get("sender_id") or "").strip() or default


@dataclass(slots=True)
class QQReplyRequest:
    message_text: str
    sender_id: str
    attachments: list[dict[str, Any]] | None = None
    is_group: bool = False
    group_id: Optional[str] = None
    user_nickname: Optional[str] = None
    is_at_bot: bool = False
    is_reply_to_bot: bool = False
    source_kind: str = "incoming"
    use_memory_context: Optional[bool] = None
    persist_memory: Optional[bool] = None
    ephemeral_session: bool = False
    group_facing: bool = False
    group_scene_mode: str = ""
    current_message_id: str = ""
    quoted_message_id: str = ""
    mentioned_user_ids: list[str] = field(default_factory=list)
    mentions_other_user: bool = False
    mentions_all: bool = False
    #: 「谁在跟谁说话」的结论（`addressing.AddresseeVerdict` 的三个字段）。
    #: 空 `addressee_kind` = **没有结论**（开放平台通道 / 合成轮 / 旁路调用），
    #: 提示词层据此退回改动前的 6 个标签 —— 刻意不是"另有一份实现"。
    #: 只带 kind/target/evidence：`first_at`（段序）与 `matched_name` 只服务减分与
    #: 硬门控，在门控内部就用完了，进请求只会多一份没消费者的数据。
    addressee_kind: str = ""
    addressee_target: str = ""
    addressee_evidence: str = ""
    reply_message_id: str = ""
    reply_context: str = ""
    at_user_id: str = ""
    fallback_to_text_on_voice_failure: bool = True
    # 内嵌合成轮（ack / 强制总结 / 缓冲汇总）继承缓冲里那些草稿的授权
    # 依赖：合成轮自己的 prompt 是干净的（快照为空），但它原样引用了
    # 记忆派生的旧草稿，撤销必须能作用到它。
    inherited_consent_snapshot: dict[str, bool] = field(default_factory=dict)
    permission_level_override: str | None = None
    force_reply: bool = False
    forward_sub_count: int = 0
    # 接收边界的 member 记忆政策快照（None=旁路调用者，build 内回退实时
    # 读）：handler 排队期间 OFF->ON 不得让收到时无授权的发言被收集。
    member_memory_at_receipt: bool | None = None
    # 群成员权限的接收边界快照。handler 排队/生成期间的升降权不得
    # 追溯改变已经说出的消息是否具有 owner trust 信号权限。
    group_speaker_permission_level_at_receipt: str | None = None
    # 接收边界的通道观测快照（"onebot" / "open"）。纯诊断：只用于碰撞探测
    # 与运维诊断，绝不参与任何键、账本分区、bind 判据或权限判定。
    speaker_channel_at_receipt: str | None = None
    # 接收边界的私聊 participant 记忆政策快照（语义同上，作用于非 admin
    # 私聊轮；admin 私聊与群轮忽略它）。
    participant_memory_at_receipt: bool | None = None
    # 私聊权限的接收边界快照。权限编辑可能发生在 handler 排队期间；记忆
    # 域必须仍按消息到达时的 admin/participant 身份选择。
    private_permission_level_at_receipt: str | None = None


@dataclass(slots=True)
class QQReplyDecision:
    action: str
    permission_level: str
    relay_probability: float | None = None
    attention_enabled: bool = False
    attention_score: float | None = None
    attention_focus_group_id: str = ""
    attention_focus_score: float | None = None
    attention_multiplier: float | None = None
    attention_gate_reason: str = ""


@dataclass(slots=True)
class QQInstructionBundle:
    system_prompt: str
    memory_context_used: bool
    core_memory_text: str
    scene_mode: str
    # 跨群上下文段原文（未注入时为空）：consent 是运行时开关，构建后到
    # 生成前的 await 窗口里可能被关掉/回滚，届时按原文从 prompt 中摘除。
    cross_group_section: str = ""
    # 活跃会话清单里披露了其他会话时的原文：与话题段同为跨群内容，撤销时
    # 一并撤除、同样计入授权依赖（私聊轮的话题段恒为空，此前那条路径根本
    # 没有可撤的依赖）。
    cross_session_section: str = ""
    # core memory 段是否含 participant 域内容：member 开关在后续 await
    # 窗口里被关掉时，该段要按同样方式撤除。
    used_member_subject: bool = False


@dataclass(slots=True)
class QQReplyContext:
    message: str
    attachments: list[dict[str, Any]] | None
    permission_level: str
    sender_id: str
    is_group: bool
    group_id: str | None
    user_nickname: str | None
    use_memory_context: bool
    persist_memory: bool
    ephemeral_session: bool
    group_facing: bool
    group_scene_mode: str
    scene_mode: str
    master_name: str
    her_name: str
    user_title: str
    character_prompt: str
    character_card_fields: dict[str, Any]
    prompt_message: str
    system_prompt: str
    memory_context_used: bool
    core_memory_text: str
    recalled_memory_text: str
    recalled_memory_used: bool
    login_status: str
    login_self_id: str | None
    login_nickname: str | None
    current_message_id: str = ""
    force_reply: bool = False
    source_kind: str = ""
    # 轮次构建时刻的 group_member_memory_enabled 快照：成员发言入 bucket
    # 与否绑定发言时刻的授权状态——生成期间才切 ON 的轮不得回溯收集。
    member_memory_enabled: bool = False
    group_speaker_permission_level_at_receipt: str | None = None
    speaker_channel_at_receipt: str | None = None
    # 本轮是否为私聊 participant 记忆轮（非 admin 私聊 + 接收时刻政策
    # ON）：读写都以对方的 participant 域为界，绝不落入 legacy 私聊主人
    # 语料（bridge 侧 subjects=None 的语义）。
    participant_memory_enabled: bool = False
    # 本轮按接收时权限选择的持久化域；None 表示本轮没有私聊记忆授权。
    private_memory_mode: str | None = None
    private_permission_level_at_receipt: str | None = None
    # 本轮 prompt 里的跨群段原文（未注入时为空）：生成前在会话锁内复检
    # 授权，撤销时按原文摘除。
    cross_group_section: str = ""
    # 同上，活跃会话清单段。
    cross_session_section: str = ""
    # core memory 段是否含 participant 域：member 授权在生成前被撤销时
    # 该段（及混合域召回）要一并撤除。
    used_member_subject: bool = False
    # 本轮上下文的唯一标识：投递钩子的幂等键。绝不能用 id(context)——
    # CPython 会把刚释放的同尺寸对象原样发回，下一轮的 context 常常拿到
    # 同一地址，幂等扫描会把新一轮的行误判成"已经补过了"。
    turn_uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    # 生成时刻的授权依赖快照：直投路径在真正发出去之前再比一次（buffer
    # 路径由 PendingReply.consent_snapshot 负责），"生成完成→发送"之间
    # 的窗口也不得漏掉撤销。None=还没生成过（读当前设置兜底）；空 dict
    # 是有意义的值——本轮没用任何记忆，撤销与它无关，不能当成"没快照"
    # 而去采样当前开关，否则一条与记忆无关的草稿会被无谓丢弃。
    consent_snapshot: dict[str, bool] | None = None
    traces: list[QQPipelineStageTrace] = field(default_factory=list)


@dataclass(slots=True)
class QQModelResult:
    reply_text: str | None = None
    # Exact visible prefix emitted before the tool-call turn. Postprocess uses
    # this structural boundary instead of guessing from literal <msg> text.
    pre_tool_text: str = ""
    source: str = "none"
    used_fallback: bool = False
    timed_out: bool = False
    allow_fallback: bool = False
    fallback_reason: str = ""
    traces: list[QQPipelineStageTrace] = field(default_factory=list)
    # Exact session-history row produced by this generation. Delivery can
    # finish after a later turn has overwritten the session-wide current row.
    history_ai_row: Any = None


@dataclass(slots=True)
class QQRelayPlan:
    source_type: str
    source_id: str
    sender_id: str
    original_message: str
    relay_text: str
    relay_probability: float
    target_admin_qq: str


@dataclass(slots=True)
class QQRelayResult:
    relayed: bool
    source_type: str
    source_id: str
    sender_id: str
    relay_text: str | None


@dataclass(slots=True)
class QQMessageBlock:
    """一个 `<msg>...</msg>` 块：容器形态借自 KiraAI，字段集合是本插件自己的。

    上游的子标签由 `TagSet` 动态注册（没有固定的块 dataclass），`keyboard`/`ark` 等
    字段上游并不存在 —— 所以这里的 "KiraAI-style" 只指容器形态，不指字段级等价。
    """
    text: str = ""
    emoji: str = ""        # QQ 表情 ID（如 "277"）
    at_user: str = ""       # @的QQ号
    reply_to: str = ""      # 引用的消息ID
    sticker: str = ""       # 表情包 ID
    poke: str = ""          # 戳一戳目标 QQ
    record: str = ""        # <record> 语音文本
    keyboard: str = ""      # 按钮文本
    ark: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class QQDeliveryPlan:
    target_type: str
    target_id: str
    blocks: list[QQMessageBlock] = field(default_factory=list)
    fallback_to_text_on_voice_failure: bool = True


@dataclass(slots=True)
class QQDeliveryResult:
    delivered: bool
    target_type: str
    target_id: str
    reply_text: str | None


@dataclass(slots=True)
class QQReplyOutcome:
    action: str
    reply_text: str | None = None
    used_default_message: bool = False
    # True when the reply came from the direct-LLM fallback: the shared
    # session history has NO ai row for this turn, so the buffer must not
    # mark the previous (delivered) reply as an undelivered draft.
    used_fallback: bool = False
    raw_reply_text: str | None = None
    pre_tool_text: str = ""
    #: 真实 tool 边界之后的最终段（原名 wait_directive_text，随 <wait> 功能一并改名）：
    #: buffer 用它判断"是否空回复"，并作为汇总的输入。
    post_tool_text: str | None = None
    postprocess_reason: str = ""
    blocks: list[QQMessageBlock] = field(default_factory=list)
    emoji_reaction_id: str = ""
    feeling: str = ""                 # <feeling> 标签提取的情绪
    forward_content: str = ""
    forward_target: str = ""
    forward_count: int = 0              # <forward count="N">，0=默认20条
    forward_mark: bool = False          # <mark/> 标记转发起点
    relay_plan: QQRelayPlan | None = None
    relay_result: QQRelayResult | None = None
    delivery_plan: QQDeliveryPlan | None = None
    delivery_result: QQDeliveryResult | None = None
    traces: list[QQPipelineStageTrace] = field(default_factory=list)
    history_ai_row: Any = None
