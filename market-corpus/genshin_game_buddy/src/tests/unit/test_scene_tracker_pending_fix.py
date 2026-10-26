"""SceneTracker 2.0.73 修复：current=X 时不该 pending=X。

拍板 2.0.73：真机 2.0.72 暴露——
  - 当前场景 = 祈愿（连续匹配）
  - pending = 祈愿 + pending_count = 5 + 3 分钟不变
  - 心跳日志显示 current=祈愿 pending=祈愿 像"状态机卡住"
  - 实际是 OCR 一直命中当前场景，pending_count 自增但永远不触发 switch
    （_maybe_fire_event 要求 pending != current）

修复：primary == current → pending=None, count=0（pending 是"待切换候选"，不是当前）。
"""
from __future__ import annotations

import pytest
from plugin.plugins.genshin_game_buddy.scene_tracker import (
    SceneEventType,
    SceneTracker,
)


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.mark.unit
def test_pending_resets_when_primary_matches_current() -> None:
    """拍板 2.0.73：当前场景稳定时，pending 不会累加冗余计数。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, clock=clock)

    # 1) 进入 scene_a（连续 3 次匹配）
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"

    # 2) 当前 = scene_a，再次匹配 scene_a → pending 应被清空
    for _ in range(5):
        tracker.update(["scene_a"])

    # pending 不应是 scene_a（语义：稳定状态下没有"候选切换"）
    assert tracker.pending_scene != "scene_a", (
        f"pending should be cleared when primary == current; got {tracker.pending_scene}"
    )
    assert tracker.pending_count == 0


@pytest.mark.unit
def test_pending_restarts_when_new_scene_appears() -> None:
    """拍板 2.0.73：当前场景稳定后切到新场景，pending 重新计数。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, clock=clock)

    # 进入 scene_a
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"

    # 重复命中 scene_a（pending 一直空）
    for _ in range(3):
        tracker.update(["scene_a"])
    assert tracker.pending_scene is None

    # 切到 scene_b → pending 从 1 开始计数
    e1 = tracker.update(["scene_b"])
    assert e1.type == SceneEventType.NO_CHANGE  # 1 次，未达阈值
    assert tracker.pending_scene == "scene_b"
    assert tracker.pending_count == 1

    # 再来 2 次 → 触发 SWITCHED
    e2 = tracker.update(["scene_b"])
    e3 = tracker.update(["scene_b"])
    assert e2.type == SceneEventType.NO_CHANGE
    assert e3.type == SceneEventType.SCENE_SWITCHED

    # 切换后 current=scene_b，pending 清零
    assert tracker.pending_scene is None
    assert tracker.pending_count == 0


@pytest.mark.unit
def test_pending_stays_cleared_on_long_stable_scene() -> None:
    """拍板 2.0.73：场景长时间稳定，pending_count 不会无限增长。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, clock=clock)

    # 进入 scene_a
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])

    # 100 次重复匹配 → pending_count 应一直 0（不会被自增）
    for _ in range(100):
        tracker.update(["scene_a"])
        assert tracker.pending_count == 0, (
            f"pending_count should stay 0 when stable; got {tracker.pending_count}"
        )


@pytest.mark.unit
def test_pending_zero_no_match_path() -> None:
    """拍板 2.0.73：当前 = scene_a，pending=None；无命中 → exit_pending_count 增加。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, exit_grace_count=3, clock=clock)

    # 进入 scene_a
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"
    assert tracker.pending_scene is None

    # 3 次无命中 → EXITED
    tracker.update([])
    tracker.update([])
    e = tracker.update([])
    assert e.type == SceneEventType.SCENE_EXITED
    # 钳位 2.0.72：exit_pending_count 不超过 grace
    assert tracker.exit_pending_count <= tracker.exit_grace_count