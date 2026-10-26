"""Read a legacy ZIP or a checksummed split ZIP without rebuilding it on disk."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from bisect import bisect_right
from collections import OrderedDict
from pathlib import Path

MAX_PART_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_PARTS = 128
MAX_MANIFEST_BYTES = 64 * 1024
_VERIFIED: OrderedDict[tuple, None] = OrderedDict()


def pack_exists(path: Path) -> bool:
    return path.is_file() or Path(str(path) + ".parts.json").is_file()


def _integer(value: object, limit: int) -> bool:
    return type(value) is int and 0 < value <= limit


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _fingerprint(path: Path) -> tuple:
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing_or_linked_archive_part")
    stat = path.stat()
    return str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino


class SplitArchive(io.RawIOBase):
    def __init__(self, paths: list[Path], sizes: list[int]):
        super().__init__()
        self._files = []
        self._offsets = [0]
        self._position = 0
        try:
            for path, size in zip(paths, sizes):
                self._files.append(path.open("rb"))
                self._offsets.append(self._offsets[-1] + size)
        except BaseException:
            self.close()
            raise

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        self._checkClosed()
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        self._checkClosed()
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self._position + offset
        elif whence == os.SEEK_END:
            position = self._offsets[-1] + offset
        else:
            raise ValueError("invalid_whence")
        if position < 0:
            raise OSError("negative_seek")
        self._position = position
        return position

    def read(self, size: int = -1) -> bytes:
        self._checkClosed()
        remaining = max(0, self._offsets[-1] - self._position)
        remaining = remaining if size is None or size < 0 else min(remaining, size)
        chunks = []
        while remaining:
            index = bisect_right(self._offsets, self._position) - 1
            file = self._files[index]
            file.seek(self._position - self._offsets[index])
            length = min(remaining, self._offsets[index + 1] - self._position)
            chunk = file.read(length)
            if len(chunk) != length:
                raise OSError("archive_part_changed_or_truncated")
            chunks.append(chunk)
            self._position += length
            remaining -= length
        return b"".join(chunks)

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def close(self) -> None:
        try:
            for file in self._files:
                file.close()
        finally:
            super().close()


class _OwnedZipFile(zipfile.ZipFile):
    def __init__(self, stream: SplitArchive):
        self._owned_stream = stream
        try:
            super().__init__(stream)
        except BaseException:
            stream.close()
            raise

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._owned_stream.close()


def open_pack(path: Path) -> zipfile.ZipFile:
    if path.is_file():
        return zipfile.ZipFile(path)
    manifest_path = Path(str(path) + ".parts.json")
    manifest_fingerprint = _fingerprint(manifest_path)
    if manifest_fingerprint[1] > MAX_MANIFEST_BYTES:
        raise ValueError("archive_manifest_too_large")
    with manifest_path.open("rb") as file:
        payload = file.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("archive_manifest_too_large")
    manifest = json.loads(payload)
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int:
        raise ValueError("invalid_archive_manifest")
    if manifest["schema_version"] != 1 or not _integer(manifest.get("size"), MAX_TOTAL_BYTES):
        raise ValueError("invalid_archive_manifest")
    if not _digest(manifest.get("sha256")):
        raise ValueError("invalid_archive_digest")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not 0 < len(parts) <= MAX_PARTS:
        raise ValueError("invalid_archive_parts")
    paths, sizes, fingerprints = [], [], []
    for index, row in enumerate(parts, 1):
        expected_name = f"{path.name}.part{index:03d}"
        if not isinstance(row, dict) or row.get("name") != expected_name:
            raise ValueError("unsafe_or_unordered_archive_part")
        if not _integer(row.get("size"), MAX_PART_BYTES) or not _digest(row.get("sha256")):
            raise ValueError("invalid_archive_part")
        part_path = path.parent / expected_name
        if part_path.resolve().parent != path.parent.resolve():
            raise ValueError("archive_part_outside_directory")
        fingerprint = _fingerprint(part_path)
        if fingerprint[1] != row["size"]:
            raise ValueError("archive_part_size_mismatch")
        paths.append(part_path)
        sizes.append(row["size"])
        fingerprints.append(fingerprint)
    if sum(sizes) != manifest["size"]:
        raise ValueError("archive_total_size_mismatch")
    key = (manifest_fingerprint, hashlib.sha256(payload).hexdigest(), tuple(fingerprints))
    stream = SplitArchive(paths, sizes)
    try:
        if key not in _VERIFIED:
            total_digest = hashlib.sha256()
            for file, row in zip(stream._files, parts):
                part_digest = hashlib.sha256()
                while chunk := file.read(1024 * 1024):
                    part_digest.update(chunk)
                    total_digest.update(chunk)
                if part_digest.hexdigest() != row["sha256"]:
                    raise ValueError("archive_part_digest_mismatch")
            if total_digest.hexdigest() != manifest["sha256"]:
                raise ValueError("archive_total_digest_mismatch")
            if [_fingerprint(p) for p in paths] != fingerprints:
                raise ValueError("archive_changed_during_validation")
            _VERIFIED[key] = None
            if len(_VERIFIED) > 8:
                _VERIFIED.popitem(last=False)
        else:
            _VERIFIED.move_to_end(key)
        return _OwnedZipFile(stream)
    except BaseException:
        stream.close()
        raise
