"""Bounded screenshot decoding for N.E.K.O vision hand-off.

The plugin never persists uploaded image bytes. It validates and compresses one
user-selected screenshot, then forwards the bytes to N.E.K.O's existing vision
path using ``push_message``.
"""

from __future__ import annotations

import base64
import binascii
from io import BytesIO

SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_VISION_BYTES = 220 * 1024
MAX_LONG_EDGE = 1600


def _sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def decode_image_data_url(data_url: str) -> tuple[bytes, str]:
    """Decode a bounded image data URL and verify its real file signature."""

    value = str(data_url or "").strip()
    if not value.startswith("data:image/") or "," not in value:
        raise ValueError("请上传 PNG、JPEG 或 WebP 图片")
    header, encoded = value.split(",", 1)
    declared_mime = header[5:].split(";", 1)[0].lower()
    if declared_mime not in SUPPORTED_IMAGE_MIMES or ";base64" not in header.lower():
        raise ValueError("只支持 PNG、JPEG 或 WebP 图片")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片数据损坏，请重新选择截图") from exc
    if not raw:
        raise ValueError("图片是空的")
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("图片超过 10MB，请先裁剪到角色列表区域")
    actual_mime = _sniff_image_mime(raw)
    if actual_mime not in SUPPORTED_IMAGE_MIMES:
        raise ValueError("无法识别图片格式，请换一张截图")
    return raw, actual_mime


def normalize_image_for_vision(
    data: bytes,
    mime: str,
    *,
    max_bytes: int = MAX_VISION_BYTES,
    max_long_edge: int = MAX_LONG_EDGE,
) -> tuple[bytes, str]:
    """Downscale/recompress an image for the message-plane payload budget."""

    if len(data) <= max_bytes and mime == "image/jpeg":
        return data, mime

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - host builds include Pillow
        if len(data) <= max_bytes:
            return data, mime
        raise ValueError("当前环境无法压缩这张图片，请裁剪后重试") from exc

    try:
        with Image.open(BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            if max(image.size) > max_long_edge:
                scale = max_long_edge / float(max(image.size))
                image = image.resize(
                    (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                    Image.Resampling.LANCZOS,
                )

            working = image
            for _round in range(5):
                for quality in (86, 78, 70, 62, 54):
                    output = BytesIO()
                    working.save(output, format="JPEG", quality=quality, optimize=True)
                    payload = output.getvalue()
                    if len(payload) <= max_bytes:
                        return payload, "image/jpeg"
                working = working.resize(
                    (max(1, int(working.width * 0.82)), max(1, int(working.height * 0.82))),
                    Image.Resampling.LANCZOS,
                )
    except (OSError, ValueError) as exc:
        raise ValueError("图片无法读取，请换一张清晰截图") from exc

    raise ValueError("图片仍然太大，请只截取角色列表区域后重试")


def prepare_image_for_vision(data_url: str) -> tuple[bytes, str]:
    raw, mime = decode_image_data_url(data_url)
    return normalize_image_for_vision(raw, mime)


__all__ = [
    "decode_image_data_url",
    "normalize_image_for_vision",
    "prepare_image_for_vision",
]
