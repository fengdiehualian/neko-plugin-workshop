"""Refine detected navigation lines on native pixels, without guessing values."""
from __future__ import annotations

import math
import re

from ..detectors.exploration import location_headings, navigation_boxes, task_stacks
from .text_reader import check_current
from .text_verification import text_key


def recover_navigation_controls(backend, image, boxes, *, cancelled=None):
    """Acquire a weak navigation PREFIX; never upgrade its destination literal.

    A merged detector row's average can be weak even with a legible control
    prefix. Weak text only selects a crop. Two high-score rec-only scales must
    independently read the prefix on this frame before it becomes a control.
    """
    from PIL import Image

    reader = getattr(backend, "recognize_line", None)
    meta = {"attempted": 0, "confirmed": 0}
    if not callable(reader):
        return boxes, meta
    result = []
    for box in boxes:
        match = re.match(r"^(请前往|距离目标|追踪目标|开启导航)\S", box["text"])
        h = box["bottom"] - box["top"]
        candidate = (match and .5 <= box["score"] < .85 and meta["attempted"] < 3
                     and 4 <= h <= 128 and 0 <= box["left"] < box["right"] <= image.width * .55
                     and image.height * .08 <= box["top"] < box["bottom"] <= image.height * .6)
        if not candidate:
            result.append(box)
            continue
        meta["attempted"] += 1
        prefix = match.group(1)
        confirmed = []
        # A merged detector row can include an upper row/shadow. Try the whole
        # height then its lower band. These are acquisition candidates only;
        # crop location alone never proves a control or a destination.
        for fraction in (1, .65):
            bounds = (max(0, math.floor(box["left"]) - 4),
                      max(0, math.floor(box["bottom"] - h * fraction) - 3),
                      min(image.width, math.ceil(min(box["right"], box["left"] + h * (len(prefix) + 1) * .6))),
                      min(image.height, math.ceil(box["bottom"]) + 3))
            crop = image.crop(bounds)
            scores = []
            for scale in (2, 3):
                check_current(cancelled)
                readings = reader(crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.BICUBIC))
                if len(readings) != 1:
                    break
                text, score = str(readings[0][0]).strip(), float(readings[0][1])
                if (not math.isfinite(score) or score < .9 or not text.startswith(prefix)
                        or not re.fullmatch(re.escape(prefix) + r"[\u4e00-\u9fff]{0,2}", text)):
                    break
                scores.append(score)
            if len(scores) == 2:
                confirmed = scores
                break
        if confirmed:
            # Score/text now describe only the verified control. The original
            # destination is discarded, not silently assigned the prefix score.
            result.append({**box, "text": prefix, "score": min(confirmed),
                           "control_only": True, "navigation_prefix_verified": True})
            meta["confirmed"] += 1
        else:
            result.append(box)
    return result, meta


def reread_navigation_lines(backend, image, boxes, *, cancelled=None):
    from PIL import Image

    reader = getattr(backend, "recognize_line", None)
    meta = {"supported": callable(reader), "checked": 0, "confirmed": 0, "rejected": 0, "control_only_retained": 0}
    if not callable(reader):
        return boxes, meta
    valid = [b for b in boxes if all(k in b for k in ("text", "score", "left", "right", "top", "bottom"))]
    navs = navigation_boxes(valid, width=image.width, height=image.height)
    selected = [*location_headings(valid, width=image.width, height=image.height), *navs]
    overlapping = set()
    for title, objective, nav in task_stacks(valid, width=image.width, height=image.height):
        selected.extend([title, objective])
        if nav["top"] < objective["bottom"]:
            overlapping.add(tuple(nav[k] for k in ("left", "top", "right", "bottom")))
    regions = {(b["left"], b["top"], b["right"], b["bottom"]) for b in selected}
    results = []
    def unconfirmed(box):
        # Keep geometry, NOT permission to quote the rejected literal. A nav
        # detected on native pixels remains evidence of a control's presence.
        if box in navs:
            results.append({**box, "control_only": True})
            meta["control_only_retained"] += 1
        else:
            results.append({**box, "literal_unconfirmed": True})
        meta["rejected"] += 1
    for box in boxes:
        coords = tuple(box.get(k, -1) for k in ("left", "top", "right", "bottom"))
        if coords not in regions:
            results.append(box)
            continue
        if box.get("navigation_prefix_verified") and box.get("control_only"):
            # Its two-scale proof covers the prefix only. Do not reinterpret
            # this merged row as a newly validated full destination string.
            results.append(box)
            continue
        check_current(cancelled)
        left, top, right, bottom = coords
        height = bottom - top
        if coords in overlapping or meta["checked"] >= 8 or not 4 <= height <= 64:
            # A multi-row detector box must not be fed to a single-line
            # recognizer and then mistaken for a validated destination.
            unconfirmed(box)
            continue
        padding = max(5, round(height * .35))
        bounds = (max(0, math.floor(left) - padding), max(0, math.floor(top) - 3),
                  min(image.width, math.ceil(right) + padding), min(image.height, math.ceil(bottom) + 3))
        crop = image.crop(bounds)
        readings = []
        meta["checked"] += 1
        for scale in (2, 3):
            check_current(cancelled)
            rows = reader(crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.BICUBIC))
            if len(rows) != 1 or not math.isfinite(float(rows[0][1])) or float(rows[0][1]) < .9:
                break
            readings.append((str(rows[0][0]).strip(), float(rows[0][1])))
        if len(readings) == 2 and text_key(readings[0][0]) == text_key(readings[1][0]) and readings[0][0]:
            # This is NEW OCR evidence from an actual crop, not a fuzzy repair
            # of the original text or an entity name chosen from a database.
            unchanged = text_key(readings[0][0]) == text_key(box["text"])
            proof_bounds = coords if unchanged else bounds
            # Extra crop padding helps OCR but is not automatically part of
            # the literal's evidence. Keep the detector box when the text is
            # unchanged; only newly recovered glyphs need expanded geometry.
            results.append({**box, "text": readings[0][0], "score": min(s for _, s in readings),
                            "left": proof_bounds[0], "top": proof_bounds[1], "right": proof_bounds[2], "bottom": proof_bounds[3],
                            "region_precision": "original_box_rechecked" if unchanged else "reread_crop"})
            meta["confirmed"] += 1
        else:
            unconfirmed(box)
    return results, meta
