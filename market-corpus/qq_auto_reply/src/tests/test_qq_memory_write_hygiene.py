"""记忆写入侧：猫娘自己的行必须去掉插件内部标记（用户看到的是剥掉标记后的内文）。

实测证据（2026-09-25 真实落盘）：
    recent.json     含 `<msg>`/`<feeling>`/`<sticker>` 等 19 处
    outbox.ndjson   173 处
    facts / reflections / persona —— 0 处（提取器重写文本，所以持久层恰好是干净的）

所以这是**卫生问题**：近窗/续接文本带着内部控件标记，可能被喂回 prompt、也会污染
任何直接渲染记忆文本的界面。持久层干净是提取器的功劳，不是写入侧的保证。

只清 `ai` 行：真人可以自己打 `<msg>`，他的话必须逐字保留。
"""

from __future__ import annotations

from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.session_memory_service import QQSessionMemoryService


def _row(msg_type: str, text: str):
    return SimpleNamespace(type=msg_type, content=text)


def _convert(rows, **kw):
    svc = QQSessionMemoryService.__new__(QQSessionMemoryService)
    return svc.conversation_slice_to_memory_messages(rows, **kw)


def _texts(messages):
    return [
        "".join(p.get("text", "") for p in m["content"] if isinstance(p, dict))
        for m in messages
    ]


def test_assistant_row_loses_the_internal_xml_tags():
    out = _convert([_row("ai", "<feeling>playful</feeling><msg><text>怎么啦，宅久？</text></msg>")])
    assert _texts(out) == ["怎么啦，宅久？"], _texts(out)
    assert out[0]["role"] == "assistant"


def test_real_world_samples_from_the_live_store():
    """三条都是真实落盘里抓到的原文。"""
    out = _convert([
        _row("ai", "<feeling>playful</feeling><msg><text>宅久是想要妈妈抱抱吗？</text></msg><sticker>11</sticker>"),
        _row("ai", "<msg><feeling>playful</feeling>嘿嘿，被发现啦，谁让我是你的专属小话痨呢~</msg>"),
        _row("ai", "<msg><text>来啦来啦</text></msg><msg><sticker>26</sticker></msg>"),
    ])
    assert _texts(out) == [
        "宅久是想要妈妈抱抱吗？",
        "嘿嘿，被发现啦，谁让我是你的专属小话痨呢~",
        "来啦来啦",
    ], _texts(out)


def test_sticker_only_reply_yields_nothing_to_remember():
    """只有表情包的回复：剥掉标记后没有文字，就不该留一条空记忆。"""
    out = _convert([_row("ai", "<msg><sticker>5</sticker></msg>")])
    assert out == []


def test_human_rows_are_preserved_verbatim():
    """真人可能真的在聊 `<msg>` —— 他的话一个字都不许动。"""
    raw = "你看这个 <msg> 标签是啥意思"
    out = _convert([_row("human", raw)])
    assert _texts(out) == [raw], _texts(out)
    assert out[0]["role"] == "user"


def test_markup_inside_a_human_row_is_not_stripped_even_when_it_looks_like_ours():
    raw = "<feeling>calm</feeling> 我照着你说的打了这个"
    out = _convert([_row("human", raw)])
    assert _texts(out) == [raw]


def test_undelivered_ai_rows_are_still_excluded():
    """既有语义不能被这次改动破坏：未投递的草稿仍然不进记忆。"""
    row = _row("ai", "<msg><text>没人看到的话</text></msg>")
    user_data = {"undelivered_draft_rows": [row]}
    out = _convert([row], user_data=user_data)
    assert out == []


def test_human_row_is_kept_while_a_sibling_ai_row_is_cleaned():
    out = _convert([
        _row("human", "今天吃什么"),
        _row("ai", "<feeling>calm</feeling><msg><text>吃小鱼干</text></msg>"),
    ])
    assert _texts(out) == ["今天吃什么", "吃小鱼干"]
    assert [m["role"] for m in out] == ["user", "assistant"]
