# pyright: reportMissingImports=false
"""Queued receipts preserve private continuation without claiming delivery."""

from conftest import PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.core.eagerness import INTERNAL_SELECTION_NOTE, NEXT_STEP_NOTE
from sticker_manager.services.sender import SendResult


def _queued_tool_result(tmp_path, monkeypatch, run_async):
    plugin, host = build_plugin(
        FakeHostContext(
            data_root=tmp_path,
            config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
        )
    )
    plugin._settings = StickerManagerSettings(enabled=True)
    plugin._library.load(force=True)
    sticker, _ = plugin._library.add(data=PNG_BYTES, desc="smile", tags=[])

    async def queue(selected, **kwargs):
        assert selected.id == sticker.id
        assert kwargs["source"] == "tool"
        return SendResult(ok=True, sticker_id=selected.id, desc=selected.desc, queued=True)

    monkeypatch.setattr(plugin._sender, "send", queue)
    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id))
    return plugin, host, sticker, result


def test_queued_receipt_is_not_a_completed_send(tmp_path, monkeypatch, run_async):
    plugin, host, sticker, result = _queued_tool_result(tmp_path, monkeypatch, run_async)

    assert result["ok"] is True
    assert result["queued"] == sticker.id
    assert result["sent"] == ""
    assert host.push.calls == []
    assert plugin._runstats.tool_calls == 1
    assert plugin._runstats.sent == 0
    assert plugin._runstats.refused == 0
    assert "表情包已提交到聊天" not in result["note"]
    assert "配文已提交" not in result["note"]


def test_queued_guidance_only_limits_same_turn_same_image(tmp_path, monkeypatch, run_async):
    _plugin, _host, _sticker, result = _queued_tool_result(tmp_path, monkeypatch, run_async)
    note = result["note"]

    assert "仅同一轮同一张图不重复提交" in note
    assert "本轮回复与显示缓冲结束后" in note
    assert "继续正常回复，无需等待" in note
    assert NEXT_STEP_NOTE.removeprefix("表情包已提交到聊天，") in note
    assert "本轮选图已完成" in note
    assert "只续接尚未说完的回复" in note
    assert "若已说完就结束" in note
    assert INTERNAL_SELECTION_NOTE in result["hint"]
    assert "group + text" not in note
    assert "不复述原话、额外配文或选图过程" in note
    assert "不要重复调用发送" not in note
