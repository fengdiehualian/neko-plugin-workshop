"""Durable Recall sessions and adaptive scheduling.

One persisted state drives the dashboard, active session, scheduler, and
companion cues. Knowledge sources stay outside this module's state model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from random import SystemRandom
from typing import Any, Literal
from uuid import uuid4

if __package__:
    from .fsrs_bridge import (
        FSRSBridge,
        StudyFsrsRating,
        create_card,
    )
    from .models import VocabularyItem, VocabularySource
else:
    from fsrs_bridge import (
        FSRSBridge,
        StudyFsrsRating,
        create_card,
    )
    from models import VocabularyItem, VocabularySource


SessionIntent = Literal["review_due", "learn_new", "mixed"]
VALID_INTENTS = {"review_due", "learn_new", "mixed"}
VALID_GRADES = {"forgot", "hard", "good"}
STATE_VERSION = 2
SCHEDULER = FSRSBridge(retention_target=0.90)
RANDOM = SystemRandom()
GRADE_RATINGS = {
    "forgot": StudyFsrsRating.Again,
    "hard": StudyFsrsRating.Hard,
    "good": StudyFsrsRating.Good,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def initial_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "selected_source": "official.cet4.core.v1",
        "familiarity": "unset",
        "items": {},
        "review_events": [],
        "sessions": {},
        "active_session_id": "",
        "processed_events": {},
    }


def migrate_state(value: Any) -> dict[str, Any]:
    """Upgrade the old UI-demo state without losing its review history."""
    if not isinstance(value, dict):
        return initial_state()
    version = int(value.get("version") or 0)
    if version == STATE_VERSION:
        state = value
    elif version == 1:
        state = initial_state()
        state["selected_source"] = str(value.get("selected_source") or state["selected_source"])
        state["familiarity"] = str(value.get("familiarity") or "unset")
        for item_id, legacy in (value.get("items") or {}).items():
            if not isinstance(legacy, dict):
                continue
            card = create_card(str(item_id)).to_dict()
            card.update(
                {
                    "due": str(legacy.get("due_at") or card["due"]),
                    "scheduled_days": float(legacy.get("interval_days") or 0),
                    "reps": int(legacy.get("repetitions") or 0),
                    "lapses": int(legacy.get("lapses") or 0),
                    "state": str(legacy.get("status") or "learning"),
                    "last_review": str(legacy.get("last_reviewed_at") or ""),
                }
            )
            state["items"][str(item_id)] = {
                "item_id": str(item_id),
                "phase": card["state"],
                "last_grade": str(legacy.get("last_grade") or ""),
                "due_at": card["due"],
                "fsrs_card": card,
                "revision": 1,
            }
        state["review_events"] = list(value.get("history") or [])[-500:]
    else:
        return initial_state()
    state["version"] = STATE_VERSION
    state.setdefault("items", {})
    state.setdefault("review_events", [])
    state.setdefault("sessions", {})
    state.setdefault("active_session_id", "")
    state.setdefault("processed_events", {})
    return state


def _normalize_in_place(state: dict[str, Any]) -> None:
    normalized = migrate_state(state)
    if normalized is not state:
        state.clear()
        state.update(normalized)


def _record_due(record: dict[str, Any], now: datetime) -> bool:
    due_at = parse_time(record.get("due_at") or (record.get("fsrs_card") or {}).get("due"))
    return due_at is not None and due_at <= now


def _active_session(state: dict[str, Any]) -> dict[str, Any] | None:
    session_id = str(state.get("active_session_id") or "")
    session = (state.get("sessions") or {}).get(session_id)
    if isinstance(session, dict) and session.get("status") == "active":
        return session
    return None


def session_summary(session: dict[str, Any] | None) -> dict[str, Any] | None:
    if not session:
        return None
    total = len(session.get("queue") or [])
    position = min(int(session.get("position") or 0), total)
    return {
        "id": session.get("id"),
        "intent": session.get("intent"),
        "status": session.get("status"),
        "position": position,
        "total": total,
        "box_size": int(session.get("box_size") or min(5, total)),
        "remaining": max(0, total - position),
        "answered": int(session.get("answered") or 0),
        "forgotten": int(session.get("forgotten") or 0),
        "new_seen": int(session.get("new_seen") or 0),
        "reviewed": int(session.get("reviewed") or 0),
    }


def dashboard(state: dict[str, Any], source: VocabularySource, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    _normalize_in_place(state)
    item_states = state["items"]
    due = sum(1 for record in item_states.values() if isinstance(record, dict) and _record_due(record, now))
    mastered = sum(
        1
        for record in item_states.values()
        if isinstance(record, dict)
        and float((record.get("fsrs_card") or {}).get("stability") or 0) >= 21
    )
    learned = sum(1 for value in item_states.values() if isinstance(value, dict))
    active = _active_session(state)
    return {
        "source": source.info.to_dict(),
        "due_count": due,
        "learned_count": learned,
        "mastered_count": mastered,
        "total_count": source.info.item_count,
        "new_count": max(0, source.info.item_count - learned),
        "familiarity": str(state.get("familiarity") or "unset"),
        "active_session": session_summary(active) if active else None,
        "memory_boundary": "学习进度只保存在 Recall，不会写入或接管 N.E.K.O 长期记忆。",
    }


def _item_map(source: VocabularySource) -> dict[str, VocabularyItem]:
    return {item.id: item for item in source.items()}


def _materialize(queue_item: dict[str, Any], state: dict[str, Any], source: VocabularySource) -> dict[str, Any] | None:
    item = _item_map(source).get(str(queue_item.get("item_id") or ""))
    if item is None:
        return None
    progress = state["items"].get(item.id, {})
    return {
        **item.to_dict(),
        "is_new": queue_item.get("kind") == "new" and not progress,
        "kind": str(queue_item.get("kind") or "review"),
        "attempt": int(queue_item.get("attempt") or 0),
        "progress": progress,
    }


def _select_queue(
    state: dict[str, Any],
    source: VocabularySource,
    *,
    intent: SessionIntent,
    now: datetime,
    new_limit: int,
    review_limit: int,
) -> list[dict[str, Any]]:
    due: list[tuple[datetime, str]] = []
    fresh: list[str] = []
    for item in source.items():
        record = state["items"].get(item.id)
        if not isinstance(record, dict):
            fresh.append(item.id)
        elif _record_due(record, now):
            due.append((parse_time(record.get("due_at")) or now, item.id))
    due.sort(key=lambda row: row[0])
    due_ids = [item_id for _, item_id in due[: max(0, review_limit)]]
    RANDOM.shuffle(due_ids)
    fresh_pool = fresh[: max(30, max(0, new_limit) * 6)]
    new_count = min(max(0, new_limit), len(fresh_pool))
    new_ids = RANDOM.sample(fresh_pool, new_count) if new_count else []
    sequential = fresh[:new_count]
    if new_count and len(fresh_pool) > new_count and new_ids == sequential:
        new_ids[-1] = fresh_pool[new_count]
    if intent == "review_due":
        return [{"item_id": item_id, "kind": "review", "attempt": 0} for item_id in due_ids]
    if intent == "learn_new":
        return [{"item_id": item_id, "kind": "new", "attempt": 0} for item_id in new_ids]

    # A mixed session is one bounded memory box. Due words take priority and
    # remaining slots are filled with new words from a shuffled near-term pool.
    box_limit = min(5, max(0, review_limit) + max(0, new_limit))
    due_ids = due_ids[:box_limit]
    new_ids = new_ids[: max(0, box_limit - len(due_ids))]
    queue: list[dict[str, Any]] = []
    for index in range(max(len(due_ids), len(new_ids))):
        if index < len(due_ids):
            queue.append({"item_id": due_ids[index], "kind": "review", "attempt": 0})
        if index < len(new_ids):
            queue.append({"item_id": new_ids[index], "kind": "new", "attempt": 0})
    return queue


def create_session(
    state: dict[str, Any],
    source: VocabularySource,
    *,
    intent: SessionIntent = "mixed",
    now: datetime | None = None,
    new_limit: int = 5,
    review_limit: int = 5,
    resume: bool = True,
) -> dict[str, Any]:
    if intent not in VALID_INTENTS:
        raise ValueError("intent must be review_due, learn_new or mixed")
    now = now or utc_now()
    _normalize_in_place(state)
    active = _active_session(state)
    if resume and active and active.get("intent") == intent:
        return session_payload(active, state, source)
    if active:
        active["status"] = "paused"
    queue = _select_queue(
        state,
        source,
        intent=intent,
        now=now,
        new_limit=new_limit,
        review_limit=review_limit,
    )
    session_id = f"rs_{uuid4().hex}"
    session = {
        "id": session_id,
        "source_id": source.info.id,
        "intent": intent,
        "status": "active" if queue else "complete",
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "position": 0,
        "queue": queue,
        "box_size": len(queue),
        "answered": 0,
        "forgotten": 0,
        "new_seen": 0,
        "reviewed": 0,
    }
    state["sessions"][session_id] = session
    state["active_session_id"] = session_id if queue else ""
    _trim_mapping(state["sessions"], 20)
    return session_payload(session, state, source)


def session_payload(session: dict[str, Any], state: dict[str, Any], source: VocabularySource) -> dict[str, Any]:
    summary = session_summary(session) or {}
    position = int(session.get("position") or 0)
    queue = session.get("queue") or []
    current = _materialize(queue[position], state, source) if position < len(queue) else None
    return {**summary, "current_card": current, "source": source.info.to_dict()}


def get_active_session(state: dict[str, Any], source: VocabularySource) -> dict[str, Any] | None:
    _normalize_in_place(state)
    active = _active_session(state)
    return session_payload(active, state, source) if active else None


def schedule_review(
    state: dict[str, Any],
    *,
    item_id: str,
    grade: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if grade not in VALID_GRADES:
        raise ValueError("grade must be forgot, hard or good")
    now = now or utc_now()
    _normalize_in_place(state)
    previous = state["items"].get(item_id) or {}
    card = previous.get("fsrs_card") if isinstance(previous, dict) else None
    if not isinstance(card, dict):
        card = create_card(item_id, now).to_dict()
    updated, schedule = SCHEDULER.rate_answer(card, GRADE_RATINGS[grade], now)
    record = {
        "item_id": item_id,
        "phase": updated.state,
        "status": updated.state,
        "repetitions": updated.reps,
        "lapses": updated.lapses,
        "interval_days": updated.scheduled_days,
        "stability": updated.stability,
        "difficulty": updated.difficulty,
        "last_grade": grade,
        "last_reviewed_at": updated.last_review,
        "due_at": updated.due,
        "fsrs_card": updated.to_dict(),
        "revision": int(previous.get("revision") or 0) + 1 if isinstance(previous, dict) else 1,
    }
    state["items"][item_id] = record
    state["review_events"].append(
        {
            "event_id": f"re_{uuid4().hex}",
            "item_id": item_id,
            "grade": grade,
            "at": _iso(now),
            "due_at": updated.due,
            "scheduled_days": updated.scheduled_days,
            "retrievability_before": round(float(schedule["retrievability_before"]), 4),
        }
    )
    del state["review_events"][:-1000]
    return record


def _schedule_copy(record: dict[str, Any]) -> dict[str, Any]:
    days = float(record.get("interval_days") or 0)
    if days <= 0.01:
        label = "这一轮稍后再见"
    elif days < 1:
        label = f"约 {max(1, round(days * 24))} 小时后"
    elif days < 2:
        label = "明天"
    else:
        label = f"约 {round(days)} 天后"
    return {"due_at": record.get("due_at"), "interval_days": days, "label": label}


def _companion_cue(grade: str, item: VocabularyItem, record: dict[str, Any], *, complete: bool) -> dict[str, str]:
    if complete:
        return {"tone": "complete", "text": "做得好，今天这组完成啦。"}
    if grade == "forgot":
        return {"tone": "gentle", "text": f"{item.word} 有点生也没关系，我们再来一次。"}
    if grade == "hard":
        return {"tone": "steady", "text": f"{item.word} 已经有印象了，继续就好。"}
    return {"tone": "warm", "text": f"接住了，{item.word}。"}


def submit_review(
    state: dict[str, Any],
    source: VocabularySource,
    *,
    session_id: str,
    item_id: str,
    grade: str,
    client_event_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Single atomic command for event, item, session, UI and companion state."""
    if not client_event_id:
        raise ValueError("client_event_id is required")
    now = now or utc_now()
    _normalize_in_place(state)
    duplicate = state["processed_events"].get(client_event_id)
    if isinstance(duplicate, dict):
        return duplicate
    session = state["sessions"].get(session_id)
    if not isinstance(session, dict) or session.get("status") != "active":
        raise ValueError("session is not active")
    position = int(session.get("position") or 0)
    queue = session.get("queue") or []
    if position >= len(queue) or str(queue[position].get("item_id")) != item_id:
        raise ValueError("review item does not match the active card")
    item = _item_map(source).get(item_id)
    if item is None:
        raise ValueError("unknown vocabulary item")

    was_new = queue[position].get("kind") == "new" and item_id not in state["items"]
    record = schedule_review(state, item_id=item_id, grade=grade, now=now)
    session["answered"] = int(session.get("answered") or 0) + 1
    session["forgotten"] = int(session.get("forgotten") or 0) + (grade == "forgot")
    session["new_seen"] = int(session.get("new_seen") or 0) + was_new
    session["reviewed"] = int(session.get("reviewed") or 0) + (not was_new)
    if grade == "forgot" and int(queue[position].get("attempt") or 0) == 0:
        queue.append({"item_id": item_id, "kind": "review", "attempt": 1})
    session["position"] = position + 1
    session["updated_at"] = _iso(now)
    if session["position"] >= len(queue):
        session["status"] = "complete"
        state["active_session_id"] = ""

    payload = session_payload(session, state, source)
    result = {
        "event_id": state["review_events"][-1]["event_id"],
        "client_event_id": client_event_id,
        "item_id": item_id,
        "grade": grade,
        "item_state": record,
        "schedule": _schedule_copy(record),
        "session": payload,
        "next_card": payload.get("current_card"),
        "companion_cue": _companion_cue(grade, item, record, complete=session["status"] == "complete"),
        "dashboard": dashboard(state, source, now=now),
    }
    state["processed_events"][client_event_id] = result
    _trim_mapping(state["processed_events"], 200)
    return result


def _trim_mapping(mapping: dict[str, Any], limit: int) -> None:
    while len(mapping) > limit:
        mapping.pop(next(iter(mapping)))


__all__ = [
    "SessionIntent",
    "create_session",
    "dashboard",
    "get_active_session",
    "initial_state",
    "migrate_state",
    "parse_time",
    "schedule_review",
    "session_payload",
    "submit_review",
    "utc_now",
]
