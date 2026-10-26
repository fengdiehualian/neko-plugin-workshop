"""Parse and validate the runtime config dict handed over by the host.

The host gives us a plain dict (``await self.config.dump()``). We never write
config back from here; the plugin manager owns the file.

Validation is deliberately non-fatal: a broken source is dropped with an error
entry so the rest of the brief still runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import BriefConfig, SourceConfig

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
SECRET_RE = re.compile(r"\$\{(secret|env):([A-Za-z0-9_]+)\}")

TYPE_ALIASES = {
    "auto": "auto",
    "weather": "builtin.weather",
    "builtin.weather": "builtin.weather",
    "rss": "rss",
    "http.json": "http.json",
    "http_json": "http.json",
    "json": "http.json",
}

MODES = ("hybrid", "model", "pipeline")


def _derive_id(url: object, index: int) -> str:
    """Stable-ish id for sources where the user only pasted a URL."""

    from urllib.parse import urlsplit

    host = ""
    try:
        host = urlsplit(str(url or "")).netloc.lower()
    except ValueError:
        host = ""
    slug = re.sub(r"[^a-z0-9]+", "_", host).strip("_")[:20]
    if not slug or not slug[0].isalpha():
        slug = f"s_{slug}" if slug else "source"
    return f"{slug}_{index + 1}"[:32]


@dataclass
class ParsedConfig:
    brief: BriefConfig
    sources: list[SourceConfig] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    tz: ZoneInfo | None = None


def _expand_secrets(value: Any, secrets: dict[str, str], strict: bool = True) -> Any:
    """Recursively expand ``${secret:KEY}`` / ``${env:VAR}`` in strings.

    A disabled source may reference a secret the user has not filled in yet;
    in that case the placeholder collapses to an empty string instead of
    invalidating the config.
    """

    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            kind, key = match.group(1), match.group(2)
            if kind == "secret":
                if key not in secrets:
                    if not strict:
                        return ""
                    raise KeyError(key)
                return str(secrets[key])
            import os

            return os.environ.get(key, "")

        try:
            return SECRET_RE.sub(replace, value)
        except KeyError as exc:
            raise ValueError(f"missing secret: {exc.args[0]}") from exc
    if isinstance(value, dict):
        return {key: _expand_secrets(item, secrets, strict) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_secrets(item, secrets, strict) for item in value]
    return value


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


def _as_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, low), high)


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return default


RESERVED_KEYS = frozenset(
    {
        "time",
        "times",
        "timezone",
        "catch_up_minutes",
        "mode",
        "max_spoken_items",
        "max_items_per_source",
        "max_total_items",
        "total_budget_seconds",
        "source_timeout_seconds",
        "spoken_char_limit",
        "card_max_items",
        "prompt_visibility",
        "concurrency",
        "proxy",
        "links",
        "brief",
        "sources",
        "secrets",
    }
)


def _extract_tasks(root: dict[str, Any]) -> list[tuple[str, str]]:
    """Collect root-level ``名称 = 内容`` entries as the user's tasks.

    Every string key that is not a reserved setting is a source the user named
    themselves::

        time = "08:00"
        weather = "https://... 获取杭州今天的天气"
        hn = "https://hnrss.org/frontpage"
    """

    tasks: list[tuple[str, str]] = []
    for key, value in root.items():
        if key in RESERVED_KEYS or str(key).startswith("plugin"):
            continue
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text.startswith("#"):
            continue
        tasks.append((str(key), text))

    # Legacy: a single ``links`` block still works.
    raw_links = root.get("links")
    if raw_links is None and isinstance(root.get("brief"), dict):
        raw_links = root["brief"].get("links")
    if isinstance(raw_links, str):
        lines = raw_links.splitlines()
    elif isinstance(raw_links, (list, tuple)):
        lines = [str(entry) for entry in raw_links]
    else:
        lines = []
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        tasks.append(("", text))
    return tasks


def _parse_brief(root: dict[str, Any]) -> BriefConfig:
    from .scheduler import normalize_times

    # Flat layout: root-level ``time`` plus one key per source. The old
    # ``[brief]`` table is merged underneath so existing configs keep working.
    section = root.get("brief") if isinstance(root.get("brief"), dict) else {}
    merged = {
        **section,
        **{key: value for key, value in root.items() if key not in ("brief", "sources", "secrets")},
    }

    mode = str(merged.get("mode") or "hybrid").strip().lower()
    if mode not in MODES:
        mode = "hybrid"
    visibility = str(merged.get("prompt_visibility") or "hidden").strip().lower()
    if visibility not in ("hidden", "chat"):
        visibility = "hidden"

    tasks = _extract_tasks(root)
    instructions = "\n".join(
        f"{name}：{text}" if name else text for name, text in tasks
    )

    if not instructions:
        # Legacy config: turn explicit sources into the same kind of sentence.
        lines = []
        for entry in root.get("sources") or []:
            if isinstance(entry, dict) and entry.get("url"):
                lines.append(f"通过链接 {entry['url']} 获取内容")
        instructions = "\n".join(lines)

    return BriefConfig(
        times=normalize_times(
            [merged["time"]]
            if merged.get("time")
            else merged.get("times", ["08:00"])
        ),
        timezone=str(merged.get("timezone") or "local"),
        catch_up_minutes=_as_int(merged.get("catch_up_minutes"), 120, 0, 24 * 60),
        mode=mode,
        max_spoken_items=_as_int(merged.get("max_spoken_items"), 5, 1, 10),
        max_items_per_source=_as_int(merged.get("max_items_per_source"), 3, 1, 10),
        max_total_items=_as_int(merged.get("max_total_items"), 12, 1, 50),
        total_budget_seconds=_as_float(merged.get("total_budget_seconds"), 15.0, 3.0, 60.0),
        source_timeout_seconds=_as_float(merged.get("source_timeout_seconds"), 10.0, 1.0, 30.0),
        spoken_char_limit=_as_int(merged.get("spoken_char_limit"), 240, 60, 600),
        card_max_items=_as_int(merged.get("card_max_items"), 30, 1, 100),
        prompt_visibility=visibility,
        concurrency=_as_int(merged.get("concurrency"), 6, 1, 16),
        proxy=str(merged.get("proxy") or "auto").strip(),
        instructions=instructions,
        tasks=tuple(tasks),
    )


def _parse_source(
    raw: dict[str, Any], secrets: dict[str, str], index: int
) -> tuple[SourceConfig | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"source #{index} is not a table"

    source_id = str(raw.get("id") or "").strip()
    if not source_id:
        source_id = _derive_id(raw.get("url"), index)
    if not ID_RE.match(source_id):
        return None, f"source #{index} has invalid id: {source_id!r}"

    raw_type = str(raw.get("type") or "").strip().lower()
    if not raw_type:
        # The friendly default: paste a URL and we figure out the rest.
        raw_type = "auto"
    source_type = TYPE_ALIASES.get(raw_type)
    if source_type is None:
        return None, f"{source_id}: unknown type {raw_type!r}"

    enabled = _as_bool(raw.get("enabled"), True)

    try:
        url = _expand_secrets(raw.get("url"), secrets, enabled) if raw.get("url") else None
        headers = _expand_secrets(raw.get("headers") or {}, secrets, enabled) or {}
        body = (
            _expand_secrets(raw.get("body"), secrets, enabled)
            if raw.get("body") is not None
            else None
        )
    except ValueError as exc:
        return None, f"{source_id}: {exc}"

    if headers and not isinstance(headers, dict):
        return None, f"{source_id}: headers must be a table"

    if source_type != "builtin.weather":
        if not url:
            return None, f"{source_id}: url is required"
        if not str(url).lower().startswith(("http://", "https://")):
            return None, f"{source_id}: url must be http(s)"

    mode = str(raw.get("mode") or ("list" if raw.get("items_path") else "single")).lower()
    if source_type == "http.json":
        if mode not in ("list", "single"):
            return None, f"{source_id}: mode must be list or single"
        if mode == "list" and not raw.get("items_path"):
            return None, f"{source_id}: list mode requires items_path"
        if mode == "single" and not raw.get("text"):
            return None, f"{source_id}: single mode requires text"

    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, (list, tuple)):
        tags = []

    # Keep the weather city at the source's top level: nested tables indent one
    # more level in the host's generic form and push values off-screen.
    params = dict(raw.get("params") or {})
    city = str(raw.get("city") or params.get("city") or "").strip()
    if city:
        params["city"] = city

    label = str(raw.get("label") or "").strip()
    if not label and source_type == "auto" and url:
        from .sources.auto import host_label

        label = host_label(str(url))

    cfg = SourceConfig(
        id=source_id,
        type=source_type,
        label=label,
        enabled=enabled,
        weight=_as_int(raw.get("weight"), 3, 1, 10),
        tags=tuple(str(tag) for tag in tags),
        max_items=_as_int(raw.get("max_items"), 3, 1, 20),
        timeout=_as_float(raw["timeout"], 0.0, 0.0, 30.0) if raw.get("timeout") else None,
        url=str(url) if url else None,
        method=str(raw.get("method") or "GET").upper(),
        headers={str(k): str(v) for k, v in (headers or {}).items()},
        body=body,
        mode=mode,
        items_path=str(raw["items_path"]) if raw.get("items_path") else None,
        map={str(k): str(v) for k, v in (raw.get("map") or {}).items()},
        text=str(raw["text"]) if raw.get("text") else None,
        params=params,
        hint=str(raw.get("hint") or "").strip()[:80],
        dedupe=_as_bool(raw.get("dedupe"), True),
    )
    return cfg, None


URL_RE = re.compile(r"https?://[^\s，。；;、|]+")
COOKIE_RE = re.compile(r"\bcookie\s*[=:：]\s*([^\s，。；;、|]+)", re.IGNORECASE)
_GOAL_PREFIX = re.compile(
    r"通过(链接|网址|地址)?|使用|用|从|访问|打开|抓取|获取|读取|查看|拉取|得到|看看|查一下|查"
)


def _clean_goal(text: str) -> str:
    cleaned = _GOAL_PREFIX.sub(" ", text)
    return re.sub(r"\s+", " ", cleaned).strip(" ，。；、:：的")


def _goal_label(goal: str) -> str:
    text = re.sub(r"^(该|这个|这条|其)", "", goal).strip()
    return text[:14]


def _parse_links(raw_links: object) -> tuple[list[dict[str, Any]], list[str]]:
    """Turn ``links`` into source tables, one instruction per line.

    Each line may read like a sentence::

        通过链接 http://api.test/weather 获取杭州的天气
        通过 http://api.test/price cookie=abc 获取币价
        http://blog.test/feed 获取最新文章

    The URL and an optional ``cookie=...`` are extracted; the rest becomes the
    source's ``hint`` (handed to the model as the user's goal). A leading ``#``
    disables the line. ``weather:杭州`` still works as a shortcut.
    """

    if isinstance(raw_links, str):
        entries: list[object] = re.split(r"[\n\r;；]+", raw_links)
    elif isinstance(raw_links, (list, tuple)):
        entries = list(raw_links)
    elif raw_links is None:
        entries = []
    else:
        return [], ["links must be a string or a list of strings"]

    tables: list[dict[str, Any]] = []
    errors: list[str] = []
    for entry in entries:
        text = str(entry).strip()
        if not text or text.startswith("#"):
            continue

        label = ""
        if "|" in text:
            text, label = (part.strip() for part in text.split("|", 1))

        lowered = text.lower()
        if lowered.startswith("weather:"):
            city = text.split(":", 1)[1].strip()
            if not city:
                errors.append("weather link needs a city, e.g. weather:杭州")
                continue
            tables.append({"type": "builtin.weather", "city": city, "label": label or "天气"})
            continue

        match = URL_RE.search(text)
        if not match:
            errors.append(f"没有找到链接：{text[:40]}")
            continue
        url = match.group(0).rstrip("，。；、)")
        cookie_match = COOKIE_RE.search(text)
        cookie = cookie_match.group(1) if cookie_match else ""

        remainder = text.replace(match.group(0), " ")
        if cookie_match:
            remainder = remainder.replace(cookie_match.group(0), " ")
        goal = _clean_goal(remainder)
        table: dict[str, Any] = {"url": url, "hint": goal}
        if label:
            table["label"] = label
        elif goal:
            table["label"] = _goal_label(goal)
        if cookie:
            table["headers"] = {"Cookie": cookie}
        tables.append(table)
    return tables, errors


def parse_config(raw: Any) -> ParsedConfig:
    if not isinstance(raw, dict):
        raw = {}
    brief_raw = raw.get("brief")
    brief = _parse_brief(raw)

    secrets: dict[str, str] = {}
    secrets_raw = raw.get("secrets")
    if isinstance(secrets_raw, dict):
        secrets = {str(key): str(value) for key, value in secrets_raw.items()}

    errors: list[dict[str, str]] = []
    sources: list[SourceConfig] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()

    raw_links = raw.get("links")
    if raw_links is None and isinstance(brief_raw, dict):
        raw_links = brief_raw.get("links")
    link_tables, link_errors = _parse_links(raw_links)
    for message in link_errors:
        errors.append({"source_id": "", "message": message})

    sources_raw: Iterable[Any] = raw.get("sources") or []
    if isinstance(sources_raw, dict):
        sources_raw = list(sources_raw.values())
    for index, entry in enumerate([*link_tables, *sources_raw]):
        cfg, error = _parse_source(entry, secrets, index)
        if error:
            errors.append({"source_id": "", "message": error})
            continue
        assert cfg is not None
        if cfg.id in seen_ids:
            errors.append({"source_id": cfg.id, "message": f"{cfg.id}: duplicate id"})
            continue
        if cfg.url and cfg.url in seen_urls:
            continue
        seen_ids.add(cfg.id)
        if cfg.url:
            seen_urls.add(cfg.url)
        sources.append(cfg)

    if not sources and not brief.tasks:
        errors.append(
            {
                "source_id": "",
                "message": "没有配置任何信息源；在配置里加一行，例如 hn = \"https://… 获取科技新闻\"",
            }
        )

    tz: ZoneInfo | None = None
    if brief.timezone and brief.timezone.lower() != "local":
        try:
            tz = ZoneInfo(brief.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(
                {
                    "source_id": "",
                    "message": f"unknown timezone {brief.timezone!r}; using local time",
                }
            )

    return ParsedConfig(brief=brief, sources=sources, errors=errors, tz=tz)


def now_in(parsed: ParsedConfig) -> Any:
    from datetime import datetime

    if parsed.tz is not None:
        return datetime.now(parsed.tz)
    return datetime.now().astimezone()


__all__ = ["ParsedConfig", "now_in", "parse_config"]
