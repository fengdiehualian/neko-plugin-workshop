"""拍板 2.0.68：ocr_perceive 三层防御 + 计数释放。

真机 2.0.67 暴露：14 分钟只 1 条 tick begin——怀疑 schedule 抛异常让
`_ocr_in_flight_count` 永久卡住、后续所有 tick 被 worker 闸静默挡掉。

本测试直接调 `ocr_perceive` 协程（绕过 timer 装饰器），构造 schedule 失败的场景，
验证：
  ① count 在异常路径被释放
  ② 外层 try/except 吞掉任何异常（timer 协程自身不抛）
  ③ early-return 路径有 INFO 日志（不能静默卡死）

不构造完整 plugin——用 `GenshinGameBuddyPlugin.__new__` 跳过 __init__，
只填 ocr_perceive 用的字段：_options / _state_lock / _perception_state /
_ocr_executor / _ocr_in_flight_lock / _ocr_in_flight_count /
_last_real_ocr_monotonic / _light_capture_dhash / _dhash_diff / logger /
_ocr_perceive_sync_entry。
"""
from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import PluginOptions


def _make_plugin_stub(
    *,
    light_dhash_returns: str = "",
    interval_seconds: int = 3,
    worker_threads: int = 1,
    change_driven_enabled: bool = False,
) -> GenshinGameBuddyPlugin:
    """最小可用 stub——只填 ocr_perceive 用的字段。"""
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    plugin._options = PluginOptions(
        ocr_perceive_interval_seconds=interval_seconds,
        ocr_worker_threads=worker_threads,
        change_driven_enabled=change_driven_enabled,
    )
    plugin._state_lock = threading.Lock()
    plugin._perception_state = "UNKNOWN"  # != OUT_OF_GAME → change_detect 启用时跑 dhash
    plugin._ocr_in_flight_lock = threading.Lock()
    plugin._ocr_in_flight_count = 0
    plugin._last_real_ocr_monotonic = 0.0
    plugin._last_light_hash = ""
    # light dhash mock
    plugin._light_capture_dhash = lambda: light_dhash_returns
    plugin._dhash_diff = GenshinGameBuddyPlugin._dhash_diff
    # logger mock——记录 INFO 调用便于断言
    plugin.logger = MagicMock()
    # executor mock + sync entry mock
    plugin._ocr_executor = MagicMock()
    # 关键：让 run_in_executor 抛异常模拟"schedule 失败"
    def _boom_run_in_executor(*_args, **_kwargs):
        raise RuntimeError("simulated schedule failure")
    plugin._ocr_executor.submit = MagicMock(side_effect=RuntimeError("simulated schedule failure"))
    plugin._ocr_perceive_sync_entry = MagicMock()
    # 不要真的调 asyncio.get_running_loop().run_in_executor —— 改用 patch
    return plugin


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__schedule_failure_releases_count(monkeypatch) -> None:
    """拍板 2.0.68：schedule 阶段抛异常 → count 必须释放（不卡 worker 闸）。

    模拟：asyncio.get_running_loop().run_in_executor 抛 RuntimeError。
    期望：函数不抛（外层 try/except）+ count 仍为 0（已释放）。
    """
    plugin = _make_plugin_stub()

    # 用 patch 让 run_in_executor 抛异常（get_running_loop 是 sync 函数）
    class _FakeLoop:
        def run_in_executor(self, *_a, **_kw):
            raise RuntimeError("simulated schedule failure")

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _FakeLoop())

    # 调一次 tick
    await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # 关键断言：count 必须为 0（已释放），不卡死后续 tick
    with plugin._ocr_in_flight_lock:
        assert plugin._ocr_in_flight_count == 0, (
            f"count not released after schedule failure: {plugin._ocr_in_flight_count}"
        )

    # 外层 try/except 吞掉 → logger.exception 被调用
    assert plugin.logger.exception.called, "outer exception logger not called"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__outer_exception_swallowed() -> None:
    """拍板 2.0.68：tick 内任何异常都被外层 try/except 吞掉——timer 不会停。"""
    plugin = _make_plugin_stub()

    # 让 _light_capture_dhash 自身抛（await 异常）
    def _boom_dhash():
        raise RuntimeError("dhash kernel panic")
    plugin._light_capture_dhash = _boom_dhash
    plugin._options = PluginOptions(  # 启用 change_detect 让 dhash 跑
        change_driven_enabled=True,
    )

    # 调一次 tick——不应抛
    try:
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    except Exception as exc:
        pytest.fail(f"ocr_perceive must not raise, got {type(exc).__name__}: {exc}")

    # 外层 exception 被记
    assert plugin.logger.exception.called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__early_return_interval_logged(monkeypatch) -> None:
    """拍板 2.0.68：interval 不达标 early-return → 必须打 INFO 日志。

    真实场景：第一次 tick 后 _last_real_ocr_monotonic 更新，下一次 3s tick 时
    interval(15s) 未到 → 静默 return 过去就是 bug。本测试验证有 INFO 日志。
    """
    plugin = _make_plugin_stub(interval_seconds=15)
    plugin._options = PluginOptions(  # 显式关掉 change_detect 简化路径
        ocr_perceive_interval_seconds=15,
        ocr_scene_interval_seconds=15,  # 拍板 2.0.71：gate 用 scene_interval
        change_driven_enabled=False,
    )

    # 让时间看上去"刚推过 0s"—— now - last = 3.0s < 15s → interval gate 应挡掉
    import time as _time
    base = _time.monotonic()
    plugin._last_real_ocr_monotonic = base  # 推过瞬间
    monkeypatch.setattr(_time, "monotonic", lambda: base + 3.0)

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # 必有 INFO 日志含 "interval_not_reached"
    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    assert any("interval_not_reached" in s for s in info_calls), (
        f"interval_not_reached INFO log missing; got: {info_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__early_return_worker_busy_logged() -> None:
    """拍板 2.0.68：worker 闸被挡 → 必须打 INFO 日志。

    场景：_ocr_in_flight_count >= workers → 静默 return 过去就是 bug。
    """
    plugin = _make_plugin_stub(worker_threads=1)
    plugin._options = PluginOptions(  # 关 change_detect 简化路径
        ocr_worker_threads=1,
        change_driven_enabled=False,
    )
    plugin._ocr_in_flight_count = 1  # 已经 1 个在跑
    plugin._last_real_ocr_monotonic = 0.0  # interval 已过 → 进 worker 闸分支

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # 必有 INFO 日志含 "worker_busy"
    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    assert any("worker_busy" in s for s in info_calls), (
        f"worker_busy INFO log missing; got: {info_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__normal_path_logs_tick_begin(monkeypatch) -> None:
    """拍板 2.0.70：正常 schedule 路径 → tick begin INFO 日志正常出现。

    2.0.70 起 schedule 改用 `self._ocr_executor.submit()` 直接拿 cf_future——mock _ocr_executor。
    """
    plugin = _make_plugin_stub(interval_seconds=15)
    plugin._options = PluginOptions(  # 关 change_detect 简化路径
        ocr_perceive_interval_seconds=15,
        change_driven_enabled=False,
    )

    # 拍板 2.0.70：mock _ocr_executor.submit() 返回成功 cf_future
    fake_future = MagicMock()
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=fake_future)
    plugin._ocr_executor = fake_executor
    plugin._ocr_perceive_sync_entry = MagicMock()

    # 关掉 threading.Timer（不真等 60s，加速测试）
    import threading as _threading
    monkeypatch.setattr(_threading, "Timer", lambda *_a, **_kw: MagicMock(start=lambda: None))

    plugin._last_real_ocr_monotonic = 0.0  # interval 已过 → tick begin 应触发

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    assert any("tick begin" in s for s in info_calls), (
        f"tick begin INFO log missing; got: {info_calls}"
    )
    # count += 1 成功
    assert plugin._ocr_in_flight_count == 1
    # add_done_callback 注册了（count 减的钩子）
    assert fake_future.add_done_callback.called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__change_detect_dhash_await_failure_swallowed(monkeypatch) -> None:
    """拍板 2.0.70：change-detect 中 await asyncio.to_thread 抛异常 → 吞掉，继续走 interval。

    注意 2.0.70 默认 change_driven_enabled=False——本测试显式开才能触发 dhash 路径。
    """
    plugin = _make_plugin_stub(change_driven_enabled=True, interval_seconds=15)
    plugin._options = PluginOptions(
        ocr_perceive_interval_seconds=15,
        change_driven_enabled=True,
    )

    # 让 asyncio.to_thread 抛异常
    async def _boom_to_thread(*_a, **_kw):
        raise RuntimeError("event loop closed")
    monkeypatch.setattr(asyncio, "to_thread", _boom_to_thread)

    # interval 已过（_last_real_ocr_monotonic=0 + now >> 15）→ 应走 interval 触发
    await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # change_detect dhash await failed 应被记
    exception_calls = [str(c) for c in plugin.logger.exception.call_args_list]
    assert any("change_detect dhash await failed" in s for s in exception_calls), (
        f"change_detect dhash await failure log missing; got: {exception_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__two_consecutive_ticks_no_count_leak(monkeypatch) -> None:
    """拍板 2.0.70：连续 2 次 tick，即使第 1 次 schedule 失败，第 2 次仍能 schedule。

    模拟时序：
      - tick 1：schedule 失败 → count 释放回 0
      - tick 2：schedule 成功 → count += 1

    2.0.70 起 schedule 用 _ocr_executor.submit——mock _ocr_executor.submit 抛/正常。
    """
    plugin = _make_plugin_stub()

    # 关掉 threading.Timer（加速测试）
    import threading as _threading
    monkeypatch.setattr(_threading, "Timer", lambda *_a, **_kw: MagicMock(start=lambda: None))

    # 第 1 次 submit 抛异常
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=RuntimeError("first attempt fails"))
    plugin._ocr_executor = fake_executor
    plugin._ocr_perceive_sync_entry = MagicMock()

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert plugin._ocr_in_flight_count == 0, "tick 1 failed but count leaked"

    # 第 2 次成功
    fake_future = MagicMock()
    fake_executor.submit = MagicMock(return_value=fake_future)

    plugin._last_real_ocr_monotonic = 0.0  # 让 interval 已过
    plugin._options = PluginOptions(
        ocr_perceive_interval_seconds=15,
        change_driven_enabled=False,  # 简化路径
    )
    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert plugin._ocr_in_flight_count == 1, "tick 2 should schedule"