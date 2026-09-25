from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _text_list(value: object, *, limit: int, item_limit: int = 160) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value[:limit] if (text := _text(item, item_limit))]


def _weight(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _preferences(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for raw in value[:12]:
        if not isinstance(raw, Mapping):
            continue
        domain = _text(raw.get("domain"), 80)
        if not domain:
            continue
        specifics: list[dict[str, Any]] = []
        raw_specifics = raw.get("specifics")
        if isinstance(raw_specifics, list):
            for item in raw_specifics[:8]:
                if not isinstance(item, Mapping):
                    continue
                name = _text(item.get("name"), 100)
                if name:
                    specifics.append(
                        {"name": name, "weight": _weight(item.get("weight"))}
                    )
        output.append(
            {
                "domain": domain,
                "weight": _weight(raw.get("weight")),
                "specifics": specifics,
            }
        )
    return output


def normalize_openbiliclaw_profile(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the useful, bounded subset of OpenBiliClaw's “My Profile” view."""
    mbti = payload.get("mbti")
    return {
        "initialized": bool(payload.get("initialized")),
        "personality_portrait": _text(payload.get("personality_portrait"), 1600),
        "current_phase": _text(payload.get("current_phase"), 800),
        "core_traits": _text_list(payload.get("core_traits"), limit=12),
        "deep_needs": _text_list(payload.get("deep_needs"), limit=8, item_limit=240),
        "values": _text_list(payload.get("values"), limit=8, item_limit=240),
        "motivational_drivers": _text_list(
            payload.get("motivational_drivers"), limit=8, item_limit=240
        ),
        "cognitive_style": _text_list(
            payload.get("cognitive_style"), limit=8, item_limit=240
        ),
        "mbti": {
            "type": _text(mbti.get("type"), 16) if isinstance(mbti, Mapping) else "",
            "confidence": (
                _weight(mbti.get("confidence")) if isinstance(mbti, Mapping) else 0.0
            ),
        },
        "likes": _preferences(payload.get("likes")),
        "dislikes": _preferences(payload.get("dislikes")),
    }


@dataclass(frozen=True, slots=True)
class OpenBiliClawProfileResult:
    profile: dict[str, Any]
    endpoint: str
    error: str = ""


async def fetch_openbiliclaw_profile(
    *, port: int, timeout: float = 8.0
) -> OpenBiliClawProfileResult:
    """Read the public profile summary from the loopback OpenBiliClaw backend."""
    from aiohttp import ClientSession, ClientTimeout

    endpoint = f"http://127.0.0.1:{port}/api/profile-summary"
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout)) as client:
            async with client.get(endpoint, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    return OpenBiliClawProfileResult(
                        {}, endpoint, f"http_{response.status}"
                    )
                payload = await response.json(content_type=None)
    except Exception as exc:
        return OpenBiliClawProfileResult({}, endpoint, type(exc).__name__)
    if not isinstance(payload, Mapping):
        return OpenBiliClawProfileResult({}, endpoint, "invalid_response")
    return OpenBiliClawProfileResult(normalize_openbiliclaw_profile(payload), endpoint)


__all__ = [
    "OpenBiliClawProfileResult",
    "fetch_openbiliclaw_profile",
    "normalize_openbiliclaw_profile",
]
