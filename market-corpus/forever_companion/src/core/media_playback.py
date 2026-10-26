"""Bounded, verified playback copies for the public static-resource SDK.

Only this dedicated directory is registered. Originals, uploads and Store
files remain private. All callers hold the gallery's cross-process lock.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import media_files
from .media import chunk_digest, validate_video_signature
from .state import _GALLERY_VIDEO_CHUNK_BYTES, _GALLERY_VIDEO_HARD_MAX_BYTES, _GALLERY_VIDEO_RESERVE_BYTES

CACHE_CONTROL = "private, no-cache, max-age=0, must-revalidate"
_ASSET = re.compile(r"^[a-f0-9]{64}\.(?:mp4|webm)$")
_INDEX = b"<!doctype html><meta charset=utf-8><title></title>"


def root(plugin: object) -> Path:
    directory = media_files.safe_root(plugin).parent / "wallpaper_playback"
    media_files._check_path(directory)
    if directory.exists() and not directory.is_dir():
        raise media_files.MediaFileError("video_path_unsafe")
    return directory


def _entries(directory: Path) -> list[Path]:
    media_files._check_path(directory)
    if not directory.exists():
        return []
    entries = list(directory.iterdir())
    for path in entries:
        media_files._check_path(path)
        if not path.is_file() or (path.name != "index.html" and not _ASSET.fullmatch(path.name)):
            raise media_files.MediaFileError("video_path_unsafe")
    return entries


def _staging(directory: Path) -> Path:
    path = directory.parent / ".wallpaper-playback.part"
    media_files._check_path(path)
    return path


def clear(plugin: object) -> None:
    directory = root(plugin)
    for path in _entries(directory):
        if path.name != "index.html":
            path.unlink()
    _staging(directory).unlink(missing_ok=True)


def usage(plugin: object) -> int:
    return sum(path.stat().st_size for path in _entries(root(plugin)) if path.name != "index.html")


def initialize(plugin: object) -> Path:
    directory = root(plugin)
    directory.mkdir(exist_ok=True)
    clear(plugin)
    index = directory / "index.html"
    with media_files._open_file(index, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, "wb") as handle:
        handle.write(_INDEX)
    return directory


def _signature(path: Path) -> tuple[int, int, int, int]:
    media_files._check_path(path)
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino


def asset_name(manifest: dict) -> str:
    mime = manifest["mime"]
    if mime not in {"video/mp4", "video/webm"}:
        raise media_files.MediaFileError("video_read_failed")
    identity = f'{mime}:{manifest["size"]}:' + ":".join(manifest["hashes"])
    return hashlib.sha256(identity.encode("ascii")).hexdigest() + (".mp4" if mime == "video/mp4" else ".webm")


def prepare(
    plugin: object, manifest: dict, verified: dict, reserved_bytes: int = 0,
    budget_bytes: int | None = None,
) -> tuple[str, bool]:
    directory = root(plugin)
    entries = [path for path in _entries(directory) if path.name != "index.html"]
    source = media_files.file_path(media_files.safe_root(plugin), manifest["storage_id"], ".bin")
    signature = _signature(source)
    size = manifest["size"]
    if signature[0] != size or not 0 < size <= _GALLERY_VIDEO_HARD_MAX_BYTES:
        raise media_files.MediaFileError("video_read_failed")
    limit = min(_GALLERY_VIDEO_HARD_MAX_BYTES, budget_bytes if budget_bytes is not None else _GALLERY_VIDEO_HARD_MAX_BYTES)
    if size > limit:
        raise media_files.MediaFileError("video_storage_full")
    name = asset_name(manifest)
    target = directory / name
    if (target.is_file() and len(entries) <= 2
        and sum(path.stat().st_size for path in entries) <= limit
        and verified.get(name) == (signature, _signature(target))):
        verification = verified.pop(name)
        verified[name] = verification
        return name, True

    # At most two copies and 512 MiB total; eviction never touches originals.
    recency = {key: index for index, key in enumerate(verified)}
    others = sorted(
        (path for path in entries if path != target),
        key=lambda path: (recency.get(path.name, -1), path.stat().st_mtime_ns),
    )
    total = sum(path.stat().st_size for path in others)
    for old in list(others):
        if len(others) < 2 and total + size <= limit:
            break
        old_size = old.stat().st_size
        try:
            old.unlink()
        except PermissionError:
            # Windows may still serve the old player's Range request.
            # Try another copy without invalidating the occupied asset.
            continue
        others.remove(old)
        total -= old_size
        verified.pop(old.name, None)
    if len(others) >= 2 or total + size > limit:
        raise media_files.MediaFileError("video_playback_busy")
    if target.exists():
        target.unlink()
        verified.pop(name, None)
    for stale in set(verified) - {path.name for path in others}:
        verified.pop(stale, None)
    # Replacing an unverified copy also needs room for its staging file.
    free, _ = media_files.disk_usage(directory)
    if free < size + _GALLERY_VIDEO_RESERVE_BYTES + reserved_bytes:
        raise media_files.MediaFileError("video_disk_full")
    staging = _staging(directory)
    try:
        with media_files._open_file(source, os.O_RDONLY, "rb") as reader:
            with media_files._open_file(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, "wb") as writer:
                for number, expected in enumerate(manifest["hashes"]):
                    block = reader.read(min(_GALLERY_VIDEO_CHUNK_BYTES, size - number * _GALLERY_VIDEO_CHUNK_BYTES))
                    if not block or chunk_digest(block) != expected:
                        raise media_files.MediaFileError("video_read_failed")
                    if number == 0:
                        validate_video_signature(block, manifest["mime"], size)
                    writer.write(block)
                if reader.read(1) or reader.tell() != size:
                    raise media_files.MediaFileError("video_read_failed")
                writer.flush()
                os.fsync(writer.fileno())
        if _signature(source) != signature:
            raise media_files.MediaFileError("video_read_failed")
        media_files._check_path(target)
        os.replace(staging, target)
        verified[name] = (signature, _signature(target))
        return name, False
    finally:
        staging.unlink(missing_ok=True)
