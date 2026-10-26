"""入站消息的"段"从哪读 —— 用**真连接器**归一化出来的字典钉死。

**为什么要有这条（2026-09-26 的真实故障）**：四个增强入口（引用 / 合并转发 / 语音 /
文件）此前读 `message["message"]`，而两个连接器归一化出来的字典里**只有 `content`（CQ 串）
与 `raw`（原始事件）** —— 既没有 `message` 也没有 `raw_message`。于是

* `_expand_reply_segments` 永远返回空 → `_pending_reply_ids` 永远打不上 →
  `_fetch_reply_content` 永远不跑 → **引用正文与引用里的图从来没进过 prompt**
* 转发 / 语音 / 文件同理（生产日志里 `get_forward_msg` / `get_record` /
  `get_group_file_url` / `get_private_file_url` 两天调用数为 **0**）

而**主消息的图一直是好的**，只因为 `_inject_image_descriptions` 单独写成
`raw.message or message["message"]` —— 同一件事两种写法，恰好只有它对。

看门狗的关键是**夹具不许手写**：这里直接跑真连接器的 `receive_message()`
（`utils.connection.onebot`，也就是生产用的那个），把它吐出来的字典喂给 enricher。
手写 `{"message": [...]}` 的夹具曾经让这四条路径"测试全绿、生产全死"。
"""

from __future__ import annotations

import asyncio

from plugin.plugins.qq_auto_reply.connector_seam import OneBotClient
from plugin.plugins.qq_auto_reply.enrichment import QQMessageEnricher

REPLY_ID = "999"
FORWARD_ID = "fwd-1"


def _normalize(raw: dict) -> dict:
    """跑真连接器的归一化，返回它给插件的那个字典。"""
    async def _run() -> dict:
        client = OneBotClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
        client._message_queue = asyncio.Queue(maxsize=10)

        async def _no(*_a, **_k):
            return False

        client._is_reply_to_bot_message = _no  # type: ignore[assignment]
        await client._message_queue.put(raw)
        return await client.receive_message(timeout=1.0) or {}

    return asyncio.run(_run())


def _raw_message(*segments, message_type: str = "private", group_id: int | None = None) -> dict:
    raw = {
        "post_type": "message",
        "message_type": message_type,
        "self_id": 10001,
        "user_id": 820040531,
        "time": 1790351722,
        "message_id": 111,
        "sender": {"nickname": "宅久"},
        "raw_message": "".join(str(s.get("_cq") or "") for s in segments),
        "message": [{k: v for k, v in s.items() if k != "_cq"} for s in segments],
    }
    if group_id is not None:
        raw["group_id"] = group_id
    if not raw["raw_message"]:
        raw.pop("raw_message")
    return raw


def _enricher() -> QQMessageEnricher:
    client = OneBotClient(onebot_url="ws://127.0.0.1:3001", direction="forward")
    client._self_id = "10001"
    return QQMessageEnricher(client)


# ── 先钉住"连接器给的是什么形态"这个事实本身 ──────────────────────

def test_connector_normalized_message_has_no_message_or_raw_message_key():
    """归一化结果里**没有** `message` / `raw_message` —— 段落在 `raw.message`。

    这条要是红了，说明连接器换了形态（那时下面所有增强入口都得跟着改）。
    """
    data = _normalize(_raw_message(
        {"type": "text", "data": {"text": "你看得到这个嘛"}, "_cq": "你看得到这个嘛"},
    ))

    assert "message" not in data, f"连接器现在会给 message 键了：{sorted(data)}"
    assert "raw_message" not in data, f"连接器现在会给 raw_message 键了：{sorted(data)}"
    assert isinstance(data.get("content"), str) and data["content"]
    assert [s["type"] for s in (data["raw"] or {}).get("message") or []] == ["text"]


# ── 四个增强入口：拿真连接器的输出，必须都能找到自己的段 ──────────────

def test_reply_segment_is_found_in_the_real_shape():
    """引用：`_expand_reply_segments` 必须从真形态里拿到被引用消息 id。

    这就是"引用的图/引用的正文解析不了"的根因所在。
    """
    data = _normalize(_raw_message(
        {"type": "reply", "data": {"id": REPLY_ID}, "_cq": f"[CQ:reply,id={REPLY_ID}]"},
        {"type": "text", "data": {"text": "你看得到这个嘛"}, "_cq": "你看得到这个嘛"},
    ))

    assert _enricher()._expand_reply_segments(data) == [REPLY_ID]


def test_file_segment_is_found_in_the_real_shape():
    data = _normalize(_raw_message(
        {"type": "file", "data": {"file": "a.md", "file_id": "fid1", "busid": 102},
         "_cq": "[CQ:file,file=a.md,file_id=fid1,busid=102]"},
    ))

    files = _enricher()._collect_file_segments(data)

    assert files and files[0]["file_id"] == "fid1" and files[0]["busid"] == 102


def test_record_segment_is_found_in_the_real_shape():
    data = _normalize(_raw_message(
        {"type": "record", "data": {"file": "voice.amr"}, "_cq": "[CQ:record,file=voice.amr]"},
    ))

    assert _enricher()._transcribe_record_segments(data) == ["voice.amr"]


def test_forward_segment_is_found_in_the_real_shape():
    data = _normalize(_raw_message(
        {"type": "forward", "data": {"id": FORWARD_ID}, "_cq": f"[CQ:forward,id={FORWARD_ID}]"},
    ))

    assert _enricher()._expand_forward_segments(data) == [FORWARD_ID]


def test_image_segment_is_found_in_the_real_shape():
    """主消息的图（此前唯一活着的那条）也要继续活着。"""
    data = _normalize(_raw_message(
        {"type": "image", "data": {"file": "x.jpg", "url": "https://example.invalid/x.jpg"},
         "_cq": "[CQ:image,file=x.jpg,url=https://example.invalid/x.jpg]"},
    ))

    segments = _enricher()._message_segments(data)

    assert [s["type"] for s in segments] == ["image"]


# ── 老形态（测试桩 / 早期调用方）不许被打断 ─────────────────────────

def test_legacy_message_key_still_works():
    """没有 `raw` 的调用方（桩、老形态）继续按 `message` 读。"""
    enricher = _enricher()

    assert enricher._expand_reply_segments(
        {"message": [{"type": "reply", "data": {"id": "7"}}]},
    ) == ["7"]
    assert enricher._transcribe_record_segments(
        {"raw_message": "[CQ:record,file=v.amr]"},
    ) == ["v.amr"]
    assert enricher._collect_file_segments(
        {"message": "[CQ:file,file=b.zip,file_id=fid9,busid=0]"},
    )[0]["file_id"] == "fid9"


def test_no_segments_at_all_is_not_an_error():
    enricher = _enricher()

    assert enricher._expand_reply_segments({}) == []
    assert enricher._expand_forward_segments({}) == []
    assert enricher._collect_file_segments({}) == []
    assert enricher._transcribe_record_segments({}) == []
    assert enricher._message_segments({}) is None


# ── 源码级：五个入口必须共用同一个真源 ─────────────────────────────

def test_all_five_entry_points_share_one_segment_source():
    import pathlib

    import plugin.plugins.qq_auto_reply.enrichment as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    assert source.count('message.get("message")') == 0, (
        "又有人直接读 message['message'] 了 —— 连接器不给这个键，五处入口都要走 "
        "`_message_segments()`"
    )
    assert source.count("self._message_segments(message)") >= 5, (
        "五个段入口（引用/转发/语音/文件/主消息图）都要走 _message_segments()"
    )
