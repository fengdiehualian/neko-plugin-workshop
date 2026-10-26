"""Minimal JSON path resolver: a JSONPath subset that users can actually learn.

Supported grammar (deliberately small):

    $.data.items          -> dict traversal (leading "$." optional)
    data.items
    $.list[0]             -> fixed index (negative allowed)
    $.items[*]            -> array expansion, ONLY as the final token

Anything unsupported or missing yields :data:`MISSING`; this module never
raises for data problems so callers can decide between "skip this item" and
"fail the source".
"""

from __future__ import annotations

import re
from typing import Any

MISSING: Any = object()

_TOKEN = re.compile(r"^([A-Za-z_][A-Za-z0-9_\-]*)((?:\[(?:-?\d+|\*)\])*)$")
_INDEX = re.compile(r"\[(-?\d+|\*)\]")


def _tokenize(path: str) -> list[tuple[str, Any]] | None:
    if not isinstance(path, str):
        return None
    text = path.strip()
    if text.startswith("$"):
        text = text[1:]
    if text.startswith("."):
        text = text[1:]
    if not text:
        return None
    tokens: list[tuple[str, Any]] = []
    for raw in text.split("."):
        part = raw.strip()
        if not part:
            return None
        match = _TOKEN.match(part)
        if not match:
            return None
        tokens.append(("key", match.group(1)))
        for bracket in _INDEX.findall(match.group(2) or ""):
            if bracket == "*":
                tokens.append(("all", None))
            else:
                tokens.append(("idx", int(bracket)))
    return tokens or None


def resolve(root: Any, path: str) -> Any:
    """Walk ``path`` in ``root``; return :data:`MISSING` when it does not fit."""

    tokens = _tokenize(path)
    if not tokens:
        return MISSING

    current: Any = root
    for position, (kind, value) in enumerate(tokens):
        is_last = position == len(tokens) - 1

        if kind == "key":
            if isinstance(current, dict) and value in current:
                current = current[value]
            elif isinstance(current, list):
                # Convenience: map over a list when a key follows it.
                mapped = [
                    row[value] for row in current if isinstance(row, dict) and value in row
                ]
                current = mapped
            else:
                return MISSING
            continue

        if kind == "idx":
            if not isinstance(current, list):
                return MISSING
            try:
                current = current[value]
            except IndexError:
                return MISSING
            continue

        # kind == "all": array expansion, last token only.
        if not is_last or not isinstance(current, list):
            return MISSING
        return current

    return current


def resolve_str(root: Any, path: str, default: str = "") -> str:
    """Resolve and stringify, with a caller-supplied fallback."""

    value = resolve(root, path)
    if value is MISSING or value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return default
    return str(value)


__all__ = ["MISSING", "resolve", "resolve_str"]
