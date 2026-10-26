"""`<msg></msg>` 不是回复：空块必须归零，不能当成"说了话"。

**实测证据**（使用者真实日志）：

    2026-09-23 17:18:12 - [RetroReview] 回溯回复已发送: <msg></msg>

投递层**确实**正确地跳过了空块（什么都不发），但 `outcome` 层面 `reply_text` 仍是
`"<msg></msg>"` 这个真值字符串。于是所有拿
`outcome.action == "reply" and outcome.reply_text` 当"是否回复了"判据的调用点全部误判
（`message_dispatcher.py` 947/953/959/966）：

| 调用点 | 误判后果 |
|---|---|
| `mark_message_reviewed` | 用户那条消息被标成已读 —— 再也不会被回溯补回 |
| `on_reply_sent()` | **注意力被当成"已回复"扣一次**，回复频率计数 +1（其实什么都没发） |
| `[LLM自判]` 日志 | 报"决定回复" |
| 回溯/破冰 | 记成"成功"，破冰不再重试 |

所以修在**解析层**：全空块 ⇒ `blocks=[]` + `reply_text=""` ⇒ 自然落到 `llm_skip`
分支，语义正好是"模型被给了机会但没说话"。这一处修好，上面四个调用点全部自动正确，
不需要各自去打补丁（各自打补丁才是会漂移的写法）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.pipeline_models import QQMessageBlock
from plugin.plugins.qq_auto_reply.reply_delivery_node import QQReplyDeliveryNode
from plugin.plugins.qq_auto_reply.reply_postprocess_node import QQReplyPostprocessNode

DEFAULT_REPLY = "嗯嗯~"


def _node():
    plugin = SimpleNamespace(
        _emit_log=lambda *a, **k: None,
        _sanitize_generated_reply=lambda text: text,
        i18n=SimpleNamespace(t=lambda key, default=None: default or DEFAULT_REPLY),
    )
    return QQReplyPostprocessNode(plugin)


def _finalize(raw: str, *, forced: bool = False):
    node = _node()
    context = SimpleNamespace(
        ephemeral_session=False,
        force_reply=forced,
        permission_level="admin" if forced else "trusted",
    )
    return asyncio.run(node.finalize(context, SimpleNamespace(reply_text=raw)))


def _is_reply(outcome) -> bool:
    """与调用点同口径的判据 —— 这就是全插件四处在用的那个式子。"""
    return bool(outcome.action == "reply" and outcome.reply_text)


# ── 空消息不得被当成回复 ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw",
    [
        "<msg></msg>",
        "<msg>   </msg>",
        "<msg>\n</msg>",
        "<msg></msg><msg></msg>",
        "<msg><text>  </text></msg>",
        # 2026-09-27：只有修饰、没有正文的，同样是"空消息"。
        # 使用者实测截图：引用块在、正文空白 —— QQ 上就是一个空回复。
        "<msg><reply>1252066434</reply></msg>",
        "<msg><at>820040531</at></msg>",
        "<msg><reply>1252066434</reply><at>820040531</at></msg>",
    ],
)
def test_empty_msg_is_not_a_reply(raw):
    outcome = _finalize(raw)
    assert not _is_reply(outcome), (
        f"{raw!r} 被当成了回复（reply_text={outcome.reply_text!r}）—— "
        f"注意力会被扣、用户消息会被误标已读；只有引用没有正文时 QQ 上是个空回复"
    )
    assert outcome.postprocess_reason == "llm_skip", (
        f"空消息应判定为 llm_skip，实际 {outcome.postprocess_reason!r}"
    )


def test_empty_msg_still_carries_the_feeling():
    """空消息 + feeling 必须同时成立：模型可以"不出声地让出焦点"。"""
    outcome = _finalize("<feeling>bored</feeling><msg></msg>")
    assert outcome.feeling == "bored"
    assert not _is_reply(outcome)


def test_forced_empty_msg_falls_back_to_default_reply():
    """强制路径的既有行为不变：空输出仍发默认回复（不是本次要改的东西）。"""
    outcome = _finalize("<msg></msg>", forced=True)
    assert outcome.used_default_message is True
    assert outcome.reply_text == DEFAULT_REPLY


# ── 有内容的回复一律不受影响（防回归）────────────────────────────────

@pytest.mark.parametrize(
    "raw",
    [
        "<msg><text>你好</text></msg>",
        "<msg>裸文本</msg>",
        "<msg><emoji>277</emoji></msg>",
        # 修饰**带上正文**才是回复（引用谁 / @谁 + 说了什么）
        "<msg><reply>12345</reply><text>收到~</text></msg>",
        "<msg><at>820040531</at><text>你说得对</text></msg>",
        # 裸文本跟在修饰后面（`<msg><reply>id</reply>你好</msg>`）也必须有正文
        "<msg><reply>12345</reply>你好</msg>",
        "<msg><poke>820040531</poke></msg>",
        "<msg><sticker>1</sticker></msg>",
        "<msg><record>语音内容</record></msg>",
        "<msg><keyboard>选项A|选项B</keyboard></msg>",
        "<msg><text>正文</text><poke>820040531</poke></msg>",
    ],
)
def test_blocks_with_content_are_still_replies(raw):
    outcome = _finalize(raw)
    assert _is_reply(outcome), f"{raw!r} 被误判成不回复了"


# ── 与投递层同口径（两处判断不能各自漂移）────────────────────────────

def test_block_has_content_agrees_with_delivery_compose_text():
    """`block_has_content` 与投递层的 `_compose_text` 必须对同一批块给出一致结论。

    这两处一旦漂移就会出现"解析说这是回复、投递说没东西可发"（或者反过来），
    正是在 `<msg></msg>` 与 `<msg><reply>id</reply></msg>` 上发生的事。

    `at_user` / `reply_to` 单独出现时为 **False**：它们是修饰，QQ 上渲染出来是
    「空引用」或「干 @ 一下」，都不是一条消息。
    """
    cases = [
        (QQMessageBlock(), False),
        (QQMessageBlock(text="hi"), True),
        (QQMessageBlock(text="   "), False),
        (QQMessageBlock(emoji="277"), True),
        (QQMessageBlock(at_user="820040531"), False),
        (QQMessageBlock(reply_to="12345"), False),
        (QQMessageBlock(reply_to="12345", at_user="820040531"), False),
        (QQMessageBlock(reply_to="12345", text="收到"), True),
        (QQMessageBlock(at_user="820040531", text="你说得对"), True),
        (QQMessageBlock(reply_to="12345", emoji="1"), True),
        (QQMessageBlock(text="hi", emoji="1"), True),
    ]
    for block, expected in cases:
        assert QQReplyPostprocessNode.block_has_content(block) is expected, (
            f"{block!r} 的 block_has_content 判定应为 {expected}"
        )
        assert bool(QQReplyDeliveryNode._compose_text(block)) is expected, (
            f"投递层 _compose_text 对 {block!r} 的判定与 block_has_content 不一致 —— "
            f"两处判据漂移了"
        )


def test_reply_only_block_is_not_sent_as_an_empty_quote():
    """端到端：只有引用的块不会被投递出去（防"空引用"再次上线）。"""
    block = QQMessageBlock(reply_to="1252066434")
    assert QQReplyDeliveryNode._compose_text(block) == "", (
        "只有 <reply> 没有正文时，投递层仍拼出了可发送的文本 —— "
        "QQ 上会显示一个只有引用、没有正文的空回复"
    )
    # 带上正文之后修饰照旧生效（不能把引用功能一起关掉）
    composed = QQReplyDeliveryNode._compose_text(QQMessageBlock(reply_to="1252066434", text="收到~"))
    assert composed.startswith("[CQ:reply,id=1252066434]")
    assert composed.endswith("收到~")


@pytest.mark.parametrize(
    "block",
    [
        QQMessageBlock(poke="820040531"),
        QQMessageBlock(sticker="1"),
        QQMessageBlock(record="语音"),
        QQMessageBlock(keyboard="A|B"),
    ],
)
def test_decoration_blocks_count_as_content(block):
    """装饰块（poke/表情包/语音/按钮）走投递层自己的分支，也算"有东西发"。"""
    assert QQReplyPostprocessNode.block_has_content(block) is True
