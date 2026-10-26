"""Publisher-built Def directory; consultations pin immutable payload generations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path
from typing import Any

MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
REFRESH_INTERVAL_SECONDS = 5.0


def signature(value: os.stat_result) -> list[int]:
    return [value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def validate_metadata(value: dict) -> None:
    if (
        value.get("format") != "neko.defs.v1"
        or any(not isinstance(value.get(k), str) for k in ("game_version", "language", "generated_at"))
        or type(value.get("complete")) is not bool
        or not isinstance(value.get("mods"), list)
        or not isinstance(value.get("issues"), list)
    ):
        raise ValueError("invalid knowledge metadata")
    for field in ("issues", "warnings"):
        if field in value and (not isinstance(value[field], list) or any(not isinstance(x, str) for x in value[field])):
            raise ValueError("invalid diagnostics")
    if "provenance_complete" in value and type(value["provenance_complete"]) is not bool:
        raise ValueError("invalid provenance")
    for mod in value["mods"]:
        if not isinstance(mod, dict) or any(not isinstance(mod.get(k), str) for k in ("package_id", "name")):
            raise ValueError("invalid mod metadata")


def validate_entry(entry: Any) -> None:
    if (
        not isinstance(entry, dict)
        or any(
            not isinstance(entry.get(k), str)
            for k in ("key", "type", "def_name", "label", "description", "source_mod", "package_id")
        )
        or not entry["type"]
        or not entry["def_name"]
        or entry["key"] != entry["type"] + "/" + entry["def_name"]
        or type(entry.get("truncated")) is not bool
    ):
        raise ValueError("invalid definition")
    for field, keys in (
        ("facts", ("name", "value")),
        ("related", ("key", "label")),
        ("diagnostics", ("path", "kind", "reason")),
    ):
        rows = entry.get(field, [] if field == "diagnostics" else None)
        if not isinstance(rows, list):
            raise ValueError("invalid definition rows")
        for row in rows:
            if (
                not isinstance(row, dict)
                or any(not isinstance(row.get(k), str) for k in keys)
                or field == "facts"
                and "type" in row
                and not isinstance(row["type"], str)
            ):
                raise ValueError("invalid definition row")



class IndexedKnowledgeView:
    """Publisher-built directory; payload reads verify the selected entry hash."""

    MAX_INDEX_BYTES = 32 * 1024 * 1024

    def __init__(self, directory: Path):
        index_path = directory / "ready.json"
        fd = os.open(index_path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > self.MAX_INDEX_BYTES:
                raise ValueError("invalid knowledge index")
            raw = source.read(self.MAX_INDEX_BYTES + 1)
            if len(raw) > self.MAX_INDEX_BYTES or signature(before) != signature(os.fstat(source.fileno())):
                raise ValueError("knowledge index changed")
        index = json.loads(raw)
        if not isinstance(index, dict) or index.get("format") != "neko.defs.index.v1":
            raise ValueError("invalid knowledge index format")
        filename = index.get("data_file", "")
        if not isinstance(filename, str) or not re.fullmatch(r"snapshot-[0-9a-f]{32}\.json", filename):
            raise ValueError("invalid knowledge payload name")
        self.path = (directory / filename).resolve()
        if self.path.parent != directory.resolve():
            raise ValueError("knowledge payload outside directory")
        total = index.get("data_bytes")
        if type(total) is not int or not 0 < total <= MAX_SNAPSHOT_BYTES:
            raise ValueError("invalid knowledge payload size")
        payload_stat = self.path.stat()
        if not stat.S_ISREG(payload_stat.st_mode) or payload_stat.st_size != total:
            raise ValueError("knowledge payload not ready")
        self._payload_signature = signature(payload_stat)
        metadata = index.get("snapshot")
        if not isinstance(metadata, dict):
            raise ValueError("invalid knowledge metadata")
        validate_metadata(metadata)
        digest = index.get("data_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid payload checksum")
        if metadata.get("sha256") != digest:
            raise ValueError("inconsistent payload checksum")
        self.metadata = metadata
        entries = index.get("entries")
        if not isinstance(entries, list) or len(entries) > 100000:
            raise ValueError("invalid knowledge directory")
        self._locations = {}
        self._rows = []
        previous_end = 0
        for row in entries:
            if not isinstance(row, dict):
                raise ValueError("invalid knowledge row")
            key, offset, length, checksum = (row.get(k) for k in ("key", "offset", "length", "sha256"))
            names, description = row.get("names"), row.get("description")
            if (
                not isinstance(key, str)
                or not key
                or key in self._locations
                or type(offset) is not int
                or type(length) is not int
                or offset < previous_end
                or not 0 < length <= 8 * 1024 * 1024
                or offset + length > total
                or not isinstance(checksum, str)
                or not re.fullmatch(r"[0-9a-f]{64}", checksum)
                or not isinstance(names, list)
                or any(not isinstance(n, str) for n in names)
                or not isinstance(description, str)
            ):
                raise ValueError("invalid knowledge record index")
            self._locations[key] = (offset, length, checksum)
            self._rows.append(
                {
                    "key": key,
                    "names": list(dict.fromkeys(n.casefold() for n in names if n)),
                    "description": description.casefold(),
                }
            )
            previous_end = offset + length
        self.index_signature = signature(before)

    def search_rows(self) -> list[dict]:
        return self._rows

    def read_entry(self, key: str) -> dict | None:
        location = self._locations.get(key)
        if location is None:
            return None
        offset, length, digest = location
        with self.path.open("rb") as source:
            if signature(os.fstat(source.fileno())) != self._payload_signature:
                raise ValueError("knowledge payload changed")
            source.seek(offset)
            data = source.read(length)
            if len(data) != length or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("knowledge entry checksum failed")
            if signature(os.fstat(source.fileno())) != self._payload_signature:
                raise ValueError("knowledge payload changed during read")
        entry = json.loads(data)
        validate_entry(entry)
        if entry["key"] != key:
            raise ValueError("knowledge entry identity mismatch")
        return entry


class KnowledgeLibrary:
    """Attach only the currently published ready directory; never import payloads."""

    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._stop = threading.Event()
        self._view: IndexedKnowledgeView | None = None
        self.last_error: str | None = None
        self.refresh()

    @property
    def closed(self) -> bool:
        return self._closed

    def open_view(self) -> IndexedKnowledgeView | None:
        with self._lock:
            if self._closed or self._view is None:
                return None
            try:
                current = (
                    self._view.index_signature == signature((self.directory / "ready.json").stat())
                    and self._view._payload_signature == signature(self._view.path.stat())
                )
            except OSError:
                current = False
            if not current:
                self._view = None
                self.last_error = "knowledge_index_not_ready"
            return self._view

    def request_refresh(self) -> None:
        """Do not wait for directory loading on the consultation path."""
        with self._lock:
            if self._closed or self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._watch, name="neko-knowledge-refresh", daemon=True)
            self._thread.start()

    def _watch(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(REFRESH_INTERVAL_SECONDS)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._view = None
            self._stop.set()

    def refresh(self) -> bool:
        with self._refresh_lock:
            if self._closed:
                return False
            try:
                ready = self.directory / "ready.json"
                with self._lock:
                    view = self._view
                if (
                    view is not None
                    and view.index_signature == signature(ready.stat())
                    and view._payload_signature == signature(view.path.stat())
                ):
                    return True
                view = IndexedKnowledgeView(self.directory)
                with self._lock:
                    if self._closed:
                        return False
                    self._view = view
                    self.last_error = None
                return True
            except (OSError, ValueError, TypeError, RecursionError):
                with self._lock:
                    self._view = None
                    self.last_error = "knowledge_index_not_ready"
                return False
