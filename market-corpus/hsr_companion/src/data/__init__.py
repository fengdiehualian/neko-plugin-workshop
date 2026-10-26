"""Versioned, provenance-aware Star Rail data access."""

from .catalog import StructuredCatalog, normalize_lookup_text
from .pack import (
    PACK_SCHEMA_VERSION,
    UPSTREAM_REVISION,
    ExternalKnowledgeCatalog,
    build_pack_records,
    default_data_pack_root,
    write_pack_database,
)

__all__ = [
    "ExternalKnowledgeCatalog",
    "PACK_SCHEMA_VERSION",
    "StructuredCatalog",
    "UPSTREAM_REVISION",
    "build_pack_records",
    "default_data_pack_root",
    "normalize_lookup_text",
    "write_pack_database",
]
