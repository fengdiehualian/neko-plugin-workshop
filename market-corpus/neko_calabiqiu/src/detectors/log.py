"""日志信号 → 对局阶段/死亡/胜负候选（无屏模式消费端）。

把 LogSensor 产出的 match_phase / player_death / match_result 事件
映射成 CompanionEvent，与 lifecycle/vision 并列进入仲裁管道。
"""

from __future__ import annotations

import time

from ..core.contracts import CompanionEvent, SceneState


class LogDetector:
    id = "log"

    def __init__(self) -> None:
        self._seen_seqs: set[int] = set()

    def reset(self) -> None:
        self._seen_seqs.clear()

    def feed(self, prev: SceneState, cur: SceneState) -> list[CompanionEvent]:
        out: list[CompanionEvent] = []
        now = time.time()
        for item in cur.events_tail:
            if not isinstance(item, dict):
                continue
            et = str(item.get("type") or "")
            if et not in ("match_phase", "player_death", "match_result"):
                continue
            try:
                seq = int(item.get("event_seq"))
            except (TypeError, ValueError):
                continue
            if seq in self._seen_seqs:
                continue
            self._seen_seqs.add(seq)
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            mapped = self._map(et, payload)
            if mapped is None:
                continue
            event_id, level = mapped
            out.append(
                CompanionEvent(
                    event_id=event_id,
                    level=level,
                    edge="enter",
                    payload=dict(payload),
                    created_at=float(item.get("ts") or now),
                )
            )
        return out

    @staticmethod
    def _map(event_type: str, payload: dict) -> tuple[str, str] | None:
        if event_type == "match_phase":
            phase = str(payload.get("phase") or "")
            if phase == "character_select":
                return "character_select", "warning"
            if phase == "in_progress":
                return "match_started", "warning"
            # match_end 不单独播报：胜负由 match_result 统一出口
            return None
        if event_type == "player_death":
            return "player_death", "critical"
        if event_type == "match_result":
            result = str(payload.get("result") or "")
            if result == "win":
                return "match_win", "warning"
            if result == "lose":
                return "match_lose", "warning"
            return None
        return None


def build_log_detectors() -> list[LogDetector]:
    return [LogDetector()]