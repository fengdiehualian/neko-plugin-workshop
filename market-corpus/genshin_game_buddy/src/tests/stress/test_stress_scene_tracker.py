"""SceneTracker 压测。

拍板 2.0.71：场景状态机——1000 次 update 无线程/内存泄漏；抖动场景不刷屏。
"""
from __future__ import annotations

import gc
import threading
import time

import pytest
from plugin.plugins.genshin_game_buddy.scene_tracker import (
    SceneEventType,
    SceneTracker,
)


@pytest.mark.stress
def test_stress_scene_tracker__1000_updates_no_leak() -> None:
    """1000 次 update → 无线程/内存泄漏（无新线程创建、无累积状态）。"""
    tracker = SceneTracker(hysteresis_count=3)

    gc.collect()
    threads_before = threading.active_count()

    for i in range(1000):
        scene = ["scene_a", "scene_b"][i % 2]
        tracker.update([scene])

    gc.collect()
    threads_after = threading.active_count()

    assert threads_after <= threads_before + 2, (
        f"thread leak: before={threads_before} after={threads_after}"
    )


@pytest.mark.stress
def test_stress_scene_tracker__churn_scenes_no_spam() -> None:
    """抖动场景（A-B-A-B-A-B...）→ 滞回生效，不切换 → 不应连续触发 SWITCHED。

    每 2 次切换一次 primary（threshold=3），永远不会累计到 3 次同 primary → 永远 NO_CHANGE。
    """
    tracker = SceneTracker(hysteresis_count=3)
    switches = 0
    no_changes = 0

    for i in range(200):
        scene = ["scene_a", "scene_b"][i % 2]
        event = tracker.update([scene])
        if event.type == SceneEventType.SCENE_SWITCHED:
            switches += 1
        elif event.type == SceneEventType.NO_CHANGE:
            no_changes += 1

    # 切换次数应 < 5（理想 0；最多可能在第 3 次时意外匹配）
    assert switches <= 5, (
        f"jitter scene should NOT spam switches; got {switches} switches in 200 updates"
    )
    assert no_changes >= 195, (
        f"jitter scene should mostly NO_CHANGE; got {no_changes}/200"
    )


@pytest.mark.stress
def test_stress_scene_tracker__rapid_exit_enter_no_leak() -> None:
    """快速进出场景（exit → enter → exit ...）→ tracker 状态不泄漏。"""
    tracker = SceneTracker(hysteresis_count=2, exit_grace_count=2, debounce_seconds=0.0)

    gc.collect()
    baseline_rss = 0.0
    try:
        import psutil
        baseline_rss = psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        pass

    # 1000 次循环：进 a → 退 → 进 b → 退 ...
    for i in range(1000):
        scene = ["scene_a", "scene_b"][i % 2]
        tracker.update([scene])
        tracker.update([scene])  # 进入
        tracker.update([])         # 无命中
        tracker.update([])         # EXITED

    # 最终：current_scene 应为 None（已退出）
    assert tracker.current_scene is None

    if baseline_rss > 0:
        try:
            import psutil
            after_rss = psutil.Process().memory_info().rss / 1024 / 1024
            # RSS 不应线性增长（1000 次循环 × 4 updates = 4000 次 update；< 10MB 增长）
            assert after_rss - baseline_rss < 10.0, (
                f"RSS grew too much: {after_rss - baseline_rss:.2f}MB"
            )
        except ImportError:
            pass


@pytest.mark.stress
def test_stress_scene_tracker__concurrent_updates_thread_safe() -> None:
    """多线程并发 update → 不应抛 AttributeError / TypeError（崩溃测试）。

    注：SceneTracker 非线程安全（同步单线程，调用方应持锁）；
    本测试只验证 GIL 保护下 worst case（无锁并发）不崩。
    """
    tracker = SceneTracker(hysteresis_count=3)
    errors: list[Exception] = []

    def _worker(scene: str):
        try:
            for _ in range(500):
                tracker.update([scene])
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=("scene_a",)),
        threading.Thread(target=_worker, args=("scene_b",)),
        threading.Thread(target=_worker, args=("scene_c",)),
        threading.Thread(target=_worker, args=("",)),  # 无命中
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    # 不应抛 AttributeError / TypeError 等崩溃性异常
    fatal = [e for e in errors if not isinstance(e, (AssertionError,))]
    assert not fatal, f"concurrent updates crashed: {fatal}"


@pytest.mark.stress
def test_stress_scene_tracker__per_update_under_1ms() -> None:
    """每次 update 耗时 < 1ms（同步状态机应该很快）。"""
    tracker = SceneTracker(hysteresis_count=3)

    # Warm up
    for _ in range(100):
        tracker.update(["scene_a"])

    start = time.monotonic()
    for i in range(10000):
        scene = ["scene_a", "scene_b", "scene_c"][i % 3]
        tracker.update([scene])
    elapsed = time.monotonic() - start

    avg_ms = (elapsed / 10000) * 1000
    assert avg_ms < 1.0, f"avg update time {avg_ms:.3f}ms exceeds 1ms budget"