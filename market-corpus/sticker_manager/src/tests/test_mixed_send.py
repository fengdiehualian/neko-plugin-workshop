# pyright: reportMissingImports=false
"""配文与图片分条：同一次工具调用先投配文，再投独立图片。

图片必须先准备好，准备失败时不能留下裸配文。传输本身没有事务或回滚，
配文已提交后图片仍可能失败；部分提交必须如实报告而不能算发表情成功。
"""

from __future__ import annotations

import json

from conftest import GIF_BYTES, PNG_BYTES, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.catalog import SEND_TEXT_MAX_CHARS
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings, StorageSettings
from sticker_manager.core.tool_surface import build_send_tool_description

_SENTINEL = "哨兵：这句话绝不能落到台账里"


def _setup(tmp_path, *, send_overrides=None):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
    )
    plugin, host = build_plugin(host)
    settings = StickerManagerSettings(
        enabled=True,
        send=SendSettings(recent_dedup_count=0, **(send_overrides or {})),
        storage=StorageSettings(),
    )
    plugin._settings = settings
    plugin._library.load(force=True)
    return plugin, host, settings


class TestMixedParts:
    def test_text_and_image_use_separate_pushes_in_order(self, tmp_path, run_async):
        plugin, host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text="就这？"
            )
        )
        assert result.ok and result.text_submitted
        caption_call, image_call = host.push.calls
        assert caption_call["parts"] == [{"type": "text", "text": "就这？"}]
        assert image_call["parts"] == [{"type": "image", "data": PNG_BYTES, "mime": "image/png"}]
        for call in host.push.calls:
            assert call["target_lanlan"] == "K"
            assert call["visibility"] == ["chat"] and call["ai_behavior"] == "blind"
        assert plugin._library.get(sticker.id).use_count == 1
        (usage,) = plugin._library.read_usage()
        assert usage["ok"] and usage["text_len"] == len("就这？")
        sent_at = plugin._sender._last_sent["K"]
        assert plugin._sender.cooldown_remaining("K", settings, now=sent_at) == settings.send.cooldown_sec

    def test_without_text_the_old_shape_is_untouched(self, tmp_path, run_async):
        # 反向样本：默认路径被改动过（比如无条件塞一个空 text part）就该红——
        # 老形状是主人实机验收过 6 版的东西。
        plugin, host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0)
        )
        assert result.ok and not result.text_submitted
        (call,) = host.push.calls
        assert [p["type"] for p in call["parts"]] == ["image"]
        assert call["visibility"] == ["chat"] and call["ai_behavior"] == "blind"

    def test_sender_forwards_the_words_verbatim(self, tmp_path, run_async):
        # 归位：sender 拿到什么就投什么（不改写她的措辞），trim 是工具入口的事。
        plugin, host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text="  嗯  ")
        )
        assert host.push.calls[0]["parts"][0]["text"] == "  嗯  "

    def test_tool_trims_the_words_before_sending(self, tmp_path, run_async):
        plugin, host, _settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, text="  嗯  "))
        assert result.get("ok") is True, result
        assert host.push.calls[0]["parts"] == [{"type": "text", "text": "嗯"}]
        assert [part["type"] for part in host.push.calls[1]["parts"]] == ["image"]


class TestImagePreparation:
    def test_image_preparation_failure_never_submits_caption(self, tmp_path, run_async):
        plugin, host, settings = _setup(tmp_path)
        big_gif = GIF_BYTES + b"\x00" * (300 * 1024)
        sticker, _ = plugin._library.add(data=big_gif, desc="大动图", tags=[])
        result = run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text="来看看这个"
            )
        )
        assert not result.ok and result.code == "sticker_too_large"
        assert not result.text_submitted
        assert host.push.calls == []

    def test_caption_does_not_reduce_the_independent_image_budget(self, tmp_path, run_async):
        """两条消息的预算独立，GIF 保持内联原字节，不通过上传压平动画。"""
        plugin, host, settings = _setup(tmp_path, send_overrides={"inline_max_bytes": 200_000})
        gif = GIF_BYTES + b"\x00" * 190_000
        sticker, _ = plugin._library.add(data=gif, desc="紧巴巴", tags=[])
        tight = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert tight.ok and len(host.push.calls) == 1
        # 第二次要越过 20s 冷却，否则先撞的是节奏闸（那测的就不是预算尺了）。
        crowded = run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1099.0, text="x" * 20_000
            )
        )
        assert crowded.ok and crowded.text_submitted
        assert len(host.push.calls) == 3
        assert host.push.calls[1]["parts"] == [{"type": "text", "text": "x" * 20_000}]
        assert host.push.calls[2]["parts"] == [{"type": "image", "data": gif, "mime": "image/gif"}]
        assert host.images.calls == [], "gif 不许为了塞进去改走上传通道"


class TestToolSurfaceForText:
    def test_oversized_caption_is_refused_before_any_delivery(self, tmp_path, run_async):
        plugin, host, _settings = _setup(tmp_path)
        plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(plugin.tool_sticker_send(group="", sticker_id="", query="笑", text="啊" * (SEND_TEXT_MAX_CHARS + 1)))
        assert result.get("ok") is False and result.get("reason") == "text_too_long"
        assert host.push.calls == []

    def test_the_standing_surface_teaches_the_one_call_shape(self):
        # One group call adds an independent image; optional caption remains separate.
        text = build_send_tool_description("・困与睡（9 张） — 深夜用。", "natural")
        assert "text" in text and "配文与图片分条发送" in text
        assert "text 留空即可" in text and "图片独立发送" in text
        assert "一起出去" not in text
        for banned in ("会拒", "别连试", "只有主人点名", "force", "挡下", "最近不重复", "冷却"):
            assert banned not in text, f"配文分条的描述里混进了禁令词：{banned}"


class TestLedgerHonesty:
    def test_usage_records_length_not_the_words(self, tmp_path, run_async):
        # 台账既有纪律是"只放非隐私字段"。这条就是那把尺在新字段上的反向验。
        plugin, host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text=_SENTINEL
            )
        )
        raw = (plugin._library.root / "usage.json").read_text(encoding="utf-8")
        assert _SENTINEL not in raw, "配文内容落盘了"
        entry = json.loads(raw)["entries"][-1]
        assert entry["text_len"] == len(_SENTINEL) and entry["ok"] is True

    def test_plain_send_records_zero_length(self, tmp_path, run_async):
        plugin, _host, settings = _setup(tmp_path)
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        entry = json.loads((plugin._library.root / "usage.json").read_text(encoding="utf-8"))["entries"][-1]
        assert entry["text_len"] == 0

    def test_log_line_shows_the_length_not_the_sentence(self, tmp_path, run_async):
        recorder: list[str] = []

        class _Log:
            def info(self, message, **_k):
                recorder.append(str(message))

            def warning(self, message, **_k):
                recorder.append(str(message))

            def error(self, message, **_k):
                recorder.append(str(message))

            def debug(self, message, **_k):
                recorder.append(str(message))

        plugin, _host, settings = _setup(tmp_path)
        plugin._sender._logger = _Log()
        sticker, _ = plugin._library.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(
            plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0, text=_SENTINEL
            )
        )
        line = [x for x in recorder if "sticker sent" in x]
        assert line and f"text_len={len(_SENTINEL)}" in line[0]
        assert _SENTINEL not in "\n".join(recorder)
