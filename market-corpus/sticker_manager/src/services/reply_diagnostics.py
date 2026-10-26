"""Content-free identifiers for tracing a tool within its original reply."""

from __future__ import annotations

import hashlib
from typing import Any


def reply_context_fields(kwargs: dict[str, Any]) -> str:
    ctx = kwargs.get("_ctx")
    reply = ctx.get("host_reply") if isinstance(ctx, dict) else None
    if not isinstance(reply, dict) or type(reply.get("version")) is not int or reply["version"] != 1:
        return "host_context=absent"
    fields = ["host_context=v1"]
    for key in ("request_id", "reply_id", "call_id", "invocation_id"):
        value = reply.get(key)
        if isinstance(value, str) and 0 < len(value) <= 1024:
            digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
            fields.append(f"{key}_hash={digest}")
    attempt = reply.get("attempt")
    if type(attempt) is int and 0 <= attempt <= 1000:
        fields.append(f"attempt={attempt}")
    return " ".join(fields)
