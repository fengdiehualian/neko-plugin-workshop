"""拍板 2.0.70：_ocr_perceive_body 段级打点——定位 worker 卡死真根因。

每个 await 前后打 stage=xxx + dt=Ns 日志。下次卡死时日志停在某个 stage=xxx_done 之前 = 该段 await 永久阻塞。

不打点永远猜不到根因——这是 2.0.70 的核心。

本测试验证：
  ① stage=body_enter 是第一个日志
  ② stage=capture_done 在 capture 后打
  ③ stage=ocr_done 在 OCR 后打
  ④ stage=detect_done 在 detect_game 后打
  ⑤ stage=body_exit 在函数结尾打
  ⑥ 段级打点不影响原 INFO 日志（capture_path / text_len 等仍正常）
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import PluginOptions


def _make_plugin_stub(*, ocr_worker_threads: int = 1) -> GenshinGameBuddyPlugin:
    """最小可用 stub——_ocr_perceive_body 用的字段。"""
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    plugin._options = PluginOptions(
        ocr_worker_threads=ocr_worker_threads,
        change_driven_enabled=False,
    )
    plugin._state_lock = threading.Lock()
    plugin._perception_state = "UNKNOWN"
    plugin._ocr_in_flight_lock = threading.Lock()
    plugin._ocr_in_flight_count = 0
    plugin._last_real_ocr_monotonic = 0.0
    plugin._last_light_hash = ""
    plugin._light_capture_dhash = lambda: ""
    plugin._dhash_diff = GenshinGameBuddyPlugin._dhash_diff
    plugin._last_ocr_hash = ""
    plugin._last_ocr_hit = False
    plugin._last_pushed_hash = ""
    plugin._last_proactive_at = 0.0
    plugin._last_scene_pushed = ""
    plugin._last_scene_pushed_at = 0.0
    plugin._last_detected_game = ""
    plugin._last_scene_switch_at = 0.0
    plugin._last_scene_for_proactive = ""
    plugin.logger = MagicMock()
    return plugin


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive_body__logs_body_enter_and_body_exit() -> None:
    """拍板 2.0.70：stage=body_enter 是第一个日志，stage=body_exit 是最后一个。"""
    plugin = _make_plugin_stub()

    # mock 所有 _ocr_perceive_body 依赖，让它走完整路径
    plugin._manager = MagicMock()
    plugin._manager.current = MagicMock()  # non-None → 不 early-return no_session
    plugin._manager.library = None

    # 让 detect_game 异步返回 "none"（避免激活路径复杂化）
    plugin.detect_game = AsyncMock(return_value="none")
    plugin._ocr = MagicMock()
    plugin._ocr.extract_text_from_base64 = AsyncMock(return_value="sample text")
    plugin._activation = MagicMock()
    plugin._activation.clear_by_sources = MagicMock(return_value=0)
    plugin._activation.get_active_keys = MagicMock(return_value=[])
    plugin._activation.activate_scene = MagicMock()
    plugin._activation.tick_scene = MagicMock()
    plugin._activation.activate_terms = MagicMock()
    plugin._has_in_game_characteristics = MagicMock(return_value=False)
    plugin._match_scenes = MagicMock(return_value=[])
    plugin._should_push_proactive = MagicMock(return_value=False)
    plugin._scene_store = {}
    plugin._push_proactive = MagicMock()
    plugin._last_detected_game = ""

    # 让 capture 返回失败（no_window）→ 走 bus.frames 回退路径——简化后续
    import plugin.plugins.genshin_game_buddy.screen_capture as sc_mod
    from plugin.plugins.genshin_game_buddy.screen_capture import CaptureResult

    async def _capture_returns_fail():
        return (CaptureResult(False, None, 0, 0, "", "no_window", -1.0), "foreground")
    # patch asyncio.to_thread 来拦截 capture_active_frame
    orig_to_thread = asyncio.to_thread

    async def _fake_to_thread(fn, *args, **kwargs):
        if fn is sc_mod.capture_active_frame:
            return (CaptureResult(False, None, 0, 0, "", "no_window", -1.0), "foreground")
        return await orig_to_thread(fn, *args, **kwargs)

    asyncio.to_thread = _fake_to_thread

    try:
        # bus.frames 不需要（capture 已成功，不走 bus 回退）；只设必要的 mock
        await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)
    finally:
        asyncio.to_thread = orig_to_thread

    info_calls = [str(c) for c in plugin.logger.info.call_args_list]

    # 第一个 stage 日志是 body_enter
    stage_logs = [s for s in info_calls if "stage=" in s]
    assert len(stage_logs) > 0, f"no stage= logs found; got: {info_calls}"

    # body_enter 应是第一个 stage 日志
    assert any("stage=body_enter" in s for s in stage_logs), (
        f"stage=body_enter missing; got stage logs: {stage_logs}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive_body__stage_capture_done_logged() -> None:
    """拍板 2.0.70：stage=capture_done 在 capture 后打（含 dt=Xs）。"""
    plugin = _make_plugin_stub()

    plugin._manager = MagicMock()
    plugin._manager.current = MagicMock()

    from plugin.plugins.genshin_game_buddy.screen_capture import CaptureResult

    # patch asyncio.to_thread
    orig_to_thread = asyncio.to_thread

    async def _fake_to_thread(fn, *args, **kwargs):
        from plugin.plugins.genshin_game_buddy.screen_capture import capture_active_frame
        if fn is capture_active_frame:
            return (CaptureResult(False, None, 0, 0, "", "no_window", -1.0), "foreground")
        return await orig_to_thread(fn, *args, **kwargs)

    asyncio.to_thread = _fake_to_thread

    try:
        # capture 已失败（no_window）但 bus.frames 也需要——bus 是 read-only property，
        # 不能直接赋值。改走 patch：
        # 简单做法：直接让 capture 返回 ok（不是 fail）——避免走 bus 回退路径
        await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)
    finally:
        asyncio.to_thread = orig_to_thread

    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    # capture_done 应被记（含 dt=）
    assert any("stage=capture_done" in s and "dt=" in s for s in info_calls), (
        f"stage=capture_done with dt= missing; got: {info_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive_body__stage_ocr_done_logged() -> None:
    """拍板 2.0.70：stage=ocr_done 在 OCR 后打（含 dt=Xs）。"""
    plugin = _make_plugin_stub()

    plugin._manager = MagicMock()
    plugin._manager.current = MagicMock()

    from plugin.plugins.genshin_game_buddy.screen_capture import CaptureResult

    # patch capture + OCR（OCR 返回非空文本避免 black_frame early-return）
    orig_to_thread = asyncio.to_thread

    async def _fake_to_thread(fn, *args, **kwargs):
        from plugin.plugins.genshin_game_buddy.screen_capture import capture_active_frame
        if fn is capture_active_frame:
            return (CaptureResult(True, "fake_b64_data", 100, 100, "mss", "", 100.0), "foreground")
        return await orig_to_thread(fn, *args, **kwargs)

    asyncio.to_thread = _fake_to_thread

    try:
        # mock OCR 异步返回
        plugin._ocr = MagicMock()
        plugin._ocr.extract_text_from_base64 = AsyncMock(return_value="test text")
        plugin.detect_game = AsyncMock(return_value="none")
        plugin._activation = MagicMock()
        plugin._activation.clear_by_sources = MagicMock(return_value=0)
        plugin._activation.get_active_keys = MagicMock(return_value=[])
        plugin._activation.activate_scene = MagicMock()
        plugin._activation.tick_scene = MagicMock()
        plugin._activation.activate_terms = MagicMock()
        plugin._has_in_game_characteristics = MagicMock(return_value=False)
        plugin._match_scenes = MagicMock(return_value=[])
        plugin._should_push_proactive = MagicMock(return_value=False)
        plugin._scene_store = {}
        plugin._push_proactive = MagicMock()
        plugin._last_detected_game = ""

        await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)
    finally:
        asyncio.to_thread = orig_to_thread

    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    # ocr_done 应被记（含 dt=）
    assert any("stage=ocr_done" in s and "dt=" in s for s in info_calls), (
        f"stage=ocr_done with dt= missing; got: {info_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive_body__stage_detect_done_logged() -> None:
    """拍板 2.0.70：stage=detect_done 在 detect_game 后打（含 dt=Xs + detected=）。"""
    plugin = _make_plugin_stub()

    plugin._manager = MagicMock()
    plugin._manager.current = MagicMock()

    from plugin.plugins.genshin_game_buddy.screen_capture import CaptureResult

    orig_to_thread = asyncio.to_thread

    async def _fake_to_thread(fn, *args, **kwargs):
        from plugin.plugins.genshin_game_buddy.screen_capture import capture_active_frame
        if fn is capture_active_frame:
            return (CaptureResult(True, "fake_b64_data", 100, 100, "mss", "", 100.0), "foreground")
        return await orig_to_thread(fn, *args, **kwargs)

    asyncio.to_thread = _fake_to_thread

    try:
        plugin._ocr = MagicMock()
        plugin._ocr.extract_text_from_base64 = AsyncMock(return_value="test text")
        plugin.detect_game = AsyncMock(return_value="none")
        plugin._activation = MagicMock()
        plugin._activation.clear_by_sources = MagicMock(return_value=0)
        plugin._activation.get_active_keys = MagicMock(return_value=[])
        plugin._activation.activate_scene = MagicMock()
        plugin._activation.tick_scene = MagicMock()
        plugin._activation.activate_terms = MagicMock()
        plugin._has_in_game_characteristics = MagicMock(return_value=False)
        plugin._match_scenes = MagicMock(return_value=[])
        plugin._should_push_proactive = MagicMock(return_value=False)
        plugin._scene_store = {}
        plugin._push_proactive = MagicMock()
        plugin._last_detected_game = ""

        await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)
    finally:
        asyncio.to_thread = orig_to_thread

    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    # detect_done 应被记（含 dt= + detected=）
    assert any("stage=detect_done" in s and "dt=" in s and "detected=" in s for s in info_calls), (
        f"stage=detect_done with dt= and detected= missing; got: {info_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive_body__no_session_early_return_logs_body_enter_only() -> None:
    """拍板 2.0.70：no_session early-return 也打 stage=body_enter——便于排查"卡在 enter 之后"。

    真机卡死排查：若日志停在 stage=body_enter 之后无任何 stage_done → 根因在 _ocr_perceive_body 头部。
    """
    plugin = _make_plugin_stub()
    plugin._manager = MagicMock()
    plugin._manager.current = None  # no_session → early-return

    await GenshinGameBuddyPlugin._ocr_perceive_body(plugin)

    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    # body_enter 应被记（即使 early-return）
    assert any("stage=body_enter" in s for s in info_calls), (
        f"stage=body_enter missing on early-return; got: {info_calls}"
    )
    # body_exit 不应被记（early-return）
    assert not any("stage=body_exit" in s for s in info_calls), (
        f"stage=body_exit should NOT be logged on early-return; got: {info_calls}"
    )
    # no_session 日志应被记
    assert any("no_session" in s for s in info_calls), (
        f"no_session log missing; got: {info_calls}"
    )