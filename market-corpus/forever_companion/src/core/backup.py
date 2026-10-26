"""Portable, versioned backups. No host SDK or filesystem access."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import re
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

FORMAT = "forever_companion_backup"
VERSION = 1
MAX_BYTES = 256 * 1024 * 1024
CHUNK_BYTES = 384 * 1024
ROLE_PREFIXES = (
    "cycle@", "mood@", "diary@", "journal@", "journal_archive@",
    "review@", "review_stats@", "review_archive@", "stats@", "caps@", "weekly@",
)
GLOBAL_KEYS = frozenset({
    "settings", "lanlan_index", "guide", "caps@*",
    "panel_appearance", "gallery_index", "panel_bg",
    "cycle_state", "mood_state", "mood_diary",
})
CONFIG_SECTIONS = (
    "tide", "mood", "fragments", "journal", "review", "agent_tier",
    "stats", "birthday", "capabilities", "emotion_sense",
)


class BackupError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _legacy_debug(key: str, known=()) -> bool:
    return (
        key.startswith(("journal@", "review@", "stats@")) and key.endswith("|pre-debug")
        and key.split("@", 1)[1] not in known
    )


def role_of(key: str, known=()) -> str:
    if key == "caps@*":
        return ""
    for prefix in ROLE_PREFIXES:
        if key.startswith(prefix):
            name = key[len(prefix):]
            return name.removesuffix("|pre-debug") if _legacy_debug(key, known) else name
    for kind in ("diary", "journal", "review", "stats"):
        prefix = f"pre_debug@{kind}@"
        if key.startswith(prefix):
            return key[len(prefix):]
    return ""


def managed_key(key: str) -> bool:
    return (
        key in GLOBAL_KEYS or bool(role_of(key))
        or key.startswith("gallery_img/") and bool(key.removeprefix("gallery_img/"))
    )


def json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise BackupError("backup_invalid") from None


def _json_load(data: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BackupError("backup_invalid")
            result[key] = value
        return result

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=lambda _: _invalid())
    except (ValueError, UnicodeError, RecursionError):
        raise BackupError("backup_invalid") from None


def _invalid():
    raise BackupError("backup_invalid")


def static_image(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    url = value.get("data_url")
    if not isinstance(url, str) or len(url) > 6_000_000:
        return False
    matched = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})", url)
    if not matched:
        return False
    try:
        content = base64.b64decode(matched[2], validate=True)
    except ValueError:
        return False
    if matched[1] == "jpeg":
        return content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")
    if matched[1] == "png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        offset = 8
        while offset + 12 <= len(content):
            size = int.from_bytes(content[offset:offset + 4], "big")
            kind = content[offset + 4:offset + 8]
            if kind == b"acTL" or size > len(content) - offset - 12:
                return False
            offset += size + 12
            if kind == b"IEND":
                return offset == len(content)
        return False
    if not (content.startswith(b"RIFF") and content[8:12] == b"WEBP"):
        return False
    if len(content) < 20 or int.from_bytes(content[4:8], "little") + 8 != len(content):
        return False
    offset = 12
    while offset + 8 <= len(content):
        kind = content[offset:offset + 4]
        size = int.from_bytes(content[offset + 4:offset + 8], "little")
        if kind in (b"ANIM", b"ANMF") or size > len(content) - offset - 8:
            return False
        if kind == b"VP8X" and size and content[offset + 8] & 2:
            return False
        offset += 8 + size + (size % 2)
    return offset == len(content)


def roles(records: dict) -> list[str]:
    names = set(records.get("lanlan_index") or [])
    known = set(names)
    names.update(role_of(key, known) for key in records if role_of(key, known))
    return sorted(names)


def summary(records: dict) -> dict:
    names = roles(records)
    return {
        "roles": names,
        "diary": sum(len(records.get(f"diary@{name}") or []) for name in names),
        "journal": sum(len(records.get(f"{prefix}@{name}") or []) for name in names for prefix in ("journal", "journal_archive")),
        "review": sum(
            len((records.get(f"review@{name}") or {}).get("entries") or [])
            + len(records.get(f"review_archive@{name}") or []) for name in names
        ),
        "images": len((records.get("gallery_index") or {}).get("items") or []),
    }


def portable_records(source: dict) -> tuple[dict, int]:
    records = deepcopy({key: value for key, value in source.items() if managed_key(key)})
    index = records.get("gallery_index") or {"items": [], "next": 1}
    items = []
    excluded = 0
    for item in index.get("items") or []:
        if item.get("kind") == "video" or not static_image(records.get(f"gallery_img/{item['id']}")):
            excluded += 1
        else:
            items.append(item)
    allowed = {f"gallery_img/{item['id']}" for item in items}
    records = {key: value for key, value in records.items() if not key.startswith("gallery_img/") or key in allowed}
    records["gallery_index"] = {**index, "items": items}
    appearance = records.get("panel_appearance")
    if isinstance(appearance, dict) and appearance.get("bg_id") not in {item["id"] for item in items}:
        appearance["bg_id"] = ""
    # Legacy media and old single-role state are normalized by the runtime before export.
    records.pop("panel_bg", None)
    if isinstance(records.get("mood_state"), dict):
        records["mood_state"].pop("proactive_prev", None)
    records["lanlan_index"] = roles(records)
    return records, excluded


def config_overrides(config: dict, saved: dict, defaults: dict) -> dict:
    def difference(values, base):
        result = {}
        for key, value in values.items():
            if key not in base:
                continue
            if isinstance(value, dict) and isinstance(base[key], dict):
                child = difference(value, base[key])
                if child:
                    result[key] = child
            elif value != base[key]:
                result[key] = deepcopy(value)
        return result

    result = {}
    def merge(target, source):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = deepcopy(value)

    for section in CONFIG_SECTIONS:
        changed = difference(config.get(section) or {}, defaults.get(section) or {})
        merge(changed, saved.get(section) or {})
        if changed:
            result[section] = changed
    return result


def _fields(value, *, text=(), counts=(), numbers=(), objects=(), lists=(), flags=()):
    if not isinstance(value, dict):
        _invalid()
    for fields, expected in ((text, str), (objects, dict), (lists, list), (flags, bool)):
        if any(field in value and not isinstance(value[field], expected) for field in fields):
            _invalid()
    for field in counts:
        if field in value and (type(value[field]) is not int or not 0 <= value[field] <= 2**53 - 1):
            _invalid()
    for field in numbers:
        if field in value and value[field] is not None:
            number = value[field]
            if type(number) not in (int, float) or abs(number) > 1e18 or not math.isfinite(number):
                _invalid()


def _entries(value, kind=""):
    if not isinstance(value, list):
        _invalid()
    for item in value:
        _fields(
            item, text=("ts", "title", "text", "entry", "quote", "source", "started_at", "span"),
            counts=("page_no", "entry_no", "turns", "self_action_count"),
            numbers=("affect", "mood_avg"), objects=("tone",), lists=("quotes",),
        )
        if "tone" in item:
            _tally(item["tone"])
        if "quotes" in item:
            _entries(item["quotes"])
        if kind == "journal":
            _entries(item.get("entries", []))


def _tally(value):
    if not isinstance(value, dict) or any(type(n) is not int or not 0 <= n <= 2**53 - 1 for n in value.values()):
        _invalid()


def _review_stats(value):
    _fields(value, text=("started_at", "last_turn_at"), counts=("turns",),
            objects=("tone",), lists=("affect_samples", "actions", "fragments"))
    _tally(value.get("tone", {}))
    for number in value.get("affect_samples", []):
        if type(number) not in (int, float) or not -1 <= number <= 1:
            _invalid()
    for item in value.get("actions", []):
        _fields(item, text=("action", "origin"))
    _entries(value.get("fragments", []))


def _stats(value):
    _fields(value, text=("first_seen",), objects=("days", "months", "milestones", "anniversary"),
            flags=("backfilled",))
    for bucket in value.get("days", {}).values():
        _fields(bucket, counts=("turns", "v_n", "cold", "made_up", "warm"),
                numbers=("v_sum",), objects=("tone",))
        _tally(bucket.get("tone", {}))
    for month in value.get("months", {}).values():
        _fields(month, text=("month", "busiest_day"),
                counts=("turns", "active_days", "busiest_turns", "longest_streak", "cold_wars", "made_ups", "warm_moments"),
                numbers=("valence_avg",), objects=("tone",), flags=("sealed",))
        _tally(month.get("tone", {}))
        if month.get("voice") is not None:
            _entries([month["voice"]])
    if any(not isinstance(item, str) for item in value.get("milestones", {}).values()):
        _invalid()
    _fields(value.get("anniversary", {}), text=("last_pushed",))


def _record(key, value, known):
    debug = re.match(r"pre_debug@(diary|journal|review|stats)@", key)
    if debug or _legacy_debug(key, known):
        kind = debug[1] if debug else key.split("@", 1)[0]
        if kind == "diary":
            _fields(value, lists=("diary",))
            _entries(value.get("diary"))
        elif kind == "journal":
            _fields(value, lists=("journal", "archive"))
            _entries(value.get("journal", []), "journal")
            _entries(value.get("archive", []), "journal")
        elif kind == "review":
            _fields(value, lists=("entries", "archive"), objects=("stats",))
            _entries(value.get("entries", []))
            _entries(value.get("archive", []))
            _review_stats(value.get("stats", {}))
        else:
            _stats(value)
    elif key.startswith(("diary@", "journal@", "journal_archive@", "review_archive@", "weekly@")) or key == "mood_diary":
        _entries(value, "journal" if key.startswith(("journal@", "journal_archive@")) else "")
    elif key.startswith("review@"):
        _fields(value, lists=("entries",), objects=("stats",))
        _entries(value.get("entries", []))
        _review_stats(value.get("stats", {}))
    elif key.startswith("review_stats@"):
        _review_stats(value)
    elif key.startswith("stats@"):
        _stats(value)
    elif key.startswith("caps@"):
        if any(type(flag) is not bool for flag in value.values()):
            _invalid()
    elif key.startswith("cycle@") or key == "cycle_state":
        _fields(value, counts=("advance_days",), objects=("params",))
        if value.get("enabled") is not None and type(value["enabled"]) is not bool:
            _invalid()
        if value.get("anchor_date") is not None and not isinstance(value["anchor_date"], str):
            _invalid()
        _fields(value.get("params", {}), counts=("cycle_length", "period_length", "ovulation_day", "ovulation_window"),
                flags=("auto_derive",))
    elif key.startswith("mood@") or key == "mood_state":
        _fields(value, text=("action", "reason", "origin"), lists=("history",))
        for field in ("started_at", "expires_at", "valence", "arousal", "affect_updated_at"):
            number = value.get(field, 0)
            if type(number) not in (int, float) or abs(number) > 1e15 or not math.isfinite(number):
                _invalid()
        for item in value.get("history", []):
            _fields(item, text=("kind", "origin", "action", "previous_action", "reason"), numbers=("ts",))
    elif key == "gallery_index":
        _fields(value, counts=("next",))
    elif key == "panel_appearance":
        _fields(value, text=("bg_id", "fill", "position"), flags=("motion",),
                numbers=("blur", "dim", "brightness", "saturate", "contrast", "glass", "card_alpha", "text_weight"))
    elif key == "guide":
        _fields(value, text=("wizard", "at", "version"))


def validate_records(records: Any) -> None:
    if not isinstance(records, dict) or len(records) > 100_000:
        raise BackupError("backup_invalid")
    json_bytes(records)
    known = records.get("lanlan_index")
    if not isinstance(known, list) or any(not isinstance(name, str) for name in known):
        _invalid()
    for key, value in records.items():
        if not isinstance(key, str) or len(key) > 2048 or not managed_key(key):
            raise BackupError("backup_invalid")
        name = role_of(key, records.get("lanlan_index", []))
        if name and (len(name) > 512 or "\x00" in name):
            raise BackupError("backup_invalid")
        if key == "lanlan_index":
            if not isinstance(value, list) or any(not isinstance(n, str) or not n or len(n) > 512 for n in value):
                raise BackupError("backup_invalid")
            if len(set(value)) != len(value):
                raise BackupError("backup_invalid")
        elif key == "mood_diary" or key.startswith(("diary@", "journal@", "journal_archive@", "review_archive@", "weekly@")) and not _legacy_debug(key, records.get("lanlan_index", [])):
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise BackupError("backup_invalid")
        elif not isinstance(value, dict):
            raise BackupError("backup_invalid")
        _record(key, value, records.get("lanlan_index", []))
    index = records.get("gallery_index")
    if not isinstance(index, dict) or not isinstance(index.get("items"), list) or len(index["items"]) > 24:
        raise BackupError("backup_invalid")
    ids = set()
    for item in index["items"]:
        if not isinstance(item, dict):
            raise BackupError("backup_invalid")
        _fields(item, text=("id", "name", "mime", "thumb", "added_at"), counts=("size",))
        if len(item.get("thumb", "")) > 400_000:
            raise BackupError("backup_invalid")
        gid = item.get("id")
        if not isinstance(gid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", gid) or gid in ids or item.get("kind") == "video":
            raise BackupError("backup_invalid")
        if not static_image(records.get(f"gallery_img/{gid}")):
            raise BackupError("backup_invalid")
        _fields(records[f"gallery_img/{gid}"], text=("data_url", "mime", "added_at"), counts=("size",))
        ids.add(gid)
    if {key.removeprefix("gallery_img/") for key in records if key.startswith("gallery_img/")} != ids:
        raise BackupError("backup_invalid")
    if records.get("lanlan_index") != roles(records):
        raise BackupError("backup_invalid")
    appearance = records.get("panel_appearance") or {}
    if appearance.get("bg_id") and appearance["bg_id"] not in ids:
        raise BackupError("backup_invalid")


def make_archive(records: dict, plugin_version: str, excluded: int = 0) -> tuple[bytes, dict]:
    validate_records(records)
    payload = json_bytes(records)
    if len(payload) > MAX_BYTES:
        raise BackupError("backup_too_large")
    manifest = {
        "format": FORMAT, "format_version": VERSION, "plugin_version": plugin_version,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(payload).hexdigest(), "summary": summary(records),
        "excluded_media": excluded,
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json_bytes(manifest))
        archive.writestr("data.json", payload)
    content = stream.getvalue()
    if len(content) > MAX_BYTES:
        raise BackupError("backup_too_large")
    return content, manifest


def read_archive(content: bytes) -> tuple[dict, dict]:
    if not content or len(content) > MAX_BYTES:
        raise BackupError("backup_too_large")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) != 2 or {item.filename for item in entries} != {"manifest.json", "data.json"}:
                raise BackupError("backup_invalid")
            if any(item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for item in entries):
                raise BackupError("backup_invalid")
            if archive.getinfo("manifest.json").file_size > 1024 * 1024 or archive.getinfo("data.json").file_size > MAX_BYTES:
                raise BackupError("backup_too_large")
            manifest = _json_load(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
                raise BackupError("backup_invalid")
            if type(manifest.get("format_version")) is not int or manifest["format_version"] != VERSION:
                raise BackupError("backup_version_unsupported")
            payload = archive.read("data.json")
    except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, NotImplementedError):
        raise BackupError("backup_invalid") from None
    if hashlib.sha256(payload).hexdigest() != manifest.get("sha256"):
        raise BackupError("backup_checksum_failed")
    records = _json_load(payload)
    validate_records(records)
    if manifest.get("summary") != summary(records):
        raise BackupError("backup_invalid")
    return records, manifest
