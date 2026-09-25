"""Node logging helpers."""

from __future__ import annotations

import time
from typing import Any


def _redact_metadata(key: str, value: Any) -> str:
    """Format log metadata without leaking secrets."""

    lowered = key.lower()
    if any(marker in lowered for marker in ("api_key", "token", "secret", "authorization")):
        return "<redacted>"
    text = str(value)
    return text if len(text) <= 160 else f"{text[:157]}..."


def _format_metadata(metadata: dict[str, Any]) -> str:
    """Format compact node log metadata."""

    parts = []
    for key, value in metadata.items():
        if value is None:
            continue
        parts.append(f"{key}={_redact_metadata(key, value)}")
    return " ".join(parts)


def emit_log(logger: Any, level: str, message: str) -> None:
    """Write through the host logger when available."""

    if logger is None:
        return
    method = getattr(logger, level, None) or getattr(logger, "info", None)
    if method is None:
        return
    try:
        method(message)
    except Exception:  # noqa: BLE001 - host logger implementations can fail unexpectedly.
        return


class LoggedNode:
    """Base helper that records node enter and exit logs."""

    node_name: str = "node"

    def __init__(self, logger: Any):
        self.logger = logger

    def _begin(self, **metadata: Any) -> float:
        emit_log(
            self.logger,
            "info",
            f"[writer_power_analysis] node.enter name={self.node_name} {_format_metadata(metadata)}".rstrip(),
        )
        return time.perf_counter()

    def _end(self, started: float, status: str = "ok", **metadata: Any) -> None:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        emit_log(
            self.logger,
            "info",
            f"[writer_power_analysis] node.exit name={self.node_name} status={status} duration_ms={duration_ms} {_format_metadata(metadata)}".rstrip(),
        )

    def _fail(self, started: float, exc: BaseException, **metadata: Any) -> None:
        self._end(started, "error", error_type=type(exc).__name__, error=str(exc), **metadata)
