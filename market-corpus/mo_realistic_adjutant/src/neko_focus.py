from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class NekoFocusError(RuntimeError):
    pass


class NekoFocusController:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("N.E.K.O URL must use HTTP on localhost")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        self.state_path = local / "NekoMOCompanion" / "focus_state.json"
        self.active = False

    def sync(self, game_running: bool) -> bool:
        if game_running:
            self.enter()
        else:
            self.exit()
        return self.active

    def enter(self) -> None:
        if self.active:
            return
        saved = self._load_saved()
        if saved is None:
            mode = self._request("GET", "/api/proactive/mode")
            settings = mode.get("settings")
            settings = settings if isinstance(settings, dict) else {}
            saved = {
                "proactive_settings": {
                    key: value
                    for key, value in settings.items()
                    if key != "proactiveVisionEnabled"
                }
            }
            self._save(saved)
        self._request("POST", "/api/proactive/mode", {"mode": "off"})
        self.active = True

    def exit(self) -> None:
        saved = self._load_saved()
        if saved is None:
            self.active = False
            return
        settings = saved.get("proactive_settings")
        if isinstance(settings, dict) and settings:
            self._request("POST", "/api/proactive/settings", settings)
        try:
            self.state_path.unlink(missing_ok=True)
        except OSError as exc:
            raise NekoFocusError(f"cannot remove focus recovery state: {exc}") from exc
        self.active = False

    def _load_saved(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NekoFocusError(f"cannot read focus recovery state: {exc}") from exc
        return value if isinstance(value, dict) else None

    def _save(self, value: dict[str, Any]) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise NekoFocusError(f"cannot save focus recovery state: {exc}") from exc

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except HTTPError as exc:
            with exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise NekoFocusError(f"N.E.K.O HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise NekoFocusError(f"N.E.K.O unavailable: {exc}") from exc
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NekoFocusError("N.E.K.O returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise NekoFocusError("N.E.K.O response must be a JSON object")
        return value
