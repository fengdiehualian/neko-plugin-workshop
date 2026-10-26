"""Bound untrusted ZIP manifest decompression before parsing JSON."""

from __future__ import annotations

import zipfile

from ..core.pack import PACK_MANIFEST_FILENAME

PACK_MANIFEST_MAX_BYTES = 4 * 1024 * 1024


class ManifestLimitError(ValueError):
    pass


def read_pack_manifest(archive: zipfile.ZipFile) -> bytes:
    info = archive.getinfo(PACK_MANIFEST_FILENAME)
    if info.is_dir() or info.file_size > PACK_MANIFEST_MAX_BYTES:
        raise ManifestLimitError("pack_manifest_size")
    with archive.open(info) as stream:
        data = stream.read(PACK_MANIFEST_MAX_BYTES + 1)
    if len(data) > PACK_MANIFEST_MAX_BYTES:
        raise ManifestLimitError("pack_manifest_size")
    return data
