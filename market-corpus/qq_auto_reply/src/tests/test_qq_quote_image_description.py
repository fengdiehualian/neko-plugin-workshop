"""引用链里的图片必须真的被"看懂"成文字，否则模型只看到一个 `[图片]`。

**为什么单独钉这个**：`_build_message_chain` 的 image 分支（`enrichment.py`）是引用链
唯一的"看图"入口 —— 引用消息里的图，模型能看到的全部就是这里的产物：

    desc 拿到 → `Text("[Image 描述]")` → 进 `_reply_context` → 拼进 prompt
    desc 空/超时/异常 → `Image(url)` → 渲染成 `[图片]`（**没有内容**）

所以这条分支静静地坏掉时，表现是"猫娘答非所问"而不是报错。此前整个分支**一条测试都没有**
（仓库里只有 file 路径的 VLM 测试 `test_file_fetch_image_goes_to_vlm`），而它用的
`_describe_reply_image` 恰好在免费线上被 400 拦了很久（见 docs/SESSION-HANDOFF.md §4.0t）。

这里钉四件事：

1. 成功：描述进链、**调用时传的是图片 URL**、`repr` 里能看到
2. 描述为空 → 退回 `[图片]`，不崩、后续片段照常进链
3. 描述抛异常 / 超时 → 同上（引用链不许因为一次 VLM 失败整条断掉）
4. 超过 `_MAX_REPLY_DEPTH` 的层级不再调 VLM（递归展开有界，别把整条引用史都跑一遍）
"""

from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import AsyncMock

import plugin.plugins.qq_auto_reply.enrichment as enrichment_module
from plugin.plugins.qq_auto_reply.connector_seam import OneBotClient
from plugin.plugins.qq_auto_reply.enrichment import _MAX_REPLY_DEPTH, QQMessageEnricher
from plugin.plugins.qq_auto_reply.message_chain import Image, Reply, Text

SOURCE = pathlib.Path(enrichment_module.__file__).read_text(encoding="utf-8")

URL = "https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=abc&rkey=xyz"


def _client() -> OneBotClient:
    client = OneBotClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
    client._self_id = "10001"
    return client


def _enricher(client: OneBotClient, describer=None) -> QQMessageEnricher:
    enricher = QQMessageEnricher(client)
    if describer is not None:
        enricher._image_describer = describer
    return enricher


def _image_msg(*, message_id: str = "m1") -> dict:
    return {
        "message_type": "group",
        "group_id": "g1",
        "user_id": "u1",
        "time": 123,
        "message_id": message_id,
        "sender": {"nickname": "小明"},
        "message": [{"type": "image", "data": {"url": URL}}],
    }


def _quoted(inner: dict) -> dict:
    """外层消息：一句文字 + 引用 inner。"""
    return {
        "message_type": "group",
        "group_id": "g1",
        "user_id": "u2",
        "time": 456,
        "message_id": "outer",
        "sender": {"nickname": "小红"},
        "message": [
            {"type": "text", "data": {"text": "这图什么意思"}},
            {"type": "reply", "data": {"id": inner["message_id"]}},
        ],
    }


def test_quoted_image_description_lands_in_the_chain():
    """引用消息里的图：VLM 描述要进链，且调用时拿到的是图片 URL。"""
    client = _client()
    inner = _image_msg()
    client.get_msg = AsyncMock(return_value={"data": inner, "status": "ok"})
    describer = AsyncMock(return_value="一只橘猫在键盘上睡觉")
    enricher = _enricher(client, describer)

    chain = asyncio.run(enricher._build_message_chain(_quoted(inner)))

    reply = next(el for el in chain.elements if isinstance(el, Reply))
    assert "[Image 一只橘猫在键盘上睡觉]" in reply.chain.repr, reply.chain.repr
    describer.assert_awaited_once_with(URL)
    assert "[Image 一只橘猫在键盘上睡觉]" in chain.repr


def test_empty_description_falls_back_to_plain_image():
    """描述为空 → 退回 `[图片]`（模型看不到内容，但链不崩）。"""
    client = _client()
    inner = _image_msg()
    client.get_msg = AsyncMock(return_value={"data": inner, "status": "ok"})
    enricher = _enricher(client, AsyncMock(return_value=""))

    chain = asyncio.run(enricher._build_message_chain(_quoted(inner)))

    reply = next(el for el in chain.elements if isinstance(el, Reply))
    assert isinstance(reply.chain.elements[0], Image)
    assert reply.chain.repr == "[图片]"


def test_describer_failure_does_not_break_the_rest_of_the_chain():
    """描述抛异常 → 退回 `[图片]`，**后面的片段照常进链**。"""
    client = _client()
    inner = _image_msg()
    inner["message"].append({"type": "text", "data": {"text": "后面这句要在"}})
    client.get_msg = AsyncMock(return_value={"data": inner, "status": "ok"})
    enricher = _enricher(client, AsyncMock(side_effect=RuntimeError("boom")))

    chain = asyncio.run(enricher._build_message_chain(_quoted(inner)))

    reply = next(el for el in chain.elements if isinstance(el, Reply))
    assert isinstance(reply.chain.elements[0], Image)
    assert isinstance(reply.chain.elements[1], Text)
    assert reply.chain.elements[1].text == "后面这句要在"


