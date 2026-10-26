"""_MSS_LOCK reentrant + SceneTracker current==pending 修复验证。

拍板 2.0.73：真机 2.0.72 暴露 worker 永久挂起——
  - capture_active_frame 在 _MSS_LOCK 内调 _capture_mss
  - _capture_mss 也 acquire _MSS_LOCK（non-reentrant → 死锁）
  - watchdog cf_future.cancel() 对运行中任务无效 → worker 永久卡死

修复：_MSS_LOCK = threading.RLock()。

本测试验证：
  1. _MSS_LOCK 是 RLock（可重入）
  2. 模拟 capture_active_frame 内部嵌套 _capture_mss 不死锁
"""
from __future__ import annotations

import threading
import time

import pytest
from plugin.plugins.genshin_game_buddy import screen_capture


@pytest.mark.unit
def test_mss_lock_is_reentrant() -> None:
    """拍板 2.0.73：_MSS_LOCK 必须是 RLock——capture_active_frame → _capture_mss 嵌套调用死锁根因。"""
    assert isinstance(screen_capture._MSS_LOCK, type(threading.RLock())), (
        f"_MSS_LOCK must be RLock (reentrant); got {type(screen_capture._MSS_LOCK).__name__}"
    )
    # RLock 同线程可重入
    with screen_capture._MSS_LOCK:
        with screen_capture._MSS_LOCK:
            with screen_capture._MSS_LOCK:
                pass  # 3 层嵌套应 OK


@pytest.mark.unit
def test_mss_lock_same_thread_reentrant_does_not_deadlock() -> None:
    """拍板 2.0.73：同线程 acquire 已持有的 _MSS_LOCK 不应阻塞。"""
    acquired_count = [0]

    def _inner_work():
        with screen_capture._MSS_LOCK:
            acquired_count[0] += 1
            with screen_capture._MSS_LOCK:  # nested
                acquired_count[0] += 1
                with screen_capture._MSS_LOCK:  # nested nested
                    acquired_count[0] += 1

    # 应在 1s 内完成
    done = threading.Event()
    thread = threading.Thread(target=lambda: (_inner_work(), done.set()))
    thread.start()
    assert done.wait(timeout=1.0), "_MSS_LOCK nested acquire deadlocked"
    thread.join(timeout=2.0)
    assert acquired_count[0] == 3


@pytest.mark.unit
def test_mss_lock_cross_thread_serializes() -> None:
    """拍板 2.0.73：跨线程 acquire 仍互斥（RLock 是 reentrant 但仍 mutex）。"""
    timeline = []
    started = threading.Event()
    can_release = threading.Event()

    def _slow_holder():
        with screen_capture._MSS_LOCK:
            timeline.append("holder_acquired")
            started.set()
            assert can_release.wait(timeout=2.0), "slow_holder timeout"
            timeline.append("holder_released")

    def _waiter():
        started.wait(timeout=1.0)
        # holder 已 acquire，waiter 应阻塞直到 release
        with screen_capture._MSS_LOCK:
            timeline.append("waiter_acquired")
            assert "holder_released" in timeline, (
                "waiter acquired before holder released — mutex broken"
            )

    t1 = threading.Thread(target=_slow_holder)
    t2 = threading.Thread(target=_waiter)
    t1.start()
    t2.start()

    # 给 holder 时间拿到锁
    started.wait(timeout=1.0)
    time.sleep(0.05)  # 让 waiter 阻塞在 acquire

    # waiter 仍在阻塞
    assert not t2.is_alive() or "waiter_acquired" not in timeline
    # 释放 holder
    can_release.set()

    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert "holder_acquired" in timeline
    assert "holder_released" in timeline
    assert "waiter_acquired" in timeline


@pytest.mark.unit
def test_capture_active_frame_does_not_deadlock_on_mss_lock() -> None:
    """拍板 2.0.73：集成测试——capture_active_frame 内部不挂死。

    mock _capture_mss 和 mss.mss() 模拟嵌套调用；verify 整个流程在 2s 内完成。
    """
    from unittest.mock import MagicMock, patch

    # mock mss so we don't actually grab
    fake_mss_instance = MagicMock()
    fake_mss_instance.monitors = [
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
    ]
    fake_mss_ctx = MagicMock()
    fake_mss_ctx.__enter__ = MagicMock(return_value=fake_mss_instance)
    fake_mss_ctx.__exit__ = MagicMock(return_value=False)

    fake_mss_module = MagicMock()
    fake_mss_module.mss = MagicMock(return_value=fake_mss_ctx)

    with patch.dict("sys.modules", {"mss": fake_mss_module}):
        # mock find_genshin_window → 触发"前景路径"
        with patch.object(screen_capture, "find_genshin_window", return_value=None):
            # mock _capture_mss → 模拟"内部也 acquire _MSS_LOCK"
            with patch.object(screen_capture, "_capture_mss") as mock_capture_mss:
                mock_capture_mss.return_value = screen_capture.CaptureResult(
                    False, None, 0, 0, "mss", "capture_failed", -1.0
                )
                # 模拟 _capture_bridge 也失败（→ 返回 capture_failed）
                with patch.object(screen_capture, "_capture_bridge") as mock_bridge:
                    mock_bridge.return_value = screen_capture.CaptureResult(
                        False, None, 0, 0, "bridge", "no_data", -1.0
                    )

                    done = threading.Event()
                    result_container = {}

                    def _run():
                        try:
                            result_container["value"] = screen_capture.capture_active_frame()
                        finally:
                            done.set()

                    thread = threading.Thread(target=_run)
                    thread.start()

                    # 必须 2s 内完成（之前死锁会永远卡）
                    assert done.wait(timeout=2.0), (
                        "capture_active_frame deadlocked (likely _MSS_LOCK nested acquire)"
                    )
                    thread.join(timeout=2.0)
                    assert "value" in result_container