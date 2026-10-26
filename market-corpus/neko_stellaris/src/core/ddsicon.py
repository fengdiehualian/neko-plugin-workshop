# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Write the advisor icon as an uncompressed BGRA DDS.

The game's advisor icons are *not* DXT-compressed: every file in
``gfx/interface/icons/advisor_vo`` is exactly 3492 bytes = 128-byte header +
29*29*4, with ``pfFlags = 0x41`` (ALPHAPIXELS|RGB), ``rgbBits = 32``, masks
``00FF0000/0000FF00/000000FF/FF000000`` and no mipmaps. Writing that same shape
by hand avoids a DDS encoder dependency and guarantees the game sees a format it
already ships.

The artwork is a small procedural "neko" mark: a rounded head, two ears, two
eyes and a highlight, drawn with 4x4 supersampling so the 29px edges are not
jagged. It is deliberately simple — the icon renders at ~29 device pixels in the
voice-selection list, where anything finer is invisible.
"""

from __future__ import annotations

import struct
from pathlib import Path

DEFAULT_SIZE = 29
_SUPERSAMPLE = 4

# N.E.K.O palette: a cool cyan-to-violet head with warm highlights.
_HEAD_TOP = (0.55, 0.86, 0.98)     # light cyan
_HEAD_BOTTOM = (0.42, 0.55, 0.92)  # soft violet-blue
_EAR_INNER = (0.98, 0.72, 0.82)    # pink
_EYE = (0.16, 0.19, 0.31)
_HIGHLIGHT = (1.0, 1.0, 1.0)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _inside_circle(x: float, y: float, cx: float, cy: float, r: float) -> bool:
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _inside_triangle(px: float, py: float,
                     ax: float, ay: float, bx: float, by: float,
                     cx: float, cy: float) -> bool:
    def sign(x1, y1, x2, y2, x3, y3):
        return (x1 - x3) * (y2 - y3) - (x2 - x3) * (y1 - y3)

    d1 = sign(px, py, ax, ay, bx, by)
    d2 = sign(px, py, bx, by, cx, cy)
    d3 = sign(px, py, cx, cy, ax, ay)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _sample(x: float, y: float, size: int) -> tuple[float, float, float, float]:
    """Return premultiplied-free (r, g, b, a) for one sample point, 0..1."""
    unit = size / 29.0
    cx = 14.5 * unit
    head_cy = 16.5 * unit
    head_r = 10.2 * unit

    # Ears first: they must be filled *before* the head so the head circle can
    # round off their bases. Painting the head first would erase the ear tips.
    for side in (-1, 1):
        tipx = cx + side * 6.4 * unit
        tipy = 5.0 * unit
        base_y = 12.5 * unit
        base_in = cx + side * 1.9 * unit
        base_out = cx + side * 9.2 * unit
        if _inside_triangle(x, y, tipx, tipy, base_in, base_y, base_out, base_y):
            inner = _inside_triangle(
                x, y,
                tipx + side * -1.2 * unit, tipy + 2.6 * unit,
                base_in + side * 1.5 * unit, base_y - 1.2 * unit,
                base_out - side * 1.4 * unit, base_y - 1.2 * unit,
            )
            base = _EAR_INNER if inner else (
                _lerp(_HEAD_TOP[0], _HEAD_BOTTOM[0], 0.1),
                _lerp(_HEAD_TOP[1], _HEAD_BOTTOM[1], 0.1),
                _lerp(_HEAD_TOP[2], _HEAD_BOTTOM[2], 0.1),
            )
            return base[0], base[1], base[2], 1.0

    if _inside_circle(x, y, cx, head_cy, head_r):
        t = (y - (head_cy - head_r)) / (2 * head_r)
        t = min(1.0, max(0.0, t))
        col = (_lerp(_HEAD_TOP[0], _HEAD_BOTTOM[0], t),
               _lerp(_HEAD_TOP[1], _HEAD_BOTTOM[1], t),
               _lerp(_HEAD_TOP[2], _HEAD_BOTTOM[2], t))
        # Eyes sit below the vertical centre so the face reads as a face.
        for side in (-1, 1):
            if _inside_circle(x, y, cx + side * 3.5 * unit, head_cy + 0.4 * unit,
                              1.75 * unit):
                return _EYE[0], _EYE[1], _EYE[2], 1.0
            if _inside_circle(x, y, cx + side * 3.5 * unit - 0.5 * unit,
                              head_cy + 0.4 * unit, 0.55 * unit):
                return _HIGHLIGHT[0], _HIGHLIGHT[1], _HIGHLIGHT[2], 1.0
        # A small muzzle dot, offset low, keeps the silhouette from reading as a
        # plain smiley at 29px.
        if _inside_circle(x, y, cx, head_cy + 4.4 * unit, 0.9 * unit):
            return _EAR_INNER[0], _EAR_INNER[1], _EAR_INNER[2], 1.0
        return col[0], col[1], col[2], 1.0

    return 0.0, 0.0, 0.0, 0.0


def render_bgra(size: int = DEFAULT_SIZE) -> bytes:
    """Render the icon and return raw BGRA rows (top-down), 4 bytes per pixel."""
    step = 1.0 / _SUPERSAMPLE
    offset = step / 2.0
    out = bytearray(size * size * 4)
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0.0
            for sy in range(_SUPERSAMPLE):
                for sx in range(_SUPERSAMPLE):
                    sr, sg, sb, sa = _sample(px + offset + sx * step,
                                             py + offset + sy * step, size)
                    r += sr * sa
                    g += sg * sa
                    b += sb * sa
                    a += sa
            n = _SUPERSAMPLE * _SUPERSAMPLE
            alpha = a / n
            if alpha > 0:
                # DDS expects straight (non-premultiplied) alpha.
                r, g, b = r / a, g / a, b / a
            idx = (py * size + px) * 4
            out[idx + 0] = int(min(255, max(0, round(b * 255))))
            out[idx + 1] = int(min(255, max(0, round(g * 255))))
            out[idx + 2] = int(min(255, max(0, round(r * 255))))
            out[idx + 3] = int(min(255, max(0, round(alpha * 255))))
    return bytes(out)


def _dds_header(size: int) -> bytes:
    """The 128-byte header vanilla's advisor icons use.

    Uncompressed BGRA, no mipmaps, masks 00FF0000/0000FF00/000000FF/FF000000 --
    byte-for-byte the shape of every file in ``gfx/interface/icons/advisor_vo``.
    """
    header = bytearray(128)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)                 # dwSize
    struct.pack_into("<I", header, 8, 0x0000100F)          # CAPS|HEIGHT|WIDTH|PIXELFORMAT|PITCH
    struct.pack_into("<I", header, 12, size)               # dwHeight
    struct.pack_into("<I", header, 16, size)               # dwWidth
    struct.pack_into("<I", header, 20, size * 4)           # dwPitchOrLinearSize
    struct.pack_into("<I", header, 24, 0)                  # dwDepth
    struct.pack_into("<I", header, 28, 0)                  # dwMipMapCount
    struct.pack_into("<I", header, 76, 32)                 # ddspf.dwSize
    struct.pack_into("<I", header, 80, 0x41)               # ALPHAPIXELS | RGB
    struct.pack_into("<I", header, 84, 0)                  # no FourCC
    struct.pack_into("<I", header, 88, 32)                 # dwRGBBitCount
    struct.pack_into("<I", header, 92, 0x00FF0000)         # R
    struct.pack_into("<I", header, 96, 0x0000FF00)         # G
    struct.pack_into("<I", header, 100, 0x000000FF)        # B
    struct.pack_into("<I", header, 104, 0xFF000000)        # A
    struct.pack_into("<I", header, 108, 0x00001000)        # DDSCAPS_TEXTURE
    return bytes(header)


def build_dds(size: int = DEFAULT_SIZE) -> bytes:
    """Complete uncompressed BGRA DDS payload matching the vanilla advisor icons."""
    if size <= 0 or size > 512:
        raise ValueError(f"unsupported icon size {size}")
    return _dds_header(size) + render_bgra(size)


def write_dds(path: str | Path, size: int = DEFAULT_SIZE) -> int:
    """Write the icon and return its size in bytes."""
    payload = build_dds(size)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return len(payload)


# --------------------------------------------------------------------------- #
# Icon from source art
# --------------------------------------------------------------------------- #

# Crop of the source illustration, as fractions of the shorter side:
# (centre x, centre y, side). Tuned by rendering a parameter sweep at the real
# 29 px and choosing what stays readable there, not by what looks good at full
# size -- at 29 px only three things survive: the dark hair silhouette, the pale
# face, and the blue accents. Tighter crops looked better as images and worse as
# icons, because zooming in far enough to fill the frame with the face also cuts
# the cat ears, which are the character's only unambiguous identifier.
ICON_CROP_CENTRE = (0.47, 0.48)
ICON_CROP_SIDE = 0.96
# A 29 px icon on a dark UI loses contrast; a little push keeps the face from
# washing into the hair.
ICON_CONTRAST = 1.12
ICON_SATURATION = 1.15
ICON_SHARPEN = 1.35

# The cover is a different job from the icon and gets its own frame. It is viewed
# large, so it can afford the whole illustration -- and it should, because the
# icon's 29 px frame shaves the bottom of the art (the hoodie and the little bear)
# and offsets what is left. At 512 px neither of those buys any legibility; they
# just look like the picture was cropped by accident. (0.5, 0.5, 1.0) is the whole
# square, centred: on a square source it crops nothing, and on a non-square one it
# trims only the long axis, which a square cover has to do regardless.
THUMBNAIL_CROP_CENTRE = (0.5, 0.5)
THUMBNAIL_CROP_SIDE = 1.0


def _square_crop(image, centre: tuple[float, float], side_fraction: float):
    width, height = image.size
    side = int(min(width, height) * side_fraction)
    cx, cy = int(width * centre[0]), int(height * centre[1])
    left = max(0, min(cx - side // 2, width - side))
    top = max(0, min(cy - side // 2, height - side))
    return image.crop((left, top, left + side, top + side))


def render_icon_from_image(source: str | Path, size: int = DEFAULT_SIZE,
                           *, crop: tuple[float, float, float] | None = None) -> bytes:
    """Build an uncompressed BGRA DDS from a source illustration.

    Pillow does the decode and the resample, so unlike :func:`build_dds` this
    needs a third-party import. It is imported here rather than at module scope
    so a host without Pillow can still use the procedural mark instead of failing
    to import the module at all.
    """
    from PIL import Image, ImageEnhance

    cx, cy, side = crop or (*ICON_CROP_CENTRE, ICON_CROP_SIDE)
    with Image.open(Path(source)) as handle:
        image = handle.convert("RGB")
    square = _square_crop(image, (cx, cy), side)

    # Enhance at full resolution, then downscale: sharpening after the resample
    # amplifies whatever aliasing the resample introduced.
    square = ImageEnhance.Contrast(square).enhance(ICON_CONTRAST)
    square = ImageEnhance.Color(square).enhance(ICON_SATURATION)
    square = ImageEnhance.Sharpness(square).enhance(ICON_SHARPEN)
    small = square.resize((size, size), Image.LANCZOS)

    rgba = small.convert("RGBA")
    pixels = rgba.tobytes()  # RGBA, row-major
    out = bytearray(size * size * 4)
    for i in range(size * size):
        r, g, b, a = pixels[i * 4], pixels[i * 4 + 1], pixels[i * 4 + 2], pixels[i * 4 + 3]
        out[i * 4 + 0] = b
        out[i * 4 + 1] = g
        out[i * 4 + 2] = r
        out[i * 4 + 3] = a
    return _dds_header(size) + bytes(out)


def render_thumbnail_png(source: str | Path, size: int = 512,
                         *, crop: tuple[float, float, float] | None = None) -> bytes:
    """A square PNG for ``thumbnail.png``, which is what the launcher and the
    Workshop listing show.

    Framed to show the whole illustration instead of reusing the icon's crop --
    see ``THUMBNAIL_CROP_*`` for why those are two different frames.
    """
    import io

    from PIL import Image, ImageEnhance

    cx, cy, side = crop or (*THUMBNAIL_CROP_CENTRE, THUMBNAIL_CROP_SIDE)
    with Image.open(Path(source)) as handle:
        image = handle.convert("RGB")
    square = _square_crop(image, (cx, cy), side)
    square = ImageEnhance.Contrast(square).enhance(1.04)
    square = ImageEnhance.Color(square).enhance(1.06)
    square = square.resize((size, size), Image.LANCZOS)
    buffer = io.BytesIO()
    square.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def write_icon_from_image(source: str | Path, path: str | Path,
                          size: int = DEFAULT_SIZE) -> int:
    payload = render_icon_from_image(source, size)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return len(payload)
