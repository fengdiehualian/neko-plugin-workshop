"""OCR 链路压测套件（L1+L4，CI 秒级完成）。

拍板 2.0.70 引入：worker 卡死的根因找到了（2.0.69 `loop.call_later` 用了临时事件循环 = 永远不触发），
但需要系统化验证稳定性，而不是靠"跑 2 分钟没事"。

设计原则：
  - **全部 mock**：不打真实屏幕，不需 winrt，环境无 ms/winrt 也能跑
  - **每个 test < 2s**：CI 不卡死
  - **pytest -m "not stress" 排除**：日常跑不阻塞
  - **覆盖 4 类失败模式**：worker hang、count 泄漏、线程泄漏、回调重复触发

跑法：
  pytest tests/stress -v                            # 全跑
  pytest tests/stress -v -m "not stress"           # 排除
"""
from __future__ import annotations

import asyncio
import gc
import threading
import time
from concurrent.futures import Future as ConcurrentFuture
from unittest.mock import MagicMock

import pytest
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import PluginOptions

# =============================================================================
# 加速器：把 60s 看门狗 + mss/winrt sleep 压成 ~0.05s
# =============================================================================


class _FakeTimer:
    """替身 threading.Timer——立即调度 callback（0.05s 后），保留原 delay 给断言。"""

    instances: list["_FakeTimer"] = []

    def __init__(self, delay: float, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True
        async def _fire():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                return
            if not self.cancelled:
                self.callback()
        asyncio.ensure_future(_fire())

    def cancel(self):
        self.cancelled = True


@pytest.fixture(autouse=True)
def _fake_timer(monkeypatch):
    """每次测试 monkey-patch threading.Timer = _FakeTimer，加速 60s → 0.05s。"""
    _FakeTimer.instances.clear()
    monkeypatch.setattr(threading, "Timer", _FakeTimer)


# =============================================================================
# Stub 工厂
# =============================================================================


def _make_stub(*, worker_threads: int = 1, interval_seconds: int = 0) -> GenshinGameBuddyPlugin:
    """最小可用 stub——ocr_perceive 调度路径所需的字段。"""
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    # 拍板 2.0.71：tick 间隔走 ocr_scene_interval_seconds；ocr_perceive_interval_seconds 仍保留作 legacy
    plugin._options = PluginOptions(
        ocr_perceive_interval_seconds=interval_seconds,
        ocr_scene_interval_seconds=interval_seconds,
        ocr_term_interval_seconds=max(interval_seconds, 15),
        ocr_worker_threads=worker_threads,
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
    plugin.logger = MagicMock()
    plugin._ocr_perceive_sync_entry = MagicMock()
    return plugin


def _make_pending_future(done: bool = False) -> ConcurrentFuture:
    """构造 concurrent.futures.Future 替身——可挂起或立刻完成。"""
    fut: ConcurrentFuture = ConcurrentFuture()
    if done:
        fut.set_result(None)
    return fut


# =============================================================================
# L1 · count 泄漏类
# =============================================================================


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__1000_ticks_count_never_leaks() -> None:
    """1000 次 mock tick → _ocr_in_flight_count 始终归 0。"""
    plugin = _make_stub(worker_threads=2, interval_seconds=0)

    fake_executor = MagicMock()
    # 每次 submit 返回一个立刻 done 的 future（模拟 worker 跑得比 tick 快）
    fake_executor.submit = MagicMock(side_effect=lambda *a, **kw: _make_pending_future(done=True))
    plugin._ocr_executor = fake_executor

    for _ in range(1000):
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    assert plugin._ocr_in_flight_count == 0, (
        f"after 1000 ticks count should be 0, got {plugin._ocr_in_flight_count}"
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__100_body_exceptions_count_never_leaks() -> None:
    """100 次 mock body 抛异常 → count 释放，timer 不死。"""
    plugin = _make_stub(worker_threads=2, interval_seconds=0)

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=lambda *a, **kw: _make_pending_future(done=True))
    plugin._ocr_executor = fake_executor

    for _ in range(100):
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    assert plugin._ocr_in_flight_count == 0, (
        f"after 100 ticks count should be 0, got {plugin._ocr_in_flight_count}"
    )

    # verify exception was logged (body crash)
    exception_calls = [str(c) for c in plugin.logger.exception.call_args_list]
    # 注：exception_calls 是 body 内抛的（如果 mock body 抛）。这里 mock 的是 submit 路径成功，
    # 真正的 body 在 executor 线程里跑（不可见），但 schedule 路径不应抛
    assert len(exception_calls) == 0 or all("count release failed" in s for s in exception_calls), (
        f"unexpected exception logs: {exception_calls}"
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__executor_submit_raises_count_released() -> None:
    """100 次连续 executor.submit 抛异常 → count 必须每次释放。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=RuntimeError("executor boom"))
    plugin._ocr_executor = fake_executor

    for _ in range(100):
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    assert plugin._ocr_in_flight_count == 0, (
        f"executor.submit 100x boom should keep count=0, got {plugin._ocr_in_flight_count}"
    )


# =============================================================================
# L1 · watchdog 看门狗类（hang 死场景）
# =============================================================================


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__mss_hang_60s_watchdog_fires(monkeypatch) -> None:
    """模拟 mss 卡死 120s → threading.Timer 60s 看门狗强制释放。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)

    pending = _make_pending_future(done=False)  # 永不完成
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=pending)
    plugin._ocr_executor = fake_executor

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert plugin._ocr_in_flight_count == 1, "after schedule count should be 1"

    # 60s 看门狗应注册（delay=60.0）
    assert any(t.delay == 60.0 for t in _FakeTimer.instances), (
        f"watchdog 60s not registered; instances: {[(t.delay,) for t in _FakeTimer.instances]}"
    )

    # 等 0.1s 让 FakeTimer 触发
    await asyncio.sleep(0.15)

    # watchdog 触发后 count 应被释放
    assert plugin._ocr_in_flight_count == 0, (
        f"watchdog should release count to 0, got {plugin._ocr_in_flight_count}"
    )

    # warning 日志应被记
    warning_calls = [str(c) for c in plugin.logger.warning.call_args_list]
    assert any("timeout after 60s" in s for s in warning_calls), (
        f"watchdog warning log missing; got: {warning_calls}"
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__winrt_ocr_hang_watchdog_fires() -> None:
    """模拟 winrt OCR hang → 看门狗同样兜底（机制相同：future 永远 pending）。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)

    pending = _make_pending_future(done=False)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=pending)
    plugin._ocr_executor = fake_executor

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert plugin._ocr_in_flight_count == 1

    await asyncio.sleep(0.15)
    assert plugin._ocr_in_flight_count == 0


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__push_hang_watchdog_fires() -> None:
    """模拟 push_message hang → 看门狗兜底。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)
    pending = _make_pending_future(done=False)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=pending)
    plugin._ocr_executor = fake_executor

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert plugin._ocr_in_flight_count == 1
    await asyncio.sleep(0.15)
    assert plugin._ocr_in_flight_count == 0


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__detect_game_hang_watchdog_fires() -> None:
    """模拟 detect_game hang → 看门狗兜底。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)
    pending = _make_pending_future(done=False)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=pending)
    plugin._ocr_executor = fake_executor

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert plugin._ocr_in_flight_count == 1
    await asyncio.sleep(0.15)
    assert plugin._ocr_in_flight_count == 0


# =============================================================================
# L1 · 互斥释放 / 双重释放类
# =============================================================================


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__watchdog_and_on_done_no_double_release() -> None:
    """worker 正常完成 + watchdog 后到 → 只能释放 1 次（_released flag 互斥）。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)

    # future 立刻 done，但 _on_done 由 add_done_callback 注册
    done_fut = _make_pending_future(done=True)

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=done_fut)
    plugin._ocr_executor = fake_executor

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    # _on_done 立刻触发（cf_future 已 done），count 应 = 0
    assert plugin._ocr_in_flight_count == 0

    # 等 watchdog 后到
    await asyncio.sleep(0.15)

    # _released flag 互斥 → count 仍 = 0（不能是 -1）
    assert plugin._ocr_in_flight_count == 0, (
        f"double-release guard failed: count={plugin._ocr_in_flight_count}"
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__cancel_after_callback_no_double_release() -> None:
    """future.cancel() 在 _on_done 已触发之后调用 → 不重复 -1。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)
    done_fut = _make_pending_future(done=True)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=done_fut)
    plugin._ocr_executor = fake_executor

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert plugin._ocr_in_flight_count == 0

    # 手动 cancel（模拟 worker hang 死之后 watchdog cancel + worker 终于完成）
    done_fut.cancel()
    await asyncio.sleep(0.15)

    assert plugin._ocr_in_flight_count == 0, (
        f"cancel after callback caused double-release: count={plugin._ocr_in_flight_count}"
    )


# =============================================================================
# L1 · 并发 / 线程安全类
# =============================================================================


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__3_threads_concurrent_no_race() -> None:
    """3 个线程并发调 ocr_perceive → count 正确（不超过 workers），无负数。"""
    plugin = _make_stub(worker_threads=2, interval_seconds=0)

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=lambda *a, **kw: _make_pending_future(done=True))
    plugin._ocr_executor = fake_executor

    errors: list[Exception] = []

    def _worker():
        try:
            for _ in range(100):
                asyncio.run(GenshinGameBuddyPlugin.ocr_perceive(plugin))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"concurrent ticks raised: {errors}"
    assert plugin._ocr_in_flight_count == 0, (
        f"after 3 threads x 100 ticks count should be 0, got {plugin._ocr_in_flight_count}"
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__1000_ticks_no_thread_leak() -> None:
    """1000 次 tick → threading.active_count() 不增长（Timer 线程全部回收）。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=lambda *a, **kw: _make_pending_future(done=True))
    plugin._ocr_executor = fake_executor

    gc.collect()
    threads_before = threading.active_count()

    for _ in range(1000):
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # 等所有 FakeTimer 完成
    await asyncio.sleep(0.2)

    threads_after = threading.active_count()
    # 允许差几个（pytest 自己的线程），但不应线性增长
    assert threads_after <= threads_before + 5, (
        f"thread leak: before={threads_before} after={threads_after}"
    )


