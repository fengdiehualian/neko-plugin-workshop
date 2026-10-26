"""通道发不出语音时**不许先合成一遍**。

`supports_voice` 这个能力标志以前**没有任何消费方**（全仓只有定义）。开放平台是
False，于是 `voice` 模式下每次都：跑一次真 TTS → 落一个音频文件 →
`send_*_record` 在那边是空桩、返回 None → 判成"未确认" → 再回退文本。

功能上没错（文字照样到），但每次白烧一次语音合成。这组测试钉三件事：

1. 不支持就别合成 —— 合成函数**一次都不许被调用**；
2. 落到文字时的判据与既有那条回退**一致**：`fallback_to_text_on_voice_failure=False`
   的调用方（转达 / 主动发言）要的是"语音没发出去就是没发出去"，不许擅自补文字；
3. 拿不到这个属性的连接按**支持**处理（旧行为不变，不许因为一次属性缺失把语音关掉）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.voice_reply_service import QQVoiceReplyService


class _Client:
    def __init__(self, *, supports_voice, record_ok: bool = False):
        self.sent_text: list[tuple[str, str]] = []
        self.sent_group_text: list[tuple[str, str, str]] = []
        self.records: list[tuple[str, str]] = []
        self.record_ok = record_ok
        if supports_voice is not None:
            self.supports_voice = supports_voice

    async def send_message(self, target_id, text, **kw):
        self.sent_text.append((str(target_id), str(text)))
        return "mid"

    async def send_group_message_segments(self, group_id, segments, **kw):
        text = "".join(
            str((seg.get("data") or {}).get("text") or "") for seg in segments
            if isinstance(seg, dict) and seg.get("type") == "text"
        )
        self.sent_group_text.append((str(group_id), text, str(kw.get("keyboard") or "")))
        return "mid"

    async def send_private_record(self, user_id, file_uri, **kw):
        self.records.append(("private", str(user_id)))
        return "mid-voice" if self.record_ok else None

    async def send_group_record(self, group_id, file_uri, **kw):
        self.records.append(("group", str(group_id)))
        return "mid-voice" if self.record_ok else None


def _service(client, *, mode="voice"):
    plugin = SimpleNamespace(
        qq_client=client,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _validate_outbound_message=lambda text: str(text or ""),
        _get_reply_mode=lambda: mode,
    )
    service = QQVoiceReplyService(plugin)
    service.synthesis_calls = []

    async def _synth(text):
        service.synthesis_calls.append(str(text))
        return (f"file://voice-{len(str(text))}.wav", "audio/wav")

    service.synthesize_reply_voice_file = _synth
    return service


# ── 私聊 ────────────────────────────────────────────────────────────

def test_private_voice_on_a_channel_without_voice_never_synthesizes():
    client = _Client(supports_voice=False)
    service = _service(client)

    delivered = asyncio.run(service.deliver_private_reply(
        "820040531", "你好", fallback_to_text_on_voice_failure=True,
    ))

    assert delivered is True
    assert service.synthesis_calls == [], "通道发不出语音，却还是合成了一遍"
    assert client.sent_text == [("820040531", "你好")]
    assert client.records == []


def test_private_voice_without_fallback_neither_synthesizes_nor_sends_text():
    client = _Client(supports_voice=False)
    service = _service(client)

    delivered = asyncio.run(service.deliver_private_reply(
        "820040531", "你好", fallback_to_text_on_voice_failure=False,
    ))

    assert delivered is False, "调用方明确不要文字回退，这里不许自己补一条"
    assert service.synthesis_calls == []
    assert client.sent_text == []


def test_private_both_mode_keeps_the_text_part_when_voice_is_unsupported():
    """`both` 模式下文字是这条回复的一部分（今天也是"语音失败保留文字"）。"""
    client = _Client(supports_voice=False)
    service = _service(client, mode="both")

    delivered = asyncio.run(service.deliver_private_reply(
        "820040531", "文字部分", voice_text="语音部分", fallback_to_text_on_voice_failure=False,
    ))

    assert delivered is True
    assert service.synthesis_calls == []
    assert client.sent_text == [("820040531", "文字部分")]


def test_a_channel_that_supports_voice_still_synthesizes():
    """对照：支持语音的通道行为**一点没变**（否则这轮改动就是拿功能换省事）。"""
    client = _Client(supports_voice=True, record_ok=True)
    service = _service(client)

    delivered = asyncio.run(service.deliver_private_reply(
        "820040531", "你好", fallback_to_text_on_voice_failure=True,
    ))

    assert delivered is True
    assert service.synthesis_calls == ["你好"]
    assert client.records == [("private", "820040531")]
    assert client.sent_text == [], "语音已确认送达，不该再多发一条文字"


def test_an_unconfirmed_voice_still_falls_back_to_text_on_a_capable_channel():
    """能力有、但这次没确认（NapCat echo 超时 / 开放平台返回 None）→ 仍回退文字。

    这条是**旧行为**：别让"合成前先问能力"顺手把这条回退也删了。
    """
    client = _Client(supports_voice=True, record_ok=False)
    service = _service(client)

    delivered = asyncio.run(service.deliver_private_reply(
        "820040531", "你好", fallback_to_text_on_voice_failure=True,
    ))

    assert delivered is True
    assert service.synthesis_calls == ["你好"]
    assert client.sent_text == [("820040531", "你好")]


def test_a_client_without_the_flag_is_treated_as_supporting_voice():
    """老连接器没有这个属性 → 按支持处理，别因为一次 getattr 失败把语音关掉。"""
    client = _Client(supports_voice=None)
    assert not hasattr(client, "supports_voice")
    service = _service(client)

    asyncio.run(service.deliver_private_reply(
        "820040531", "你好", fallback_to_text_on_voice_failure=True,
    ))

    assert service.synthesis_calls == ["你好"]


# ── 群聊 ────────────────────────────────────────────────────────────

def test_group_voice_on_a_channel_without_voice_never_synthesizes():
    client = _Client(supports_voice=False)
    service = _service(client)

    delivered = asyncio.run(service.deliver_group_reply(
        "1048307485", "大家好", keyboard="A|B", fallback_to_text_on_voice_failure=True,
    ))

    assert delivered is True
    assert service.synthesis_calls == []
    assert client.records == []
    # keyboard 要跟着走：否则用户拿到的是一句"想看哪个？"却一个按钮都没有
    assert client.sent_group_text == [("1048307485", "大家好", "A|B")]


def test_group_voice_without_fallback_sends_nothing():
    client = _Client(supports_voice=False)
    service = _service(client)

    delivered = asyncio.run(service.deliver_group_reply(
        "1048307485", "大家好", fallback_to_text_on_voice_failure=False,
    ))

    assert delivered is False
    assert service.synthesis_calls == []
    assert client.sent_group_text == []
