"""唯一 NEKO 输出边界。

投递模型与战争雷霆插件保持同一组边界：先构造不可变 delivery，再做
时效/重复/背压检查，最后跨越宿主 push_message 边界。
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..core.contracts import CompanionEvent
from ..core.instructions import CBQ_CONTEXT_INSTRUCTIONS, CBQ_RESTORE_INSTRUCTIONS
from .dispatch_observer import DispatchObserver
from .event_delivery import EventDelivery
from .runtime_timeline import RuntimeTimeline

COALESCE_KEY = "neko_calabiqiu:companion_event"
COMPANION_REPLY_CONTRACT = "short_tts_line"
COMPANION_REPLY_MAX_CHARS = 16
COMPANION_RESPONSE_MODULE_HINT = "calabiyau_realtime_cue"
HOST_CALLBACK_CONTRACT_VERSION = "neko.callback.v1"
REPEAT_COLLAPSE_SECONDS = 6.0
REPEAT_COLLAPSE_EVENT_IDS = frozenset(
    {"enemy_spotted", "enemy_nearby", "enemy_left_view", "multi_threat"}
)
# 只有真正“贴脸/危险”的警报才打断正在进行的对话语音；普通方位警报
# （如发现敌人）只排队，不打断，避免反复插话淹没玩家与猫娘的聊天。
INTERRUPT_PENDING_EVENTS = frozenset({"enemy_nearby"})

_OBSERVED_DELIVERY_METADATA_KEYS = (
    "coalesce_key",
    "companion_reply_contract",
    "reply_contract",
    "max_reply_chars",
    "reply_max_chars",
    "response_module_hint",
    "plugin_recommended_reply",
    "plugin_owned_output",
    "direct_tts",
    "delivery_method",
    "replace_pending",
    "interrupt_pending",
    "reply_style_contract",
    "dialogue_policy_owner",
    "plugin_dialogue_policy",
    "host_callback_contract_version",
    "delivery_ttl_seconds",
    "delivery_intent",
    "interrupt_policy",
    "event_ts",
    "event_age_seconds",
    "event_max_age_seconds",
    "event_expires_at",
)

_BEARING_LABELS: dict[str, str] = {
    "screen_center": "前方",
    "screen_left": "左边",
    "screen_right": "右边",
    "screen_upper": "上方",
    "screen_lower": "下方",
    "screen_upper_left": "左上方",
    "screen_upper_right": "右上方",
    "screen_lower_left": "左下方",
    "screen_lower_right": "右下方",
}


_ALERT_CACHE: dict[str, list[str]] | None = None


def _load_alert_pool() -> dict[str, list[str]]:
    """从 i18n JSON 加载话术池，按需缓存。"""
    global _ALERT_CACHE
    if _ALERT_CACHE is not None:
        return _ALERT_CACHE
    path = Path(__file__).resolve().parent.parent / "i18n" / "zh-CN.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        data = {}
    pool: dict[str, list[str]] = {
        "enemy_nearby": data.get("alerts.enemy_nearby.with_bearing", []),
        "enemy_nearby_nb": data.get("alerts.enemy_nearby.no_bearing", []),
        "enemy_left_view": data.get("alerts.enemy_left_view", []),
        "multi_threat": data.get("alerts.multi_threat", []),
        "enemy_spotted": data.get("alerts.enemy_spotted.with_bearing", []),
        "enemy_spotted_nb": data.get("alerts.enemy_spotted.no_bearing", []),
        "character_select": data.get("alerts.character_select", []),
        "match_started": data.get("alerts.match_started", []),
        "player_death": data.get("alerts.player_death", []),
        "match_win": data.get("alerts.match_win", []),
        "match_lose": data.get("alerts.match_lose", []),
        "chatter": data.get("alerts.chatter", []),
    }
    # 空 fallback（仅开发时生效，正式包永远有 JSON）
    for key, default in (
        ("enemy_nearby", ["{bearing}近处有敌人喵！"]),
        ("enemy_nearby_nb", ["近处有敌人喵！"]),
        ("enemy_left_view", ["敌人离开视野了喵。"]),
        ("multi_threat", ["发现多个敌人喵！"]),
        ("enemy_spotted", ["{bearing}发现敌人喵！"]),
        ("enemy_spotted_nb", ["发现敌人喵！"]),
        ("character_select", ["选好角色啦喵！"]),
        ("match_started", ["对局开始啦喵，加油！"]),
        ("player_death", ["你被打倒啦喵…"]),
        ("match_win", ["赢啦喵！太棒了！"]),
        ("match_lose", ["这局输啦喵…下次再来！"]),
        ("chatter", ["暂时没有敌人喵，先喘口气～"]),
    ):
        if not pool[key]:
            pool[key] = list(default)
    _ALERT_CACHE = pool
    return pool


def _alert_pool(event: CompanionEvent) -> tuple[str, ...]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    bearing = _BEARING_LABELS.get(str(payload.get("bearing") or ""), "")
    pool = _load_alert_pool()
    if event.event_id == "enemy_nearby":
        key = "enemy_nearby" if bearing else "enemy_nearby_nb"
    elif event.event_id == "enemy_left_view":
        key = "enemy_left_view"
    elif event.event_id == "multi_threat":
        key = "multi_threat"
    elif event.event_id == "character_select":
        key = "character_select"
    elif event.event_id == "match_started":
        key = "match_started"
    elif event.event_id == "player_death":
        key = "player_death"
    elif event.event_id == "match_win":
        key = "match_win"
    elif event.event_id == "match_lose":
        key = "match_lose"
    elif event.event_id == "chatter":
        key = "chatter"
    else:
        key = "enemy_spotted" if bearing else "enemy_spotted_nb"
    templates = pool.get(key, pool.get("enemy_spotted_nb", ["发现敌人喵！"]))
    if bearing:
        return tuple(t.replace("{bearing}", bearing) for t in templates)
    return tuple(templates)


def _alert_signature(event: CompanionEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return f"{event.event_id}:{str(payload.get('bearing') or '')}"


def _event_freshness_metadata(
    event: CompanionEvent, now: float, max_age: float
) -> dict[str, float]:
    metadata: dict[str, float] = {}
    if event.created_at > 0:
        metadata["event_ts"] = round(float(event.created_at), 3)
        if now >= event.created_at:
            metadata["event_age_seconds"] = round(float(now - event.created_at), 3)
        if max_age > 0:
            metadata["event_max_age_seconds"] = round(max_age, 3)
            metadata["event_expires_at"] = round(float(event.created_at + max_age), 3)
    elif max_age > 0:
        metadata["event_max_age_seconds"] = round(max_age, 3)
    return metadata


def _target_lanlan(plugin: Any) -> str:
    for owner in (getattr(plugin, "cfg", None), plugin):
        value = getattr(owner, "target_lanlan", "") if owner is not None else ""
        if value:
            return str(value).strip()
    return ""


def _project_tts_url() -> str:
    raw_port = str(os.environ.get("MAIN_SERVER_PORT") or "48911").strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = 48911
    if port < 1 or port > 65535:
        port = 48911
    return f"http://127.0.0.1:{port}/api/game/calabiyau/speak"


def _speak_project_tts(
    line: str,
    *,
    target_lanlan: str = "",
    interrupt_audio: bool = False,
    event_id: str = "companion_alert",
    timeout: float = 1.5,
) -> dict[str, Any]:
    """Queue an exact line on N.E.K.O's existing character TTS pipeline."""
    payload: dict[str, Any] = {
        "line": str(line),
        "mirror_text": True,
        "emit_turn_end": True,
        "interrupt_audio": bool(interrupt_audio),
        "source": "neko_calabiqiu",
        "event": {
            "kind": str(event_id or "companion_alert"),
            "source": "neko_calabiqiu",
        },
    }
    if target_lanlan:
        payload["lanlan_name"] = target_lanlan
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        _project_tts_url(),
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=max(0.5, float(timeout))) as response:
        result = json.loads(response.read().decode("utf-8", "replace"))
    if not isinstance(result, dict):
        raise ValueError("invalid_project_tts_response")
    if result.get("ok") is not True or result.get("audio_queued") is not True:
        raise RuntimeError(str(result.get("reason") or "project_tts_not_queued"))
    return result


