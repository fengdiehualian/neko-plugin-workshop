"""Re-read relic numbers without the adjacent green upgrade-count badge."""
from __future__ import annotations

import math
import re
import time
from statistics import median

from ..detectors.relic_geometry import detail_column, unique_readings, upgrade_label
from ..runtime.screen_facts import (
    _adjacent_value,
    _boxes,
    _relic_panel,
    relic_stat_label,
    relic_stat_rows,
    relic_stat_text,
    safe_text,
)
from .text_reader import _box_value, check_current

NUMBER = re.compile(r"\+?\d[\d,]*(?:\.\d+)?%?")


def atlas_fragment_bounds(box, placement):
    """Bound detector unclip to its OWN pasted row, then map only intersection.

    Detector boxes may include a few pixels of the neutral canvas. Text with
    its center outside the source, large overhang, weak score or non-finite
    geometry cannot become a source-frame numeric proof.
    """
    px, py, sw, sh = placement[:4]
    try:
        left, top, right, bottom = (float(box[k]) for k in ("left", "top", "right", "bottom"))
        score = float(box.get("score") or 0)
    except (KeyError, ValueError, TypeError):
        return None
    if not all(math.isfinite(v) for v in (left, top, right, bottom, score)) or score < .9:
        return None
    if left >= right or top >= bottom or not (px <= (left + right) / 2 <= px + sw
                                              and py <= (top + bottom) / 2 <= py + sh):
        return None
    margin = max(2., sh * .12)
    if max(px - left, py - top, right - px - sw, bottom - py - sh) > margin:
        return None
    clipped = (max(px, left), max(py, top), min(px + sw, right), min(py + sh, bottom))
    if ((clipped[2] - clipped[0]) * (clipped[3] - clipped[1])
            < .8 * (right - left) * (bottom - top)):
        return None
    return clipped


def badge_bounds(image):
    """A filled mint circular control, not any green text or a guessed suffix."""
    from PIL import Image
    pixels = list(image.getdata())
    mask = Image.new("L", image.size)
    mask.putdata([255 if g > 110 and g - r > 30 and g - b > 15 else 0 for r, g, b in pixels])
    bounds = mask.getbbox()
    if not bounds:
        return None
    left, top, right, bottom = bounds
    w, h = right - left, bottom - top
    if min(w, h) < 8 or not .72 <= w / h <= 1.3 or h < image.height * .4:
        return None
    fill = sum(1 for p in mask.crop(bounds).getdata() if p) / (w * h)
    # Leave actual number pixels before the control. A completely green value
    # or an isolated digit isn't permission to trim its last character.
    return bounds if fill >= .55 and left >= image.height * .3 else None


