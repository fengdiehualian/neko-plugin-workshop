from __future__ import annotations

from typing import Any, Iterable, Mapping

_NEGATIVE = (
    "不喜欢",
    "没兴趣",
    "不要推荐",
    "别推荐",
    "讨厌",
    "not interested",
    "don't recommend",
)


def settle_history(
    history: Iterable[Mapping[str, Any]],
    messages: Iterable[Mapping[str, Any]],
    *,
    now: float,
    reply_window_seconds: float,
    ignored_window_seconds: float,
) -> list[dict[str, Any]]:
    rows = [dict(item) for item in history]
    new_messages = sorted(
        (dict(item) for item in messages),
        key=lambda item: float(item.get("timestamp", 0.0)),
    )
    for item in rows:
        if item.get("mode") != "live" or item.get("outcome") != "pending":
            continue
        submitted_at = float(item.get("timestamp", 0.0))
        reply = next(
            (
                message
                for message in new_messages
                if submitted_at
                < float(message.get("timestamp", 0.0))
                <= submitted_at + reply_window_seconds
            ),
            None,
        )
        if reply is not None:
            text = str(reply.get("text") or "").lower()
            item["outcome"] = (
                "rejected" if any(marker in text for marker in _NEGATIVE) else "engaged"
            )
            item["settled_at"] = float(reply.get("timestamp", now))
        elif now - submitted_at >= ignored_window_seconds:
            item["outcome"] = "ignored"
            item["settled_at"] = now
    return rows[-200:]


def apply_feedback_to_profile(
    profile: Mapping[str, Any],
    before: Iterable[Mapping[str, Any]],
    after: Iterable[Mapping[str, Any]],
    *,
    now: float,
) -> dict[str, Any]:
    old = {str(item.get("candidate_id")): str(item.get("outcome")) for item in before}
    changes = [
        item
        for item in after
        if str(item.get("outcome")) != old.get(str(item.get("candidate_id")))
        and item.get("outcome") in {"engaged", "rejected", "ignored"}
    ]
    result = {
        "interests": [dict(item) for item in profile.get("interests", [])],
        "updated_at": profile.get("updated_at", 0.0),
    }
    by_name = {
        str(item.get("name") or "").lower(): item for item in result["interests"]
    }
    delta = {"engaged": 0.18, "rejected": -0.4, "ignored": -0.1}
    for event in changes:
        adjustment = delta[str(event["outcome"])]
        for name in event.get("matched_interests", []):
            item = by_name.get(str(name).lower())
            if item is not None:
                item["weight"] = min(
                    1.0, max(-1.0, float(item.get("weight", 0.0)) + adjustment)
                )
                item["updated_at"] = now
    if changes:
        result["updated_at"] = now
    return result
