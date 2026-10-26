"""记忆写入侧：单条过长要截断（真机实测逼出来的）。

**现象**（2026-09-26，私聊 + 开放平台）：用户把一个 13981 字符的文件发给猫娘，之后
每发一次，**下一轮** system prompt 就涨约 12.7k，三次之后 12.5k → 50.7k。用临时插桩
量到的分段时间线（只记每段长度）：

    13:06:21  total=12550  核心记忆= 4505
    13:06:35  total=12596  核心记忆= 4514   ← 刚收到那个文件
    13:07:01  total=25303  核心记忆=17221   ← +12707，**全在「核心记忆」这一段**

另外 14 段一个字节都没动。链路是：附件正文并进消息 → 会话历史里是一条 human 行 →
**逐字**同步给 Memory Server → 服务端 bootstrap 的"最近对话"块逐字回灌 → 进 prompt。
而且**跨重启存活**（存在服务端），所以每轮都在为同一段文本付费。

这组测试钉的是修法的两条边界：

1. 同步出去的那份要截断（并留痕说明省了多少）；
2. **会话历史本身一个字节都不许动** —— 本轮她还得读得到全文，否则就是拿功能换省钱。
"""

from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.session_memory_service import QQSessionMemoryService

CAP = QQSessionMemoryService.MEMORY_MESSAGE_MAX_CHARS


def _row(msg_type: str, text):
    return SimpleNamespace(type=msg_type, content=text)


def _convert(rows, **kw):
    svc = QQSessionMemoryService.__new__(QQSessionMemoryService)
    return svc.conversation_slice_to_memory_messages(rows, **kw)


def _text(messages, index: int = 0) -> str:
    return "".join(
        part.get("text", "") for part in messages[index]["content"] if isinstance(part, dict)
    )


def test_a_short_message_is_untouched():
    raw = "今天吃什么"
    out = _convert([_row("human", raw)])
    assert _text(out) == raw


def test_a_message_exactly_at_the_cap_gets_no_marker():
    """边界不许差一：正好等于上限时不该冒出"已省略 0 字"这种噪音。"""
    raw = "字" * CAP
    out = _convert([_row("human", raw)])
    assert _text(out) == raw
    assert "省略" not in _text(out)


def test_an_overlong_human_message_is_truncated_with_a_trace():
    raw = "字" * (CAP + 137)
    out = _convert([_row("human", raw)])

    text = _text(out)
    assert text.startswith("字" * CAP)
    assert "已省略后 137 字" in text, text[-60:]
    # 截断后总长只比上限多一点（标记本身很短）
    assert len(text) < CAP + 60


def test_the_session_history_row_is_never_mutated():
    """**关键**：截的是同步出去的那一份，会话历史里那条仍然是全文。

    改错这一处的症状很隐蔽：她本轮就只读得到前 4000 字，但没人会想到是"为了省
    prompt 顺手改坏了读文件"。
    """
    raw = "字" * (CAP + 500)
    row = _row("human", raw)

    out = _convert([row])

    assert _text(out).endswith("已省略后 500 字）")
    assert row.content == raw, "会话历史那一条被就地改短了"
    assert len(row.content) == CAP + 500


def test_a_multimodal_content_list_is_capped_after_joining():
    raw = "字" * (CAP + 10)
    row = _row("human", [{"type": "text", "text": raw}])
    out = _convert([row])
    assert "已省略后 10 字" in _text(out)
    # 原对象里的分片也不许被改
    assert row.content[0]["text"] == raw


def test_an_overlong_assistant_row_is_capped_too():
    """对偶：猫娘自己发出去的长文（比如转述一大段）同样不该整段进记忆。"""
    raw = "<msg><text>" + "字" * (CAP + 3) + "</text></msg>"
    out = _convert([_row("ai", raw)])
    text = _text(out)
    assert out[0]["role"] == "assistant"
    assert "<msg>" not in text
    assert "已省略后 3 字" in text


def test_the_cap_leaves_short_neighbours_alone():
    """一条长的被截，旁边的短的必须逐字保留。"""
    rows = [_row("human", "在吗"), _row("ai", "在的！"), _row("human", "字" * (CAP + 1))]
    out = _convert(rows)
    assert [_text(out, 0), _text(out, 1)] == ["在吗", "在的！"]
    assert "已省略后 1 字" in _text(out, 2)
