from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ProactivePolicyResult:
    mode: str
    settings: dict[str, Any]
    endpoint: str
    error: str = ""


async def fetch_main_proactive_policy(
    *, port: int = 48911, timeout: float = 3.0
) -> ProactivePolicyResult:
    """Read NEKO's authoritative proactive-chat gate from the main service."""
    from aiohttp import ClientSession, ClientTimeout

    endpoint = f"http://127.0.0.1:{port}/api/proactive/mode"
    try:
        async with ClientSession(
            timeout=ClientTimeout(total=timeout), trust_env=False
        ) as client:
            async with client.get(endpoint, headers={"Accept": "application/json"}) as response:
                if response.status != 200:
                    return ProactivePolicyResult(
                        "unavailable", {}, endpoint, f"http_{response.status}"
                    )
                payload = await response.json(content_type=None)
    except Exception as exc:
        return ProactivePolicyResult("unavailable", {}, endpoint, type(exc).__name__)

    if not isinstance(payload, Mapping) or payload.get("success") is not True:
        return ProactivePolicyResult("unavailable", {}, endpoint, "invalid_response")
    settings = payload.get("settings")
    if not isinstance(settings, Mapping):
        return ProactivePolicyResult("unavailable", {}, endpoint, "invalid_settings")
    return ProactivePolicyResult(
        str(payload.get("mode") or "custom"),
        {str(key): value for key, value in settings.items()},
        endpoint,
    )


__all__ = ["ProactivePolicyResult", "fetch_main_proactive_policy"]
