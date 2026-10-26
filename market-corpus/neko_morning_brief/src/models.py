"""Data contracts shared by every layer of the plugin.

Nothing in this module imports the N.E.K.O SDK, so it stays unit-testable
outside a host checkout.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SourceStatus = Literal["ok", "cached", "error", "empty"]


@dataclass(frozen=True)
class SourceConfig:
    """One configured information source."""

    id: str
    type: str
    label: str = ""
    enabled: bool = True
    weight: int = 3
    tags: tuple[str, ...] = ()
    max_items: int = 3
    timeout: float | None = None
    # http.json / rss
    url: str | None = None
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None
    # http.json
    mode: str = "single"           # "list" | "single"
    items_path: str | None = None
    map: dict[str, str] = field(default_factory=dict)
    text: str | None = None
    # builtin.weather
    params: dict[str, Any] = field(default_factory=dict)
    #: One-line natural-language goal from ``links`` (e.g. "杭州的天气").
    hint: str = ""
    #: Feed-like sources skip repeats; snapshot sources (weather, price) do not.
    dedupe: bool = True

    @property
    def display_label(self) -> str:
        return self.label or self.id


@dataclass
class BriefItem:
    """Normalized item; the only shape that crosses module boundaries."""

    source_id: str
    title: str
    summary: str = ""
    url: str | None = None
    ts: float | None = None
    weight: int = 3
    tags: tuple[str, ...] = ()
    #: A snapshot (price/status/page title) always repeats; feeds do not.
    snapshot: bool = False

    @property
    def dedupe_key(self) -> str:
        raw = f"{self.title.strip().lower()}|{(self.url or '').strip().lower()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BriefItem":
        return cls(
            source_id=str(data.get("source_id", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            url=data.get("url"),
            ts=data.get("ts"),
            weight=int(data.get("weight", 3)),
            tags=tuple(data.get("tags") or ()),
            snapshot=bool(data.get("snapshot", False)),
        )


@dataclass
class SourceReport:
    """Outcome of collecting a single source."""

    source_id: str
    label: str
    status: SourceStatus
    items: list[BriefItem] = field(default_factory=list)
    fetched_at: float = 0.0
    elapsed_ms: int = 0
    error_code: str | None = None
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "cached") and bool(self.items)


@dataclass(frozen=True)
class BriefConfig:
    """Runtime settings for one brief run."""

    times: tuple[str, ...] = ("08:00",)
    timezone: str = "local"
    catch_up_minutes: int = 120
    mode: str = "hybrid"            # hybrid | model | pipeline
    max_spoken_items: int = 5
    max_items_per_source: int = 3
    max_total_items: int = 12
    total_budget_seconds: float = 15.0
    source_timeout_seconds: float = 10.0
    spoken_char_limit: int = 240
    card_max_items: int = 30
    prompt_visibility: str = "hidden"   # hidden | chat
    concurrency: int = 6
    proxy: str = "auto"                 # auto | none | http://host:port
    #: Raw instructions from the user, handed to the model verbatim.
    instructions: str = ""
    #: ``(名称, 内容)`` pairs collected from top-level key/value entries.
    tasks: tuple[tuple[str, str], ...] = ()
