"""Text and time normalization shared by every adapter.

Rules are intentionally boring and predictable: strip markup, collapse
whitespace, cut at a hard limit, canonicalize URLs, and turn whatever a feed
calls a date into epoch seconds.
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import BriefItem

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "spm", "scm", "ref_src", "share_")
_TRACKING_EXACT = {"fbclid", "gclid", "yclid", "igshid", "mc_cid", "mc_eid"}

DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%d %b %Y %H:%M:%S %z",
)


def strip_html(text: str) -> str:
    """Remove tags, decode entities and collapse whitespace; never raises."""

    if not text:
        return ""
    without_tags = _TAG.sub(" ", text)
    return _WS.sub(" ", html.unescape(without_tags)).strip()


def clean_text(text: str, limit: int = 120) -> str:
    """Strip markup, collapse whitespace, cut on a word-ish boundary."""

    if text is None:
        return ""
    value = _WS.sub(" ", strip_html(str(text))).strip()
    if limit and len(value) > limit:
        cut = value[: limit - 1].rstrip()
        # Prefer not to slice a Latin word in half.
        if " " in cut[-12:]:
            cut = cut[: cut.rfind(" ")].rstrip()
        value = cut + "…"
    return value


def clean_url(url: str | None) -> str | None:
    """Keep http(s) only and drop common tracking parameters."""

    if not url or not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not (key.lower() in _TRACKING_EXACT or key.lower().startswith(_TRACKING_PREFIXES))
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def parse_ts(value: object) -> float | None:
    """Best-effort epoch seconds from int/float/str; ``None`` if hopeless."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            parsed = _parse_datetime(text)
            return parsed
    if number <= 0:
        return None
    if number > 1e14:          # microseconds
        return number / 1_000_000
    if number > 1e11:          # milliseconds
        return number / 1_000
    return number


def _parse_datetime(text: str) -> float | None:
    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def relative_time(ts: float | None, now: float | None = None) -> str:
    """Human-facing age, computed by the plugin so the model never does math."""

    if not ts:
        return ""
    current = time.time() if now is None else now
    delta = current - ts
    if delta < -300:
        return "刚刚"
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    if delta < 86400 * 2:
        return "昨天"
    if delta < 86400 * 7:
        return f"{int(delta // 86400)} 天前"
    return datetime.fromtimestamp(ts).strftime("%m-%d")


def make_item(
    *,
    source_id: str,
    title: object,
    summary: object = "",
    url: object = None,
    ts: object = None,
    weight: int = 3,
    tags: tuple[str, ...] = (),
    title_limit: int = 80,
    summary_limit: int = 120,
    snapshot: bool = False,
) -> BriefItem | None:
    """Normalize raw feed/API values into a ``BriefItem`` (or ``None`` if empty)."""

    clean_title = clean_text(str(title) if title is not None else "", title_limit)
    if not clean_title:
        return None
    return BriefItem(
        source_id=source_id,
        title=clean_title,
        summary=clean_text(str(summary) if summary is not None else "", summary_limit),
        url=clean_url(str(url)) if url else None,
        ts=parse_ts(ts),
        weight=weight,
        tags=tags,
        snapshot=snapshot,
    )


__all__ = [
    "DATE_FORMATS",
    "clean_text",
    "clean_url",
    "make_item",
    "parse_ts",
    "relative_time",
    "strip_html",
]
