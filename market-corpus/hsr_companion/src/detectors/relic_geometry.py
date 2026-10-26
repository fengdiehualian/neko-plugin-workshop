"""Same-frame relic geometry, not identity memory or fuzzy value correction."""
import re
import unicodedata


def center_y(box):
    return (box["top"] + box["bottom"]) / 2


def boundary_label(text):
    # Only the registered layout heading permits one adjacent UI ornament.
    # This rule is NEVER used to clean an equipment value or arbitrary prose.
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))
    return bool(re.fullmatch(r"[·•.:]?套装效果", value))


def unique_readings(boxes, *, key=None):
    """Collapse exact text AND overlapping geometry, never conflicting values.

    An explicit caller key can equate UI-decorated labels; default remains
    exact raw text for titles, grades, numbers and object boundary evidence.
    """
    result = []
    for box in sorted(boxes, key=lambda b: -b["score"]):
        duplicate = False
        for other in result:
            if (key(box) if key else box["text"]) != (key(other) if key else other["text"]):
                continue
            w = max(0, min(box["right"], other["right"]) - max(box["left"], other["left"]))
            h = max(0, min(box["bottom"], other["bottom"]) - max(box["top"], other["top"]))
            area = min((b["right"] - b["left"]) * (b["bottom"] - b["top"]) for b in (box, other))
            if area > 0 and w * h / area >= .8:
                duplicate = True
                break
        if not duplicate:
            result.append(box)
    return sorted(result, key=lambda b: (center_y(b), b["left"]))


def detail_column(boxes, part, end):
    h = part["bottom"] - part["top"]
    return [b for b in boxes if b["left"] >= part["left"] - h
            and center_y(part) < center_y(b) < center_y(end)]


def upgrade_label(text):
    return bool(re.fullmatch(r"\+(?:[0-9]|1[0-5])", str(text)))
