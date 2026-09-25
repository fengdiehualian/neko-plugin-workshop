"""数据层 HTTP 客户端：GET /api/scene → SceneState。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..core.contracts import IDLE, SceneState
from ..core.scenario import ScenarioResolver


def fetch_scene(base_url: str, timeout: float, since_event: int = 0) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}/api/scene?since_event={int(since_event)}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def parse_scene(payload: dict[str, Any] | None) -> SceneState:
    if not isinstance(payload, dict):
        return SceneState(connected=False, scenario=IDLE)

    game = payload.get("game") if isinstance(payload.get("game"), dict) else {}
    session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
    capture = payload.get("capture") if isinstance(payload.get("capture"), dict) else {}
    flags_raw = payload.get("flags") if isinstance(payload.get("flags"), dict) else {}
    awareness = payload.get("awareness") if isinstance(payload.get("awareness"), dict) else {}
    classifier = payload.get("classifier") if isinstance(payload.get("classifier"), dict) else {}
    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    targets = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    events = payload.get("events_tail") if isinstance(payload.get("events_tail"), list) else []

    state = SceneState(
        connected=True,
        data_layer_version=str(meta.get("service_version") or ""),
        data_layer_instance_id=str(meta.get("service_instance_id") or ""),
        timestamp=float(payload.get("timestamp") or 0.0),
        frame_seq=int(payload.get("frame_seq") or 0),
        event_seq=int(payload.get("event_seq") or 0),
        game_running=bool(game.get("running")),
        game_region=str(game.get("region") or "unknown"),
        process_name=(str(game["process_name"]) if game.get("process_name") else None),
        session_phase=str(session.get("phase") or "idle"),
        session_active=bool(session.get("active")),
        degraded=bool(session.get("degraded")),
        degrade_reason=(str(session["degrade_reason"]) if session.get("degrade_reason") else None),
        flags={str(k): bool(v) for k, v in flags_raw.items()},
        awareness=dict(awareness),
        classifier=dict(classifier),
        model=dict(model),
        targets=[t for t in targets if isinstance(t, dict)],
        events_tail=[e for e in events if isinstance(e, dict)],
        events_truncated=bool(payload.get("events_truncated")),
        infer_mode=str(meta.get("infer_mode") or "unknown"),
        age_seconds=_num(capture.get("age_s")),
        fps_estimate=_num(capture.get("fps_estimate")),
        infer_ms=_num(capture.get("infer_ms")),
    )
    state.scenario = ScenarioResolver().resolve(state)
    return state


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SceneClient:
    def __init__(self, base_url: str, timeout: float = 1.5) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._since_event = 0
        self._service_instance_id = ""
        # HTTP 超时断路器
        self._consecutive_timeouts = 0
        self._skip_until = 0.0

    def _should_skip(self, now: float) -> bool:
        """断路器：连续超时后跳过几轮，避免 spinning。"""
        if now < self._skip_until:
            return True
        return False

    def _record_timeout(self, now: float) -> None:
        self._consecutive_timeouts += 1
        if self._consecutive_timeouts >= 3:
            # 跳过接下来的 10 次轮询（约 1 秒）
            self._skip_until = now + 1.0
            self._consecutive_timeouts = 0

    def _record_success(self) -> None:
        self._consecutive_timeouts = 0
        self._skip_until = 0.0

    def poll(self) -> SceneState:
        import time as _time
        now = _time.time()
        if self._should_skip(now):
            return SceneState(connected=False, scenario=IDLE)
        requested_since = self._since_event
        payload = fetch_scene(self.base_url, self.timeout, requested_since)
        if payload is None:
            self._record_timeout(now)
        else:
            self._record_success()
        state = parse_scene(payload)
        if state.connected:
            incoming_instance_id = state.data_layer_instance_id
            if (
                incoming_instance_id
                and self._service_instance_id
                and incoming_instance_id != self._service_instance_id
            ):
                # The event ring restarts from 1 with every data-layer process.
                # Drop the response fetched with the old cursor, then refetch
                # from 0 on the next poll so low sequence numbers are not lost.
                self._service_instance_id = incoming_instance_id
                self._since_event = 0
                state.events_tail = []
                return state
            if incoming_instance_id and not self._service_instance_id:
                # 首次连接：数据层事件环里可能残留上一次会话的历史事件
                # （如 target_lost），回放会把刚打开插件的判定搅乱、甚至
                # 重复播报。首次连接只关心“从此之后”的新事件：把游标直接
                # 对齐到当前最新 seq，丢弃本次返回的历史尾巴。
                self._service_instance_id = incoming_instance_id
                self._since_event = state.event_seq
                state.events_tail = []
                return state
            if incoming_instance_id:
                self._service_instance_id = incoming_instance_id
            # Client-side cursor guard: even if a stale/cached server response
            # repeats an old tail, never feed the same event back into arbitration.
            fresh_events: list[dict[str, Any]] = []
            for event in state.events_tail:
                try:
                    if int(event.get("event_seq")) > requested_since:
                        fresh_events.append(event)
                except (TypeError, ValueError):
                    continue
            state.events_tail = fresh_events
            if state.events_truncated:
                self._since_event = state.event_seq
            else:
                self._since_event = max(self._since_event, state.event_seq)
        return state