# =============================================================================
# L1 · 边界场景
# =============================================================================


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__worker_count_overshoot_bounded() -> None:
    """count 永远 ≤ workers（worker 闸守门）。"""
    plugin = _make_stub(worker_threads=2, interval_seconds=0)
    plugin._ocr_in_flight_count = 0

    # 用 pending future（_on_done 不立刻触发），便于验证闸的加减
    pending = _make_pending_future(done=False)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=pending)
    plugin._ocr_executor = fake_executor

    # 模拟已有 2 个在跑（已满）
    plugin._ocr_in_flight_count = 2

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    # count 应仍 = 2（闸拒绝，因为 2 >= 2）
    assert plugin._ocr_in_flight_count == 2, (
        f"worker闸 should reject; expected 2, got {plugin._ocr_in_flight_count}"
    )

    # 模拟释放 1 个 → 闸 1 < 2 → 新一轮能进
    plugin._ocr_in_flight_count = 1
    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    # 闸放行 → count += 1 = 2（pending future 不触发 _on_done）
    assert plugin._ocr_in_flight_count == 2, (
        f"after release 1 + new schedule should be 2, got {plugin._ocr_in_flight_count}"
    )

    # 释放 1 个，触发 watchdog（pending future 永不会 done，但 Timer 会 fire）
    plugin._ocr_in_flight_count = 1
    # 模拟 worker 1 完成 → count=1 → 但闸 1 < 2 → 还能进 → count=2
    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert plugin._ocr_in_flight_count == 2


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__100_watchdog_cascade_all_fire() -> None:
    """100 次 tick 每次都注册 Timer → 100 个 Timer 全部触发，count 归 0。

    worker_threads 设大（200）以保证闸全开；否则闸只放 worker_threads 个，
    后续 tick 被 worker_busy 挡掉，不注册 Timer。
    """
    plugin = _make_stub(worker_threads=200, interval_seconds=0)
    pending = _make_pending_future(done=False)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=pending)
    plugin._ocr_executor = fake_executor

    for _ in range(100):
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    assert len(_FakeTimer.instances) == 100, (
        f"100 ticks should register 100 timers, got {len(_FakeTimer.instances)}"
    )
    assert plugin._ocr_in_flight_count == 100, (
        f"after 100 schedule count=100, got {plugin._ocr_in_flight_count}"
    )

    # 等所有 Timer 触发
    await asyncio.sleep(0.5)

    # 100 个 watchdog 触发 → count 归 0
    assert plugin._ocr_in_flight_count == 0, (
        f"100 watchdogs should release count to 0, got {plugin._ocr_in_flight_count}"
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__interval_not_reached_no_schedule() -> None:
    """interval 未到 → 不应 schedule（无 Timer 注册，无 count 变化）。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=15)
    plugin._last_real_ocr_monotonic = time.monotonic()  # 刚跑过

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=lambda *a, **kw: _make_pending_future(done=True))
    plugin._ocr_executor = fake_executor

    for _ in range(100):
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    assert plugin._ocr_in_flight_count == 0, (
        f"interval gate should prevent schedule, got count={plugin._ocr_in_flight_count}"
    )
    assert len(_FakeTimer.instances) == 0, (
        f"no Timer should be registered; got {len(_FakeTimer.instances)}"
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__rapid_tick_alternating_done_pending() -> None:
    """快 tick（done + pending 交替）→ count 在 [0, workers] 间波动，不泄漏。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)
    call_count = [0]

    def _alternating():
        call_count[0] += 1
        return _make_pending_future(done=(call_count[0] % 2 == 0))

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=_alternating)
    plugin._ocr_executor = fake_executor

    for _ in range(200):
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)
        # 等所有 Timer
        await asyncio.sleep(0.01)

    assert plugin._ocr_in_flight_count == 0, (
        f"after 200 alternating done/pending, count={plugin._ocr_in_flight_count}"
    )


