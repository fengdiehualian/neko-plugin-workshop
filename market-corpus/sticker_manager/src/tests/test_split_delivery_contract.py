# pyright: reportMissingImports=false
"""Independent tool and timer contracts for caption-first split delivery."""

from __future__ import annotations

import pytest
from conftest import GIF_BYTES, PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.services import sender as sender_module
from sticker_manager.services.sender import SendResult
from sticker_manager.services.turn_end import TurnEndLogs

_CAPTION = "private caption\nsecond line with  two spaces"
_ROLE = "K"


class _Signals(TurnEndLogs):
    def busy_states(self):
        return {_ROLE: False}


def _setup(tmp_path, monkeypatch, *, deferred=False, data=PNG_BYTES):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path / "data"))
    plugin._settings = StickerManagerSettings(
        enabled=True,
        send=SendSettings(
            recent_dedup_count=0, cooldown_sec=20.0, reply_tail_display_buffer_sec=2.0,
            defer_tool_sends=deferred,
        ),
    )
    plugin._library.load(force=True)
    sticker, _ = plugin._library.add(data=data, desc="smile", tags=[])
    clock = [100.0]
    monkeypatch.setattr(sender_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sender_module.time, "time", lambda: 1000.0)
    log = tmp_path / "logs" / "N.E.K.O_Main_20261002.log"
    if deferred:
        log.parent.mkdir()
        log.write_bytes(b"initial record\n")
        plugin._sender._turn_end = _Signals(plugin, [log.parent])
    return plugin, host, sticker, clock, log


def _tool(plugin, sticker):
    return plugin.tool_sticker_send(
        sticker_id=sticker.id, text=_CAPTION, _ctx={"lanlan_name": _ROLE},
    )


def _append_end(log):
    with log.open("ab") as stream:
        stream.write(
            b"2026-10-02 00:00:00 - N.E.K.O.Main.main_logic.cross_server - INFO - "
            b"[K] turn_end analyze check: history=2 recent=2 has_user=True "
            b"had_input=True agent_callback_turn=False avatar_drop_turn=False\n"
        )


def _append_input(log):
    with log.open("ab") as stream:
        stream.write(
            b"2026-10-02 00:00:01 - N.E.K.O.Main.main_logic.core - INFO - "
            b"[K] voice user_transcript session=OmniOfflineClient "
            b"ws_connected=True len=3\n"
        )


def _release(plugin, clock, log, run_async):
    _append_end(log)
    run_async(plugin.on_sticker_tail())
    clock[0] += 2.0
    run_async(plugin.on_sticker_tail())


def _fail_push(host, monkeypatch, *, ordinal, raises):
    original = host.push_message

    def push(**kwargs):
        accepted = original(**kwargs)
        if len(host.push.calls) == ordinal:
            if raises:
                raise RuntimeError("split transport failure")
            return {"submitted": False, "reason": "split_backpressure"}
        return accepted

    monkeypatch.setattr(host, "push_message", push)


def _assert_parts(host, *, behavior, data=PNG_BYTES):
    assert len(host.push.calls) == 2
    caption, image = host.push.calls
    assert caption["parts"] == [{"type": "text", "text": _CAPTION}]
    assert image["parts"] == [{"type": "image", "data": data, "mime": "image/png"}]
    for call in (caption, image):
        assert call["target_lanlan"] == _ROLE
        assert call["visibility"] == ["chat"]
        assert call["ai_behavior"] == behavior


def _assert_not_sent(plugin, sticker):
    assert not plugin._sender._last_sent
    assert plugin._sender.cooldown_remaining(_ROLE, plugin._settings, now=1000.0) == 0.0
    assert plugin._library.get(sticker.id).use_count == 0
    assert plugin._runstats.sent == 0


def test_result_partial_delivery_flag_defaults_to_false():
    assert SendResult(ok=False).text_submitted is False
    assert SendResult.failure("transport_unavailable").text_submitted is False


@pytest.mark.parametrize("deferred", [False, True])
def test_two_pushes_preserve_caption_order_and_count_sticker_once(
    tmp_path, monkeypatch, run_async, caplog, deferred,
):
    plugin, host, sticker, clock, log = _setup(tmp_path, monkeypatch, deferred=deferred)
    caplog.set_level("INFO", logger="sticker_manager.test")
    result = run_async(_tool(plugin, sticker))
    assert result["ok"] is True
    assert plugin._runstats.tool_calls == 1
    if deferred:
        assert result["queued"] == sticker.id and result["sent"] == ""
        assert not host.push.calls and not plugin._library.read_usage()
        _release(plugin, clock, log, run_async)
    else:
        assert result["sent"] == sticker.id
    _assert_parts(host, behavior="blind")
    assert plugin._runstats.sent == 1 and plugin._runstats.refused == 0
    assert plugin._library.get(sticker.id).use_count == 1
    assert plugin._sender.cooldown_remaining(_ROLE, plugin._settings, now=1000.0) == 20.0
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is True and usage["text_len"] == len(_CAPTION)
    assert "private caption" not in (plugin._library.root / "usage.json").read_text(encoding="utf-8")
    assert "private caption" not in caplog.text
    run_async(plugin.on_sticker_tail())
    assert len(host.push.calls) == 2
    assert plugin._runstats.sent == 1 and len(plugin._library.read_usage()) == 1


