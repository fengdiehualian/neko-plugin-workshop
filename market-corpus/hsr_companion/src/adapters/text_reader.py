"""Native-pixel OCR coverage independent of game page and character roster."""

from __future__ import annotations

import math
import re
import time
from typing import Any


class ObservationSuperseded(Exception):
    """Cooperative exit between OCR calls; never a recognition failure."""


def check_current(cancelled=None):
    if cancelled is not None and cancelled():
        raise ObservationSuperseded()


def _box_value(box: Any) -> dict[str, Any]:
    keys = ("text", "score", "left", "top", "right", "bottom")
    return dict(box) if isinstance(box, dict) else {key: getattr(box, key, None) for key in keys}


def native_tiles(width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Cover every pixel with overlap; never trigger shared OCR downsampling."""
    columns, rows = math.ceil(width / 1400), math.ceil(height / 1400)
    tiles = []
    for row in range(rows):
        for column in range(columns):
            tiles.append(
                (
                    max(0, round(column * width / columns) - 64),
                    max(0, round(row * height / rows) - 64),
                    min(width, round((column + 1) * width / columns) + 64),
                    min(height, round((row + 1) * height / rows) + 64),
                )
            )
    if len(tiles) > 16:
        raise ValueError("画面比例超出本地文字识别范围")
    return tiles


def _deduplicate(boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepted = []
    for box in sorted(boxes, key=lambda item: -float(item.get("score") or 0)):
        if not str(box.get("text") or "").strip():
            continue
        cx, cy = (box["left"] + box["right"]) / 2, (box["top"] + box["bottom"]) / 2
        if any(
            other["text"] == box["text"]
            and abs((other["left"] + other["right"]) / 2 - cx) < max(12, (box["right"] - box["left"]) * 0.25)
            and abs((other["top"] + other["bottom"]) / 2 - cy) < max(8, (box["bottom"] - box["top"]) * 0.5)
            for other in accepted
        ):
            continue
        accepted.append(box)
    return sorted(accepted, key=lambda item: (item["top"], item["left"]))


def split_label_geometry(image, box):
    """Split a detected wide text row at actual blank columns, not name length.

    Return no split if ink/background does not establish clear whitespace.
    No roster, fixed HUD coordinates or character substring offsets involved.
    Original OCR stays available; these regions are only retry pixel crops.
    """
    left, top = max(0, math.floor(box["left"])), max(0, math.floor(box["top"]))
    right, bottom = min(image.width, math.ceil(box["right"])), min(image.height, math.ceil(box["bottom"]))
    height = bottom - top
    if height < 4 or right - left < height * 8:
        return []
    crop = image.crop((left, top, right, bottom)).convert("RGB")
    pixels = crop.load()
    ink = []
    for x in range(crop.width):
        count = 0
        for y in range(crop.height):
            r, g, b = pixels[x, y]
            if min(r, g, b) >= 180 and max(r, g, b) - min(r, g, b) <= 60:
                count += 1
        ink.append(count >= max(2, round(height * .07)))
    gap = max(12, round(height * .7))
    occupied = [i for i, present in enumerate(ink) if present]
    if not occupied:
        return []
    intervals, start, previous = [], occupied[0], occupied[0]
    for x in occupied[1:]:
        if x - previous > gap:
            intervals.append((start, previous + 1))
            start = x
        previous = x
    intervals.append((start, previous + 1))
    if len(intervals) < 2:
        return []
    return [(max(0, left + a - 8), max(0, top - 6), min(image.width, left + b + 8), min(image.height, bottom + 6))
            for a, b in intervals if b - a >= height * .5]


def read_native_text(
    backend: Any, image: Any, *, catalog: Any = None, cancelled=None, on_progress=None,
    verify_names: bool = False,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Uniform native tiles, then one bounded reread of wide merged OCR rows."""
    started = time.monotonic()
    boxes, failures = [], []
    tiles = native_tiles(*image.size)
    completed = 0
    scene_probe_seconds = None
    calls = []

    def read(sample, stage):
        check_current(cancelled)
        call_started = time.monotonic()
        try:
            return backend.extract_text_with_boxes(sample)
        finally:
            # Includes runtime initialization and shared-backend waiting. Do
            # not mislabel this as pure model inference time.
            calls.append({"stage": stage, "size": list(sample.size),
                          "backend_call_seconds": round(time.monotonic() - call_started, 3)})
    # One reduced full-frame pass reads large UI headings/controls, not names.
    # It covers pages with custom team titles too. Native identity coverage is
    # unchanged and never inherits names from this lower-resolution probe.
    if on_progress is not None:
        check_current(cancelled)
        try:
            probe = image.copy()
            probe.thumbnail((768, 768))
            _, values = read(probe, "scene_probe")
            check_current(cancelled)
            sx, sy = image.width / probe.width, image.height / probe.height
            scaled = []
            for value in values:
                box = _box_value(value)
                scaled.append({**box, **{key: float(box.get(key) or 0) * scale for key, scale in (
                    ("left", sx), ("right", sx), ("top", sy), ("bottom", sy),
                )}})
            on_progress(scaled)
            scene_probe_seconds = round(time.monotonic() - started, 3)
        except ObservationSuperseded:
            raise
        except Exception as exc:
            failures.append("scene_probe_" + type(exc).__name__)
    for left, top, right, bottom in tiles:
        check_current(cancelled)
        if time.monotonic() - started > 22:
            failures.append("ocr_time_budget")
            break
        try:
            _, values = read(image.crop((left, top, right, bottom)), "native_tile")
            check_current(cancelled)
            completed += 1
            for value in values:
                box = _box_value(value)
                boxes.append(
                    {
                        **box,
                        "left": left + float(box.get("left") or 0),
                        "right": left + float(box.get("right") or 0),
                        "top": top + float(box.get("top") or 0),
                        "bottom": top + float(box.get("bottom") or 0),
                    }
                )
        except ObservationSuperseded:
            raise
        except Exception as exc:
            failures.append(type(exc).__name__)
        if on_progress is not None:
            on_progress(_deduplicate(boxes))
    if not completed:
        raise RuntimeError("文字识别未完成：" + ",".join(failures[:3]))
    boxes = _deduplicate(boxes)
    rereads = 0
    icon_rereads = 0
    # Re-read detected text geometry, not hard-coded exploration coordinates.
    # Wide lines commonly merge several character nameplates with their fields.
    if time.monotonic() - started < 18:
        check_current(cancelled)
        from PIL import Image, ImageOps

        parts = []
        icon_parts = set()
        color_parts = set()
        if catalog is not None:
            from ..detectors.names import read_name_label

            # A one-letter icon/shortcut may merge with a registered name. Do
            # not strip it into a fact: re-read the remaining original pixels.
            # This is shared by all names, not an alias for one collab role.
            for box in boxes:
                raw = str(box["text"]).strip()
                if not re.match(r"^[A-Za-z]\s*[\u3400-\u9fff]", raw):
                    continue
                records, _ = read_name_label(raw[1:], catalog)
                if not records:
                    continue
                width = box["right"] - box["left"]
                h = max(1, box["bottom"] - box["top"])
                # Chinese glyphs are approximately twice a Latin/icon cell;
                # only the retry crop uses this estimate. Accepted coordinates
                # still come from its fresh OCR detector.
                cut = width / max(3, 2 * len(raw[1:].strip()) + 1)
                part = (
                    max(0, math.floor(box["left"] + cut)),
                    max(0, math.floor(box["top"] - h * 0.1)),
                    min(image.width, math.ceil(box["right"] + 4)),
                    min(image.height, math.ceil(box["bottom"] + h * 0.1)),
                )
                if part[2] > part[0] and part[3] > part[1] and len(parts) < 4:
                    parts.append(part)
                    icon_parts.add(part)
        for box in sorted(boxes, key=lambda value: (len(str(value["text"])) > 20, -float(value.get("score") or 0))):
            # A complete high-confidence registered name already has native
            # evidence. Spend the extra read on unresolved/merged text instead.
            # Icon-prefixed and low-score labels are still retried above/below.
            if catalog is not None and float(box.get("score") or 0) >= .95:
                if catalog.resolve_character(str(box["text"])) is not None:
                    continue
            if catalog is not None:
                from ..data import normalize_lookup_text
                from ..detectors.layout import PAGE_RULES
                ui_terms = {normalize_lookup_text(term) for rule in PAGE_RULES for term in (*rule["strong"], *rule["anchors"])}
                if normalize_lookup_text(box["text"]) in ui_terms:
                    continue  # Known controls do not need a name retry.
            segments = split_label_geometry(image, box)
            if segments:
                for part in segments:
                    if len(parts) < 12:
                        parts.append(part)
                        color_parts.add(part)
                continue
            h = max(1, box["bottom"] - box["top"])
            wide = box["right"] - box["left"] >= max(300, h * 12)
            short_cjk = 1 <= len(str(box["text"])) <= 20 and re.search(r"[\u3400-\u9fff]", str(box["text"]))
            if not wide and not short_cjk:
                continue
            top, bottom = max(0, int(box["top"] - h * 0.3)), min(image.height, math.ceil(box["bottom"] + h * 0.3))
            span = max(240, min(700, round(h * 10)))
            for left in range(
                max(0, int(box["left"] - 12)), min(image.width, math.ceil(box["right"])), max(1, span - 64)
            ):
                right = min(image.width, math.ceil(box["right"] + 12), left + span)
                if right - left > 32 and len(parts) < 12:
                    parts.append((left, top, right, bottom))
        if parts:
            scale, padding = 2, 24
            width = max((right - left) * scale for left, _, right, _ in parts) + padding * 2
            height = sum((bottom - top) * scale + padding for _, top, _, bottom in parts) + padding
            # Keep all samples native or larger, never let an oversized atlas
            # shrink back down inside the shared engine.
            while height > 1550 and parts:
                parts.pop()
                height = sum((bottom - top) * scale + padding for _, top, _, bottom in parts) + padding
            if parts:
                atlas = Image.new("RGB", (width, height), "black")
                positions = []
                y = padding
                for left, top, right, bottom in parts:
                    crop = image.crop((left, top, right, bottom)).resize(
                        ((right - left) * scale, (bottom - top) * scale), Image.Resampling.LANCZOS
                    )
                    # Additional high-contrast reading of detected text, not a
                    # replacement of the native colour sample. The original
                    # tile remains authoritative when thresholding loses ink.
                    if (left, top, right, bottom) not in icon_parts | color_parts:
                        crop = ImageOps.grayscale(crop).point(lambda pixel: 255 if pixel > 175 else 0).convert("RGB")
                    atlas.paste(crop, (padding, y))
                    positions.append((left, top, right, bottom, y))
                    y += crop.height + padding
                try:
                    check_current(cancelled)
                    _, values = read(atlas, "unresolved_lines")
                    check_current(cancelled)
                    rereads = len(parts)
                    icon_rereads = sum(part in icon_parts for part in parts)
                    for value in values:
                        box = _box_value(value)
                        cy = (float(box["top"]) + float(box["bottom"])) / 2
                        for left, top, right, bottom, atlas_y in positions:
                            if atlas_y <= cy < atlas_y + (bottom - top) * scale:
                                boxes.append(
                                    {
                                        **box,
                                        "left": max(left, left + (float(box["left"]) - padding) / scale),
                                        "right": min(right, left + (float(box["right"]) - padding) / scale),
                                        "top": max(top, top + (float(box["top"]) - atlas_y) / scale),
                                        "bottom": min(bottom, top + (float(box["bottom"]) - atlas_y) / scale),
                                    }
                                )
                                break
                except ObservationSuperseded:
                    raise
                except Exception as exc:
                    failures.append("reread_" + type(exc).__name__)
    check_current(cancelled)
    boxes = _deduplicate(boxes)
    verification = {"attempted": 0, "confirmed": 0}
    if verify_names and catalog is not None:
        boxes, verification = verify_name_regions(backend, image, boxes, catalog, cancelled=cancelled, read=read)
    return (
        "\n".join(str(box["text"]) for box in boxes),
        boxes,
        {
            "strategy": "native_overlapping_tiles_and_detected_lines",
            "tiles_total": len(tiles),
            "tiles_completed": completed,
            "full_frame_covered": completed == len(tiles),
            "line_rereads": rereads,
            "icon_name_rereads": icon_rereads,
            "scene_probe_seconds": scene_probe_seconds,
            "backend_calls": calls,
            "name_verification": verification,
            "processing_seconds": round(time.monotonic() - started, 3),
            "errors": failures[:5],
        },
    )


def verify_name_regions(backend, image, boxes, catalog, *, cancelled=None, read=None):
    """Re-read canonical label pixels, not a second expensive full screenshot.

    Both reads must agree on a complete canonical name at high confidence.
    This validates printed text only, not 3D identity or scene continuity;
    live_refresh still checks a later independent capture before handoff.
    Merged rows, aliases, prose and uncertain readings keep the multi-frame gate.
    """
    from PIL import Image

    from ..data import normalize_lookup_text
    from ..detectors.names import label_fields_only, read_name_label

    candidates = []
    for box in boxes:
        records, _ = read_name_label(str(box.get("text") or ""), catalog)
        record = records[0] if len(records) == 1 else None
        if (record and float(box.get("score") or 0) >= .90
                and normalize_lookup_text(record["name"]) in normalize_lookup_text(box["text"])):
            left, top = max(0, math.floor(box["left"] - 8)), max(0, math.floor(box["top"] - 6))
            right, bottom = min(image.width, math.ceil(box["right"] + 8)), min(image.height, math.ceil(box["bottom"] + 6))
            if right > left and bottom > top:
                candidates.append((box, record, (left, top, right, bottom)))
    stats = {"attempted": len(candidates), "confirmed": 0, "batches": 0, "seconds": 0.0,
             "rejected": 0}
    if not candidates:
        return boxes, stats
    started = time.monotonic()
    # Bounded atlases retain native pixels or enlarge them, including English
    # collab names. A long label must never trigger the backend's downsampling.
    pending = list(candidates[:32])
    while pending:
        check_current(cancelled)
        batch, y = [], 24
        while pending:
            candidate = pending[0]
            left, top, right, bottom = candidate[2]
            scale = min(2.0, 1400 / (right - left))
            if scale < 1:
                pending.pop(0)
                continue
            h = math.ceil((bottom - top) * scale)
            if batch and y + h + 24 > 1500:
                break
            pending.pop(0)
            batch.append((*candidate, scale, y, h))
            y += h + 24
        if not batch:
            break
        atlas = Image.new("RGB", (max(math.ceil((b[2][2] - b[2][0]) * b[3]) for b in batch) + 48, y), "black")
        for _, _, bounds, scale, atlas_y, h in batch:
            crop = image.crop(bounds)
            atlas.paste(crop.resize((math.ceil(crop.width * scale), h), Image.Resampling.LANCZOS), (24, atlas_y))
        try:
            _, values = read(atlas, "name_verification") if read else backend.extract_text_with_boxes(atlas)
            check_current(cancelled)
        except ObservationSuperseded:
            raise
        except Exception:
            continue  # No corroboration: ordinary multi-frame confirmation.
        stats["batches"] += 1
        for original, record, _, _, atlas_y, h in batch:
            lines = [_box_value(value) for value in values
                     if atlas_y <= (float(_box_value(value).get("top") or 0) + float(_box_value(value).get("bottom") or 0)) / 2 < atlas_y + h]
            # One complete name can be accompanied by a separate level/path
            # line. Never join split fragments to manufacture a name.
            lines = [line for line in lines if str(line.get("text") or "").strip()]
            name_lines = []
            fields_valid = True
            for line in lines:
                reread, _ = read_name_label(str(line.get("text") or ""), catalog)
                if (len(reread) == 1 and reread[0]["id"] == record["id"]
                        and float(line.get("score") or 0) >= .90
                        and normalize_lookup_text(record["name"]) in normalize_lookup_text(line["text"])):
                    name_lines.append(line)
                elif not label_fields_only(line["text"]):
                    fields_valid = False
            if len(name_lines) == 1 and fields_valid:
                original["name_validation"] = {
                    "entity_id": record["id"], "method": "native_and_enlarged_label_agree",
                    "scores": [float(original["score"]), float(name_lines[0]["score"])],
                }
                stats["confirmed"] += 1
            else:
                stats["rejected"] += 1
    stats["seconds"] = round(time.monotonic() - started, 3)
    return boxes, stats
