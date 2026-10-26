"""External, versioned Star Rail knowledge-pack storage.

The N.E.K.O plugin ships only the small playable-character registry.  The
larger knowledge snapshot is built from a pinned public source into a SQLite
file under the user's local data directory.  Runtime reads are strictly
read-only and every selected pack is verified against its manifest hash.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable

from .catalog import normalize_lookup_text

PACK_SCHEMA_VERSION = 1
UPSTREAM_REVISION = "dbe8cdfcfb0bf657b9fe1cf92d5537f118ccb487"
UPSTREAM_GAME_VERSION = "4.6.0"
UPSTREAM_SNAPSHOT_DATE = "2026-09-29"
PACK_BUILDER_REVISION = 2
UPSTREAM_NAME = "Mar-7th/StarRailRes"
UPSTREAM_URL = "https://github.com/Mar-7th/StarRailRes"
UPSTREAM_LICENSE = "AGPL-3.0-only"
SOURCE_ID = "starrailres_structured_cn"
BASE_URL = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/{UPSTREAM_REVISION}/index_min/cn"
LICENSE_URL = f"https://raw.githubusercontent.com/Mar-7th/StarRailRes/{UPSTREAM_REVISION}/LICENSE"
UPSTREAM_TABLES = (
    "characters",
    "character_skills",
    "character_ranks",
    "character_skill_trees",
    "character_promotions",
    "light_cones",
    "light_cone_ranks",
    "light_cone_promotions",
    "relic_sets",
    "items",
    "paths",
    "elements",
)


JsonFetcher = Callable[[str], dict[str, Any]]
TextFetcher = Callable[[str], str]


def default_data_pack_root() -> Path:
    override = str(os.environ.get("NEKO_HSR_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    local = str(os.environ.get("LOCALAPPDATA") or "").strip()
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "N.E.K.O" / "data-packs" / "hsr_companion"


def _download_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "N.E.K.O-hsr-companion-data-pack/0.9"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"upstream payload is not an object: {url}")
    return payload


def _download_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "N.E.K.O-hsr-companion-data-pack/0.9"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8")


def _json_copy(value: object) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _source() -> dict[str, Any]:
    return {
        "source_id": SOURCE_ID,
        "name": UPSTREAM_NAME,
        "source_url": UPSTREAM_URL,
        "revision": UPSTREAM_REVISION,
        "license_id": UPSTREAM_LICENSE,
        "license_url": f"{UPSTREAM_URL}/blob/{UPSTREAM_REVISION}/LICENSE",
        "usage": (
            "Pinned structured zh-CN snapshot transformed locally into a SQLite "
            "knowledge pack; no artwork or audio is copied into the pack."
        ),
    }


def _material_ids(value: object) -> list[str]:
    result: list[str] = []
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            material_id = current.get("id")
            if material_id is not None and str(material_id) not in result:
                result.append(str(material_id))
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return result


def build_pack_records(registry: dict[str, Any], tables: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Transform pinned upstream tables into searchable, related records."""

    records: list[dict[str, Any]] = []
    source_to_identity: dict[str, str] = {}
    identity_names: dict[str, str] = {}
    owners_by_skill: dict[str, list[tuple[str, str]]] = defaultdict(list)
    owners_by_rank: dict[str, list[tuple[str, str]]] = defaultdict(list)
    owners_by_tree: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for item in registry.get("records") or []:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").strip()
        name = str(item.get("name") or "").strip()
        identity_id = f"character.{tag}"
        identity_names[identity_id] = name
        source_ids = [str(value) for value in item.get("source_ids") or []]
        for source_id in source_ids:
            source_to_identity[source_id] = identity_id
        records.append(
            {
                "id": identity_id,
                "entity_type": "character",
                "name": name,
                "tag": tag,
                "aliases": list(item.get("aliases") or []),
                "form_ids": [f"character_form.{value}" for value in source_ids],
                "source_refs": [SOURCE_ID],
            }
        )

    skills = tables["character_skills"]
    ranks = tables["character_ranks"]
    trees = tables["character_skill_trees"]
    promotions = tables["character_promotions"]
    for source_id, item in sorted(tables["characters"].items()):
        if not isinstance(item, dict):
            continue
        identity_id = source_to_identity.get(str(source_id))
        if not identity_id:
            continue
        identity_name = identity_names[identity_id]
        skill_ids = [str(value) for value in item.get("skills") or []]
        rank_ids = [str(value) for value in item.get("ranks") or []]
        tree_ids = [str(value) for value in item.get("skill_trees") or []]
        for value in skill_ids:
            owners_by_skill[value].append((identity_id, identity_name))
        for value in rank_ids:
            owners_by_rank[value].append((identity_id, identity_name))
        for value in tree_ids:
            owners_by_tree[value].append((identity_id, identity_name))
        records.append(
            {
                "id": f"character_form.{source_id}",
                "entity_type": "character_form",
                "name": str(item.get("name") or identity_name).strip(),
                "identity_id": identity_id,
                "source_id": str(source_id),
                "rarity": int(item.get("rarity") or 0),
                "path_id": str(item.get("path") or ""),
                "element_id": str(item.get("element") or ""),
                "max_sp": int(item.get("max_sp") or 0),
                "skills": [_json_copy(skills[value]) for value in skill_ids if value in skills],
                "ranks": [_json_copy(ranks[value]) for value in rank_ids if value in ranks],
                "skill_trees": [_json_copy(trees[value]) for value in tree_ids if value in trees],
                "promotion": _json_copy(promotions.get(str(source_id)) or {}),
                "material_ids": _material_ids(
                    [promotions.get(str(source_id)), *[trees.get(value) for value in tree_ids]]
                ),
                "source_refs": [SOURCE_ID],
            }
        )

    def append_owned_records(
        table: dict[str, Any],
        owners: dict[str, list[tuple[str, str]]],
        entity_type: str,
    ) -> None:
        for source_id, item in sorted(table.items()):
            if not isinstance(item, dict) or source_id not in owners:
                continue
            owner_values = owners[source_id]
            payload = _json_copy(item)
            payload.update(
                {
                    "id": f"{entity_type}.{source_id}",
                    "entity_type": entity_type,
                    "name": str(item.get("name") or f"{owner_values[0][1]} {entity_type}"),
                    "owner_ids": [value[0] for value in owner_values],
                    "owner_names": [value[1] for value in owner_values],
                    "source_refs": [SOURCE_ID],
                }
            )
            records.append(payload)

    append_owned_records(skills, owners_by_skill, "character_skill")
    append_owned_records(ranks, owners_by_rank, "character_rank")
    append_owned_records(trees, owners_by_tree, "character_trace")

    light_cone_ranks = tables["light_cone_ranks"]
    light_cone_promotions = tables["light_cone_promotions"]
    for source_id, item in sorted(tables["light_cones"].items()):
        if not isinstance(item, dict):
            continue
        payload = _json_copy(item)
        payload.update(
            {
                "id": f"light_cone.{source_id}",
                "entity_type": "light_cone",
                "name": str(item.get("name") or "").strip(),
                "rank_effect": _json_copy(light_cone_ranks.get(str(source_id)) or {}),
                "promotion": _json_copy(light_cone_promotions.get(str(source_id)) or {}),
                "material_ids": _material_ids(light_cone_promotions.get(str(source_id))),
                "source_refs": [SOURCE_ID],
            }
        )
        if payload["name"]:
            records.append(payload)

    simple_tables = {
        "relic_sets": "relic_set",
        "items": "item",
        "paths": "path",
        "elements": "element",
    }
    for table_name, entity_type in simple_tables.items():
        for source_id, item in sorted(tables[table_name].items()):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("text") or "").strip()
            if not name:
                continue
            payload = _json_copy(item)
            payload.update(
                {
                    "id": f"{entity_type}.{source_id}",
                    "entity_type": entity_type,
                    "name": name,
                    "source_refs": [SOURCE_ID],
                }
            )
            records.append(payload)

    records.sort(key=lambda item: (str(item["entity_type"]), str(item["id"])))
    return records


