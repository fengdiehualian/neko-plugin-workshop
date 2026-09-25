from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.#-]{1,}|[\u4e00-\u9fff]{2,6}")


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / max(1, len(a | b))


def was_previously_delivered(
    candidate: Mapping[str, Any],
    history: Iterable[Mapping[str, Any]],
) -> bool:
    """Match prior deliveries by stable ID, URL, or normalized title."""
    candidate_id = str(candidate.get("id") or "").strip()
    candidate_url = str(candidate.get("url") or "").strip()
    candidate_title = " ".join(str(candidate.get("title") or "").lower().split())
    for item in history:
        if candidate_id and candidate_id == str(item.get("candidate_id") or "").strip():
            return True
        if candidate_url and candidate_url == str(item.get("url") or "").strip():
            return True
        history_title = " ".join(str(item.get("title") or "").lower().split())
        if candidate_title and candidate_title == history_title:
            return True
    return False


def rank_candidates(
    candidates: Iterable[Mapping[str, Any]],
    interests: Iterable[Mapping[str, Any]],
    history: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    interest_list = [dict(item) for item in interests]
    recent_titles = [str(item.get("title") or "") for item in history][-20:]
    ranked: list[dict[str, Any]] = []
    for raw in candidates:
        item = dict(raw)
        haystack = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
        lexical = 0.0
        negative_penalty = 0.0
        matched = [
            str(value)
            for value in item.get("matched_interests", [])
            if str(value).strip()
        ][:4]
        for interest in interest_list:
            name = str(interest.get("name") or "").lower()
            weight = float(interest.get("weight", 0.0))
            if name and name in haystack:
                if weight > 0:
                    lexical = max(lexical, min(1.0, 0.55 + weight * 0.45))
                    if name not in matched:
                        matched.append(name)
                else:
                    negative_penalty = max(negative_penalty, min(1.0, abs(weight)))
        llm_relevance = float(item.get("llm_relevance", 0.0))
        quality = float(item.get("llm_quality", 0.5))
        relevance = max(lexical, llm_relevance)
        novelty = 1.0 - max(
            (
                _similarity(str(item.get("title") or ""), title)
                for title in recent_titles
            ),
            default=0.0,
        )
        score = (
            0.62 * relevance + 0.18 * quality + 0.20 * novelty - 0.55 * negative_penalty
        )
        item["score"] = round(min(1.0, max(0.0, score)), 4)
        item["matched_interests"] = matched[:4]
        ranked.append(item)
    return sorted(ranked, key=lambda item: float(item.get("score", 0.0)), reverse=True)
