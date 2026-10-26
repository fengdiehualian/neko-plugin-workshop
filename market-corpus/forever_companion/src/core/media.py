"""Bounded video payload validation; no paths, SDK calls, or codec dependencies."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from typing import Any

from .state import (
    _GALLERY_THUMB_MAX_CHARS,
    _GALLERY_VIDEO_CHUNK_BYTES,
    _GALLERY_VIDEO_CHUNK_PREFIX,
    _GALLERY_VIDEO_FILE_MANIFEST_PREFIX,
    _GALLERY_VIDEO_MAX_BYTES,
    _GALLERY_VIDEO_MIMES,
    _GALLERY_VIDEO_POSTER_MAX_CHARS,
    _GALLERY_VIDEO_PREFIX,
)

_STORAGE_ID = re.compile(r"^[a-f0-9]{32}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_POSTER_MIMES = {"image/png", "image/jpeg", "image/webp"}


class GalleryMediaError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def valid_storage_id(value: Any) -> bool:
    return isinstance(value, str) and _STORAGE_ID.fullmatch(value) is not None


def video_manifest_key(item_id: str) -> str:
    return _GALLERY_VIDEO_PREFIX + item_id


def video_file_manifest_key(item_id: str) -> str:
    return _GALLERY_VIDEO_FILE_MANIFEST_PREFIX + item_id


def video_chunk_key(storage_id: str, chunk_index: int) -> str:
    return f"{_GALLERY_VIDEO_CHUNK_PREFIX}{storage_id}/{chunk_index}"


def video_chunk_count(size: int) -> int:
    return (size + _GALLERY_VIDEO_CHUNK_BYTES - 1) // _GALLERY_VIDEO_CHUNK_BYTES


def video_chunk_size(size: int, chunk_index: int) -> int:
    return min(_GALLERY_VIDEO_CHUNK_BYTES, size - chunk_index * _GALLERY_VIDEO_CHUNK_BYTES)


def video_size(value: Any, maximum: int = _GALLERY_VIDEO_MAX_BYTES) -> int:
    if type(value) is not int or value <= 0:
        raise GalleryMediaError("video_size_invalid")
    if value > maximum:
        raise GalleryMediaError("video_too_large")
    return value


def video_mime(value: Any) -> str:
    if not isinstance(value, str) or value.strip().lower() not in _GALLERY_VIDEO_MIMES:
        raise GalleryMediaError("video_type_unsupported")
    return value.strip().lower()


def video_poster(value: Any, *, thumbnail: bool = False) -> str:
    maximum = _GALLERY_THUMB_MAX_CHARS if thumbnail else _GALLERY_VIDEO_POSTER_MAX_CHARS
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise GalleryMediaError("video_poster_invalid")
    header, separator, encoded = value.partition(",")
    mime = header.lower().removeprefix("data:").removesuffix(";base64")
    if not separator or mime not in _POSTER_MIMES or header.lower() != f"data:{mime};base64":
        raise GalleryMediaError("video_poster_invalid")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise GalleryMediaError("video_poster_invalid") from None
    if base64.b64encode(decoded).decode("ascii") != encoded:
        raise GalleryMediaError("video_poster_invalid")
    valid = (
        (mime == "image/png" and decoded.startswith(b"\x89PNG\r\n\x1a\n"))
        or (mime == "image/jpeg" and decoded.startswith(b"\xff\xd8\xff"))
        or (mime == "image/webp" and decoded[:4] == b"RIFF" and decoded[8:12] == b"WEBP")
    )
    if not valid:
        raise GalleryMediaError("video_poster_invalid")
    return value


def decode_video_chunk(value: Any, expected: int) -> bytes:
    if not isinstance(value, str) or len(value) != ((expected + 2) // 3) * 4:
        raise GalleryMediaError("video_chunk_invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise GalleryMediaError("video_chunk_invalid") from None
    if len(decoded) != expected or base64.b64encode(decoded).decode("ascii") != value:
        raise GalleryMediaError("video_chunk_invalid")
    return decoded


def chunk_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def valid_digest(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def validate_video_signature(value: bytes, mime: str, size: int) -> None:
    # Browsers perform the actual codec decode. Here we reject MIME spoofing and
    # obviously unrelated payloads before publishing any gallery item.
    if mime == "video/mp4":
        valid = (
            len(value) >= 16
            and value[4:8] == b"ftyp"
            and 16 <= int.from_bytes(value[:4], "big") <= size
            and value[8:12] != b"\x00\x00\x00\x00"
        )
    else:
        valid = value.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in value[:4096]
    if not valid:
        raise GalleryMediaError("video_signature_invalid")
