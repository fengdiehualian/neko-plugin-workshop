"""进程启停边沿 → game_started / game_stopped。"""

from __future__ import annotations

import time

from ..core.contracts import CompanionEvent, SceneState


class LifecycleDetector:
    id = "lifecycle"

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
            if et not in ("game_started", "game_stopped"):
                continue
            try:
                seq = int(item.get("event_seq"))
            except (TypeError, ValueError):
                continue
            if seq in self._seen_seqs:
                continue
            self._seen_seqs.add(seq)
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            out.append(
                CompanionEvent(
                    event_id=et,
                    level="critical",
                    edge="enter",
                    payload=dict(payload),
                    created_at=float(item.get("ts") or now),
                )
            )
        # 无 events_tail 时的状态跳变兜底。
        # 仅要求 cur.connected 即可：prev.connected 可能在 runtime.reset() 后为 False，
        # 但此时 cur 已恢复连接且 game_running 为 True，仍应触发 game_started。
        if not out and cur.connected:
            if (not prev.game_running) and cur.game_running:
                out.append(
                    CompanionEvent(
                        event_id="game_started",
                        level="critical",
                        payload={"region": cur.game_region, "process_name": cur.process_name},
                        created_at=now,
                    )
                )
            elif prev.game_running and (not cur.game_running):
                out.append(
                    CompanionEvent(
                        event_id="game_stopped",
                        level="critical",
                        payload={},
                        created_at=now,
                    )
                )
        return out


def build_lifecycle_detectors() -> list[LifecycleDetector]:
    return [LifecycleDetector()]
