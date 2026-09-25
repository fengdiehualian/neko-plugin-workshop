"""视觉 events_tail / flags → 陪玩候选。"""

from __future__ import annotations

import time

from ..core.contracts import CompanionEvent, SceneState


class VisionDetector:
    id = "vision"

    def __init__(self) -> None:
        self._seen_seqs: set[int] = set()
        self._multi_active = False
        self._has_observation = False
        # “敌人离开视野”只在每次“有→无”状态切换时播报一次；
        # 一旦报过一次且此后视野里一直没目标，就不再重复响（除非再次看到目标）。
        self._lost_view_announced = False

    def reset(self) -> None:
        self._seen_seqs.clear()
        self._multi_active = False
        self._has_observation = False
        self._lost_view_announced = False

    def feed(self, prev: SceneState, cur: SceneState) -> list[CompanionEvent]:
        if not cur.game_running:
            self.reset()
            return []
        out: list[CompanionEvent] = []
        now = time.time()
        previous_target_in_view = (
            bool(prev.flags.get("target_in_view")) if self._has_observation else False
        )
        target_state_known = "target_in_view" in cur.flags
        target_in_view = bool(cur.flags.get("target_in_view"))
        spotted_announced = False
        for item in cur.events_tail:
            if not isinstance(item, dict):
                continue
            et = str(item.get("type") or "")
            mapping = {
                "target_appeared": ("enemy_spotted", "warning"),
                "target_lost": ("enemy_left_view", "warning"),
                "threat_escalated": ("enemy_nearby", "critical"),
            }
            if et not in mapping:
                continue
            try:
                seq = int(item.get("event_seq"))
            except (TypeError, ValueError):
                continue
            if seq in self._seen_seqs:
                continue
            self._seen_seqs.add(seq)
            event_id, level = mapping[et]
            # 轨迹 ID 在镜头快速移动时可能更换，但“发现敌人”是场景级状态：
            # 仅在视野从无目标进入有目标时播一次，同一帧多个 appeared 也只取一个。
            if et == "target_appeared":
                if (
                    previous_target_in_view
                    or (target_state_known and not target_in_view)
                    or spotted_announced
                ):
                    continue
                spotted_announced = True
            # 重复丢失：如果已经把“敌人离开视野”播报过一次，且在播报之后
            # 视野里一直没有任何目标（状态没有变化），就不再重复响。
            if et == "target_lost":
                # 单条轨迹消失但画面里仍有其他目标，不等于“敌人离开视野”。
                if target_in_view or self._lost_view_announced:
                    continue
            # near/critical 升级才报 nearby；mid 的 escalated 也映射 nearby 但 level warning
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            threat = str(payload.get("threat_level") or "")
            if et == "threat_escalated" and threat not in ("near", "critical"):
                event_id, level = "enemy_spotted", "warning"
            if et == "threat_escalated" and threat == "near":
                level = "warning"
            out.append(
                CompanionEvent(
                    event_id=event_id,
                    level=level,
                    edge="enter",
                    payload=dict(payload),
                    created_at=float(item.get("ts") or now),
                )
            )
            if et == "target_lost":
                self._lost_view_announced = True

        # 视野里重新出现目标：允许下一次“离开视野”再次播报。
        if target_in_view:
            self._lost_view_announced = False

        multi = bool(cur.flags.get("multi_target"))
        if multi and not self._multi_active:
            self._multi_active = True
            out.append(
                CompanionEvent(
                    event_id="multi_threat",
                    level="warning",
                    payload={"target_count": int((cur.awareness or {}).get("target_count") or 0)},
                    created_at=now,
                )
            )
        elif not multi:
            self._multi_active = False
        self._has_observation = True
        return out


def build_vision_detectors() -> list[VisionDetector]:
    return [VisionDetector()]