def test_describer_timeout_degrades_to_plain_image():
    """描述超时 → 同样退回 `[图片]`，不把整条引用链的处理卡死。

    （`asyncio.wait_for` 到点抛的就是 `TimeoutError`，所以这里直接抛那个异常；
    真实预算是源码里的 8 秒，由 `test_the_chain_gives_the_describer_a_time_budget` 钉。）
    """
    client = _client()
    inner = _image_msg()
    client.get_msg = AsyncMock(return_value={"data": inner, "status": "ok"})
    enricher = _enricher(client, AsyncMock(side_effect=asyncio.TimeoutError()))

    chain = asyncio.run(enricher._build_message_chain(_quoted(inner)))

    reply = next(el for el in chain.elements if isinstance(el, Reply))
    assert isinstance(reply.chain.elements[0], Image)
    assert reply.chain.repr == "[图片]"


def test_the_chain_gives_the_describer_a_time_budget():
    """引用链必须给看图设超时（实测线上单张约 1.0–2.5 秒，8 秒够用）。

    没有超时的话，一次挂住的 VLM 调用会把整轮回复拖住 —— 而这条链是**串行**的，
    引用里有两三张图就更明显。
    """
    source = SOURCE
    body = source[source.index("async def _build_message_chain"):]
    body = body[: body.index("async def _build_file_element")]
    assert "asyncio.wait_for(self._image_describer(img_url)" in body, "引用链看图没有超时保护"
    assert "timeout=8.0" in body, "引用链看图的超时值变了（8 秒 → ？）"


def test_depth_limit_stops_calling_the_describer():
    """递归深度到顶后不再调 VLM：整条引用史都跑一遍会拖死一轮回复。"""
    client = _client()
    describer = AsyncMock(return_value="不该被调用")
    enricher = _enricher(client, describer)

    chain = asyncio.run(enricher._build_message_chain(
        _image_msg(), depth=_MAX_REPLY_DEPTH + 1,
    ))

    describer.assert_not_awaited()
    assert isinstance(chain.elements[0], Image)


def test_quoted_image_description_is_visible_in_the_log():
    """解析成功要在 UI 日志里留一条，且与主消息那条区分开。

    理由：`_emit_log` 只进 UI 环形缓冲、**不进文件**，所以"没报错"不等于"解析成功"。
    使用者问"引用链能解析图片了嘛"时，唯一能当场回答的就是这条留痕。
    """
    client = _client()
    inner = _image_msg()
    client.get_msg = AsyncMock(return_value={"data": inner, "status": "ok"})
    logs: list[tuple[str, str]] = []
    enricher = _enricher(client, AsyncMock(return_value="一只橘猫在键盘上睡觉"))
    enricher._emit_log = lambda level, msg: logs.append((level, msg))

    asyncio.run(enricher._build_message_chain(_quoted(inner)))

    assert any("引用图描述" in msg and "橘猫" in msg for _lvl, msg in logs), logs


def test_quoted_image_failure_is_visible_in_the_log():
    """失败也要留一条（DEBUG）：表现只是"答非所问"，没有痕就查不到。"""
    client = _client()
    inner = _image_msg()
    client.get_msg = AsyncMock(return_value={"data": inner, "status": "ok"})
    logs: list[tuple[str, str]] = []
    enricher = _enricher(client, AsyncMock(side_effect=RuntimeError("boom")))
    enricher._emit_log = lambda level, msg: logs.append((level, msg))

    asyncio.run(enricher._build_message_chain(_quoted(inner)))

    assert any("引用图描述失败" in msg for _lvl, msg in logs), logs


def test_reply_context_carries_the_description_into_the_prompt():
    """端到端到 prompt 的那一步：`_reply_context` 里能看到描述。

    （`reply_context_node` 会把 `reply_context` 前置拼进 prompt_text，
    所以这一步过了，模型就真的看得到引用的图是什么。）

    ⚠️ 入站消息用**真连接器给的那种形态**（`content` + `raw.message`，没有 `message`
    键）—— 引用 id 也**不手打**，走 `_expand_reply_segments()` 现场识别：这条链上
    曾经断的就是"识别"这一步（见 tests/test_qq_segment_source_shape.py）。
    """
    client = _client()
    inner = _image_msg()
    client.get_msg = AsyncMock(return_value={"data": inner, "status": "ok"})
    segments = [
        {"type": "reply", "data": {"id": "m1"}},
        {"type": "text", "data": {"text": "这图什么意思"}},
    ]
    message = {
        "message_type": "group",
        "group_id": "g1",
        "user_id": "u2",
        "message_id": "outer",
        "content": "这图什么意思[CQ:reply,id=m1]",
        "raw": {"message": segments},
        "sender": {"nickname": "小红"},
    }
    enricher = _enricher(client, AsyncMock(return_value="一只橘猫在键盘上睡觉"))

    reply_ids = enricher._expand_reply_segments(message)
    assert reply_ids == ["m1"], f"引用 id 没识别出来：{reply_ids}"
    message["_pending_reply_ids"] = reply_ids
    asyncio.run(enricher.enrich_message(message))

    context = str(message.get("_reply_context") or "")
    assert "[Image 一只橘猫在键盘上睡觉]" in context, context
