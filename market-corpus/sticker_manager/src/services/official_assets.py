"""Strict validation for official media releases before modifying a library."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.catalog import content_sha256, detect_image_format
from ..core.pack import PACK_MAX_ENTRIES, parse_manifest, safe_member_name
from .bundled_pack import open_pack
from .pack_io import read_pack_manifest


def validate_official_assets(path: Path) -> None:
    with open_pack(path) as archive:
        try:
            raw = json.loads(read_pack_manifest(archive))
        except KeyError:
            return
        rows = raw.get("stickers", [])
        media = raw.get("media_policy") == "full_motion_webp"
        dual = raw.get("media_policy") == "original_gif_with_full_motion_delivery"
        if not media and not dual and not any(isinstance(row, dict) and row.get("legacy_sha256") for row in rows):
            return
        if not isinstance(rows, list) or not rows or len(rows) > PACK_MAX_ENTRIES:
            raise ValueError("invalid_media_manifest")
        entries = parse_manifest(raw)
        if len(entries) != len(rows):
            raise ValueError("invalid_media_entries")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate_archive_members")
        owners: dict[str, str] = {}
        files: set[str] = set()
        assets: set[str] = set()
        for row, entry in zip(rows, entries):
            name = row.get("file")
            if name != safe_member_name(name) or name in files or any(char in name for char in "/\\:"):
                raise ValueError("unsafe_or_duplicate_media_filename")
            files.add(name)
            if media or dual:
                asset = row.get("asset_id")
                if not isinstance(asset, str) or not asset or asset in assets:
                    raise ValueError("invalid_or_duplicate_asset_id")
                assets.add(asset)
            digests = [entry.sha256, *entry.legacy_sha256]
            if row.get("legacy_sha256", []) != entry.legacy_sha256:
                raise ValueError("invalid_legacy_digest")
            for digest in digests:
                if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    raise ValueError("invalid_media_digest")
                if digest in owners and owners[digest] != name:
                    raise ValueError("ambiguous_media_digest")
                owners[digest] = name
            info = archive.getinfo("stickers/" + name)
            limit = 256 * 1024 if media else 8 * 1024 * 1024
            if info.is_dir() or not 0 < info.file_size <= limit:
                raise ValueError("invalid_media_size")
            data = archive.read(info)
            if content_sha256(data) != entry.sha256 or detect_image_format(data) is None:
                raise ValueError("invalid_media_digest_or_format")
            if dual:
                delivery = row.get("delivery_file")
                if not isinstance(delivery, str) or delivery != safe_member_name(delivery) or any(c in delivery for c in "/\\:"):
                    raise ValueError("unsafe_delivery_filename")
                info = archive.getinfo("delivery/" + delivery)
                if info.is_dir() or not 0 < info.file_size <= 256 * 1024:
                    raise ValueError("invalid_delivery_size")
                payload = archive.read(info)
                if content_sha256(payload) != row.get("delivery_sha256") or detect_image_format(payload) != ("webp", "image/webp"):
                    raise ValueError("invalid_delivery_digest_or_format")