def read_relic_table(image, boxes, *, page, backend, cancelled=None):
    """Read the bounded stat table once, masking count controls before OCR.

    A missed/low-confidence label cannot be recovered by re-reading numbers
    alone. This crop covers the entire semantic table, including its labels.
    """
    if page.get("page_type") != "relic_detail":
        return boxes, {"accepted": False, "backend_calls": []}
    from PIL import ImageDraw
    materialized = _boxes(boxes, image.width, image.height)
    panel = _relic_panel(materialized)
    if not panel:
        return boxes, {"accepted": False, "backend_calls": []}
    part, end = panel
    h = part["bottom"] - part["top"]
    column = detail_column(materialized, part, end)
    labels = [b for b in relic_stat_rows(column) if abs(b["left"] - part["left"]) <= h * 2]
    values = [b if relic_stat_text(b) != relic_stat_label(b) else _adjacent_value(b, column, numeric_row=True) for b in labels]
    values = [b for b in values if b]
    if not values:
        return boxes, {"accepted": False, "backend_calls": []}
    # A main-row icon can merge into an unusually wide label box. Locate the
    # actual text column by SAME-frame majority, never fixed screen pixels.
    consensus = median(b["left"] for b in labels) if labels else part["left"]
    aligned = [b for b in labels if abs(b["left"] - consensus) <= h * .5]
    avoid_icon = (len(aligned) >= 3 and len(aligned) > len(labels) / 2
                  and any(b["left"] < consensus - h * .75 for b in labels))
    label_left = min(b["left"] for b in aligned) - h * .1 if avoid_icon else part["left"] - h * .25
    bounds = (max(0, int(label_left)), int(part["bottom"]),
              min(image.width, int(max(b["right"] for b in values) + h * .5)), int(end["top"]))
    crop = image.crop(bounds)
    # Separate green components by row bands. Only filled circular controls
    # in the numeric half of this known table are masked; no suffix guessing.
    active = []
    for y in range(crop.height):
        if any(g > 110 and g - r > 30 and g - b > 15 for r, g, b in crop.crop((0, y, crop.width, y + 1)).getdata()):
            active.append(y)
    bands = []
    for y in active:
        if not bands or y > bands[-1][1] + 1:
            bands.append([y, y])
        else:
            bands[-1][1] = y
    masked = 0
    draw = ImageDraw.Draw(crop)
    for top, bottom in bands:
        band = crop.crop((0, max(0, top - 2), crop.width, min(crop.height, bottom + 3)))
        badge = badge_bounds(band)
        if badge and badge[0] > crop.width * .5:
            left, dy, right, db = badge
            draw.rectangle((left - 1, top - 2 + dy - 1, right + 1, top - 2 + db + 1), fill=(25, 32, 46))
            masked += 1
    check_current(cancelled)
    started = time.monotonic()
    try:
        _, raw = backend.extract_text_with_boxes(crop)
    except (RuntimeError, ValueError, TypeError):
        return boxes, {"accepted": False, "masked_badges": masked, "backend_calls": []}
    check_current(cancelled)
    readings = [_box_value(b) for b in raw]
    shifted = [{**b, "text": safe_text(b["text"]), "left": b["left"] + bounds[0], "right": b["right"] + bounds[0],
                "top": b["top"] + bounds[1], "bottom": b["bottom"] + bounds[1]} for b in readings]
    valid = _boxes(shifted, image.width, image.height)
    accepted = len(unique_readings([b for b in valid if relic_stat_label(b)], key=relic_stat_text)) >= len(labels)
    details = {"accepted": accepted, "masked_badges": masked, "crop": list(bounds),
               "crop_avoids_label_icon": avoid_icon, "label_column_consensus": consensus, "backend_calls": [{
        "stage": "relic_table", "size": list(crop.size), "backend_call_seconds": round(time.monotonic() - started, 3)}]}
    if not accepted:
        return boxes, details
    for b in shifted:
        b["numeric_recheck"] = {"method": "same_frame_badge_masked_table_ocr", "confirmed": True, "crop": list(bounds)}
    # This pass owns the stat table, not independent same-frame object/grade
    # anchors. Keep grade reads even if this crop misses them. Conflicting
    # values remain separate and are rejected by unique binding downstream.
    def replaced_label(old):
        label = relic_stat_label(old)
        return bool(label) and any(relic_stat_label(new) == label and new["score"] >= .9
            and abs((new["top"] + new["bottom"] - old["top"] - old["bottom"]) / 2) <= h * .5
            and new["right"] - new["left"] < (old["right"] - old["left"]) * .8 for new in valid)

    retained = [b for b in boxes if upgrade_label(b["text"]) or not replaced_label(b) and not (
        bounds[0] <= b["left"] and b["right"] <= bounds[2]
        and bounds[1] <= b["top"] and b["bottom"] <= bounds[3])]
    return unique_readings(retained + shifted), details


