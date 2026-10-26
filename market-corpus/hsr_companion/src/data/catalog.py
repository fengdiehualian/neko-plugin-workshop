"""Strict structured catalog used by recognition and knowledge lookup.

The catalog is deliberately read-only at runtime.  Player corrections live in
the plugin store and never mutate the distributed game-data snapshot.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable

_SEPARATORS = re.compile(r"[\s·•・:：,，。.!！?？'\"“”‘’()（）\[\]【】_-]+")


def normalize_lookup_text(value: object) -> str:
    return _SEPARATORS.sub("", str(value or "").strip().lower())


class StructuredCatalog:
    """Validated entity catalog with exact aliases and source provenance."""

    def __init__(self, payload: dict[str, Any], sources: dict[str, Any]):
        self.meta = dict(payload.get("meta") or {})
        self.records = [dict(item) for item in payload.get("records", []) if isinstance(item, dict)]
        self.sources = {
            str(item.get("source_id")): dict(item)
            for item in sources.get("sources", [])
            if isinstance(item, dict) and item.get("source_id")
        }
        self._by_id: dict[str, dict[str, Any]] = {}
        self._character_aliases: dict[str, str] = {}
        self._voice_aliases: dict[str, str] = {}
        self._ocr_aliases: dict[str, str] = {}
        self._validate_and_index()

    def _validate_and_index(self) -> None:
        if int(self.meta.get("schema_version") or 0) < 2:
            raise ValueError("structured catalog schema_version must be >= 2")
        if not self.meta.get("data_revision"):
            raise ValueError("structured catalog requires data_revision")

        for record in self.records:
            record_id = str(record.get("id") or "").strip()
            name = str(record.get("name") or "").strip()
            entity_type = str(record.get("entity_type") or "").strip()
            refs = [str(item) for item in record.get("source_refs") or [] if item]
            if not record_id or not name or not entity_type:
                raise ValueError("catalog records require id, name and entity_type")
            if record_id in self._by_id:
                raise ValueError(f"duplicate catalog id: {record_id}")
            missing = [ref for ref in refs if ref not in self.sources]
            if not refs or missing:
                raise ValueError(f"catalog record {record_id} has invalid source refs: {missing}")
            self._by_id[record_id] = record

            if entity_type != "character":
                continue
            for alias in [name, record.get("tag"), *(record.get("aliases") or [])]:
                self._index_character_alias(alias, record_id, voice_only=False)
            for alias in record.get("speech_aliases") or []:
                self._index_character_alias(alias, record_id, voice_only=True)
            for alias in record.get("ocr_aliases") or []:
                key = normalize_lookup_text(alias)
                existing = self._ocr_aliases.get(key)
                if not key or (existing and existing != record_id):
                    raise ValueError(f"ambiguous character OCR alias: {alias!r}")
                self._ocr_aliases[key] = record_id

        expected = int(self.meta.get("record_count") or 0)
        if expected and expected != len(self.records):
            raise ValueError(f"catalog record_count mismatch: metadata={expected}, actual={len(self.records)}")

    def _index_character_alias(self, alias: object, record_id: str, *, voice_only: bool) -> None:
        key = normalize_lookup_text(alias)
        if not key:
            return
        target = self._voice_aliases if voice_only else self._character_aliases
        existing = target.get(key)
        if existing and existing != record_id:
            raise ValueError(f"ambiguous character alias: {alias!r}")
        target[key] = record_id

    @property
    def revision(self) -> str:
        return str(self.meta.get("data_revision") or "")

    @property
    def coverage(self) -> dict[str, int]:
        configured = self.meta.get("coverage")
        if isinstance(configured, dict):
            return {str(key): int(value) for key, value in configured.items()}
        result: dict[str, int] = {}
        for record in self.records:
            key = str(record.get("entity_type") or "unknown")
            result[key] = result.get(key, 0) + 1
        return result

    def get(self, record_id: str) -> dict[str, Any] | None:
        record = self._by_id.get(str(record_id or "").strip())
        return deepcopy(record) if record else None

    def resolve_character(self, candidate: str, *, modality: str = "text") -> dict[str, Any] | None:
        key = normalize_lookup_text(candidate)
        record_id = self._character_aliases.get(key)
        if not record_id and str(modality or "").lower() == "voice":
            record_id = self._voice_aliases.get(key)
        return self.get(record_id or "")

    def match_characters_in_text(self, text: str) -> list[dict[str, Any]]:
        """Return only canonical characters explicitly present in OCR text.

        One-character names (for example ``刃``) require a complete OCR line;
        this prevents common prose from turning into a false character match.
        Speech-only homophones are never used for visual OCR.
        """

        lines = [normalize_lookup_text(line) for line in str(text or "").splitlines()]
        compact = normalize_lookup_text(text)
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for alias, record_id in self._character_aliases.items():
            matched = alias in lines if len(alias) <= 1 else alias in compact
            if not matched or record_id in seen:
                continue
            seen.add(record_id)
            record = self.get(record_id)
            if record:
                found.append(record)
        for alias, record_id in self._ocr_aliases.items():
            if alias not in lines or record_id in seen:
                continue
            seen.add(record_id)
            record = self.get(record_id)
            if record:
                found.append(record)
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
        allowed = {str(item) for item in entity_types or [] if item}
        scored: list[tuple[int, dict[str, Any]]] = []
        for record in self.records:
            if allowed and str(record.get("entity_type")) not in allowed:
                continue
            names = [record.get("name"), *(record.get("aliases") or [])]
            normalized = [normalize_lookup_text(item) for item in names if item]
            score = 100 if key in normalized else 70 if any(key in item for item in normalized) else 0
            if score:
                scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("name") or "")))
        result: list[dict[str, Any]] = []
        for _, record in scored[: max(1, min(int(limit or 8), 30))]:
            value = deepcopy(record)
            value["sources"] = [deepcopy(self.sources[source_id]) for source_id in value.get("source_refs") or []]
            result.append(value)
        return result

    def match_labels(self, labels: Iterable[str], *, entity_types: Iterable[str]) -> list[dict[str, Any]]:
        keys = {normalize_lookup_text(label) for label in labels}
        allowed = set(entity_types)
        return [
            {**deepcopy(record), "sources": [deepcopy(self.sources[ref]) for ref in record.get("source_refs") or []]}
            for record in self.records
            if record.get("entity_type") in allowed and normalize_lookup_text(record.get("name")) in keys
        ]


__all__ = ["StructuredCatalog", "normalize_lookup_text"]
