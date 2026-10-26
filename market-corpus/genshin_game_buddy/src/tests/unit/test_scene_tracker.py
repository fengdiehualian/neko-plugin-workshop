"""SceneTracker 单元测试。

拍板 2.0.71：场景状态机——滞回 + 防抖动 + 切换即推。
本测试覆盖：
  - 无变化不触发
  - 连续 N 次新场景才切换
  - 中途回到旧场景清零 pending_count
  - 退出场景（连续 N 次无命中）→ SCENE_EXITED
  - 防抖动（场景离开后短时间内又回来 → 不算切换）
  - hysteresis_count / exit_grace_count 可调
"""
from __future__ import annotations

import pytest
from plugin.plugins.genshin_game_buddy.scene_tracker import (
    SceneEventType,
    SceneTracker,
)


class _FakeClock:
    """可控时钟——手动 tick，避免 time.sleep。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.mark.unit
def test_scene_tracker__no_change_when_same_scene() -> None:
    """拍板 2.0.71：已进入场景后，重复命中 → NO_CHANGE（无新切换）。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, clock=clock)

    # 先跑 3 次让 SWITCHED 触发（进入 scene_a）
    for _ in range(3):
        tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"

    # 此后连续 10 次重复命中 → 一直 NO_CHANGE
    for _ in range(10):
        event = tracker.update(["scene_a"])
        assert event.type == SceneEventType.NO_CHANGE
        clock.advance(1.0)


@pytest.mark.unit
def test_scene_tracker__switch_only_after_hysteresis() -> None:
    """连续 N 次同一新场景 → 才触发 SCENE_SWITCHED。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, clock=clock)

    tracker.update(["scene_a"])  # 1 → pending=scene_a, count=1
    e2 = tracker.update(["scene_a"])  # 2 → count=2 (still no switch)
    e3 = tracker.update(["scene_a"])  # 3 → SWITCH

    assert e2.type == SceneEventType.NO_CHANGE
    assert e3.type == SceneEventType.SCENE_SWITCHED
    assert e3.from_scene is None
    assert e3.to_scene == "scene_a"


@pytest.mark.unit
def test_scene_tracker__interrupted_match_resets_count() -> None:
    """中途回到旧场景清零 pending_count（不能累计）。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, clock=clock)

    # 进入 scene_a（3 次匹配）
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"

    # 切到 scene_b 但中途回到 scene_a → pending_count 重置
    tracker.update(["scene_b"])  # pending=scene_b, count=1
    tracker.update(["scene_b"])  # count=2
    e_back = tracker.update(["scene_a"])  # primary == current → count=1
    e_next = tracker.update(["scene_b"])  # pending reset → count=1

    assert e_back.type == SceneEventType.NO_CHANGE
    assert e_next.type == SceneEventType.NO_CHANGE
    assert tracker.current_scene == "scene_a"  # 未切换


@pytest.mark.unit
def test_scene_tracker__scene_exited_after_grace() -> None:
    """连续 N 次无命中 → 触发 SCENE_EXITED。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, exit_grace_count=3, clock=clock)

    tracker.update(["scene_a"])  # 进场景
    tracker.update(["scene_a"])  # 2 次
    tracker.update(["scene_a"])  # 3 次 → 切换
    assert tracker.current_scene == "scene_a"

    # 3 次无命中
    e1 = tracker.update([])
    e2 = tracker.update([])
    e3 = tracker.update([])

    assert e1.type == SceneEventType.NO_CHANGE
    assert e2.type == SceneEventType.NO_CHANGE
    assert e3.type == SceneEventType.SCENE_EXITED
    assert e3.from_scene == "scene_a"
    assert e3.to_scene is None
    assert tracker.current_scene is None


@pytest.mark.unit
def test_scene_tracker__debounce_returns_without_event() -> None:
    """场景离开后短时间又回来 → 不算切换（防抖，静默恢复）。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, exit_grace_count=2, debounce_seconds=10.0, clock=clock)

    # 进场景 a
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"

    # 离开 → exit_grace_count=2 → SCENE_EXITED
    tracker.update([])
    tracker.update([])
    assert tracker.current_scene is None

    # 5 秒后 scene_a 又出现（debounce=10s）→ 静默恢复，无事件
    clock.advance(5.0)
    e = tracker.update(["scene_a"])

    # 静默恢复 → NO_CHANGE，且 current_scene 恢复
    assert e.type == SceneEventType.NO_CHANGE
    assert tracker.current_scene == "scene_a"

    # 后续 match 也 NO_CHANGE（因为已恢复，无切换）
    e2 = tracker.update(["scene_a"])
    assert e2.type == SceneEventType.NO_CHANGE