def _search_text(record: dict[str, Any]) -> str:
    values: list[str] = [
        str(record.get("name") or ""),
        str(record.get("tag") or ""),
        *[str(value) for value in record.get("aliases") or []],
        *[str(value) for value in record.get("owner_names") or []],
    ]
    for key in ("type_text", "effect_text", "simple_desc", "desc", "skill"):
        value = record.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value not in (None, ""):
            values.append(str(value))
    return normalize_lookup_text(" ".join(values))


def write_pack_database(
    database_path: Path,
    *,
    registry: dict[str, Any],
    tables: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    records = build_pack_records(registry, tables)
    coverage = Counter(str(item["entity_type"]) for item in records)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    # sqlite3.Connection's context manager commits/rolls back but does not close
    # the handle.  An explicit close is required before the finished database can
    # be atomically renamed on Windows.
    with closing(sqlite3.connect(database_path)) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE records (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                name_key TEXT NOT NULL,
                search_text TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX records_type_idx ON records(entity_type);
            CREATE INDEX records_name_key_idx ON records(name_key);
            """
        )
        meta = {
            "pack_id": "hsr-knowledge-cn-v08",
            "schema_version": PACK_SCHEMA_VERSION,
            "data_revision": f"starrailres:{UPSTREAM_REVISION}",
            "upstream_revision": UPSTREAM_REVISION,
            "game_version": UPSTREAM_GAME_VERSION,
            "snapshot_date": UPSTREAM_SNAPSHOT_DATE,
            "builder_revision": PACK_BUILDER_REVISION,
            "language": "zh-CN",
            "record_count": len(records),
            "coverage": dict(sorted(coverage.items())),
            "verification_status": "pinned_external_knowledge_pack",
            "content_policy": "structured_text_no_media",
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in meta.items()],
        )
        source = _source()
        connection.execute(
            "INSERT INTO sources(source_id, payload_json) VALUES (?, ?)",
            (SOURCE_ID, json.dumps(source, ensure_ascii=False)),
        )
        connection.executemany(
            """
            INSERT INTO records(id, entity_type, name, name_key, search_text, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(record["id"]),
                    str(record["entity_type"]),
                    str(record["name"]),
                    normalize_lookup_text(record["name"]),
                    _search_text(record),
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                )
                for record in records
            ],
        )
        connection.commit()
    return meta


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ExternalKnowledgeCatalog:
    """Catalog-compatible reader backed by an optional external SQLite pack."""

    def __init__(self, *, registry: dict[str, Any], root: Path | None = None) -> None:
        self.registry = deepcopy(registry)
        self.root = Path(root or default_data_pack_root())
        self.database_path: Path | None = None
        self._source = _source()
        self._characters: list[dict[str, Any]] = []
        self._aliases: dict[str, str] = {}
        self._voice_aliases: dict[str, str] = {}
        self._ocr_aliases: dict[str, str] = {}
        self._status: dict[str, Any] = {}
        self._index_registry()
        self.reload()

    def _index_registry(self) -> None:
        for item in self.registry.get("records") or []:
            if not isinstance(item, dict):
                continue
            record = {
                "id": f"character.{item.get('tag')}",
                "entity_type": "character",
                "name": str(item.get("name") or ""),
                "tag": str(item.get("tag") or ""),
                "aliases": list(item.get("aliases") or []),
                "speech_aliases": list(item.get("speech_aliases") or []),
                "ocr_aliases": list(item.get("ocr_aliases") or []),
                "form_ids": [f"character_form.{value}" for value in item.get("source_ids") or []],
                "source_refs": [SOURCE_ID],
            }
            if not record["name"]:
                continue
            self._characters.append(record)
            for alias in [record["name"], record["tag"], *record["aliases"]]:
                key = normalize_lookup_text(alias)
                if key:
                    self._aliases[key] = record["id"]
            for alias in record["speech_aliases"]:
                key = normalize_lookup_text(alias)
                if key:
                    self._voice_aliases[key] = record["id"]
            for alias in record["ocr_aliases"]:
                key = normalize_lookup_text(alias)
                if key:
                    self._ocr_aliases[key] = record["id"]

    @property
    def records(self) -> list[dict[str, Any]]:
        # Compatibility surface: callers only enumerate this for character UI.
        return deepcopy(self._characters)

    @property
    def record_count(self) -> int:
        return int(self.meta.get("record_count") or len(self._characters))

    @property
    def meta(self) -> dict[str, Any]:
        return deepcopy(self._status.get("meta") or {})

    @property
    def coverage(self) -> dict[str, int]:
        value = self.meta.get("coverage")
        return {str(key): int(count) for key, count in value.items()} if isinstance(value, dict) else {}

    @property
    def revision(self) -> str:
        return str(self.meta.get("data_revision") or "minimal-character-registry")

    def status(self) -> dict[str, Any]:
        return deepcopy(self._status)

    def reload(self) -> dict[str, Any]:
        self._source = _source()
        pointer = self.root / "current.json"
        minimal_meta = {
            "pack_id": "hsr-minimal-character-registry",
            "schema_version": PACK_SCHEMA_VERSION,
            "data_revision": str((self.registry.get("meta") or {}).get("upstream_revision") or ""),
            "language": "zh-CN",
            "record_count": len(self._characters),
            "coverage": {"character": len(self._characters)},
            "verification_status": "minimal_registry_only",
            "content_policy": "identity_only",
        }
        self.database_path = None
        self._status = {
            "state": "missing",
            "message": "完整资料组件尚未安装；角色防幻觉名单仍然有效",
            "root": str(self.root),
            "meta": minimal_meta,
            "installed": False,
        }
        try:
            selected = json.loads(pointer.read_text(encoding="utf-8"))
            relative = str(selected.get("database") or "")
            expected_hash = str(selected.get("sha256") or "")
            database = (self.root / relative).resolve()
            if self.root.resolve() not in database.parents:
                raise ValueError("knowledge pack path escapes data root")
            if not database.is_file():
                raise ValueError("knowledge pack database is missing")
            actual_hash = _file_sha256(database)
            if not expected_hash or actual_hash != expected_hash:
                raise ValueError("knowledge pack hash mismatch")
            meta = self._read_metadata(database)
            if int(meta.get("schema_version") or 0) != PACK_SCHEMA_VERSION:
                raise ValueError("knowledge pack schema is incompatible")
            self.database_path = database
            self._status = {
                "state": "ready",
                "message": "完整资料组件已就绪",
                "root": str(self.root),
                "database": str(database),
                "sha256": actual_hash,
                "installed": True,
                "meta": meta,
                "update_available": meta.get("upstream_revision") != UPSTREAM_REVISION or meta.get("builder_revision") != PACK_BUILDER_REVISION,
                "available_game_version": UPSTREAM_GAME_VERSION,
            }
            # Attribute installed records to their actual stored snapshot, not
            # the newest revision bundled with a newer plugin.
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                source_row = connection.execute("SELECT payload_json FROM sources WHERE source_id = ?", (SOURCE_ID,)).fetchone()
            if source_row:
                self._source = json.loads(source_row[0])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            self._status.update(
                {
                    "state": "error",
                    "message": f"资料组件不可用：{exc}",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        return self.status()

    @staticmethod
    def _read_metadata(database: Path) -> dict[str, Any]:
        uri = database.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute("SELECT key, value FROM metadata").fetchall()
        return {str(key): json.loads(value) for key, value in rows}

    def install_pinned(
        self,
        *,
        json_fetcher: JsonFetcher = _download_json,
        text_fetcher: TextFetcher = _download_text,
    ) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)

        # Fetch independent pinned tables concurrently.  This keeps the first
        # run bounded by one network timeout instead of twelve consecutive
        # timeouts when the upstream is unavailable.
        def fetch_table(name: str) -> tuple[str, dict[str, Any]]:
            return name, json_fetcher(f"{BASE_URL}/{name}.json")

        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="hsr-pack") as pool:
            tables = dict(pool.map(fetch_table, UPSTREAM_TABLES))
        pack_dir = self.root / "packs" / UPSTREAM_REVISION
        pack_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix="knowledge-", suffix=".sqlite.tmp", dir=pack_dir)
        os.close(fd)
        temporary = Path(temporary_name)
        database = pack_dir / "knowledge.sqlite"
        try:
            meta = write_pack_database(
                temporary,
                registry=self.registry,
                tables=tables,
            )
            os.replace(temporary, database)
        finally:
            if temporary.exists():
                temporary.unlink()
        license_path = pack_dir / "StarRailRes-AGPL-3.0.txt"
        license_path.write_text(text_fetcher(LICENSE_URL), encoding="utf-8")
        digest = _file_sha256(database)
        manifest = {
            "schema_version": PACK_SCHEMA_VERSION,
            "database": str(database.relative_to(self.root)).replace("\\", "/"),
            "sha256": digest,
            "size_bytes": database.stat().st_size,
            "data_revision": meta["data_revision"],
            "source": self._source,
            "license_file": str(license_path.relative_to(self.root)).replace("\\", "/"),
        }
        manifest_path = pack_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        pointer_tmp = self.root / "current.json.tmp"
        pointer_tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(pointer_tmp, self.root / "current.json")
        return self.reload()

    def _record_with_sources(self, payload_json: str) -> dict[str, Any]:
        record = json.loads(payload_json)
        record["sources"] = [deepcopy(self._source)]
        return record

    def get(self, record_id: str) -> dict[str, Any] | None:
        normalized_id = str(record_id or "").strip()
        if not normalized_id:
            return None
        for item in self._characters:
            if item["id"] == normalized_id:
                minimal = deepcopy(item)
                minimal["sources"] = [deepcopy(self._source)]
                if self.database_path is None:
                    return minimal
                break
        if self.database_path is None:
            return None
        uri = self.database_path.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            row = connection.execute("SELECT payload_json FROM records WHERE id = ?", (normalized_id,)).fetchone()
        return self._record_with_sources(row[0]) if row else None

    def resolve_character(self, candidate: str, *, modality: str = "text") -> dict[str, Any] | None:
        key = normalize_lookup_text(candidate)
        record_id = self._aliases.get(key)
        if not record_id and str(modality or "").lower() == "voice":
            record_id = self._voice_aliases.get(key)
        return self.get(record_id or "")

    def match_labels(self, labels: Iterable[str], *, entity_types: Iterable[str]) -> list[dict[str, Any]]:
        """One bounded read for a frame, not a database connection per OCR box."""
        keys = sorted({normalize_lookup_text(label) for label in labels if normalize_lookup_text(label)})[:240]
        allowed = sorted(set(entity_types))
        if not keys or not allowed or self.database_path is None:
            return []
        uri = self.database_path.as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM records WHERE name_key IN (" + ",".join("?" for _ in keys)
                + ") AND entity_type IN (" + ",".join("?" for _ in allowed) + ")", [*keys, *allowed],
            ).fetchall()
        return [self._record_with_sources(row[0]) for row in rows]

    def match_characters_in_text(self, text: str) -> list[dict[str, Any]]:
        lines = [normalize_lookup_text(line) for line in str(text or "").splitlines()]
        compact = normalize_lookup_text(text)
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for alias, record_id in self._aliases.items():
            matched = alias in lines if len(alias) <= 1 else alias in compact
            if matched and record_id not in seen:
                record = self.get(record_id)
                if record:
                    found.append(record)
                    seen.add(record_id)
        for alias, record_id in self._ocr_aliases.items():
            if alias in lines and record_id not in seen:
                record = self.get(record_id)
                if record:
                    found.append(record)
                    seen.add(record_id)
        found.sort(key=lambda item: (-len(str(item.get("name") or "")), item["id"]))
        return found

    def search(
        self,
        query: str,
        *,
        entity_types: Iterable[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        key = normalize_lookup_text(query)
        if not key:
            return []
        allowed = [str(item) for item in entity_types or [] if item]
        if self.database_path is None:
            result: list[dict[str, Any]] = []
            for record in self._characters:
                names = [record.get("name"), *(record.get("aliases") or [])]
                normalized = [normalize_lookup_text(value) for value in names if value]
                if key in normalized or any(value and value in key for value in normalized):
                    item = deepcopy(record)
                    item["sources"] = [deepcopy(self._source)]
                    result.append(item)
            return result[: max(1, min(int(limit or 8), 30))]

        uri = self.database_path.as_uri() + "?mode=ro"
        clauses = ["(search_text LIKE ? OR ? LIKE '%' || name_key || '%')"]
        params: list[Any] = [f"%{key}%", key]
        if allowed:
            clauses.append("entity_type IN (" + ",".join("?" for _ in allowed) + ")")
            params.extend(allowed)
        params.append(200)
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                "SELECT name_key, search_text, payload_json FROM records WHERE " + " AND ".join(clauses) + " LIMIT ?",
                params,
            ).fetchall()
        scored: list[tuple[int, dict[str, Any]]] = []
        for name_key, search_text, payload_json in rows:
            score = 100 if key == name_key else 90 if name_key and name_key in key else 70
            if key in str(search_text):
                score += 5
            scored.append((score, self._record_with_sources(payload_json)))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("name") or "")))
        # Whole natural-language questions rarely occur verbatim in data.
        # Resolve an exact catalogue name contained in the question first;
        # include its related forms instead of returning only a roster entry.
        identities = self.match_characters_in_text(query) if not allowed or "character" in allowed or "character_form" in allowed else []
        seen = {item["id"] for item in identities}
        selected = [*identities, *[item for _, item in scored if item["id"] not in seen]]
        result = selected[: max(1, min(int(limit or 8), 30))]
        for record in result:
            if record.get("entity_type") == "character":
                record["forms"] = []
                for form_id in (record.get("form_ids") or [])[:10]:
                    form = self.get(form_id)
                    if not form:
                        continue
                    path = self.get(f"path.{form.get('path_id')}") or {}
                    element = self.get(f"element.{form.get('element_id')}") or {}
                    record["forms"].append({
                        "id": form["id"], "name": form["name"], "rarity": form.get("rarity"),
                        "path": path.get("name") or form.get("path_id"),
                        "element": element.get("name") or form.get("element_id"),
                        "skills": (form.get("skills") or [])[:12],
                        "ranks": (form.get("ranks") or [])[:6],
                        "sources": form.get("sources") or [],
                    })
        return result


__all__ = [
    "ExternalKnowledgeCatalog",
    "PACK_SCHEMA_VERSION",
    "UPSTREAM_REVISION",
    "build_pack_records",
    "default_data_pack_root",
    "write_pack_database",
]
