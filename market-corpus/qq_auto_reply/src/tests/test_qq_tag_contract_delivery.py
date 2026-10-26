"""标签**块内/块外**与**投递降级**的契约：两条都曾静默把内容弄丢或漏给用户。

两处缺陷（都来自一次提示词⇄解析器审计，均已实测复现）：

1. **`<ark>` 内容泄漏**：Ark 卡片没有投递实现（`reply_delivery_node` 的
   `if block.ark:` 只记一条 warning）。模型按开放平台格式段写出块外的
   `<ark title="…" desc="…">正文</ark>` 时，标签壳不在清洗白名单里 →
   **整段原始 XML 原样发到群里**。这是泄漏，不是丢弃。

2. **（已修复）`open_platform` + `neko_scene` 组合下解析整段被跳过**：格式段按平台选
   （`session_instruction_service.py:388`，`is_open_plat` 优先），解析却只按
   `strategy_mode == "neko_dynamic"` 开门。该组合下提示词教了整套标签、
   解析器一个都不认 → `<at>123456</at>` 变成裸数字 `123456`（不是 @）、
   `<reply>114514</reply>` 变成裸 ID、`<sticker>5</sticker>` 只剩数字。
   后来判据改成镜像提示词那一处；**再后来 neko_scene 本身被合并掉**
   （用户决定：只保留动态注意力策略），解析门控如今只剩「有没有正文」这一个条件，
   「教了标签却不认」在结构上不可能再出现。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.pipeline_models import QQDeliveryPlan, QQMessageBlock
from plugin.plugins.qq_auto_reply.reply_delivery_node import QQReplyDeliveryNode
from plugin.plugins.qq_auto_reply.reply_postprocess_node import QQReplyPostprocessNode

# ── 1. <ark> 不得把原始 XML 发给用户 ─────────────────────────────────

class _RecordingQQClient:
    """记录实际发出去的载荷；needs_attention=False 模拟开放平台。

    方法名按 `reply_delivery_node` 真正调用到的那些（`send_group_message` /
    `send_group_message_segments` / `send_message` / `send_group_poke` …）。
    """

    def __init__(self, *, needs_attention: bool = False) -> None:
        self.needs_attention = needs_attention
        self.sent: list[tuple[str, str, str]] = []
        self.reactions: list[tuple[str, str]] = []

    async def send_group_message(self, group_id, text, **kw):
        self.sent.append(("text", str(group_id), str(text)))
        return {"status": "ok"}

    async def send_message(self, target_id, text, **kw):
        self.sent.append(("text", str(target_id), str(text)))
        return {"status": "ok"}

    async def send_group_message_segments(self, group_id, segments, **kw):
        for seg in segments:
            data = seg.get("data", {}) if isinstance(seg, dict) else {}
            self.sent.append((str(seg.get("type", "")), str(group_id), str(data.get("text", ""))))
        return {"status": "ok"}

    async def send_group_poke(self, group_id, user_id):
        self.sent.append(("poke", str(group_id), str(user_id)))
        return {"status": "ok"}

    async def send_group_image(self, group_id, file, **kw):
        self.sent.append(("sticker", str(group_id), str(file)))
        return {"status": "ok"}

    async def send_group_record(self, group_id, file, **kw):
        self.sent.append(("record", str(group_id), str(file)))
        return {"status": "ok"}

    async def send_private_record(self, user_id, file, **kw):
        self.sent.append(("record", str(user_id), str(file)))
        return {"status": "ok"}

    async def set_msg_emoji_like(self, message_id, emoji_id):
        self.reactions.append((str(message_id), str(emoji_id)))
        return {"status": "ok"}


def _delivery_node(*, needs_attention: bool = False):
    client = _RecordingQQClient(needs_attention=needs_attention)

    async def _fake_synth(text):
        return (f"file://voice-{len(str(text))}.wav", None)

    plugin = SimpleNamespace(
        qq_client=client,
        voice_reply_service=SimpleNamespace(synthesize_reply_voice_file=_fake_synth),
        logger=SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None),
        _emit_log=lambda *a, **k: None,
        _mask_token=lambda t: t,
        _get_reply_mode=lambda: "text",
        i18n=SimpleNamespace(t=lambda key, default=None: default or key),
    )
    return QQReplyDeliveryNode(plugin), client


def _plan(text: str, *, is_group: bool = True) -> QQDeliveryPlan:
    return QQDeliveryPlan(
        target_type="group" if is_group else "private",
        target_id="1048307485" if is_group else "820040531",
        blocks=[QQMessageBlock(text=text)],
    )


def test_ark_xml_is_never_sent_as_literal_text():
    """块外 `<ark>` 的标签壳必须被剥掉，正文留下 —— 绝不能把 XML 发给用户。"""
    node, client = _delivery_node()
    payload = '<ark title="运行状态" desc="已在线3小时">当前一切正常</ark>'
    asyncio.run(node.deliver(_plan(payload)))

    assert client.sent, "什么都没发出去，测试前提不成立"
    _kind, _target, sent = client.sent[0]
    assert "<ark" not in sent and "</ark>" not in sent, (
        f"原始 XML 泄漏给用户了: {sent!r}"
    )
    assert "当前一切正常" in sent, "正文被一起删掉了（应只剥标签壳）"


def test_other_template_tags_are_still_stripped():
    """同一条兜底清洗对其它标签仍然有效（防回归）。"""
    node, client = _delivery_node()
    asyncio.run(node.deliver(_plan("<text>正文</text><feeling>calm</feeling>")))
    _kind, _target, sent = client.sent[0]
    for tag in ("<text>", "</text>", "<feeling>", "</feeling>"):
        assert tag not in sent, f"{tag} 没被剥掉: {sent!r}"


# ── 2. 解析门控必须与"提示词有没有教标签"一致 ────────────────────────

def _postprocess_node(*, needs_attention: bool):
    plugin = SimpleNamespace(
        _emit_log=lambda *a, **k: None,
        _sanitize_generated_reply=lambda text: text,
        qq_client=SimpleNamespace(needs_attention=needs_attention),
        i18n=SimpleNamespace(t=lambda key, default=None: default or key),
    )
    return QQReplyPostprocessNode(plugin)


def _finalize(*, needs_attention: bool, raw: str):
    node = _postprocess_node(needs_attention=needs_attention)
    context = SimpleNamespace(
        ephemeral_session=False, force_reply=False, permission_level="trusted",
    )
    return asyncio.run(node.finalize(context, SimpleNamespace(reply_text=raw)))


OPEN_PLAT_STYLE_OUTPUT = "<at>820040531</at> 收到~ <reply>114514</reply>"


def test_open_platform_parses_tags():
    """开放平台：标签必须被解析（提示词教了它们）。"""
    outcome = _finalize(needs_attention=False, raw=OPEN_PLAT_STYLE_OUTPUT)
    assert outcome.blocks, "没有解析出任何块 —— 标签会退化成裸文本"
    block = outcome.blocks[0]
    assert block.at_user == "820040531", "@ 没解析出来，用户会看到裸 QQ 号"
    assert block.reply_to == "114514", "引用没解析出来，用户会看到裸消息 ID"


def test_napcat_also_parses_tags():
    """NapCat 同样解析：模式合并后唯一策略就是动态注意力策略，它教标签也认标签。

    历史上这里有一条「NapCat + neko_scene 不解析」的防回归断言 —— 那个组合已经
    不存在（neko_scene 被合并掉），所以断言反过来：现在必须解析。
    """
    outcome = _finalize(needs_attention=True, raw=OPEN_PLAT_STYLE_OUTPUT)
    assert outcome.blocks, "NapCat 下标签必须被解析（提示词教了它们）"
    assert outcome.blocks[0].at_user == "820040531"


# ── 3. `<record>` 与 `<text>` 同块：两者都要发 ───────────────────────
#
# 提示词明说 `<record>` 可以「和 `<text>` 组合」，而投递层以前在 record 分支直接
# `continue` —— 那段文字永远发不出去，用户听到语音、看不到文字，历史行里存的却是文字。
# 使用者确认：**改投递层让两者都发**（顺序与 `<sticker>` 那条一致：先文字后语音）。

def _record_plan(*, text: str = "", record: str = "", keyboard: str = ""):
    return QQDeliveryPlan(
        target_type="group",
        target_id="1048307485",
        blocks=[QQMessageBlock(text=text, record=record, keyboard=keyboard)],
    )


def test_record_block_with_text_sends_both_in_order():
    """同块 text + record：先发文字、再发语音，两个都不能丢。"""
    node, client = _delivery_node()
    result = asyncio.run(
        node.deliver(_record_plan(text="哈哈哈", record="语音内容"))
    )

    kinds = [kind for kind, _target, _payload in client.sent]
    assert kinds == ["text", "record"], (
        f"同块的文字与语音没有都发出去（实际顺序 {kinds}）—— "
        f"使用者要求两者都发、先文字后语音"
    )
    assert client.sent[0][2] == "哈哈哈"
    assert result is not None and result.delivered, "两者都发出去了却报未投递"


def test_pure_record_block_is_unchanged():
    """纯语音块行为不变（只有语音、没有多余的空文本消息）。"""
    node, client = _delivery_node()
    asyncio.run(node.deliver(_record_plan(record="只有语音")))

    kinds = [kind for kind, _target, _payload in client.sent]
    assert kinds == ["record"], f"纯语音块发多了东西: {client.sent!r}"


def test_record_block_does_not_leak_keyboard_labels_into_text():
    """record 块不把 keyboard 文案并进正文（与 delivered_blocks_text 的排除一致）。"""
    node, client = _delivery_node()
    asyncio.run(node.deliver(_record_plan(text="正文", record="语音", keyboard="A|B")))

    text_payloads = [p for kind, _t, p in client.sent if kind == "text"]
    assert text_payloads == ["正文"], (
        f"keyboard 文案被并进了 record 块的正文: {text_payloads!r}"
    )


def test_delivered_blocks_text_counts_both_halves():
    """记忆/mention 侧必须同时记下文字与语音文本 —— 两段都真的送到用户面前了。"""
    from plugin.plugins.qq_auto_reply.pipeline_models import delivered_blocks_text

    got = delivered_blocks_text([QQMessageBlock(text="哈哈哈", record="语音内容")])
    assert "哈哈哈" in got and "语音内容" in got, (
        f"只记了一半，digest 会丢掉真实说过的话: {got!r}"
    )


def test_record_plus_text_is_no_longer_treated_as_undelivered():
    """投递层两者都发之后，"文字+语音同块"不再是"历史行没送出去"的形状。

    以前 `_primary_row_superseded` 把这个形状判成 superseded，于是整轮被标成未投递、
    那些**真的说出去的话**被排除出 digest。留着这条判断会把已正常送达的轮次误标。
    """
    from plugin.plugins.qq_auto_reply.reply_pipeline import QQReplyPipelineRunner

    outcome = SimpleNamespace(
        used_fallback=False, used_default_message=False, raw_reply_text="",
    )
    plan = _record_plan(text="哈哈哈", record="语音内容")
    assert QQReplyPipelineRunner._primary_row_superseded(outcome, plan) is False, (
        "文字+语音同块仍被判成未投递 —— 会把它从 digest 里丢掉"
    )

    # 默认回复替换了非空正文这条形状必须保留
    replaced = SimpleNamespace(
        used_fallback=False, used_default_message=True, raw_reply_text="原本要说的话",
    )
    assert QQReplyPipelineRunner._primary_row_superseded(replaced, plan) is True, (
        "「默认回复替换了非空正文」这条形状被误删了"
    )


# ── 4. `<emoji>` 表情反应：贴到**对方那条消息**上 ────────────────────
#
# 使用者确认语义就是「贴表情到对方消息上」（反应），不是把表情当消息发出去。
# 现状核对：`emoji_reaction_id` 解析出来**没有任何消费方**；`_vendor` 里有
# `set_msg_emoji_like` 但插件侧从没调用过。所以以前模型照提示词做也零效果。

def test_reaction_is_attached_to_the_triggering_message():
    """反应必须打在触发本轮的那条消息上，且**不**当成普通消息发出去。"""
    node, client = _delivery_node(needs_attention=True)  # NapCat
    ok = asyncio.run(node.send_emoji_reaction("123456", "277"))

    assert ok, "反应发送未被确认"
    assert client.reactions == [("123456", "277")], (
        f"反应没有打在正确的消息上: {client.reactions!r}"
    )
    assert not client.sent, (
        f"反应被当成普通消息发出去了（提示词说的是贴表情，不是发一条表情）: {client.sent!r}"
    )


def test_reaction_skips_cleanly_when_there_is_no_target_message():
    """合成轮（回溯补回/破冰）没有具体消息可贴 —— 跳过即可，不能抛。"""
    node, client = _delivery_node(needs_attention=True)
    assert asyncio.run(node.send_emoji_reaction("", "277")) is False
    assert client.reactions == [], "没有目标消息却发了反应"


def test_reaction_failure_is_logged_not_raised():
    """反应失败只降级成日志：它是装饰，不该让整轮回复失败。"""
    node, client = _delivery_node(needs_attention=True)

    async def _boom(message_id, emoji_id):
        raise RuntimeError("napcat 不支持")

    client.set_msg_emoji_like = _boom
    assert asyncio.run(node.send_emoji_reaction("123456", "277")) is False


def test_unconfirmed_reaction_is_not_reported_as_success():
    """开放平台的 `set_msg_emoji_like` 是返回 `{}` 的空桩 —— 不能算成功。"""
    node, client = _delivery_node(needs_attention=False)  # 开放平台

    async def _stub(message_id, emoji_id):
        return {}

    client.set_msg_emoji_like = _stub
    assert asyncio.run(node.send_emoji_reaction("123456", "277")) is False, (
        "空桩返回值被当成了发送成功"
    )


def test_reaction_only_output_is_not_treated_as_silence():
    """只贴一个表情、不发文字，是合法回复 —— 不能判成 `llm_skip`。"""
    outcome = _finalize(
        needs_attention=True, raw="<emoji>277</emoji>"
    )
    assert outcome.emoji_reaction_id == "277", "反应 id 没解析出来"
    assert outcome.postprocess_reason != "llm_skip", (
        "只发反应的输出被判成了「不说话」—— 反应就永远发不出去了"
    )
    assert not outcome.reply_text, "反应不该变成一条文本消息"


def test_pipeline_targets_the_triggering_message():
    """接线：`_run_delivery` 必须把**触发本轮的那条消息 id** 交给反应。

    这一条用结构断言而不是整桩 `_run_delivery`：那个函数还管缓冲/冷却/记忆打标，
    为它造一整套桩会让测试比被测逻辑还复杂；而这里要钉的恰恰是"传的是哪个 id"
    这一件事（传错就等于给无关的消息贴表情）。
    """
    import ast
    import pathlib
    import re

    import plugin.plugins.qq_auto_reply.reply_pipeline as pipeline_module

    tree = ast.parse(pathlib.Path(pipeline_module.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send_emoji_reaction"
    ]
    assert len(calls) == 1, f"预期只有一处反应接线，实际 {len(calls)} 处"

    call = calls[0]
    assert len(call.args) == 2, "反应调用应传 (message_id, emoji_id)"
    assert ast.unparse(call.args[1]).find("emoji_reaction_id") != -1, (
        "反应传的不是解析出来的 emoji_reaction_id"
    )

    # 第一个实参可能是个中间变量 —— 顺着它的赋值往下查，别只认字面写法
    target_src = ast.unparse(call.args[0])
    if not re.search(r"current_message_id|quoted_message_id", target_src):
        assert isinstance(call.args[0], ast.Name), (
            f"反应的目标消息既不是触发消息、也不是简单变量: {target_src!r}"
        )
        name = call.args[0].id
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            )
        ]
        assert assignments, f"找不到 {name} 的赋值，无法确认它来自哪条消息"
        sources = " ".join(ast.unparse(a.value) for a in assignments)
        assert re.search(r"current_message_id|quoted_message_id", sources), (
            f"反应的目标消息取的不是触发本轮的消息（{name} = {sources!r}）"
        )


