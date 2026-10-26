from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class BridgeError(RuntimeError):
    pass


PROTOCOL_VERSION = 10


class BridgeClient:
    def __init__(self, base_url: str, timeout_seconds: float = 2.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("MO bridge URL must use HTTP on localhost")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("MO bridge URL contains unsupported components")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def state(self) -> dict[str, Any]:
        return self._request("GET", "/v1/state")

    def query_entities(self, filters: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/entities/query", filters)

    def command_harvester(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/actions/harvester", command)

    def command_unit(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/actions/unit", command)

    def assign_group_batch(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/actions/group", command)

    def command_production(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/actions/production", command)

    def command_building_placement(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/actions/building-placement", command)

    def command_support_power(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/actions/support-power", command)

    def set_control(self, enabled: bool, *, network_opt_in: bool = False) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/session/control",
            {"enabled": enabled, "network_opt_in": network_opt_in},
        )

    def emergency_stop(self) -> dict[str, Any]:
        return self._request("POST", "/v1/emergency-stop", {})

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except HTTPError as exc:
            with exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise BridgeError(f"bridge HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise BridgeError(f"bridge unavailable: {exc}") from exc

        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError("bridge returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise BridgeError("bridge response must be a JSON object")
        if value.get("protocol_version") != PROTOCOL_VERSION:
            raise BridgeError("unsupported MO bridge protocol version")
        return value