@pytest.mark.parametrize("raises", [False, True])
def test_image_failure_after_caption_reports_partial_delivery_without_success(
    tmp_path, monkeypatch, run_async, caplog, raises,
):
    plugin, host, sticker, _clock, _log = _setup(tmp_path, monkeypatch)
    caplog.set_level("INFO", logger="sticker_manager.test")
    _fail_push(host, monkeypatch, ordinal=2, raises=raises)
    result = run_async(_tool(plugin, sticker))

    assert result["ok"] is False and result["text_submitted"] is True
    reason = "transport_unavailable" if raises else "split_backpressure"
    assert result["reason"] == reason
    assert "配文" in result["hint"] and "已" in result["hint"] and "重复" in result["hint"]
    _assert_parts(host, behavior="blind")
    _assert_not_sent(plugin, sticker)
    assert plugin._runstats.tool_calls == 1 and plugin._runstats.refused == 1
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is False and usage["code"] == reason
    assert usage["text_submitted"] is True
    assert "private caption" not in (plugin._library.root / "usage.json").read_text(encoding="utf-8")
    assert "private caption" not in caplog.text


@pytest.mark.parametrize("raises", [False, True])
def test_deferred_image_failure_is_counted_once_with_partial_delivery_flag(
    tmp_path, monkeypatch, run_async, raises,
):
    plugin, host, sticker, clock, log = _setup(tmp_path, monkeypatch, deferred=True)
    _fail_push(host, monkeypatch, ordinal=2, raises=raises)
    outcomes = []
    original_drain = plugin._sender.drain

    async def drain(**kwargs):
        results = await original_drain(**kwargs)
        outcomes.extend(results)
        return results

    monkeypatch.setattr(plugin._sender, "drain", drain)
    result = run_async(_tool(plugin, sticker))
    assert result["ok"] and result["queued"] == sticker.id
    _release(plugin, clock, log, run_async)
    run_async(plugin.on_sticker_tail())

    _assert_parts(host, behavior="blind")
    _assert_not_sent(plugin, sticker)
    assert plugin._runstats.tool_calls == 1 and plugin._runstats.refused == 1
    ((source, delivered),) = outcomes
    assert source == "tool" and delivered.ok is False and delivered.text_submitted is True
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is False and usage["text_submitted"] is True
    assert usage["code"] == ("transport_unavailable" if raises else "split_backpressure")
    assert not plugin._sender._inflight and not plugin._sender._pending


@pytest.mark.parametrize("raises", [False, True])
def test_caption_failure_never_attempts_image(tmp_path, monkeypatch, run_async, raises):
    plugin, host, sticker, _clock, _log = _setup(tmp_path, monkeypatch)
    _fail_push(host, monkeypatch, ordinal=1, raises=raises)
    result = run_async(_tool(plugin, sticker))

    assert result["ok"] is False and not result.get("text_submitted", False)
    assert result["reason"] == ("transport_unavailable" if raises else "split_backpressure")
    (caption,) = host.push.calls
    assert caption["parts"] == [{"type": "text", "text": _CAPTION}]
    _assert_not_sent(plugin, sticker)
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is False and "text_submitted" not in usage
    assert plugin._runstats.refused == 1


def test_image_preparation_failure_sends_neither_caption_nor_image(
    tmp_path, monkeypatch, run_async,
):
    plugin, host, sticker, _clock, _log = _setup(
        tmp_path, monkeypatch, data=GIF_BYTES + b"\0" * (300 * 1024),
    )
    result = run_async(_tool(plugin, sticker))

    assert result["ok"] is False and result["reason"] == "sticker_too_large"
    assert not result.get("text_submitted", False)
    assert not host.push.calls and not host.images.calls
    _assert_not_sent(plugin, sticker)
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is False and "text_submitted" not in usage


def test_new_input_cancels_both_queued_parts_and_counts_one_failed_sticker(
    tmp_path, monkeypatch, run_async,
):
    plugin, host, sticker, clock, log = _setup(tmp_path, monkeypatch, deferred=True)
    result = run_async(_tool(plugin, sticker))
    assert result["ok"] and result["queued"] == sticker.id and not host.push.calls
    _append_end(log)
    run_async(plugin.on_sticker_tail())
    clock[0] += 0.5
    _append_input(log)
    run_async(plugin.on_sticker_tail())
    clock[0] += 2.0
    run_async(plugin.on_sticker_tail())

    assert not host.push.calls
    _assert_not_sent(plugin, sticker)
    assert plugin._runstats.tool_calls == 1 and plugin._runstats.refused == 1
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is False and usage["code"] == "turn_superseded"
    assert "text_submitted" not in usage
    assert not plugin._sender._inflight and not plugin._sender._pending
