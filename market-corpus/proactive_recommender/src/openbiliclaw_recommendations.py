from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse


def _safe_http_url(value: object) -> str:
    url = str(value or "").strip()[:1000]
    if not url or any(character.isspace() for character in url):
        return ""
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return ""
    return url


def normalize_openbiliclaw_recommendations(
    payload: Mapping[str, Any], *, now: float | None = None
) -> list[dict[str, Any]]:
    """Convert OpenBiliClaw's public recommendation response into NEKO candidates."""
    rows = payload.get("items")
    if not isinstance(rows, list):
        return []
    discovered_at = time.time() if now is None else now
    output: list[dict[str, Any]] = []
    for index, raw in enumerate(rows[:20]):
        if not isinstance(raw, Mapping):
            continue
        title = str(raw.get("title") or "").strip()[:300]
        bvid = str(raw.get("bvid") or "").strip()
        url = _safe_http_url(raw.get("content_url"))
        if not url and bvid:
            url = f"https://www.bilibili.com/video/{bvid}"
        identity = str(
            raw.get("item_key")
            or raw.get("content_id")
            or bvid
            or raw.get("id")
            or url
        ).strip()
        if not title or not url or not identity:
            continue
        platform = str(raw.get("source_platform") or "web").strip().lower()[:32]
        topic = str(raw.get("topic_label") or "").strip()[:120]
        expression = str(raw.get("expression") or "").strip()
        body = str(raw.get("body_text") or "").strip()
        author = str(raw.get("up_name") or "").strip()
        snippet_parts = [part for part in (expression, body, author) if part]
        candidate_id = hashlib.sha256(
            f"openbiliclaw\n{platform}\n{identity}".encode("utf-8")
        ).hexdigest()[:24]
        output.append(
            {
                "id": candidate_id,
                "source": f"openbiliclaw:{platform}",
                "source_platform": platform,
                "title": title,
                "snippet": "\n".join(snippet_parts)[:1200],
                "url": url,
                "query": topic,
                "discovered_at": discovered_at,
                # These are already admitted and personalized by OpenBiliClaw.
                # Preserve its ordering without trusting an arbitrary wire score.
                "llm_relevance": max(0.82, 0.96 - index * 0.01),
                "llm_quality": 0.8,
                "matched_interests": [topic] if topic else [],
                "openbiliclaw_id": str(raw.get("id") or "")[:64],
                "openbiliclaw_item_key": str(raw.get("item_key") or "")[:160],
            }
        )
    return output


@dataclass(frozen=True, slots=True)
class OpenBiliClawRecommendationResult:
    candidates: list[dict[str, Any]]
    endpoint: str
    error: str = ""


async def fetch_openbiliclaw_recommendations(
    *, port: int, timeout: float = 8.0
) -> OpenBiliClawRecommendationResult:
    """Read sanitized recommendation output from a loopback OpenBiliClaw backend."""
    from aiohttp import ClientSession, ClientTimeout

    endpoint = f"http://127.0.0.1:{port}/api/recommendations"
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as client:
            async with client.get(endpoint, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    return OpenBiliClawRecommendationResult(
                        [], endpoint, f"http_{response.status}"
                    )
                payload = await response.json(content_type=None)
    except Exception as exc:
        return OpenBiliClawRecommendationResult(
            [], endpoint, type(exc).__name__
        )
    if not isinstance(payload, Mapping):
        return OpenBiliClawRecommendationResult([], endpoint, "invalid_response")
    return OpenBiliClawRecommendationResult(
        normalize_openbiliclaw_recommendations(payload), endpoint
    )


__all__ = [
    "OpenBiliClawRecommendationResult",
    "fetch_openbiliclaw_recommendations",
    "normalize_openbiliclaw_recommendations",
]
