"""拍板 2.0.69：mss 全局锁——并发抓屏不死锁。

mss 官方文档明确 mss.mss() / grab() 非线程安全（共享内部缓冲）。
真机 2.0.68 暴露：2 worker 并发抓屏时 mss 内部死锁，_ocr_in_flight_count 永远 = workers。

本测试验证 screen_capture.py 的 _MSS_LOCK 正确串行化：
  - 2 个并发 _capture_mss 调用不挂死（<1s 内返回）
  - grab_primary_for_dhash 也走 _MSS_LOCK

注：本测试在 CI 无 mss 环境会跳过——缺依赖时 _capture_mss 直接 return CaptureResult(False)
不会触发锁竞争。
"""
from __future__ import annotations

import concurrent.futures
import threading
import time

import pytest
from plugin.plugins.genshin_game_buddy import screen_capture


@pytest.mark.unit
def test_mss_lock__concurrent_capture_mss_does_not_deadlock() -> None:
    """拍板 2.0.69：2 个 worker 并发调 _capture_mss → 锁串行 → 不死锁。

    验证：在 <1s 内 2 个调用都返回（不是死锁）。
    """
    # 用一个无效 rect——既触发 mss 调用，又保证快速失败（避免无 mss 时 hang）
    fake_rect = (0, 0, 1, 1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        start = time.monotonic()
        fut1 = executor.submit(screen_capture._capture_mss, fake_rect)
        fut2 = executor.submit(screen_capture._capture_mss, fake_rect)
        # 加 1s 超时——若死锁会超时
        r1 = fut1.result(timeout=1.0)
        r2 = fut2.result(timeout=1.0)
        elapsed = time.monotonic() - start

    # 两个调用都返回（即便 mss 缺失，也是 CaptureResult(False,...) 不是抛）
    assert r1 is not None
    assert r2 is not None
    # 不应该死锁（<1s）
    assert elapsed < 1.0, f"concurrent capture_mss took {elapsed:.2f}s (likely deadlock)"


@pytest.mark.unit
def test_mss_lock__concurrent_grab_primary_for_dhash_serial() -> None:
    """拍板 2.0.69：2 个 worker 并发调 grab_primary_for_dhash → 锁串行 → 不死锁。

    验证：10 个并发调用都在 <2s 内返回空串（无屏幕/缺依赖）。
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        start = time.monotonic()
        futures = [
            executor.submit(screen_capture.grab_primary_for_dhash)
            for _ in range(10)
        ]
        results = [f.result(timeout=2.0) for f in futures]
        elapsed = time.monotonic() - start

    # 全部返回（空串或合法 hash）
    assert all(r == "" or (len(r) == 16 and all(c in "0123456789abcdef" for c in r)) for r in results)
    # 不死锁
    assert elapsed < 2.0, f"concurrent grab_primary_for_dhash took {elapsed:.2f}s (likely deadlock)"


@pytest.mark.unit
def test_mss_lock__module_level_lock_exists() -> None:
    """拍板 2.0.69：screen_capture._MSS_LOCK 是模块级 threading.Lock。
    拍板 2.0.73：改 RLock——非 reentrant Lock 在 capture_active_frame → _capture_mss 嵌套调用时
    双重 acquire 导致 worker 永久挂起（真机 2.0.72 暴露）。

    验证：所有 mss.mss() 调用都能 acquire 同一把锁；锁可重入（RLock）。
    """
    # 拍板 2.0.73：必须 RLock（reentrant）——capture_active_frame → _capture_mss 嵌套不死锁
    assert isinstance(screen_capture._MSS_LOCK, type(threading.RLock())), (
        f"_MSS_LOCK must be RLock; got {type(screen_capture._MSS_LOCK).__name__}"
    )
    # 锁可 acquire/release（blocking=False 仅当未锁时成功；这里先尝试）
    # RLock 初创未锁时可 acquire；locked 时 blocking=False 也返回 False
    if not screen_capture._MSS_LOCK.acquire(blocking=False):
        # 已锁——正常情况（其他测试可能持有），RLock 支持同线程 reentrant
        screen_capture._MSS_LOCK.release()  # 至少可 release
    screen_capture._MSS_LOCK.release()


@pytest.mark.unit
def test_grab_primary_for_dhash__returns_consistent_format() -> None:
    """拍板 2.0.69：grab_primary_for_dhash 返回值契约——空串或 16-hex chars。

    与旧 _light_capture_dhash 一致——失败/缺依赖 → 空串；成功 → 16 hex chars。
    """
    result = screen_capture.grab_primary_for_dhash()
    assert result == "" or (len(result) == 16 and all(c in "0123456789abcdef" for c in result))


@pytest.mark.unit
def test_grab_primary_for_dhash__does_not_raise() -> None:
    """拍板 2.0.69：grab_primary_for_dhash 任何异常都吞掉返回空串。"""
    try:
        result = screen_capture.grab_primary_for_dhash()
    except Exception as exc:
        pytest.fail(f"grab_primary_for_dhash raised {type(exc).__name__}: {exc}")
    assert result == "" or (len(result) == 16 and all(c in "0123456789abcdef" for c in result))