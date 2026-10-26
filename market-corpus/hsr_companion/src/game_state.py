"""Structured, short-lived Star Rail screen-state observations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

GAME_STATE_TTL_SECONDS = 5 * 60

GAME_STATE_LABELS = {
    "story": "剧情 / 对话",
    "combat": "战斗",
    "exploration": "探索 / 跑图",
    "menu": "菜单 / 养成",
    "team_setup": "队伍编成 / 挑战准备",
    "warp": "跃迁 / 抽卡",
    "reward": "结算 / 奖励",
    "loading": "登录 / 加载",
    "other": "其他星铁画面",
    "unknown": "暂时无法判断",
}

GAME_STATE_CONFIDENCE = {"high", "mixed", "low"}
GAME_STATE_VERIFICATION = {"verified", "player_confirmed", "unverified"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_text(value: object, *, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_evidence(values: Iterable[object] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        item = _clean_text(value, limit=120)
        if item and item not in result:
            result.append(item)
        if len(result) >= 6:
            break
    return result


def build_game_state_observation(
    *,
    primary_state: str,
    confidence: str = "mixed",
    substate: str = "",
    evidence: Iterable[object] | None = None,
    summary: str = "",
    source: str = "vision",
    verification: str = "unverified",
    visual_fingerprint: str = "",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    state = str(primary_state or "").strip().lower()
    if state not in GAME_STATE_LABELS:
        raise ValueError("未知的星铁画面状态")
    normalized_confidence = str(confidence or "mixed").strip().lower()
    if normalized_confidence not in GAME_STATE_CONFIDENCE:
        raise ValueError("画面状态置信度必须是 high、mixed 或 low")
    normalized_verification = str(verification or "unverified").strip().lower()
    if normalized_verification not in GAME_STATE_VERIFICATION:
        raise ValueError("画面状态验证级别无效")
    now = observed_at or _utc_now()
    return {
        "primary_state": state,
        "label": GAME_STATE_LABELS[state],
        "substate": _clean_text(substate, limit=80),
        "confidence": normalized_confidence,
        "evidence": _clean_evidence(evidence),
        "summary": _clean_text(summary, limit=300),
        "source": _clean_text(source, limit=40) or "vision",
        "verification": normalized_verification,
        "visual_fingerprint": _clean_text(visual_fingerprint, limit=80),
        "observed_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=GAME_STATE_TTL_SECONDS)),
    }


def game_state_is_fresh(
    observation: object,
    *,
    now: datetime | None = None,
) -> bool:
    if not isinstance(observation, dict):
        return False
    expires_at = _parse_iso(observation.get("expires_at"))
    return bool(expires_at and (now or _utc_now()) <= expires_at)


def public_game_state(
    observation: object,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(observation, dict) or not observation:
        return {
            "primary_state": "unknown",
            "label": GAME_STATE_LABELS["unknown"],
            "fresh": False,
            "has_observation": False,
        }
    state = str(observation.get("primary_state") or "unknown")
    if state not in GAME_STATE_LABELS:
        state = "unknown"
    return {
        "primary_state": state,
        "label": GAME_STATE_LABELS[state],
        "substate": str(observation.get("substate") or ""),
        "confidence": str(observation.get("confidence") or "low"),
        "evidence": list(observation.get("evidence") or []),
        "summary": str(observation.get("summary") or ""),
        "source": str(observation.get("source") or ""),
        "verification": str(observation.get("verification") or "unverified"),
        "verified": str(observation.get("verification") or "unverified") in {"verified", "player_confirmed"},
        "observed_at": str(observation.get("observed_at") or ""),
        "expires_at": str(observation.get("expires_at") or ""),
        "fresh": game_state_is_fresh(observation, now=now),
        "has_observation": True,
    }


def evaluate_state_transition(
    previous: object,
    candidate: dict[str, Any],
    pending: object = None,
) -> tuple[bool, dict[str, Any], str]:
    """Apply small hysteresis so one weak frame cannot flip a known state."""

    previous_public = public_game_state(previous)
    previous_state = str(previous_public.get("primary_state") or "unknown")
    candidate_state = str(candidate.get("primary_state") or "unknown")
    confidence = str(candidate.get("confidence") or "low")
    evidence = list(candidate.get("evidence") or [])
    verification = str(candidate.get("verification") or "unverified")

    if verification not in {"verified", "player_confirmed"}:
        return False, {}, "unverified_hint_not_committed"

    if not previous_public.get("fresh") or previous_state == "unknown":
        return True, {}, "no_fresh_known_state"
    if candidate_state == previous_state:
        return True, {}, "same_state_refresh"
    if candidate_state == "unknown" or confidence == "low":
        return False, {}, "weak_frame_kept_previous"
    if confidence == "high" and len(evidence) >= 2:
        return True, {}, "strong_transition"

    pending_value = pending if isinstance(pending, dict) else {}
    confirmations = (
        int(pending_value.get("confirmations") or 0) + 1 if pending_value.get("primary_state") == candidate_state else 1
    )
    next_pending = {
        "primary_state": candidate_state,
        "confirmations": confirmations,
        "last_observed_at": candidate.get("observed_at"),
    }
    if confirmations >= 2:
        return True, {}, "confirmed_transition"
    return False, next_pending, "awaiting_confirmation"


__all__ = [
    "GAME_STATE_LABELS",
    "GAME_STATE_TTL_SECONDS",
    "GAME_STATE_VERIFICATION",
    "build_game_state_observation",
    "game_state_is_fresh",
    "evaluate_state_transition",
    "public_game_state",
]
