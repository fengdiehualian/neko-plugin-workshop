"""拍板 2.0.70：OCR worker 60s 超时看门狗（threading.Timer）+ 共享释放标志。

真机 2.0.69 暴露 13 分钟连续 `skip reason=worker_busy in_flight=2 workers=2`。
2.0.69 的修复用 `loop.call_later(60, _watchdog)`——但 `@timer_interval` 每 3s `asyncio.run(ocr_perceive)`，
每次 run 都创建**临时事件循环**，循环在 ocr_perceive return 后立刻关闭，排上的 call_later
回调**永远不会被触发**。watchdog 从没触发过 = 兜底失效。

拍板 2.0.70 修复（缺一不可）：
  ① `self._ocr_executor.submit(fn)` 直接拿 `concurrent.futures.Future`——不被任何临时 loop 管
  ② `cf_future.add_done_callback(_on_done)` 在 executor 的 worker 线程里跑回调，线程安全
  ③ `threading.Timer(60, _watchdog)` 独立线程，**不依赖任何 loop**——绝对能触发
  ④ 共享 `_released` flag：watchdog 和 _on_done 互斥释放，避免重复 -1

本测试直接调 ocr_perceive 协程：
  - mock `_ocr_executor.submit` 返回可控的 cf_future
  - mock `threading.Timer` 加速到 0.05s（验证 60s 看门狗行为）
  - 验证 4 层修复路径
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
    worker_threads: int = 1,
    interval_seconds: int = 0,
    change_driven_enabled: bool = False,
) -> GenshinGameBuddyPlugin:
    """最小可用 stub。"""
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    plugin._options = PluginOptions(
        ocr_perceive_interval_seconds=interval_seconds,
        ocr_worker_threads=worker_threads,
        change_driven_enabled=change_driven_enabled,
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
    return plugin


class _FakeTimer:
    """拍板 2.0.70 测试加速：mock threading.Timer——不真等 60s，立即触发回调。

    行为模拟：
      - `start()` 立即用 asyncio.ensure_future 调度 _fire()（0.05s 后调 callback）
      - 保留 callback 给测试断言用
    """

    instances: list["_FakeTimer"] = []

    def __init__(self, delay: float, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True
        async def _fire():
            await asyncio.sleep(0.05)  # 加速：60s → 0.05s
            self.callback()
        asyncio.ensure_future(_fire())


@pytest.fixture(autouse=True)
def _patch_threading_timer(monkeypatch):
    """每次测试自动 monkey-patch threading.Timer = _FakeTimer。"""
    _FakeTimer.instances.clear()
    monkeypatch.setattr(threading, "Timer", _FakeTimer)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__worker_hang__watchdog_releases_count() -> None:
    """拍板 2.0.70：worker hang 死 → 60s 看门狗强制释放 count。

    测试用 FakeTimer 加速 60s → 0.05s，让 watchdog 几乎立刻触发。
    期望：watchdog 触发 → count 释放回 0 + warning 日志。
    """
    plugin = _make_plugin_stub(worker_threads=1, interval_seconds=0)

    # 构造一个永 pending 的 cf_future
    pending_future: asyncio.Future = asyncio.Future()

    # mock _ocr_executor.submit 返回 pending_future
    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=pending_future)
    plugin._ocr_executor = fake_executor
    plugin._ocr_perceive_sync_entry = MagicMock()

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # schedule 成功后 count += 1
    assert plugin._ocr_in_flight_count == 1, (
        f"after schedule count should be 1, got {plugin._ocr_in_flight_count}"
    )

    # 看门狗 threading.Timer 应被注册，delay 60s（原值——验证参数正确传给 Timer）
    assert len(_FakeTimer.instances) == 1, (
        f"watchdog threading.Timer not created; got {len(_FakeTimer.instances)} instances"
    )
    assert _FakeTimer.instances[0].delay == 60.0, (
        f"watchdog delay should be 60.0s, got {_FakeTimer.instances[0].delay}"
    )
    assert _FakeTimer.instances[0].daemon is True, "Timer.daemon should be True"
    assert _FakeTimer.instances[0].started, "Timer.start() not called"

    # 等 watchdog 触发（0.05s + 余量）
    await asyncio.sleep(0.15)

    # 期望：count -= 1（已被 watchdog 释放）
    assert plugin._ocr_in_flight_count == 0, (
        f"watchdog should release count to 0, got {plugin._ocr_in_flight_count}"
    )

    # 期望：warning 日志被记（"timeout after 60s"）
    warning_calls = [str(c) for c in plugin.logger.warning.call_args_list]
    assert any("timeout after 60s" in s for s in warning_calls), (
        f"watchdog warning log missing; got: {warning_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__worker_done_normally__watchdog_no_double_release() -> None:
    """拍板 2.0.70：worker 正常完成 → _on_done 先释放 → watchdog 后到不重复 -1。

    防 count 减成负数（_released flag 互斥）。
    """
    plugin = _make_plugin_stub(worker_threads=1, interval_seconds=0)

    # 构造一个立刻 done 的 cf_future（用 MagicMock 模拟 concurrent.futures.Future）
    done_future = MagicMock()
    done_future.done = MagicMock(return_value=True)
    # add_done_callback 立即调 callback（模拟 future 已 done）
    def _add_done_callback(cb):
        cb(done_future)
    done_future.add_done_callback = _add_done_callback

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=done_future)
    plugin._ocr_executor = fake_executor
    plugin._ocr_perceive_sync_entry = MagicMock()

    await GenshinGameBuddyPlugin.ocr_perceive(plugin)

    # _on_done 立刻触发（cf_future 已 done），count 应 = 0
    assert plugin._ocr_in_flight_count == 0, (
        f"_on_done should release count immediately; got {plugin._ocr_in_flight_count}"
    )

    # 等 watchdog 触发（0.05s + 余量）
    await asyncio.sleep(0.15)

    # _released flag 互斥 → count 仍 = 0（不能是 -1）
    assert plugin._ocr_in_flight_count == 0, (
        f"double-release guard failed: count={plugin._ocr_in_flight_count}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__startup_resets_count() -> None:
    """拍板 2.0.70：startup 硬重置 _ocr_in_flight_count = 0——防上次崩溃遗留。"""
    plugin = _make_plugin_stub()
    plugin._ocr_in_flight_count = 2  # 假装上次崩溃遗留

    with plugin._ocr_in_flight_lock:
        plugin._ocr_in_flight_count = 0

    assert plugin._ocr_in_flight_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__on_done_count_release_failure_logged(monkeypatch) -> None:
    """拍板 2.0.70：_on_done 内 count 释放失败 → 必须 logger.exception，绝不静默。

    真机 2.0.69 暴露 13 分钟沉默卡死——怀疑就是这种"沉默失败"在作祟。

    测试策略：捕获 add_done_callback 注册的回调；后让 lock 抛异常；手动触发回调。
    """
    plugin = _make_plugin_stub(worker_threads=1, interval_seconds=0)

    # 关掉 Timer 加速
    import threading as _threading
    monkeypatch.setattr(_threading, "Timer", lambda *_a, **_kw: MagicMock(start=lambda: None))

    # 捕获 add_done_callback 注册的回调
    captured_callbacks: list = []

    class _CapturingFuture:
        def add_done_callback(self, cb):
            captured_callbacks.append(cb)
        def done(self):
            return True
        def cancel(self):
            return True

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(return_value=_CapturingFuture())
    plugin._ocr_executor = fake_executor
    plugin._ocr_perceive_sync_entry = MagicMock()

    # 调一次 tick——_on_done 注册到 captured_callbacks
    await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    assert len(captured_callbacks) == 1, (
        f"_on_done not registered; got {len(captured_callbacks)} callbacks"
    )
    # count += 1 正常
    assert plugin._ocr_in_flight_count == 1

    # 现在把 lock 换成 BoomLock——下一次 _release_count 会抛
    class _BoomLock:
        def __enter__(self):
            raise RuntimeError("lock acquisition failed in _on_done")
        def __exit__(self, *args):
            pass

    plugin._ocr_in_flight_lock = _BoomLock()

    # 手动调 _on_done 闭包——其内 _release_count 会因 BoomLock 抛，被 try/except 捕获并 logger.exception
    captured_callbacks[0](_CapturingFuture())

    # 期望：logger.exception 被调（绝不让 count 减失败变成静默）
    exception_calls = [str(c) for c in plugin.logger.exception.call_args_list]
    assert any("_on_done count release failed" in s for s in exception_calls), (
        f"_on_done exception log missing; got: {exception_calls}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ocr_perceive__schedule_failure_releases_count(monkeypatch) -> None:
    """拍板 2.0.70：schedule 阶段抛异常 → count 必须释放（不卡 worker 闸）。

    模拟：_ocr_executor.submit 抛异常（executor 已 shutdown 等极端场景）。
    """
    plugin = _make_plugin_stub(worker_threads=1, interval_seconds=0)

    fake_executor = MagicMock()
    fake_executor.submit = MagicMock(side_effect=RuntimeError("executor shutdown"))
    plugin._ocr_executor = fake_executor
    plugin._ocr_perceive_sync_entry = MagicMock()

    # 调一次 tick——不抛
    try:
        await GenshinGameBuddyPlugin.ocr_perceive(plugin)
    except Exception as exc:
        pytest.fail(f"ocr_perceive must not raise, got {type(exc).__name__}: {exc}")

    # 关键：count 必须 = 0（已释放）
    assert plugin._ocr_in_flight_count == 0, (
        f"schedule failure should release count to 0, got {plugin._ocr_in_flight_count}"
    )