"""Transport-contract tests using strict stubs, not a running host integration."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import GIF_BYTES, PNG_BYTES, FakeHostContext, FakePush, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.core.eagerness import NEXT_STEP_NOTE


class _FeedbackPush(FakePush):
    """Model read-image feedback independently of chat visibility in this stub."""

    def __init__(self):
        super().__init__()
        self.vision_pending = []
        self.chat_parts = []

    def push_message(self, **kwargs):
        result = super().push_message(**kwargs)
        if result.get("submitted"):
            parts = kwargs.get("parts", [])
            if "chat" in kwargs.get("visibility", []):
                self.chat_parts.extend(parts)
            if kwargs.get("ai_behavior") == "read":
                self.vision_pending.extend(part for part in parts if part["type"] == "image")
        return result


def _setup(tmp_path, *, data=PNG_BYTES, **send_overrides):
    host = FakeHostContext(data_root=tmp_path)
    host.push = _FeedbackPush()
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings(
        enabled=True,
        send=SendSettings(recent_dedup_count=0, **send_overrides),
    )
    sticker, error = plugin._library.add(
        data=data, desc="A playful reaction", tags=["playful"], group="reactions"
    )
    assert not error and sticker is not None
    return plugin, host, sticker


@pytest.mark.parametrize("data,mime", [(PNG_BYTES, "image/png"), (GIF_BYTES, "image/gif")])
def test_inline_tool_image_preserves_bytes_and_chat_without_read_feedback(tmp_path, run_async, data, mime):
    plugin, host, sticker = _setup(tmp_path, data=data)

    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))

    assert result["ok"] is True and result["sent"] == sticker.id
    assert not host.images.calls
    (call,) = host.push.calls
    assert call["parts"] == [{"type": "image", "data": data, "mime": mime}]
    assert call["visibility"] == ["chat"]
    assert call["ai_behavior"] == "blind"
    assert call["target_lanlan"] == "K"
    assert host.push.chat_parts == call["parts"]
    assert host.push.vision_pending == []


def test_uploaded_tool_image_keeps_url_visible_without_read_feedback(tmp_path, run_async):
    data = PNG_BYTES + b"large" * (70 * 1024)
    plugin, host, sticker = _setup(tmp_path, data=data)

    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id))

    assert result["ok"] is True and result["sent"] == sticker.id
    assert host.images.calls == [data]
    (call,) = host.push.calls
    assert call["parts"] == [
        {"type": "image", "url": "https://stub.local/1.jpg", "mime": "image/jpeg"}
    ]
    assert call["ai_behavior"] == "blind" and call["visibility"] == ["chat"]
    assert host.push.chat_parts == call["parts"]
    assert host.push.vision_pending == []


def test_tool_caption_and_image_stay_separate_and_both_blind(tmp_path, run_async):
    plugin, host, sticker = _setup(tmp_path)

    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, text="A small caption"))

    assert result["ok"] is True and result["sent"] == sticker.id
    assert len(host.push.calls) == 2
    caption_call, image_call = host.push.calls
    assert caption_call["parts"] == [{"type": "text", "text": "A small caption"}]
    assert [part["type"] for part in image_call["parts"]] == ["image"]
    assert all(call["ai_behavior"] == "blind" for call in host.push.calls)
    assert all(call["visibility"] == ["chat"] for call in host.push.calls)
    assert [part["type"] for part in host.push.chat_parts] == ["text", "image"]
    assert host.push.vision_pending == []


@pytest.mark.parametrize("source", ["panel", "agent"])
def test_panel_and_agent_images_retain_read_feedback(tmp_path, run_async, source):
    plugin, host, sticker = _setup(tmp_path)

    result = run_async(
        plugin._sender.send(
            sticker, lanlan="K", settings=plugin._settings, source=source, now=1000.0
        )
    )

    assert result.ok
    (call,) = host.push.calls
    assert call["ai_behavior"] == "read" and call["visibility"] == ["chat"]
    assert host.push.chat_parts == call["parts"]
    assert host.push.vision_pending == call["parts"]
    assert plugin._library.read_usage()[-1]["source"] == source


def test_tool_receipt_retains_selected_description_and_next_step_note(tmp_path, run_async):
    plugin, host, sticker = _setup(tmp_path)

    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id))

    assert result["ok"] is True and result["sent"] == sticker.id
    assert "A playful reaction" in result["desc"]
    assert result["note"] == NEXT_STEP_NOTE
    assert host.push.calls[0]["ai_behavior"] == "blind"
    assert plugin._runstats.sticker_send_calls == 1
    assert plugin._runstats.sent == 1
    assert plugin._library.get(sticker.id).use_count == 1


def test_blind_tool_sends_still_obey_cooldown_and_resume_after_it(tmp_path, run_async):
    plugin, host, sticker = _setup(tmp_path, cooldown_sec=20.0)

    def send(now):
        return run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=plugin._settings, source="tool", now=now
            )
        )

    assert send(1000.0).ok
    blocked = send(1001.0)
    assert not blocked.ok and blocked.code == "send_cooldown"
    assert send(1021.0).ok
    assert len(host.push.calls) == 2
    assert all(call["ai_behavior"] == "blind" for call in host.push.calls)
    assert len(host.push.chat_parts) == 2 and host.push.vision_pending == []
    assert plugin._library.get(sticker.id).use_count == 2


def test_queued_tool_caption_and_image_use_same_blind_policy(tmp_path, run_async):
    plugin, host, sticker = _setup(
        tmp_path, defer_tool_sends=True, reply_tail_display_buffer_sec=0.0
    )

    class ReadyTail:
        def arm(self, _lanlan):
            return SimpleNamespace(matched=False, invalid=False, superseded=False)

        def poll(self, ticket):
            ticket.matched = True
            return True

        def busy_states(self):
            return {"K": False}

    plugin._sender._turn_end = ReadyTail()
    queued = run_async(
        plugin._sender.send(
            sticker,
            lanlan="K",
            settings=plugin._settings,
            source="tool",
            now=1000.0,
            text="Queued caption",
        )
    )
    assert queued.ok and queued.queued and host.push.calls == []

    outcomes = run_async(plugin._sender.drain(settings=plugin._settings))

    assert len(outcomes) == 1 and outcomes[0][0] == "tool" and outcomes[0][1].ok
    assert len(host.push.calls) == 2
    assert all(call["ai_behavior"] == "blind" for call in host.push.calls)
    assert [part["type"] for part in host.push.chat_parts] == ["text", "image"]
    assert host.push.vision_pending == []


def test_awareness_reminders_remain_invisible_read_cues(tmp_path, run_async):
    plugin, host, _ = _setup(tmp_path)
    settings = replace(
        plugin._settings,
        awareness=replace(plugin._settings.awareness, enabled=True, min_interval_sec=0.0),
    )

    result = run_async(
        plugin._awareness.inject_now(settings=settings, lanlan="K", now=1000.0)
    )

    assert result["status"] == "injected"
    (call,) = host.push.calls
    assert call["ai_behavior"] == "read" and call["visibility"] == []
    assert call["coalesce_key"] == "sticker_manager:awareness:K"
    assert [part["type"] for part in call["parts"]] == ["text"]
    assert host.push.chat_parts == [] and host.push.vision_pending == []
