from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Iterable, Mapping

_NEGATIVE_MARKERS = (
    "不喜欢",
    "没兴趣",
    "不要推荐",
    "别推荐",
    "讨厌",
    "not interested",
    "don't recommend",
)
_STOPWORDS = {
    "这个",
    "那个",
    "什么",
    "怎么",
    "为什么",
    "可以",
    "就是",
    "还是",
    "一个",
    "现在",
    "really",
    "about",
    "with",
    "that",
    "this",
    "have",
    "what",
    "when",
    "where",
}
_SENSITIVE_MARKERS = (
    "身份证",
    "手机号",
    "家庭住址",
    "银行卡",
    "病历",
    "性取向",
    "宗教信仰",
    "政治立场",
    "password",
    "phone number",
    "home address",
    "medical record",
    "religion",
    "sexual orientation",
)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.#-]{2,}|[\u4e00-\u9fff]{2,8}")


def message_from_memory_record(record: Any) -> dict[str, Any] | None:
    payload = getattr(record, "payload", None)
    if not isinstance(payload, Mapping):
        dumper = getattr(record, "dump", None)
        payload = dumper() if callable(dumper) else None
    if not isinstance(payload, Mapping) or payload.get("type") != "user_message":
        return None
    text = str(payload.get("content") or "").strip()
    if not text:
        return None
    ts = float(payload.get("_ts") or 0.0)
    lanlan = str(payload.get("lanlan") or "")
    digest = hashlib.sha256(f"{ts}:{lanlan}:{text}".encode("utf-8")).hexdigest()[:24]
    return {"id": digest, "text": text[:1000], "timestamp": ts, "lanlan": lanlan}


def heuristic_updates(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    positive: Counter[str] = Counter()
    negative: Counter[str] = Counter()
    for message in messages:
        text = str(message.get("text") or "").strip()
        lowered = text.lower()
        target = (
            negative
            if any(marker in lowered for marker in _NEGATIVE_MARKERS)
            else positive
        )
        for token in _TOKEN_RE.findall(lowered):
            token = token.strip().lower()
            if token not in _STOPWORDS and not any(
                marker in token for marker in _SENSITIVE_MARKERS
            ):
                target[token] += 1
    updates: list[dict[str, Any]] = []
    for topic, count in positive.most_common(8):
        updates.append(
            {
                "topic": topic,
                "polarity": "positive",
                "confidence": min(0.75, 0.35 + count * 0.1),
            }
        )
    for topic, count in negative.most_common(8):
        updates.append(
            {
                "topic": topic,
                "polarity": "negative",
                "confidence": min(0.9, 0.55 + count * 0.1),
            }
        )
    return updates


def apply_profile_updates(
    profile: Mapping[str, Any] | None,
    updates: Iterable[Mapping[str, Any]],
    *,
    now: float,
) -> dict[str, Any]:
    current = profile if isinstance(profile, Mapping) else {}
    by_name: dict[str, dict[str, Any]] = {}
    for item in (
        current.get("interests", [])
        if isinstance(current.get("interests"), list)
        else []
    ):
        if isinstance(item, Mapping) and str(item.get("name") or "").strip():
            by_name[str(item["name"]).strip().lower()] = dict(item)

    for update in updates:
        name = str(update.get("topic") or "").strip()[:80]
        if not name:
            continue
        key = name.lower()
        item = by_name.get(
            key, {"name": name, "weight": 0.0, "evidence_count": 0, "negative_count": 0}
        )
        confidence = min(1.0, max(0.0, float(update.get("confidence", 0.5))))
        if str(update.get("polarity") or "positive") == "negative":
            item["negative_count"] = int(item.get("negative_count", 0)) + 1
            item["weight"] = max(-1.0, float(item.get("weight", 0.0)) - confidence)
        else:
            item["evidence_count"] = int(item.get("evidence_count", 0)) + 1
            item["weight"] = min(
                1.0, float(item.get("weight", 0.0)) + confidence * 0.35
            )
        item["status"] = (
            "active" if int(item.get("evidence_count", 0)) >= 2 else "trial"
        )
        item["updated_at"] = now
        by_name[key] = item

    interests = sorted(
        by_name.values(), key=lambda item: float(item.get("weight", 0.0)), reverse=True
    )[:32]
    return {"interests": interests, "updated_at": now}


def active_interests(
    profile: Mapping[str, Any] | None, *, include_trial: bool = True
) -> list[dict[str, Any]]:
    if not isinstance(profile, Mapping):
        return []
    result = []
    for item in (
        profile.get("interests", [])
        if isinstance(profile.get("interests"), list)
        else []
    ):
        if not isinstance(item, Mapping) or float(item.get("weight", 0.0)) <= 0:
            continue
        if not include_trial and item.get("status") != "active":
            continue
        result.append(dict(item))
    return result
