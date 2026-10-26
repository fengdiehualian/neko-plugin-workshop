"""Dependency-free RSS 2.0 / RSS 1.0 (RDF) / Atom parser.

The host ships Python but not ``feedparser``, and vendoring one more library
for three XML dialects is not worth it. Namespaces are ignored by matching the
local tag name, which is exactly what real feeds need.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

_XML_DECL = re.compile(rb"^\s*<\?xml[^>]*\?>", re.IGNORECASE)


def _local(tag: str) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def _first_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element:
        if _local(child.tag) in names:
            text = _text(child)
            if text:
                return text
    return ""


def _atom_link(element: ET.Element) -> str:
    fallback = ""
    for child in element:
        if _local(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        if not href:
            continue
        rel = (child.get("rel") or "alternate").strip().lower()
        if rel == "alternate":
            return href
        if not fallback:
            fallback = href
    if fallback:
        return fallback
    # RSS-style <link>text</link>
    for child in element:
        if _local(child.tag) == "link":
            text = _text(child)
            if text:
                return text
    return ""


def parse_feed(payload: bytes | str) -> list[dict[str, Any]]:
    """Return ``[{title, link, summary, published}]``; raises ValueError on garbage."""

    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    raw = _XML_DECL.sub(b"", raw, count=1).strip()
    if not raw:
        raise ValueError("empty feed")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"not valid XML: {exc}") from exc

    root_name = _local(root.tag)
    entries: list[ET.Element] = []

    if root_name == "feed":                     # Atom
        entries = [child for child in root if _local(child.tag) == "entry"]
    elif root_name == "rss":                    # RSS 2.0
        container = next(
            (child for child in root if _local(child.tag) == "channel"), None
        )
        if container is not None:
            entries = [child for child in container if _local(child.tag) == "item"]
    elif root_name == "rdf":                    # RSS 1.0
        entries = [child for child in root if _local(child.tag) == "item"]
    else:
        # Unknown wrapper: look for items anywhere, one level deep.
        entries = [child for child in root.iter() if _local(child.tag) == "item"]

    parsed: list[dict[str, Any]] = []
    for entry in entries:
        title = _first_text(entry, ("title",))
        link = _atom_link(entry) or _first_text(entry, ("guid", "id"))
        summary = _first_text(
            entry, ("description", "summary", "content", "encoded", "subtitle")
        )
        published = _first_text(
            entry, ("pubdate", "published", "updated", "date", "created", "issued")
        )
        if not title and not link:
            continue
        parsed.append(
            {"title": title, "link": link, "summary": summary, "published": published}
        )
    return parsed


__all__ = ["parse_feed"]
