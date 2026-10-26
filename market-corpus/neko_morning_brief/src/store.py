"""SQLite persistence: dedupe window, per-source cache, run log.

One connection guarded by a lock, because the SDK runs timers on their own
threads. Writes are short and synchronous; the volume is tiny.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .models import BriefItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  k          TEXT PRIMARY KEY,
  first_seen REAL NOT NULL,
  last_seen  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS source_state (
  source_id     TEXT PRIMARY KEY,
  last_ok       REAL,
  last_status   TEXT,
  last_error    TEXT,
  last_items    TEXT,
  etag          TEXT,
  last_modified TEXT
);

CREATE TABLE IF NOT EXISTS brief_log (
  day          TEXT NOT NULL,
  slot         TEXT NOT NULL,
  ran_at       REAL,
  trigger      TEXT,
  spoken_chars INTEGER,
  ok_count     INTEGER,
  fail_count   INTEGER,
  PRIMARY KEY (day, slot)
);

CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY,
  v TEXT
);
"""

DEFAULT_SEEN_DAYS = 7
DEFAULT_CACHE_HOURS = 24


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass

    # -- dedupe ------------------------------------------------------------

    def drop_seen(self, items: Iterable[BriefItem], now: float | None = None) -> list[BriefItem]:
        """Return items whose dedupe key has not been seen in the window."""

        current = time.time() if now is None else now
        keys = [item.dedupe_key for item in items]
        if not keys:
            return []
        cutoff = current - DEFAULT_SEEN_DAYS * 86400
        fresh: list[BriefItem] = []
        with self._lock:
            placeholders = ",".join("?" for _ in keys)
            rows = self._db.execute(
                f"SELECT k FROM seen WHERE k IN ({placeholders}) AND last_seen >= ?",
                (*keys, cutoff),
            ).fetchall()
            known = {row["k"] for row in rows}
        for item, key in zip(items, keys):
            if key not in known:
                fresh.append(item)
        return fresh

    def remember(self, items: Iterable[BriefItem], now: float | None = None) -> None:
        current = time.time() if now is None else now
        rows = [(item.dedupe_key, current, current) for item in items]
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO seen (k, first_seen, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(k) DO UPDATE SET last_seen = excluded.last_seen",
                rows,
            )
            self._db.commit()

    def purge_seen(self, now: float | None = None, days: int = DEFAULT_SEEN_DAYS) -> int:
        current = time.time() if now is None else now
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM seen WHERE last_seen < ?", (current - days * 86400,)
            )
            self._db.commit()
            return cursor.rowcount

    # -- per-source cache --------------------------------------------------

    def save_state(
        self,
        source_id: str,
        *,
        status: str,
        items: Iterable[BriefItem] = (),
        error_code: str | None = None,
        fetched_at: float | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        current = time.time() if fetched_at is None else fetched_at
        payload = json.dumps([item.to_dict() for item in items], ensure_ascii=False)
        with self._lock:
            self._db.execute(
                "INSERT INTO source_state "
                "(source_id, last_ok, last_status, last_error, last_items, etag, last_modified) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "  last_ok = CASE WHEN excluded.last_status IN ('ok','empty') "
                "                 THEN excluded.last_ok ELSE source_state.last_ok END, "
                "  last_status = excluded.last_status, "
                "  last_error = excluded.last_error, "
                "  last_items = CASE WHEN excluded.last_status = 'ok' "
                "                    THEN excluded.last_items ELSE source_state.last_items END, "
                "  etag = COALESCE(excluded.etag, source_state.etag), "
                "  last_modified = COALESCE(excluded.last_modified, source_state.last_modified)",
                (source_id, current, status, error_code, payload, etag, last_modified),
            )
            self._db.commit()

    def cached_items(
        self,
        source_id: str,
        *,
        within_hours: int = DEFAULT_CACHE_HOURS,
        now: float | None = None,
    ) -> list[BriefItem] | None:
        current = time.time() if now is None else now
        with self._lock:
            row = self._db.execute(
                "SELECT last_ok, last_items FROM source_state WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if not row or not row["last_items"] or not row["last_ok"]:
            return None
        if current - float(row["last_ok"]) > within_hours * 3600:
            return None
        try:
            raw = json.loads(row["last_items"])
        except (TypeError, ValueError):
            return None
        items = [BriefItem.from_dict(entry) for entry in raw if isinstance(entry, dict)]
        return items or None

    def source_meta(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT last_ok, last_status, last_error, etag, last_modified "
                "FROM source_state WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_validators(self, source_id: str, etag: str | None, last_modified: str | None) -> None:
        if not etag and not last_modified:
            return
        with self._lock:
            self._db.execute(
                "UPDATE source_state SET etag = ?, last_modified = ? WHERE source_id = ?",
                (etag, last_modified, source_id),
            )
            self._db.commit()

    # -- run log -----------------------------------------------------------

    def has_run(self, day: str, slot: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM brief_log WHERE day = ? AND slot = ?", (day, slot)
            ).fetchone()
        return row is not None

    def log_brief(
        self,
        day: str,
        slot: str,
        *,
        trigger: str,
        spoken_chars: int = 0,
        ok_count: int = 0,
        fail_count: int = 0,
        ran_at: float | None = None,
    ) -> None:
        current = time.time() if ran_at is None else ran_at
        with self._lock:
            self._db.execute(
                "INSERT INTO brief_log "
                "(day, slot, ran_at, trigger, spoken_chars, ok_count, fail_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(day, slot) DO UPDATE SET "
                "  ran_at = excluded.ran_at, trigger = excluded.trigger, "
                "  spoken_chars = excluded.spoken_chars, "
                "  ok_count = excluded.ok_count, fail_count = excluded.fail_count",
                (day, slot, current, trigger, spoken_chars, ok_count, fail_count),
            )
            self._db.commit()

    def last_run(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM brief_log ORDER BY ran_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # -- small key/value ---------------------------------------------------

    def kv_get(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else None

    def kv_set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        stored = value
        if ttl_seconds:
            stored = json.dumps({"v": value, "exp": time.time() + ttl_seconds}, ensure_ascii=False)
        with self._lock:
            self._db.execute(
                "INSERT INTO kv (k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (key, stored),
            )
            self._db.commit()

    def kv_get_ttl(self, key: str) -> str | None:
        raw = self.kv_get(key)
        if not raw:
            return None
        try:
            wrapper = json.loads(raw)
        except (TypeError, ValueError):
            return raw
        if not isinstance(wrapper, dict) or "v" not in wrapper:
            return raw
        if float(wrapper.get("exp", 0)) < time.time():
            return None
        return str(wrapper["v"])


__all__ = ["Store"]