class NekoDispatcher:
    def __init__(
        self,
        plugin: Any,
        *,
        timeline: RuntimeTimeline | None = None,
        clock: Callable[[], float] | None = None,
        speaker: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.plugin = plugin
        self.timeline = timeline
        self.logger = getattr(plugin, "logger", None)
        self._observer = DispatchObserver(timeline)
        self._clock = clock or time.time
        self._speaker = speaker or getattr(plugin, "speak_project_tts", None) or _speak_project_tts
        self._last_push_at: float | None = None
        self._last_push_priority = -1
        self._last_event_push: dict[str, tuple[float, str, str]] = {}
        self._alert_cursors: dict[str, int] = {}

    def _next_alert(self, event: CompanionEvent) -> str:
        pool = _alert_pool(event)
        key = event.event_id
        index = self._alert_cursors.get(key, 0) % len(pool)
        self._alert_cursors[key] = index + 1
        return pool[index]

    def push_context(self, text: str) -> bool:
        """注入/恢复常驻场景上下文；失败会被记录且不伪装为成功。"""
        target_lanlan = _target_lanlan(self.plugin)
        metadata: dict[str, Any] = {"plugin": "neko_calabiqiu", "kind": "context"}
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        try:
            self.plugin.push_message(
                source="neko_calabiqiu",
                visibility=[],
                ai_behavior="read",
                parts=[{"type": "text", "text": text}],
                priority=0,
                metadata=metadata,
                target_lanlan=target_lanlan or None,
            )
        except Exception as exc:  # noqa: BLE001
            if self.timeline:
                self.timeline.record_stage(
                    stage="context_failed",
                    outcome="failed",
                    reason=type(exc).__name__,
                    kind="context",
                    ai_behavior="read",
                    pushed=False,
                    dry_run=False,
                )
            warning = getattr(self.logger, "warning", None)
            if callable(warning):
                warning(f"push_context failed: {type(exc).__name__}")
            return False
        if self.timeline:
            self.timeline.record_stage(
                stage="context_pushed",
                outcome="pushed",
                reason="push_message_accepted",
                kind="context",
                ai_behavior="read",
                pushed=True,
                dry_run=False,
                safe_summary="context/read",
                target_lanlan=target_lanlan,
            )
        return True

    def _resolve_perf_context(self) -> str:
        """Inject current FPS/PING into the context instructions."""
        fps = "—"
        ms = "—"
        try:
            plugin = self.plugin
            state = getattr(plugin, "scene_state", None)
            if state is not None:
                fps_val = getattr(state, "fps_estimate", None)
                ms_val = getattr(state, "infer_ms", None)
                if fps_val is not None:
                    fps = str(round(float(fps_val), 1))
                if ms_val is not None:
                    ms = str(round(float(ms_val), 1))
        except Exception:  # noqa: BLE001
            pass
        return CBQ_CONTEXT_INSTRUCTIONS.replace("{INFER_FPS}", fps).replace("{INFER_MS}", ms)

    def push_game_started(self, *, dry_run: bool) -> str:
        if dry_run:
            return "dry_run(context=game_started)"
        if self.push_context(self._resolve_perf_context()):
            return "pushed(context=game_started)"
        return "suppressed(context=game_started, reason=push_failed)"

    def push_game_stopped(self, *, dry_run: bool) -> str:
        if dry_run:
            return "dry_run(context=game_stopped)"
        if self.push_context(CBQ_RESTORE_INSTRUCTIONS):
            return "pushed(context=game_stopped)"
        return "suppressed(context=game_stopped, reason=push_failed)"

    def push_event(self, event: CompanionEvent, *, dry_run: bool) -> str:
        if event.event_id == "game_started":
            return self.push_game_started(dry_run=dry_run)
        if event.event_id == "game_stopped":
            return self.push_game_stopped(dry_run=dry_run)
        # 用户要求静音死亡播报：player_death 不再进入 TTS 输出链路。
        if event.event_id == "player_death":
            return self._suppress_event(event, "player_death_muted")

        if dry_run:
            self._observer.record_event(
                event,
                stage="dispatcher_dry_run",
                outcome="dry_run",
                reason="dry_run_enabled",
                dry_run=True,
            )
            return (
                f"dry_run(event={event.event_id}/{event.edge}/{event.level}, "
                f"prio={event.priority}, preempt={event.preempt_eligible})"
            )

        now = self._clock()
        cfg = getattr(self.plugin, "cfg", None)
        max_age_key = (
            "vision_output_event_max_age_seconds"
            if event.category == "vision"
            else "output_event_max_age_seconds"
        )
        default_max_age = 3.0 if event.category == "vision" else 8.0
        max_age = float(getattr(cfg, max_age_key, default_max_age) or 0.0)
        freshness = _event_freshness_metadata(event, now, max_age)
        if max_age > 0 and event.created_at > 0 and now >= event.created_at:
            if now - event.created_at > max_age:
                return self._suppress_event(event, "event_expired", **freshness)

        alert_signature = _alert_signature(event)
        if self._is_repeated_event_collapsed(event, alert_signature, now):
            return self._suppress_event(
                event,
                "repeated_event_collapsed",
                **freshness,
            )
        if self._is_backpressured(event, now):
            return self._suppress_event(event, "output_backpressure", **freshness)

        recommended_reply = self._next_alert(event)
        target_lanlan = _target_lanlan(self.plugin)
        delivery = self._build_delivery(
            event,
            recommended_reply=recommended_reply,
            target_lanlan=target_lanlan,
            freshness=freshness,
        )
        try:
            cfg_timeout = float(getattr(cfg, "http_timeout_seconds", 1.5) or 1.5)
            speech_result = self._speaker(
                recommended_reply,
                target_lanlan=target_lanlan,
                interrupt_audio=bool(delivery.metadata.get("interrupt_pending")),
                event_id=event.event_id,
                timeout=cfg_timeout,
            )
        except Exception as exc:
            self._observer.record_event(
                event,
                stage="dispatcher_failed",
                outcome="failed",
                reason="project_tts_failed",
                dry_run=False,
                ai_behavior=delivery.ai_behavior,
                pushed=False,
                error_type=type(exc).__name__,
            )
            # 输出失败计入安全熔断（连续失败会触发 safety_auto_stop），
            # 并占用本次输出槽，避免同一失败事件在极短周期内无限重试。
            safety = getattr(self.plugin, "safety", None)
            if safety is not None:
                try:
                    safety.record_failure()
                except Exception:  # noqa: BLE001
                    pass
            self._last_push_at = now
            self._last_push_priority = event.priority
            warning = getattr(self.logger, "warning", None)
            if callable(warning):
                warning(f"project TTS failed: {type(exc).__name__}")
            return f"suppressed(event={event.event_id}/{event.edge}, reason=project_tts_failed)"

        # 宿主已接受后，观测/记账失败不能导致同一事件重试。
        try:
            self._last_push_at = now
            self._last_push_priority = event.priority
            self._last_event_push[event.event_id] = (
                now,
                event.level,
                alert_signature,
            )
            observed = {
                key: delivery.metadata[key]
                for key in _OBSERVED_DELIVERY_METADATA_KEYS
                if key in delivery.metadata
            }
            self._observer.record_event(
                event,
                stage="dispatcher_pushed",
                outcome="pushed",
                reason="project_tts_queued",
                dry_run=False,
                ai_behavior=delivery.ai_behavior,
                pushed=True,
                target_lanlan=target_lanlan,
                visibility=list(delivery.visibility),
                audio_queued=bool(speech_result.get("audio_queued")),
                **observed,
            )
        except Exception as exc:  # noqa: BLE001
            warning = getattr(self.logger, "warning", None)
            if callable(warning):
                warning(f"post-acceptance output bookkeeping failed: {type(exc).__name__}")
        return f"pushed(event={event.event_id}/{event.edge}, method=project_tts)"

    def _build_delivery(
        self,
        event: CompanionEvent,
        *,
        recommended_reply: str,
        target_lanlan: str,
        freshness: dict[str, float],
    ) -> EventDelivery:
        interrupt_pending = event.event_id in INTERRUPT_PENDING_EVENTS or event.level == "critical"
        remaining_ttl: float | None = None
        if freshness.get("event_max_age_seconds") is not None:
            remaining = float(freshness["event_max_age_seconds"]) - float(
                freshness.get("event_age_seconds") or 0.0
            )
            if remaining > 0:
                remaining_ttl = round(remaining, 3)
        callback_delivery: dict[str, Any] = {
            "coalesce_key": COALESCE_KEY,
            "replace_pending": True,
            "interrupt_pending": interrupt_pending,
            "priority": event.priority,
        }
        if freshness.get("event_expires_at") is not None:
            callback_delivery["expires_at"] = freshness["event_expires_at"]
        if freshness.get("event_max_age_seconds") is not None:
            callback_delivery["max_age_seconds"] = freshness["event_max_age_seconds"]

        reply_style = (
            "Boundary: output exactly the plugin_recommended_reply as one Chinese line; "
            "do not answer other messages, add analysis, advice, emotion, or follow-up."
        )
        dialogue_policy = {
            "owner": "plugin",
            "mode": COMPANION_REPLY_CONTRACT,
            "max_chars": COMPANION_REPLY_MAX_CHARS,
            "single_line": True,
            "exact_recommended_reply": True,
            "no_followup": True,
            "prompt_owned": True,
            "style": "short_line",
            "style_hint": reply_style,
        }
        host_callback_contract: dict[str, Any] = {
            "version": HOST_CALLBACK_CONTRACT_VERSION,
            "kind": "realtime_cue",
            "delivery": callback_delivery,
            "freshness": {
                key: freshness[key]
                for key in (
                    "event_ts",
                    "event_age_seconds",
                    "event_max_age_seconds",
                    "event_expires_at",
                )
                if freshness.get(key) is not None
            },
        }
        if target_lanlan:
            host_callback_contract["target"] = {"lanlan": target_lanlan}

        metadata: dict[str, Any] = {
            "plugin": "neko_calabiqiu",
            "event_id": event.event_id,
            "edge": event.edge,
            "level": event.level,
            "coalesce_key": COALESCE_KEY,
            "replace_pending": True,
            "interrupt_pending": interrupt_pending,
            "companion_reply_contract": COMPANION_REPLY_CONTRACT,
            "reply_contract": COMPANION_REPLY_CONTRACT,
            "max_reply_chars": COMPANION_REPLY_MAX_CHARS,
            "reply_max_chars": COMPANION_REPLY_MAX_CHARS,
            "response_module_hint": COMPANION_RESPONSE_MODULE_HINT,
            "plugin_recommended_reply": recommended_reply,
            "plugin_owned_output": True,
            "direct_tts": True,
            "delivery_method": "project_tts",
            "reply_style_contract": reply_style,
            "dialogue_policy_owner": "plugin",
            "plugin_dialogue_policy": dialogue_policy,
            "host_callback_contract_version": HOST_CALLBACK_CONTRACT_VERSION,
            "host_callback_contract": host_callback_contract,
            "delivery_intent": "realtime_cue",
            "interrupt_policy": "drop",
            **freshness,
        }
        if remaining_ttl is not None:
            metadata["delivery_ttl_seconds"] = remaining_ttl
        if target_lanlan:
            metadata["target_lanlan"] = target_lanlan
        return EventDelivery(
            text=recommended_reply,
            ai_behavior="blind",
            visibility=("chat",),
            metadata=metadata,
            target_lanlan=target_lanlan,
        )

    def speak_text(
        self,
        text: str,
        *,
        event_id: str = "test_say",
        interrupt_audio: bool = False,
    ) -> dict[str, Any]:
        cfg = getattr(self.plugin, "cfg", None)
        timeout = float(getattr(cfg, "http_timeout_seconds", 1.5) or 1.5)
        return self._speaker(
            str(text),
            target_lanlan=_target_lanlan(self.plugin),
            interrupt_audio=interrupt_audio,
            event_id=event_id,
            timeout=timeout,
        )

    def _suppress_event(self, event: CompanionEvent, reason: str, **metadata: Any) -> str:
        self._observer.record_event(
            event,
            stage="dispatcher_suppressed",
            outcome="dropped",
            reason=reason,
            dry_run=False,
            pushed=False,
            **metadata,
        )
        return f"suppressed(event={event.event_id}/{event.edge}, reason={reason})"

    def _is_backpressured(self, event: CompanionEvent, now: float) -> bool:
        if event.level == "critical" or event.event_id in INTERRUPT_PENDING_EVENTS:
            return False
        cfg = getattr(self.plugin, "cfg", None)
        guard = float(getattr(cfg, "output_backpressure_seconds", 6.0) or 0.0)
        if guard <= 0 or self._last_push_at is None:
            return False
        if now - self._last_push_at >= guard:
            return False
        return event.priority <= self._last_push_priority

    def _is_repeated_event_collapsed(
        self, event: CompanionEvent, alert_signature: str, now: float
    ) -> bool:
        if event.event_id not in REPEAT_COLLAPSE_EVENT_IDS:
            return False
        last_at, last_level, last_signature = self._last_event_push.get(
            event.event_id, (-1e9, "", "")
        )
        if now - last_at >= REPEAT_COLLAPSE_SECONDS:
            return False
        if event.level == "critical" and last_level != "critical":
            return False
        return last_signature == alert_signature
