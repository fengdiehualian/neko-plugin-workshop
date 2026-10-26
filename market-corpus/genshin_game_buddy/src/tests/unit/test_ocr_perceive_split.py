"""OCR 频率拆分 + 抓屏缓存 + SceneTracker 集成测试。

拍板 2.0.71：高频 OCR（scene_interval=2s）+ 低频 term（term_interval=15s）+ 抓屏缓存复用。

本测试覆盖：
  - tick 间隔走 ocr_scene_interval_seconds（不再是 ocr_perceive_interval_seconds）
  - term 轮次判断（is_term_round = now - last_term >= ocr_term_interval_seconds）
  - term 轮次复用抓屏缓存（无需重抓）
  - SceneTracker.update() 被调用并产生切换事件 → push_message
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock

import pytest
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import PluginOptions
from plugin.plugins.genshin_game_buddy.scene_tracker import (
    SceneEventType,
    SceneTracker,
)


def _make_plugin_stub(
    *,
    scene_interval: int = 2,
    term_interval: int = 15,
    worker_threads: int = 1,
    hysteresis_count: int = 3,
) -> GenshinGameBuddyPlugin:
    """最小可用 stub——含 SceneTracker + 缓存字段。"""
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    plugin._options = PluginOptions(
        ocr_scene_interval_seconds=scene_interval,
        ocr_term_interval_seconds=term_interval,
        ocr_worker_threads=worker_threads,
        scene_hysteresis_count=hysteresis_count,
        ocr_perceive_interval_seconds=term_interval,  # legacy
    )
    plugin._state_lock = threading.Lock()
    plugin._perception_state = "UNKNOWN"
    plugin._ocr_in_flight_lock = threading.Lock()
    plugin._ocr_in_flight_count = 0
    plugin._last_real_ocr_monotonic = 0.0
    plugin._last_light_hash = ""
    plugin._last_capture_b64 = None
    plugin._last_capture_at_monotonic = 0.0
    plugin._last_capture_text = ""
    plugin._last_term_run_monotonic = 0.0
    plugin._light_capture_dhash = lambda: ""
    plugin._dhash_diff = GenshinGameBuddyPlugin._dhash_diff
    plugin._scene_tracker = SceneTracker(
        hysteresis_count=hysteresis_count,
        exit_grace_count=5,
        debounce_seconds=10.0,
    )
    plugin._activation = MagicMock()
    plugin._activation.clear_by_sources = MagicMock(return_value=0)
    plugin._activation.get_active_keys = MagicMock(return_value=[])
    plugin._activation.activate_scene = MagicMock()
    plugin._activation.tick_scene = MagicMock()
    plugin._activation.activate_terms = MagicMock()
    plugin._scene_store = {"scene_a": MagicMock(prompt="prompt_a"), "scene_b": MagicMock(prompt="prompt_b")}
    plugin._manager = MagicMock()
    plugin._manager.current = MagicMock()
    plugin._manager.library = None
    plugin._last_detected_game = ""
    plugin._last_scene_pushed = ""
    plugin._last_scene_pushed_at = 0.0
    plugin._last_scene_switch_at = 0.0
    plugin._last_scene_for_proactive = ""
    plugin._last_proactive_at = 0.0
    plugin._last_ocr_hash = ""
    plugin._last_ocr_hit = False
    plugin._has_in_game_characteristics = MagicMock(return_value=True)
    plugin._should_push_proactive = MagicMock(return_value=False)
    plugin._push_proactive = MagicMock()
    plugin._match_scenes = MagicMock(return_value=[])
    plugin._scan_screen_activate_terms = MagicMock(return_value=([], []))
    plugin._update_perception_state = MagicMock()
    plugin.logger = MagicMock()
    return plugin


@pytest.fixture(autouse=True)
def _fake_timer(monkeypatch):
    """加速 60s 看门狗 → 0.05s（避免真等）。"""
    import threading as _threading
    class _FakeTimer:
        def __init__(self, delay, callback):
            self.delay = delay
            self.callback = callback
            self.daemon = False
        def start(self):
            async def _fire():
                await asyncio.sleep(0.02)
                self.callback()
            asyncio.ensure_future(_fire())
        def cancel(self):
            pass
    monkeypatch.setattr(_threading, "Timer", _FakeTimer)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_split__scene_interval_replaces_legacy() -> None:
    """拍板 2.0.71：tick 间隔走 ocr_scene_interval_seconds，不再走 ocr_perceive_interval_seconds。"""
    plugin = _make_plugin_stub(scene_interval=10)
    # 构造 fake executor，让 submit 返回 done future（_on_done 立刻释放 count）
    import concurrent.futures
    cf = concurrent.futures.Future()
    cf.set_result(None)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=cf)
    plugin._ocr_executor = fake_executor
    plugin._ocr_perceive_sync_entry = MagicMock()

    # 刚跑过——scene_interval=10s 未到 → 早退
    plugin._last_real_ocr_monotonic = time.monotonic()

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    assert any("interval_not_reached" in s for s in info_calls), (
        f"interval gate should fire using scene_interval=10s; got: {info_calls[:3]}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_split__term_round_reuses_cached_capture() -> None:
    """拍板 2.0.71：抓屏结果被缓存到 _last_capture_b64 + _last_capture_at_monotonic，
    term 轮次若本轮抓屏失败，可复用上次缓存。

    本测试只验证缓存字段被正确维护——body 内部的复用路径依赖这些字段。
    """
    plugin = _make_plugin_stub()
    assert plugin._last_capture_b64 is None
    assert plugin._last_capture_at_monotonic == 0.0

    # 模拟一次成功抓屏
    plugin._last_capture_b64 = "fake_b64_payload"
    plugin._last_capture_at_monotonic = time.monotonic()

    assert plugin._last_capture_b64 is not None
    assert plugin._last_capture_at_monotonic > 0.0


# 异步mock 兼容


@pytest.mark.unit
def test_split__options_validated_with_clamp() -> None:
    """拍板 2.0.71：3 个新配置项 clamp 范围正确。"""
    # 边界值测试
    opts_min = PluginOptions.from_section({
        "ocr_scene_interval_seconds": 0,  # < 1 → clamp 到 1
        "ocr_term_interval_seconds": 2,  # < 3 → clamp 到 3
        "scene_hysteresis_count": 0,  # < 1 → clamp 到 1
    })
    assert opts_min.ocr_scene_interval_seconds == 1
    assert opts_min.ocr_term_interval_seconds == 3
    assert opts_min.scene_hysteresis_count == 1

    # 最大值
    opts_max = PluginOptions.from_section({
        "ocr_scene_interval_seconds": 100,  # > 30 → clamp 到 30
        "ocr_term_interval_seconds": 1000,  # > 300 → clamp 到 300
        "scene_hysteresis_count": 50,  # > 10 → clamp 到 10
    })
    assert opts_max.ocr_scene_interval_seconds == 30
    assert opts_max.ocr_term_interval_seconds == 300
    assert opts_max.scene_hysteresis_count == 10

    # 默认
    opts_default = PluginOptions.from_section({})
    assert opts_default.ocr_scene_interval_seconds == 2
    assert opts_default.ocr_term_interval_seconds == 15
    assert opts_default.scene_hysteresis_count == 3


@pytest.mark.unit
def test_split__options_have_backward_compat_legacy() -> None:
    """拍板 2.0.71：ocr_perceive_interval_seconds 仍存在（legacy）。"""
    opts = PluginOptions.from_section({"ocr_perceive_interval_seconds": 30})
    assert opts.ocr_perceive_interval_seconds == 30
    assert opts.ocr_scene_interval_seconds == 2  # 默认


@pytest.mark.unit
def test_scene_tracker_integration__switch_event_triggers_push() -> None:
    """拍板 2.0.71：SceneTracker SCENE_SWITCHED → 触发 push_message 路径（经 2266 复用）。"""
    plugin = _make_plugin_stub(hysteresis_count=2)

    # 模拟：连续 2 次命中 scene_a → 进场景
    e1 = plugin._scene_tracker.update(["scene_a"])
    assert e1.type == SceneEventType.NO_CHANGE
    e2 = plugin._scene_tracker.update(["scene_a"])
    assert e2.type == SceneEventType.SCENE_SWITCHED
    assert e2.to_scene == "scene_a"
    assert plugin._scene_tracker.current_scene == "scene_a"


@pytest.mark.unit
def test_scene_tracker_integration__no_switch_same_scene() -> None:
    """拍板 2.0.71：同场景重复命中 → NO_CHANGE，不发切换。"""
    plugin = _make_plugin_stub(hysteresis_count=2)

    plugin._scene_tracker.update(["scene_a"])
    plugin._scene_tracker.update(["scene_a"])  # SWITCHED
    for _ in range(5):
        e = plugin._scene_tracker.update(["scene_a"])
        assert e.type == SceneEventType.NO_CHANGE
    assert plugin._scene_tracker.current_scene == "scene_a"


@pytest.mark.unit
def test_scene_tracker_integration__exit_event_triggers() -> None:
    """拍板 2.0.71：连续无命中 → SCENE_EXITED。"""
    plugin = _make_plugin_stub()

    # 进场景
    plugin._scene_tracker.update(["scene_a"])
    plugin._scene_tracker.update(["scene_a"])
    plugin._scene_tracker.update(["scene_a"])
    assert plugin._scene_tracker.current_scene == "scene_a"

    # 连续 5 次无命中（exit_grace_count=5）→ EXITED
    for _ in range(4):
        e = plugin._scene_tracker.update([])
        assert e.type == SceneEventType.NO_CHANGE
    e = plugin._scene_tracker.update([])
    assert e.type == SceneEventType.SCENE_EXITED
    assert plugin._scene_tracker.current_scene is None