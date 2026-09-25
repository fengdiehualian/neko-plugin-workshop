"""Shared interpretation of the dispatcher result protocol."""

from __future__ import annotations

COMMITTED_RESULT_PREFIXES = ("pushed(", "dry_run(")


def output_was_committed(result: str | None) -> bool:
    return bool(result) and str(result).startswith(COMMITTED_RESULT_PREFIXES)
