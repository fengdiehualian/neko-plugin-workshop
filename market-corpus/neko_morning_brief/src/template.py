"""Tiny, predictable template renderer used by ``http.json`` source in single mode.

Syntax::

    BTC {data.bitcoin.cny} 元（{data.bitcoin.cny_24h_change:+.1f}%）
    NAS 剩余 {free_gb?:.1f} GB

* ``{path}``        - required placeholder; missing data fails the source.
* ``{path?}``       - optional placeholder; renders as an empty string.
* ``{path:spec}``   - ``spec`` is handed to :func:`format` on the value.
* ``{{`` / ``}}``   - literal braces.

Failure mode: a required placeholder that cannot be resolved raises
:class:`TemplateError` so the adapter reports ``PARSE_ERROR`` instead of
emitting a half-rendered string with stray ``{}`` in it.
"""

from __future__ import annotations

import re
from typing import Any

from .paths import MISSING, resolve

_LITERAL_OPEN = "\x00"
_LITERAL_CLOSE = "\x01"

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.\[\]\-]+)(\?)?(?::([^{}]*))?\}")


class TemplateError(ValueError):
    """Raised when a template cannot be rendered against the given payload."""


def _format(value: Any, spec: str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        value = int(value)
    if spec:
        try:
            return format(value, spec)
        except (ValueError, TypeError) as exc:
            raise TemplateError(f"bad format spec {spec!r} for {value!r}") from exc
    return str(value)


def render(template: str, root: Any) -> str:
    if not isinstance(template, str):
        raise TemplateError("template must be a string")

    protected = template.replace("{{", _LITERAL_OPEN).replace("}}", _LITERAL_CLOSE)

    def replace(match: re.Match[str]) -> str:
        path, optional, spec = match.group(1), match.group(2) == "?", match.group(3)
        value = resolve(root, path)
        if value is MISSING or value is None:
            if optional:
                return ""
            raise TemplateError(f"missing field: {path}")
        if isinstance(value, (dict, list)):
            raise TemplateError(f"field is not a scalar: {path}")
        return _format(value, spec)

    rendered = _PLACEHOLDER.sub(replace, protected)
    if "{" in rendered or "}" in rendered:
        # Unbalanced or unsupported placeholders: fail loudly rather than ship braces.
        raise TemplateError("unbalanced braces in template")
    rendered = rendered.replace(_LITERAL_OPEN, "{").replace(_LITERAL_CLOSE, "}")
    return rendered


__all__ = ["TemplateError", "render"]
