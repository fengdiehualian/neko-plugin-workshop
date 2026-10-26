"""Charset-tolerant decoding plus stdlib-only HTML extraction.

Two responsibilities:

* turn raw search-engine bytes into text (they frequently mislabel the charset),
* pull structured ``title`` / ``url`` / ``snippet`` records and readable article
  text out of HTML without a third-party parser.
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Iterable, cast

MAX_TITLE_LEN = 200
MAX_SNIPPET_LEN = 320
MAX_CONTENT_LEN = 12_000

# A page this short with no result container is a redirect stub, not results.
_REDIRECT_SHELL_BYTES = 4096

_ALIAS = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "x-gbk": "gb18030",
    "windows-936": "gb18030",
    "iso-8859-1": "utf-8",
    "us-ascii": "utf-8",
    "latin-1": "utf-8",
}
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?\s*([a-zA-Z0-9_:.-]+)", re.I)
_META_HTTP_EQUIV = re.compile(
    rb"<meta[^>]+http-equiv=[\"']?content-type[\"']?[^>]*content=[\"'][^\"']*charset=([a-zA-Z0-9_:.-]+)",
    re.I,
)


def collapse(value: object) -> str:
    return " ".join(str(value or "").split())


def sanitize_text(value: object, limit: int = 0) -> str:
    """Strip characters that break the host's text/TTS pipeline.

    Search pages are full of private-use glyphs (Baidu icon fonts), zero-width
    joiners, and bidi overrides that can reverse a spoken sentence.
    """
    text = str(value or "")
    if not text:
        return ""
    drop = {0x200B, 0x200C, 0x200D, 0xFEFF, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0xFFFD}
    cleaned: list[str] = []
    for char in text:
        code = ord(char)
        if code in drop or 0xE000 <= code <= 0xF8FF:
            continue
        if unicodedata.category(char) in {"Cc", "Cf"} and char not in "\n\t":
            continue
        cleaned.append(char)
    out = " ".join("".join(cleaned).replace("\xa0", " ").split())
    if limit and len(out) > limit:
        out = out[: limit - 1].rstrip() + "…"
    return out


def _try_decode(raw: bytes, label: str) -> str | None:
    label = (label or "").strip().strip("\"'").casefold()
    if not label:
        return None
    for candidate in {_ALIAS.get(label, label), label}:
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return None


def decode_body(raw: bytes, content_type: str = "") -> str:
    """Decode HTML bytes, trusting reality over the declared charset.

    Baidu has been observed sending GBK bytes behind ``charset=utf-8``; a naive
    ``bytes.decode('utf-8')`` yields mojibake that then reaches the model and the
    voice pipeline.
    """
    if not raw:
        return ""
    declared = ""
    match = re.search(r"charset\s*=\s*([a-zA-Z0-9_:./-]+)", content_type or "", re.I)
    if match:
        declared = match.group(1)
    if declared.casefold().replace("-", "") in {"utf8"}:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    else:
        decoded = _try_decode(raw, declared)
        if decoded is not None:
            return decoded

    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16-le"),
                          (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            decoded = _try_decode(raw, encoding)
            if decoded is not None:
                return decoded

    for pattern in (_META_CHARSET, _META_HTTP_EQUIV):
        found = pattern.search(raw[:8192])
        if found:
            decoded = _try_decode(raw, found.group(1).decode("ascii", "ignore"))
            if decoded is not None:
                return decoded

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


class _StopParsing(Exception):
    """Internal: abort parsing once a text budget is met."""


class _BaseParser(HTMLParser):
    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, dict[str, str]]] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        attributes = {str(key).casefold(): (value or "") for key, value in attrs}
        if tag not in self._VOID:
            self._stack.append((tag, attributes))

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def error(self, message):  # type: ignore[no-untyped-def]
        return None

    def _attrs(self, depth: int) -> dict[str, str]:
        index = len(self._stack) - 1 - depth
        return self._stack[index][1] if 0 <= index < len(self._stack) else {}

    def _tag(self, depth: int) -> str:
        index = len(self._stack) - 1 - depth
        return self._stack[index][0] if 0 <= index < len(self._stack) else ""


class LinkCollector(_BaseParser):
    """Collect anchors together with the full text of their result container.

    Search engines mark a result block differently per engine: DuckDuckGo puts
    the class on the ``<a>`` itself, Bing on the wrapping ``<li>``, Baidu on a
    ``<div>``. Recording each link's ancestor snapshot and each container's
    accumulated text lets all of those resolve without a CSS engine or a
    third-party parser.
    """

    _NOISE = frozenset({"script", "style", "noscript", "svg", "template"})
    _CONTAINER_TAGS = frozenset({"li", "div", "article", "section", "ol", "ul"})

    def __init__(self, container_markers: Iterable[str] = ()) -> None:
        super().__init__()
        self._markers = tuple(marker.casefold() for marker in container_markers if marker)
        self.links: list[dict[str, object]] = []
        # Each entry: (container id, tag, nesting depth). The last entry is the
        # innermost open container, since block elements cannot nest inside <a>.
        self._container_stack: list[tuple[int, str, int]] = []
        self._container_parts: dict[int, list[str]] = {}
        self._container_seq = 0
        self._capture: dict[str, object] | None = None

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        super().handle_starttag(tag, attrs)
        if tag in self._NOISE:
            self._skip_depth += 1
        if tag in self._CONTAINER_TAGS:
            own = self._attrs(0)
            blob = f"{own.get('class', '')} {own.get('id', '')}".casefold().strip()
            explicit = self._markers and any(marker in blob for marker in self._markers)
            generic = (not self._markers) and bool(blob)
            if blob and (explicit or generic):
                self._container_seq += 1
                # Record the stack height so the matching close tag can be
                # identified by depth, not just by name.
                self._container_stack.append((self._container_seq, tag, len(self._stack)))
                self._container_parts[self._container_seq] = []
        if tag == "a" and self._capture is None and not self._skip_depth:
            own = self._attrs(0)
            href = own.get("href", "").strip()
            if not href or href.lower().startswith(("#", "javascript:", "mailto:", "tel:")):
                return
            enclosing = [
                (name, self._describe(name, attrs_map))
                for name, attrs_map in self._stack[:-1]
            ]
            self._capture = {
                "href": href,
                "class": own.get("class", ""),
                "rels": own.get("rel", ""),
                "data": {key: value for key, value in own.items() if key.startswith("data-")},
                "ancestors": enclosing,
                "container": self._container_stack[-1][0] if self._container_stack else None,
                "marker_hit": bool(self._markers) and any(
                    marker in " ".join(f"{name} {classes}" for name, classes in enclosing).casefold()
                    for marker in self._markers
                ),
                "text": [],
            }

    @staticmethod
    def _describe(tag: str, attrs_map: dict[str, str]) -> str:
        """Class/id plus the ad flags engines put on result wrappers.

        The attribute *name* is included, so a skip marker such as
        ``data-tuiguang`` matches the wrapper rather than its value.
        """
        parts = [attrs_map.get("class", ""), attrs_map.get("id", "")]
        parts += [f"{key}={value}" for key, value in attrs_map.items()
                  if key.startswith("data-")
                  and ("ad" in key or "tui" in key or "promo" in key or "spon" in key)]
        return " ".join(part for part in parts if part)

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag in self._NOISE:
            self._skip_depth = max(0, self._skip_depth - 1)
        # Pop containers by the stack height they were opened at. Matching on the
        # tag name alone would let `</div>` of an inner element close an outer
        # result card, truncating its text before the summary is reached.
        while self._container_stack and self._container_stack[-1][2] > len(self._stack):
            self._container_stack.pop()
        if (self._container_stack and self._container_stack[-1][1] == tag
                and self._container_stack[-1][2] == len(self._stack)):
            self._container_stack.pop()
        if tag == "a" and self._capture is not None:
            record = self._capture
            self._capture = None
            record_text = cast(list[str], record["text"])
            text = collapse("".join(record_text))
            if text:
                self.links.append({
                    "href": record["href"],
                    "text": text,
                    "class": record["class"],
                    "rels": record["rels"],
                    "data": record["data"],
                    "ancestors": record["ancestors"],
                    "container": record["container"],
                    "marker_hit": record["marker_hit"],
                })
        super().handle_endtag(tag)

    def handle_data(self, data):  # type: ignore[no-untyped-def]
        if self._skip_depth:
            return
        text = str(data)
        if not text.strip():
            return
        if self._capture is not None:
            cast(list[str], self._capture["text"]).append(text)
        # Every open container accumulates all its text, not just anchor text, so
        # summaries living in <div class="c-abstract"> or <a class="result__snippet">
        # are recoverable.
        for container_id, _, _ in self._container_stack:
            self._container_parts.setdefault(container_id, []).append(text)

    @property
    def container_texts(self) -> dict[int, str]:
        return {key: collapse(" ".join(parts)) for key, parts in self._container_parts.items()}


class TextExtractor(_BaseParser):
    """Strip markup down to readable text, dropping chrome and scripts."""

    _SKIP = frozenset({
        "script", "style", "noscript", "svg", "canvas", "template", "iframe",
        "nav", "footer", "header", "aside", "form", "button", "select", "option",
    })
    _BLOCK = frozenset({
        "p", "div", "section", "article", "li", "ul", "ol", "h1", "h2", "h3",
        "h4", "h5", "h6", "br", "hr", "tr", "table", "blockquote", "pre", "dt",
        "dd", "figcaption", "main", "td",
    })

    def __init__(self, limit: int = MAX_CONTENT_LEN) -> None:
        super().__init__()
        try:
            self.limit = max(200, int(limit))
        except (TypeError, ValueError):
            raise
        self._chunks: list[str] = []
        self._size = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
        super().handle_starttag(tag, attrs)
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BLOCK:
            self._chunks.append("\n")
            self._size += 1

    def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in self._BLOCK:
            self._chunks.append("\n")
            self._size += 1
        super().handle_endtag(tag)

    def handle_data(self, data):  # type: ignore[no-untyped-def]
        text = str(data)
        if self._in_title:
            self.title = collapse(self.title + text)
            return
        if self._skip_depth or not text.strip():
            return
        self._chunks.append(text)
        self._size += len(text)
        if self._size >= self.limit:
            raise _StopParsing()

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def collect_links(markup: str, container_markers: Iterable[str] = ()) -> LinkCollector:
    parser = LinkCollector(container_markers)
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        pass
    return parser


def extract_results(
    markup: str,
    *,
    anchor_markers: Iterable[str] = (),
    container_markers: Iterable[str] = (),
    skip_markers: Iterable[str] = (),
    prefer_h2_h3: bool = False,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Return ``[{title, url, snippet}]`` for the result blocks in ``markup``.

    A link qualifies when its own class matches ``anchor_markers`` or one of its
    ancestors matches ``container_markers``. The snippet is the container's text
    minus the title, so engines that inline the summary (Baidu, Sogou, Bing) and
    engines that use a sibling ``<a>`` (DuckDuckGo) both work.

    Real result blocks and navigation/suggestion chrome frequently match the same
    loose markers, so candidates are scored and the best ``limit`` are kept
    instead of the first ``limit``: an explicit marker match and an off-engine
    target host both count for a lot, a self-link or a near-empty container
    counts against.
    """
    anchors = tuple(marker.casefold() for marker in anchor_markers)
    containers = tuple(marker.casefold() for marker in container_markers)
    skips = tuple(marker.casefold() for marker in skip_markers)
    parser = collect_links(markup, containers or ("li", "div"))
    container_texts = parser.container_texts
    candidates: list[tuple[int, int, dict[str, str]]] = []
    seen: set[str] = set()

    for order, link in enumerate(parser.links):
        ancestor_blob = " ".join(
            f"{name} {classes}" for name, classes in (link.get("ancestors") or [])  # type: ignore[misc]
        ).casefold()
        own_class = str(link.get("class") or "").casefold()
        # Ad containers wrap the anchor rather than marking it, so the skip test
        # has to see the enclosing elements too.
        skip_blob = " ".join([own_class, ancestor_blob])
        if skips and any(marker in skip_blob for marker in skips):
            continue
        href = str(link.get("href") or "").strip()
        title = sanitize_text(link.get("text"), MAX_TITLE_LEN)
        if not title or not href:
            continue
        if prefer_h2_h3 and not any(
            name in {"h2", "h3"} for name, _ in (link.get("ancestors") or [])  # type: ignore[misc]
        ):
            continue
        anchor_hit = any(marker in own_class for marker in anchors)
        container_hit = any(marker in ancestor_blob for marker in containers)
        if not (anchor_hit or container_hit):
            continue

        score = 0
        if anchor_hit:
            score += 60
        if container_hit:
            score += 40
        snippet = ""
        owner = link.get("container")
        if isinstance(owner, int):
            block = container_texts.get(owner, "")
            if block:
                remainder = block.replace(str(link.get("text") or ""), " ", 1)
                snippet = sanitize_text(remainder, MAX_SNIPPET_LEN)
                if len(block) > 1200:
                    # A >1k container is a whole result list, not one card.
                    score += 5
                elif len(block) > 120:
                    score += 15
                elif len(block) > 24:
                    # Most genuine cards carry a short abstract, not a paragraph.
                    score += 5
                else:
                    # Tab bars and "related searches" chips are near-empty.
                    score -= 25
        if link.get("marker_hit"):
            # The link's own enclosing block carries the result marker, i.e. this
            # is the card itself rather than a stray link inside a wrapper.
            score += 25
        if len(title) >= 8:
            score += 5
        candidates.append((score, order, {"title": title, "url": href, "snippet": snippet,
                                          "score": str(score)}))

    candidates.sort(key=lambda item: (-item[0], item[1]))
    out: list[dict[str, str]] = []
    for _score, _order, item in candidates:
        key = item["url"].casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def readable_text(markup: str, limit: int = MAX_CONTENT_LEN) -> tuple[str, str]:
    """Return ``(title, readable_text)`` for a document."""
    parser = TextExtractor(limit=limit)
    try:
        parser.feed(markup)
        parser.close()
    except _StopParsing:
        pass
    except Exception:
        pass
    body = re.sub(r"[ \t]+", " ", parser.text)
    lines = [collapse(line) for line in body.splitlines()]
    kept: list[str] = []
    used = 0
    for line in lines:
        if not line:
            if kept and kept[-1]:
                kept.append("")
            continue
        kept.append(line)
        used += len(line)
        if used > limit:
            break
    text = "\n".join(kept)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return sanitize_text(parser.title, MAX_TITLE_LEN), sanitize_text(text)


def looks_like_redirect_shell(markup: str) -> bool:
    """Detect the JS/meta-refresh stub served instead of a results page."""
    if len(markup) > _REDIRECT_SHELL_BYTES:
        return False
    lowered = markup[:_REDIRECT_SHELL_BYTES].casefold()
    return bool(
        re.search(r"http-equiv\s*=\s*[\"']?refresh", lowered)
        or "location.replace(" in lowered
        or "location.assign(" in lowered
        or re.search(r"window\.location\s*=", lowered)
        or "location.href=" in lowered
    )
