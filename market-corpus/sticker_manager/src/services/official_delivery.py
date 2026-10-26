"""Read budgeted official send variants without changing the collected original."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.catalog import content_sha256, detect_image_format
from .bundled_pack import open_pack, pack_exists
from .pack_io import read_pack_manifest


class OfficialDelivery:
    def __init__(self, pack_path: Path):
        self._pack_path = pack_path
        self._index: dict[str, tuple[str, str]] | None = None

    def resolve(self, original: bytes) -> bytes | None:
        if self._index is None:
            if not pack_exists(self._pack_path):
                self._index = {}
                return None
            with open_pack(self._pack_path) as archive:
                manifest = json.loads(read_pack_manifest(archive))
            self._index = {
                row["sha256"]: (row["delivery_file"], row["delivery_sha256"])
                for row in manifest.get("stickers", [])
                if isinstance(row, dict) and row.get("sha256") and row.get("delivery_file")
            }
        match = self._index.get(content_sha256(original))
        if match is None:
            return None
        name, expected = match
        if Path(name).name != name or any(c in name for c in "/\\:") or name.startswith("."):
            raise ValueError("unsafe_delivery_file")
        with open_pack(self._pack_path) as archive:
            info = archive.getinfo("delivery/" + name)
            if not 0 < info.file_size <= 256 * 1024:
                raise ValueError("delivery_size")
            data = archive.read(info)
        if content_sha256(data) != expected or detect_image_format(data) != ("webp", "image/webp"):
            raise ValueError("delivery_digest_or_format")
        return data
