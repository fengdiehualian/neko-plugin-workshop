# -*- coding: utf-8 -*-
"""语音发不出去时：**别被角色的 voice_id 挡住本地那条路，失败原因要说人话**。

真机背景（2026-09-29，使用者报告）：

> 「主要是 gsv 环境，会出现这个报错，但是接入百炼就好了」

那条报错是 `当前猫娘未配置 voice_id，无法发送语音`。根因不是"配置错了"，而是
``synthesize_reply_voice_file`` 在**入口**就要求角色必须有 voice_id —— 它挡住的是排在最前面
的**本地那条路**：``_synthesize_local_tts``（自建 SoVITS/CosyVoice，音色取自
``tts_custom.voice_name``，缺 voice_id 时用 ``"default"``，本来跑得通）。于是 GSV/自建 TTS 的
用户永远发不出语音；换百炼能好，只是因为百炼正好在插件的合成链上、而且选音色时把 voice_id
写上了。

要求现在留在真正需要它的地方：云端各分支自己有守卫（DashScope 那条见下）。本文件钉四件事：

1. 没有 voice_id 时，本地那条**照样能合成**（回归守卫 —— 这条以前是红的）；
2. 真的没有音色可用时，报错要**指名 voice_id 与本地那条已试过**；
3. 撞到宿主的 ``gsv:`` 音色要**当场说清插件没有这一路**，而不是拿它去问百炼；
4. 配置性失败只打**一句人话**（不再整段 traceback），且回退文字时补一句说明；
   真正的意外（非 RuntimeError）照旧带栈，不许把 bug 也降噪掉。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import utils.config_manager as host_config_manager
from plugin.plugins.qq_auto_reply import voice_reply_service as vrs
from plugin.plugins.qq_auto_reply.pipeline_models import QQDeliveryPlan, QQMessageBlock
from plugin.plugins.qq_auto_reply.reply_delivery_node import QQReplyDeliveryNode
from plugin.plugins.qq_auto_reply.voice_reply_service import QQVoiceReplyService

WAV_BYTES = b"RIFF....WAVEfake-audio"


class _FakeConfigManager:
    """只提供合成链在"走到守卫之前"会碰到的那些方法。"""

    def __init__(self, *, api_key: str = "key", voices=None, tts_custom=None):
        self._api_key = api_key
        self._voices = voices or {}
        self._tts_custom = tts_custom or {}

    def get_voices_for_current_api(self):
        return self._voices

    def get_model_api_config(self, name):
        cfg = {"api_key": self._api_key, "base_url": ""}
        if name == "tts_custom":
            cfg.update(self._tts_custom)
        return cfg

    async def aget_core_config(self):
        return {}

    def get_cosyvoice_clone_runtime(self, provider):
        return {}

    def get_tts_api_key(self, provider):
        return ""


def _service(tmp_path, monkeypatch, *, voice_id: str, local_result=None):
    """最小可跑的 QQVoiceReplyService：把宿主 config_manager 与本地那条 TTS 都换掉。"""
    monkeypatch.setattr(vrs, "get_active_realtime_native_provider_for_ui", None)
    fake_cm = _FakeConfigManager()
    monkeypatch.setattr(host_config_manager, "get_config_manager", lambda: fake_cm)

    service = QQVoiceReplyService(SimpleNamespace(
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        data_path=lambda: tmp_path,
    ))

    async def _local(_text):
        return local_result

    async def _vid():
        return voice_id

    monkeypatch.setattr(service, "_synthesize_local_tts", _local)
    monkeypatch.setattr(service, "get_current_voice_id", _vid)
    return service


# ── 1 & 2：入口那道闸 ─────────────────────────────────────────────────

def test_the_local_line_works_without_a_character_voice(tmp_path, monkeypatch):
    """GSV/自建 TTS：角色没有 voice_id 也该能合成 —— 以前这里直接抛。"""
    service = _service(tmp_path, monkeypatch, voice_id="", local_result=(WAV_BYTES, "audio/wav"))

    uri, mime = asyncio.run(service.synthesize_reply_voice_file("你好呀"))

    assert mime == "audio/wav"
    assert uri.startswith("file://")
    assert (tmp_path / "voice_cache").is_dir(), "音频文件要落进 voice_cache"


def test_without_a_voice_the_reason_names_the_voice_and_the_local_line(tmp_path, monkeypatch):
    """真没音色可用时：说清缺什么、以及本地那条已经试过。"""
    service = _service(tmp_path, monkeypatch, voice_id="", local_result=None)

    with pytest.raises(RuntimeError) as err:
        asyncio.run(service.synthesize_reply_voice_file("你好呀"))

    message = str(err.value)
    assert "voice_id" in message
    assert "本地" in message, "要说明本地 SoVITS/CosyVoice 那条已经试过了，否则看不出为什么"


def test_a_gsv_voice_says_the_plugin_has_no_such_line(tmp_path, monkeypatch):
    """宿主的 ``gsv:`` 音色：插件这条链上没有它，别拿它去问百炼。"""
    service = _service(tmp_path, monkeypatch, voice_id="gsv:my_voice", local_result=None)

    with pytest.raises(RuntimeError) as err:
        asyncio.run(service.synthesize_reply_voice_file("你好呀"))

    message = str(err.value)
    assert "GPT-SoVITS" in message and vrs.GSV_VOICE_PREFIX in message
    assert "百炼" in message, "要给出可行的替代（换插件支持的音色）"


# ── 4：投递这一层的日志 ───────────────────────────────────────────────

class _Client:
    def __init__(self):
        self.sent_text: list[tuple[str, str]] = []

    async def send_message(self, target_id, text, **kw):
        self.sent_text.append((str(target_id), str(text)))
        return "mid"

    async def send_group_message(self, group_id, text, **kw):
        self.sent_text.append((str(group_id), str(text)))
        return "mid"


def _node(client, synth):
    logs: list[tuple[str, str, dict]] = []

    def _record(level):
        def _log(message, *args, **kwargs):
            logs.append((level, str(message), kwargs))
        return _log

    plugin = SimpleNamespace(
        qq_client=client,
        voice_reply_service=SimpleNamespace(synthesize_reply_voice_file=synth),
        logger=SimpleNamespace(info=_record("info"), warning=_record("warning")),
        _emit_log=lambda *a, **k: None,
    )
    return QQReplyDeliveryNode(plugin), logs


def _voice_plan() -> QQDeliveryPlan:
    return QQDeliveryPlan(
        target_type="private",
        target_id="820040531",
        blocks=[QQMessageBlock(record="用语音说这句话")],
        fallback_to_text_on_voice_failure=True,
    )


def test_a_config_failure_logs_one_line_and_still_sends_the_text():
    client = _Client()

    async def _synth(_text):
        raise RuntimeError("当前猫娘未配置 voice_id，而这条 TTS 线路需要音色")

    node, logs = _node(client, _synth)

    delivered = asyncio.run(node._send_record(_voice_plan(), QQMessageBlock(record="用语音说这句话")))

    assert delivered is True, "回退的文字发出去并确认了，就该算已投递"
    assert client.sent_text == [("820040531", "用语音说这句话")]
    warning = next(entry for entry in logs if entry[0] == "warning")
    assert "voice_id" in warning[1], "原因要进日志，不能只剩一句『语音发送失败』"
    assert "exc_info" not in warning[2], "配置性失败不该打整段 traceback"
    assert any(level == "info" and "改发文字" in text for level, text, _ in logs), (
        "`[Send] … 已发送（语音）` 那个标签不会跟着变，所以要补一句说明实际发的是文字"
    )


def test_an_unexpected_error_still_gets_a_traceback():
    """别把真正的 bug 也降噪掉：非 RuntimeError 照旧带栈。"""
    client = _Client()

    async def _synth(_text):
        raise ValueError("something we did not expect")

    node, logs = _node(client, _synth)

    delivered = asyncio.run(node._send_record(_voice_plan(), QQMessageBlock(record="用语音说这句话")))

    assert delivered is True, "意外异常也走同一条回退（文字照样要发出去）"
    warning = next(entry for entry in logs if entry[0] == "warning")
    assert warning[2].get("exc_info") is True