def refine_relic_values(image, boxes, *, page, backend, cancelled=None, table_bounds=None):
    """At most one small OCR atlas call; all readings belong to this capture."""
    if page.get("page_type") != "relic_detail":
        return boxes, {"candidates": 0, "confirmed": 0, "backend_calls": []}
    from PIL import Image
    materialized = _boxes(boxes, image.width, image.height)
    panel = _relic_panel(materialized)
    if panel is None:
        return boxes, {"candidates": 0, "confirmed": 0, "backend_calls": []}
    part, end = panel
    column = detail_column(materialized, part, end)
    candidates = []
    for row in relic_stat_rows(column):
        label = relic_stat_label(row)
        if not label:
            continue
        inline = relic_stat_text(row) != label
        value = row if inline else _adjacent_value(row, column, numeric_row=True)
        value_text = relic_stat_text(row)[len(label):].lstrip(":： ") if inline else value["text"] if value else ""
        missing_number = not NUMBER.fullmatch(value_text)
        insert = value is None
        if insert and not table_bounds:
            continue
        if missing_number and table_bounds and not inline:
            rh = row["bottom"] - row["top"]
            bounds = (int(max(row["right"] + rh, table_bounds[0] + (table_bounds[2] - table_bounds[0]) * .55)),
                      max(0, int(row["top"] - rh * .15)), int(table_bounds[2]), min(image.height, int(row["bottom"] + rh * .15)))
            if bounds[0] >= bounds[2]:
                continue
            if insert:
                value = {"left": bounds[0], "top": bounds[1], "right": bounds[2], "bottom": bounds[3],
                         "score": row["score"], "text": "数值待复核"}
        else:
            bounds = (int(value["left"]), max(0, int(value["top"]) - 2),
                      min(image.width, int(value["right"]) + 2), min(image.height, int(value["bottom"]) + 2))
        crop = image.crop(bounds)
        badge = badge_bounds(crop)
        if badge is None and not missing_number:
            continue
        trim = badge[0] - 2 if badge else crop.width
        if trim < 4:
            continue
        numeric_crop = crop.crop((0, 0, trim, crop.height))
        # A malformed reading is not permission to lose an observed digit.
        # Only an actually detected/masked upgrade badge permits suffix loss.
        # '%' terminates the numeric literal; an OCR control merged AFTER it
        # is not a digit of the stat. Still require the independently read
        # literal below; this check never trims or invents an output value.
        protected_digits = "".join(re.findall(r"\d", value_text.partition("%")[0])) if missing_number and badge is None else ""
        candidates.append((value, numeric_crop, bounds, label if inline else "", insert,
                           row["bottom"] - row["top"], protected_digits))
    if not candidates or len(candidates) > 5:
        return boxes, {"candidates": len(candidates), "confirmed": 0, "backend_calls": []}
    # Uniform row height with generous separation avoids introducing one OCR
    # request per stat. Original coordinates remain the evidence geometry.
    atlas_width = min(1024, max(320, max(round(crop.width * 48 / crop.height) + 32 for _, crop, _, _, _, _, _ in candidates)))
    atlas = Image.new("RGB", (atlas_width, len(candidates) * 88), (25, 32, 46))
    placements = []
    for index, (_, crop, bounds, _, _, _, _) in enumerate(candidates):
        scaled = crop.copy()
        scaled.thumbnail((atlas_width - 32, 56))
        # Small source text benefits from a bounded enlargement.
        if scaled.height < 48 and scaled.width * 48 / scaled.height <= atlas_width - 32:
            scaled = scaled.resize((max(1, round(scaled.width * 48 / scaled.height)), 48))
        atlas.paste(scaled, (16, index * 88 + 16))
        placements.append((16, index * 88 + 16, scaled.width, scaled.height, crop.width, crop.height, bounds))
    check_current(cancelled)
    started = time.monotonic()
    error = ""
    try:
        _, raw_values = backend.extract_text_with_boxes(atlas)
    except (RuntimeError, ValueError, TypeError) as exc:
        raw_values = []
        error = type(exc).__name__
    seconds = time.monotonic() - started
    check_current(cancelled)
    readings = [_box_value(b) for b in raw_values]
    replacements, additions, confirmed = {}, [], 0
    for index, (original, _, bounds, inline_label, insert, row_height, protected_digits) in enumerate(candidates):
        fragments = sorted([b for b in readings
                            if index * 88 + 8 <= (b["top"] + b["bottom"]) / 2 <= index * 88 + 80], key=lambda b: b["left"])
        literal = safe_text("".join(str(b["text"]).strip() for b in fragments))
        text = relic_stat_text({"text": literal}) if inline_label else literal
        if inline_label and text.startswith(inline_label):
            text = text[len(inline_label):].lstrip(":： ")
        px, py, sw, sh, cw, ch, _ = placements[index]
        source_bounds = [atlas_fragment_bounds(b, placements[index]) for b in fragments]
        valid = bool(NUMBER.fullmatch(text)) and bool(fragments) and all(source_bounds)
        # Containment proves where a fragment came from, not that it contains
        # the WHOLE numeral. A lower-half '6' from a full-height '16' must not
        # create its own tight proof and then certify itself on that half.
        glyph_complete = bool(source_bounds) and all(b and (b[3] - b[1]) * ch / sh >= row_height * .6
                                                     for b in source_bounds)
        digits_preserved = not protected_digits or protected_digits in "".join(re.findall(r"\d", text))
        valid = valid and glyph_complete and digits_preserved
        if valid and len(source_bounds) > 1:
            centers = [(b[1] + b[3]) / 2 for b in source_bounds]
            valid = max(centers) - min(centers) <= sh * .4 and all(
                b[0] - a[2] <= sh * .6 and a[2] - b[0] <= .2 * min(a[2] - a[0], b[2] - b[0])
                for a, b in zip(source_bounds, source_bounds[1:]))
        geometry = {}
        if valid:
            geometry = {
                "left": bounds[0] + (min(b[0] for b in source_bounds) - px) * cw / sw,
                "right": bounds[0] + (max(b[2] for b in source_bounds) - px) * cw / sw,
                "top": bounds[1] + (min(b[1] for b in source_bounds) - py) * ch / sh,
                "bottom": bounds[1] + (max(b[3] for b in source_bounds) - py) * ch / sh,
            }
        confirmed += int(valid)
        replacement = {
            **geometry,
            "text": literal if valid else (inline_label + ":" if inline_label else "") + "数值待复核",
            "score": min([original["score"], *(float(b["score"]) for b in fragments)]) if valid else original["score"],
            "numeric_recheck": {"method": "same_frame_badge_separated_ocr", "confirmed": valid,
                                "row_glyph_complete": glyph_complete,
                                "observed_digits_preserved": digits_preserved,
                                "original_text": original["text"], "search_crop": list(bounds),
                                "crop": [geometry[k] for k in ("left", "top", "right", "bottom")] if valid else list(bounds)},
        }
        if insert:
            additions.append({**original, **replacement})
        else:
            replacements[(original["left"], original["top"], original["text"])] = replacement
    updated = []
    for raw in boxes:
        item = dict(raw)
        replacement = replacements.get((item.get("left"), item.get("top"), safe_text(item.get("text"))))
        if replacement:
            item.update(replacement)
        updated.append(item)
    return updated + additions, {"candidates": len(candidates), "confirmed": confirmed, "error": error,
                     "backend_calls": [{"stage": "relic_value_atlas", "size": list(atlas.size),
                                        "backend_call_seconds": round(seconds, 3)}]}
