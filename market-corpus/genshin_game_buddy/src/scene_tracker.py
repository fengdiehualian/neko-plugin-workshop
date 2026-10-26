"""拍板 2.0.71：场景状态机——滞回 + 防抖动 + 切换即推事件。

设计原则：
  - **纯同步**：不用 asyncio、不在持锁期间 await。
  - **单实例**：整个插件共用一个 SceneTracker（在 __init__ 里初始化一次）。
  - **滞回 (hysteresis)**：连续 N 次同一新场景才算切换（防单次误判）。
  - **防抖动 (debounce)**：场景离开后 N 秒内回来，不算切换（防快速跳变刷屏）。
  - **退出判定**：连续 N 次无场景命中 → 发出 SCENE_EXITED 事件（让插件切回 OUT_OF_GAME）。

事件类型（SceneEventType）：
  - NO_CHANGE        — 当前场景未变，不触发推送
  - SCENE_SWITCHED   — 切到新场景（建议立即 push）
  - SCENE_EXITED     — 离开所有场景（建议切回 OUT_OF_GAME / 不推）

跑法：见 tests/unit/test_scene_tracker.py 与 tests/stress/test_stress_scene_tracker.py。
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Callable


class SceneEventType(enum.Enum):
    NO_CHANGE = "no_change"
    SCENE_SWITCHED = "scene_switched"
    SCENE_EXITED = "scene_exited"


@dataclass(frozen=True)
class SceneEvent:
    """SceneTracker.update() 返回的事件。

    Attributes:
        type: 事件类型。
        from_scene: 旧场景 ID（SCENE_EXITED 时可能为 None）。
        to_scene:   新场景 ID（SCENE_EXITED 时为 None）。
        elapsed_in_old: 在旧场景里待了多久（秒，0 = 首次）。
    """
    type: SceneEventType
    from_scene: str | None
    to_scene: str | None
    elapsed_in_old: float = 0.0


class SceneTracker:
    """场景状态机：滞回 + 防抖动。

    典型用法：
        tracker = SceneTracker(hysteresis_count=3, exit_grace_count=5, debounce_seconds=10.0)
        while True:
            matched = scan_scenes()  # list[str] or None
            event = tracker.update(matched)
            if event.type == SceneEventType.SCENE_SWITCHED:
                push_prompt(event.to_scene)
            elif event.type == SceneEventType.SCENE_EXITED:
                mark_out_of_game()

    线程安全：所有方法同步；调用方负责持锁。
    """

    def __init__(
        self,
        *,
        hysteresis_count: int = 3,
        exit_grace_count: int = 5,
        debounce_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hysteresis_count = max(1, hysteresis_count)
        self._exit_grace_count = max(1, exit_grace_count)
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._clock = clock

        # 当前场景：None 表示"未在任何场景"（= OUT_OF_GAME）
        self._current: str | None = None
        self._current_entered_at: float = self._clock()
        # 待定场景：连续匹配中
        self._pending: str | None = None
        self._pending_count: int = 0
        # 退出待定：连续无命中
        self._exit_pending_count: int = 0
        # 防抖：最近一次离开的场景 ID 和时间
        self._last_exited_scene: str | None = None
        self._last_exited_at: float = -1.0

    # ==================================================================
    # 查询
    # ==================================================================

    @property
    def current_scene(self) -> str | None:
        return self._current

    @property
    def hysteresis_count(self) -> int:
        return self._hysteresis_count

    @property
    def exit_grace_count(self) -> int:
        return self._exit_grace_count

    @property
    def debounce_seconds(self) -> float:
        return self._debounce_seconds

    @property
    def pending_scene(self) -> str | None:
        """拍板 2.0.71：调试用——当前待定的候选场景（连续匹配中，未达 hysteresis 阈值）。"""
        return self._pending

    @property
    def pending_count(self) -> int:
        """拍板 2.0.71：调试用——候选场景连续匹配次数。"""
        return self._pending_count

    @property
    def exit_pending_count(self) -> int:
        """拍板 2.0.71：调试用——无命中累计次数（≥ exit_grace_count 时触发 SCENE_EXITED）。"""
        return self._exit_pending_count

    def snapshot(self) -> dict:
        """拍板 2.0.71：调试/日志快照——返回所有内部状态字段。

        用于周期性打 INFO 日志，让用户在场景不切换时也能确认状态机在跑。
        """
        return {
            "current": self._current,
            "pending": self._pending,
            "pending_count": self._pending_count,
            "exit_pending_count": self._exit_pending_count,
        }

    # ==================================================================
    # 状态转移
    # ==================================================================

    def update(self, matched: list[str] | None) -> SceneEvent:
        """每次 OCR 判定后调一次。

        Args:
            matched: 当前帧命中的场景 ID 列表（去重保持顺序）；None/空表示无命中。

        Returns:
            SceneEvent（NO_CHANGE / SCENE_SWITCHED / SCENE_EXITED）。
        """
        now = self._clock()
        # 归一化：None / [] 一律当无命中
        matched_set = list(dict.fromkeys(matched or []))

        # 当前状态分支
        if not matched_set:
            self._handle_no_match(now)
            return self._maybe_fire_event(now)

        # 有命中
        primary = matched_set[0]
        # 防抖：离开后短时间内又回来 → 视为继续待原场景（不切换，**不发任何事件**）
        if (
            self._current is None
            and self._last_exited_scene == primary
            and (now - self._last_exited_at) < self._debounce_seconds
        ):
            # 静默恢复——current_scene 直接恢复成 primary，pending 清零。
            # 不触发 SCENE_SWITCHED：用户视角从未真正离开过。
            self._current = primary
            self._current_entered_at = self._last_exited_at  # 保留原始进入时间
            self._pending = None
            self._pending_count = 0
            self._exit_pending_count = 0
            self._last_exited_scene = None  # 一次性效果，防反复触发
            self._last_exited_at = -1.0
            return SceneEvent(
                type=SceneEventType.NO_CHANGE,
                from_scene=primary,
                to_scene=primary,
                elapsed_in_old=0.0,
            )

        # 正常路径
        self._exit_pending_count = 0
        # 拍板 2.0.73：pending 不应等于 current。
        # current 已稳态时再设 pending=current 是无意义的：
        #   1) pending_count 只会涨、永远不触发 switch（_maybe_fire_event 要求 pending != current）
        #   2) 心跳日志里 pending=X / current=X 看起来像"状态机卡住"，实际只是"OCR 一直命中同一场景"
        # 修复：primary == current → 清空 pending 状态机（pending=None, count=0），
        # 下次 primary 变成别的场景时再重新计数。
        if primary == self._current:
            self._pending = None
            self._pending_count = 0
        elif primary == self._pending:
            self._pending_count += 1
        else:
            self._pending = primary
            self._pending_count = 1

        return self._maybe_fire_event(now)

    # ==================================================================
    # 内部
    # ==================================================================

    def _handle_no_match(self, now: float) -> None:
        self._exit_pending_count += 1
        # pending 场景若也在退出计数中被证伪 → 清零
        if self._exit_pending_count >= self._exit_grace_count:
            self._pending = None
            self._pending_count = 0
        # 拍板 2.0.72：钳位——达到 exit_grace_count 后不再递增，避免 heartbeat 日志里
        # exit_pending_count=15/20/...无限增长（真机 2.0.71 暴露：长期无命中场景下 15+ 还在涨）。
        # SCENE_EXITED 只触发一次；之后 current_scene=None，再累计也不会再触发 EXITED。
        if self._exit_pending_count > self._exit_grace_count:
            self._exit_pending_count = self._exit_grace_count

    def _maybe_fire_event(self, now: float) -> SceneEvent:
        """根据 pending / exit_pending 决定是否触发切换/退出事件。"""
        # 场景退出
        if (
            self._current is not None
            and self._exit_pending_count >= self._exit_grace_count
        ):
            old = self._current
            elapsed = now - self._current_entered_at
            self._current = None
            self._current_entered_at = now
            self._last_exited_scene = old
            self._last_exited_at = now
            self._pending = None
            self._pending_count = 0
            self._exit_pending_count = 0
            return SceneEvent(
                type=SceneEventType.SCENE_EXITED,
                from_scene=old,
                to_scene=None,
                elapsed_in_old=elapsed,
            )

        # 场景切换
        if (
            self._pending is not None
            and self._pending != self._current
            and self._pending_count >= self._hysteresis_count
        ):
            old = self._current
            elapsed = now - self._current_entered_at if old is not None else 0.0
            self._current = self._pending
            self._current_entered_at = now
            self._pending = None
            self._pending_count = 0
            self._exit_pending_count = 0
            return SceneEvent(
                type=SceneEventType.SCENE_SWITCHED,
                from_scene=old,
                to_scene=self._current,
                elapsed_in_old=elapsed,
            )

        return SceneEvent(
            type=SceneEventType.NO_CHANGE,
            from_scene=self._current,
            to_scene=self._current,
            elapsed_in_old=0.0,
        )

    def reset(self) -> None:
        """强制重置——startup / apply_settings 等场景。"""
        now = self._clock()
        self._current = None
        self._current_entered_at = now
        self._pending = None
        self._pending_count = 0
        self._exit_pending_count = 0
        self._last_exited_scene = None
        self._last_exited_at = -1.0