"""Database-bound character detection from OCR text."""

from __future__ import annotations

from typing import Any

from ..data import StructuredCatalog, normalize_lookup_text


def detect_characters(text: str, catalog: StructuredCatalog) -> list[dict[str, Any]]:
    return [
        {
            "entity_id": item["id"],
            "name": item["name"],
            "evidence": (
                "exact_catalog_text"
                if normalize_lookup_text(item["name"]) in normalize_lookup_text(text)
                else "catalog_ocr_alias_line"
            ),
            "confidence": (
                "high"
                if normalize_lookup_text(item["name"]) in normalize_lookup_text(text)
                else "mixed"
            ),
        }
        for item in catalog.match_characters_in_text(text)
    ]


__all__ = ["detect_characters"]
