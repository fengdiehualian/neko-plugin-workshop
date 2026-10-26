"""Animation detection without adding a runtime image-codec dependency."""

from __future__ import annotations


def preserves_animation(data: bytes, mime: str) -> bool:
    if mime == "image/gif":
        return True
    if mime != "image/webp" or len(data) < 12:
        return False
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return False
    end = min(len(data), int.from_bytes(data[4:8], "little") + 8)
    offset = 12
    while offset + 8 <= end:
        kind = data[offset:offset + 4]
        size = int.from_bytes(data[offset + 4:offset + 8], "little")
        start = offset + 8
        if start + size > end:
            return False
        if kind == b"VP8X" and size >= 10 and data[start] & 0x02:
            return True
        if kind in {b"ANIM", b"ANMF"}:
            return True
        offset = start + size + (size & 1)
    return False
