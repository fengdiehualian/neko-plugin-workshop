from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class ProactiveReportError(RuntimeError):
    pass


class NekoSpeaker:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("N.E.K.O URL must use HTTP on localhost")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("N.E.K.O URL contains unsupported components")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(0.1, float(timeout_seconds))

    def speak(self, line: str, event: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "session_id": "mo_companion",
            "lanlan_name": "小pan",
            "line": line,
            "event": event or {"kind": "mental_omega_report"},
        }
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        request = Request(
            f"{self.base_url}/api/game/mental_omega/speak",
            data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except HTTPError as exc:
            with exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ProactiveReportError(f"N.E.K.O HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProactiveReportError(f"N.E.K.O unavailable: {exc}") from exc

        if not body:
            return {"accepted": True}
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProactiveReportError("N.E.K.O returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ProactiveReportError("N.E.K.O response must be a JSON object")
        return result