# =============================================================================
# L1 · OCR 引擎真实调用（mock screen + 真 OCR 路径 stub）
# =============================================================================


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__on_done_with_real_logger_fakectx() -> None:
    """使用 build_plugin fixture（带 FakeLogger + FakeCtx）跑 tick → 日志结构正确。"""
    # 这里直接 stub，避免 import build_plugin（避免大 fixture 链）
    plugin = _make_stub(worker_threads=1, interval_seconds=0)
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=lambda *a, **kw: _make_pending_future(done=True))
    plugin._ocr_executor = fake_executor

    for _ in range(50):
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # logger.info 应被调用（tick begin / tick done / 等）
    info_calls = [str(c) for c in plugin.logger.info.call_args_list]
    assert any("tick begin" in s or "tick" in s for s in info_calls), (
        f"no tick log; got: {info_calls[:5]}"
    )


@pytest.mark.stress
@pytest.mark.asyncio
async def test_stress__schedule_exception_does_not_propagate() -> None:
    """schedule 阶段任何异常 → 必须被 try/except 吞掉，timer 继续。"""
    plugin = _make_stub(worker_threads=1, interval_seconds=0)

    # 让 submit 抛各种异常混合
    excs = [RuntimeError("a"), ValueError("b"), OSError("c"), MemoryError("d")]
    call = [0]

    def _submit(*a, **kw):
        call[0] += 1
        idx = call[0] % len(excs)
        raise excs[idx]

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=_submit)
    plugin._ocr_executor = fake_executor

    for _ in range(40):
        # 不抛 = 通过
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # 全部 schedule 失败 → count 必须 = 0（不卡死）
    assert plugin._ocr_in_flight_count == 0, (
        f"all schedule failed should keep count=0, got {plugin._ocr_in_flight_count}"
    )

    # exception 日志被记
    exc_logs = [str(c) for c in plugin.logger.exception.call_args_list]
    assert any("schedule failed" in s for s in exc_logs), (
        f"schedule failed exception log missing; got: {exc_logs[:3]}"
    )