@pytest.mark.unit
def test_scene_tracker__no_debounce_after_long_absence() -> None:
    """场景离开后超过 debounce_seconds → 正常切换流程（需 hysteresis 次匹配）。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=3, exit_grace_count=2, debounce_seconds=5.0, clock=clock)

    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update([])
    tracker.update([])  # EXITED

    # 超过 debounce 时间
    clock.advance(20.0)

    # scene_a 重新出现 — 不走 debounce 路径 → 需 hysteresis 次匹配
    e1 = tracker.update(["scene_a"])  # pending=scene_a, count=1
    e2 = tracker.update(["scene_a"])  # count=2
    e3 = tracker.update(["scene_a"])  # count=3 → SWITCHED

    assert e1.type == SceneEventType.NO_CHANGE
    assert e2.type == SceneEventType.NO_CHANGE
    assert e3.type == SceneEventType.SCENE_SWITCHED


@pytest.mark.unit
def test_scene_tracker__switch_from_existing_scene() -> None:
    """已有 scene_a，切到 scene_b。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=2, clock=clock)

    # 进入 scene_a
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"

    # 切到 scene_b（连续 2 次）
    e1 = tracker.update(["scene_b"])
    e2 = tracker.update(["scene_b"])

    assert e1.type == SceneEventType.NO_CHANGE
    assert e2.type == SceneEventType.SCENE_SWITCHED
    assert e2.from_scene == "scene_a"
    assert e2.to_scene == "scene_b"
    assert e2.elapsed_in_old >= 0  # 未推进时钟 → elapsed = 0（合理）


@pytest.mark.unit
def test_scene_tracker__empty_matched_is_no_match() -> None:
    """matched=None 或 [] 一律当无命中。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=2, clock=clock)

    e1 = tracker.update(None)
    e2 = tracker.update([])
    assert e1.type == SceneEventType.NO_CHANGE
    assert e2.type == SceneEventType.NO_CHANGE


@pytest.mark.unit
def test_scene_tracker__multiple_matched_takes_first() -> None:
    """matched=[scene_a, scene_b] → 用 scene_a（去重保持顺序）。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=2, clock=clock)

    e1 = tracker.update(["scene_a", "scene_b"])  # pending=scene_a, count=1
    e2 = tracker.update(["scene_b", "scene_a"])  # 顺序变了，但去重后第一个仍是 scene_a

    # pending_count 计数取决于是否同一个 primary
    # 第一次：primary=scene_a
    # 第二次：matched_set=[scene_b, scene_a] → primary=scene_b，与 pending=scene_a 不同 → count 重置
    assert e1.type == SceneEventType.NO_CHANGE
    assert e2.type == SceneEventType.NO_CHANGE  # 还没到阈值


@pytest.mark.unit
def test_scene_tracker__reset_clears_all_state() -> None:
    """reset() 后回到初始状态。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=2, clock=clock)

    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    tracker.update(["scene_a"])
    assert tracker.current_scene == "scene_a"

    tracker.reset()

    assert tracker.current_scene is None
    # 重新进场景应像首次一样
    e1 = tracker.update(["scene_a"])
    assert e1.type == SceneEventType.NO_CHANGE  # 第一次


@pytest.mark.unit
def test_scene_tracker__hysteresis_count_minimum_one() -> None:
    """hysteresis_count=1 → 第一次匹配就切换（边界）。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=1, clock=clock)

    e1 = tracker.update(["scene_a"])
    assert e1.type == SceneEventType.SCENE_SWITCHED


@pytest.mark.unit
def test_scene_tracker__elapsed_in_old_tracks_time() -> None:
    """切换/退出事件的 elapsed_in_old 反映在旧场景里待了多久。"""
    clock = _FakeClock()
    tracker = SceneTracker(hysteresis_count=2, clock=clock)

    tracker.update(["scene_a"])
    tracker.update(["scene_a"])  # 进场景
    clock.advance(7.5)
    tracker.update(["scene_b"])  # pending
    e = tracker.update(["scene_b"])  # SWITCHED

    # 7.5s 在 scene_a，elapsed 应 ≥ 7.0
    assert e.elapsed_in_old >= 7.0
    assert e.type == SceneEventType.SCENE_SWITCHED
    assert e.from_scene == "scene_a"
    assert e.to_scene == "scene_b"