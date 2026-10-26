"""services/sender.py 的链路测试：通道选择、冷却、fail-closed、台账。"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import GIF_BYTES, PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.configuration import (  # pyright: ignore[reportMissingImports] — 包名由 conftest 在测试时注册；独立仓静态面不可解析
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)


def _setup(tmp_path, *, enabled=True, send_overrides=None):
    host = FakeHostContext(data_root=tmp_path)
    plugin, host = build_plugin(host)
    # 节奏门（去重/概率）另有 test_rhythm.py 专铉；本文件只铉通道/冷却/fail-closed，
    # 默认把去重关掉免得误伤跨冷却窗口的重发场景。
    overrides = {"recent_dedup_count": 0, **(send_overrides or {})}
    settings = StickerManagerSettings(
        enabled=enabled,
        send=SendSettings(**overrides),
        storage=StorageSettings(),
    )
    lib = plugin._library
    lib.load(force=True)
    return plugin, host, settings, lib


class TestSendChannel:
    def test_small_image_goes_inline(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert result.ok
        (call,) = host.push.calls
        part = call["parts"][0]
        assert part["type"] == "image" and part["data"] == PNG_BYTES
        assert not host.images.calls  # 没走上传

    def test_large_image_goes_upload(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        big = PNG_BYTES + b"\x00" * (300 * 1024)  # 超过 256KiB 内联预算
        sticker, _ = lib.add(data=big, desc="大图", tags=[])
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert result.ok
        (call,) = host.push.calls
        part = call["parts"][0]
        assert part.get("url", "").startswith("https://stub.local/")
        assert len(host.images.calls) == 1  # 走过一次上传

    def test_gif_over_budget_refused_not_flattened(self, tmp_path, run_async):
        # 动图超过内联预算：宁可不发，也不走上传被压平成 JPEG
        plugin, host, settings, lib = _setup(tmp_path)
        big_gif = GIF_BYTES + b"\x00" * (300 * 1024)
        sticker, _ = lib.add(data=big_gif, desc="大动图", tags=[])
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "sticker_too_large"
        assert host.push.calls == []
        assert host.images.calls == []  # 上传通道一次都没碰

    def test_push_contract(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        (call,) = host.push.calls
        assert call["visibility"] == ["chat"]
        assert call["ai_behavior"] == "blind"
        assert call["target_lanlan"] == "K"

    def test_tool_send_is_immediate_even_when_turn_end_adapter_exists(self, tmp_path, run_async):
        """A host log marker must not cancel a valid model tool selection."""
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])

        class MustNotGate:
            def busy_states(self):
                raise AssertionError("tool sends must not query reply state")

            def arm(self, _lanlan):
                raise AssertionError("tool sends must not arm a deferred ticket")

        plugin._sender._turn_end = MustNotGate()
        result = run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0)
        )

        assert result.ok and not result.queued
        assert len(host.push.calls) == 1
        assert plugin._library.get(sticker.id).use_count == 1
        assert plugin._library.read_usage()[-1]["ok"] is True


class TestGuards:
    def test_fail_closed_when_disabled(self, tmp_path, run_async):
        from dataclasses import replace

        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        settings = replace(settings, enabled=False)
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "not_enabled"
        assert host.push.calls == []

    def test_disabled_sticker_never_leaks(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        banned, _ = lib.update(sticker.id, disabled=True)
        result = run_async(plugin._sender.send(banned, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "sticker_disabled"
        assert host.push.calls == []

    def test_missing_file_reports(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        lib.image_path(sticker).unlink()
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "sticker_file_missing"

    def test_transport_rejection_propagates(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        host.push.reject_reason = "payload_too_large"
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "payload_too_large"
        # 被拒的投递不推进冷却、不记使用次数
        assert plugin._sender.cooldown_remaining("K", settings, now=1000.0) == 0.0
        assert lib.get(sticker.id).use_count == 0

    def test_upload_failure_becomes_too_large(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        big = PNG_BYTES + b"\x00" * (300 * 1024)
        sticker, _ = lib.add(data=big, desc="大图", tags=[])
        host.images.fail = True
        result = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert not result.ok
        assert result.code == "sticker_too_large"
        assert host.push.calls == []


class TestCooldown:
    def test_disabled_cooldown_ignores_future_submission_timestamp(self, tmp_path):
        plugin, _host, _settings, _lib = _setup(tmp_path)
        settings = StickerManagerSettings(enabled=True, send=SendSettings(cooldown_sec=0.0))
        plugin._sender._last_sent["K"] = 1000.001
        assert plugin._sender.cooldown_remaining("K", settings, now=1000.0) == 0.0

    def test_second_send_within_cooldown_blocked(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        first = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        assert first.ok
        second = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1010.0))
        assert not second.ok
        assert second.code == "send_cooldown"
        # 过了冷却窗口就放行
        third = run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0 + 21.0))
        assert third.ok
        assert len(host.push.calls) == 2

    def test_cooldown_is_per_character(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        assert run_async(plugin._sender.send(sticker, lanlan="A", settings=settings, source="tool", now=1000.0)).ok
        assert run_async(plugin._sender.send(sticker, lanlan="B", settings=settings, source="tool", now=1001.0)).ok


class TestConcurrentSend:
    @pytest.mark.parametrize("source,force", [("tool", False), ("tool", True), ("panel", False), ("agent", False)])
    def test_same_character_cannot_send_during_upload(self, tmp_path, run_async, source, force):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES + b"0" * (300 * 1024), desc="large", tags=[])

        async def scenario():
            started, release = asyncio.Event(), asyncio.Event()

            async def upload(*args, **kwargs):
                started.set()
                await release.wait()
                return {"type": "image", "url": "https://stub.local/image.jpg"}

            host.images.upload = upload
            first = asyncio.create_task(
                plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0)
            )
            await started.wait()
            second = asyncio.create_task(
                plugin._sender.send(
                    sticker, lanlan="K", settings=settings, source=source, force=force, now=1000.0
                )
            )
            await asyncio.sleep(0)
            release.set()
            return await asyncio.gather(first, second)

        first, second = run_async(scenario())
        assert first.ok and not second.ok and second.code == "send_cooldown"
        assert len(host.push.calls) == 1
        assert lib.get(sticker.id).use_count == 1
        assert len(lib.read_usage()) == 1

    def test_pending_send_does_not_block_another_character(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        large, _ = lib.add(data=PNG_BYTES + b"0" * (300 * 1024), desc="large", tags=[])
        small, _ = lib.add(data=PNG_BYTES, desc="small", tags=[])

        async def scenario():
            started, release = asyncio.Event(), asyncio.Event()

            async def upload(*args, **kwargs):
                started.set()
                await release.wait()
                return {"type": "image", "url": "https://stub.local/image.jpg"}

            host.images.upload = upload
            first = asyncio.create_task(
                plugin._sender.send(large, lanlan="A", settings=settings, source="tool", now=1000.0)
            )
            await started.wait()
            try:
                second = await plugin._sender.send(
                    small, lanlan="B", settings=settings, source="tool", now=1000.0
                )
            finally:
                release.set()
            return await first, second

        assert all(result.ok for result in run_async(scenario()))
        assert len(host.push.calls) == 2

    def test_cancelled_upload_releases_reservation_without_starting_cooldown(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES + b"0" * (300 * 1024), desc="large", tags=[])

        async def scenario():
            started = asyncio.Event()
            original_upload = host.images.upload

            async def upload(*args, **kwargs):
                started.set()
                await asyncio.Event().wait()

            host.images.upload = upload
            task = asyncio.create_task(
                plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0)
            )
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            host.images.upload = original_upload
            return await plugin._sender.send(
                sticker, lanlan="K", settings=settings, source="tool", now=1000.0
            )

        assert run_async(scenario()).ok
        assert len(host.push.calls) == 1


    def test_reservation_is_shared_between_host_threads(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        large, _ = lib.add(data=PNG_BYTES + b"0" * (300 * 1024), desc="large", tags=[])
        small, _ = lib.add(data=PNG_BYTES, desc="small", tags=[])
        started, release = threading.Event(), threading.Event()

        async def upload(*args, **kwargs):
            started.set()
            assert await asyncio.to_thread(release.wait, 5.0)
            return {"type": "image", "url": "https://stub.local/image.jpg"}

        host.images.upload = upload
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(
                run_async, plugin._sender.send(large, lanlan="K", settings=settings, source="tool", now=1000.0)
            )
            try:
                assert started.wait(5.0)
                second = run_async(
                    plugin._sender.send(small, lanlan="K", settings=settings, source="panel", now=1000.0)
                )
                assert not second.ok and second.code == "send_cooldown"
            finally:
                release.set()
            assert first.result(timeout=5.0).ok
        assert len(host.push.calls) == 1

    def test_upload_failure_releases_reservation(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES + b"0" * (300 * 1024), desc="large", tags=[])
        host.images.fail = True
        assert not run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0)
        ).ok
        host.images.fail = False
        assert run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0)
        ).ok
        assert len(host.push.calls) == 1

    def test_cooldown_starts_after_upload_finishes(self, tmp_path, run_async, monkeypatch):
        from sticker_manager.services import sender as sender_module

        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES + b"0" * (300 * 1024), desc="large", tags=[])
        clock = [10.0]
        monkeypatch.setattr(sender_module.time, "monotonic", lambda: clock[0])

        async def upload(*args, **kwargs):
            clock[0] += 8.0
            return {"type": "image", "url": "https://stub.local/image.jpg"}

        host.images.upload = upload
        assert run_async(
            plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0)
        ).ok
        assert plugin._sender.cooldown_remaining("K", settings, now=1008.0) == 20.0


class TestLedger:
    def test_success_recorded(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        sticker, _ = lib.add(data=PNG_BYTES, desc="笑", tags=[])
        run_async(plugin._sender.send(sticker, lanlan="K", settings=settings, source="tool", now=1000.0))
        entries = lib.read_usage()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["id"] == sticker.id
        assert entry["lanlan"] == "K"
        assert entry["source"] == "tool"
        assert entry["ok"] is True

    def test_failure_recorded_via_note_attempt(self, tmp_path, run_async):
        plugin, host, settings, lib = _setup(tmp_path)
        plugin._sender.note_attempt_failed(
            lanlan="K", sticker_id="ghost", code="no_match", settings=settings, now=1000.0
        )
        (entry,) = lib.read_usage()
        assert entry["ok"] is False
        assert entry["code"] == "no_match"
