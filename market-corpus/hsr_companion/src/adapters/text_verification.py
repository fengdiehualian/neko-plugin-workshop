"""Exact, region-local rereads; a pixel mismatch is not a semantic change.

No fuzzy aliases or catalogue guesses. Reuse the existing OCR backend, serially
inside the observation producer. Nothing is persisted and no game input occurs.
"""
from __future__ import annotations

import math
import re
import unicodedata
from io import BytesIO

from ..detectors.exploration import generic_navigation
from ..detectors.relic_geometry import boundary_label
from ..detectors.relic_labels import label_key
from .text_reader import _box_value, check_current

NUMBER_LITERAL = re.compile(r"\+?\d[\d,]*(?:\.\d+)?%?")

def text_key(text, *, control=False):
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = re.sub(r"\s+", "", value)
    # A navigation button's distance is not evidence of a different quest.
    # Only proof controls use this rule, never literal text returned to users.
    if control and generic_navigation(value):
        return "前往任务目标"
    if control and re.fullmatch(r"请前往\S{1,100}", value):
        return "导航目的地提示"
    if control and re.fullmatch(r"状态效果[Oo0]?", value):
        return "状态效果"  # Adjacent keyboard O is not a semantic field value.
    if control and boundary_label(value):
        return "套装效果"
    return value


def proof_key(proof):
    region = proof.get("region") or {}
    stat_label = proof.get("validation_scope") == "relic_stat_label"
    return (tuple(region.get(k, -1) for k in ("left", "top", "right", "bottom")),
            label_key(proof.get("text")) if stat_label else text_key(proof.get("text"), control=proof.get("control") is True),
            "stat_label" if stat_label else "control" if proof.get("control") is True else "literal")


def identity_literal(actor):
    """A label proves a text identity, never a changing portrait or guessed name.

    Reread the actual OCR line (including level/aliases), not canonical name
    search. Keep this internal and bound to the actor's own capture geometry.
    """
    text = actor.get("raw_label")
    if (actor.get("identity_scope") == "hud_portrait" or not actor.get("region")
            or not isinstance(text, str) or not text.strip()):
        return None
    return {"region": actor["region"], "text": text, "identity_literal": True}


def verify_text_regions(backend, payload, proofs, *, cancelled=None, trace=None):
    """Return keys with high-quality, exact rereads at the expected geometry.

    Two crop scales must agree. OCR on the same image is not
    two independent frames; this verifies text against the NEW captured frame.
    Work is bounded; unverified/overflow proofs remain unknown.
    """
    from PIL import Image

    confirmed = set()
    with Image.open(BytesIO(payload)) as opened:
        image = opened.convert("RGB")
    for proof in proofs[:16]:
        check_current(cancelled)
        coords, expected = proof_key(proof)[:2]
        def skipped(reason):
            if trace is not None:
                trace.append({"stage": "proof_skipped", "key": proof_key(proof), "reason": reason})
        if not expected or "已隐藏]" in expected or len(expected) > 240:
            skipped("invalid_expected_text")
            continue
        if not all(isinstance(v, (float, int)) and math.isfinite(v) and 0 <= v <= 1 for v in coords):
            skipped("invalid_coordinates")
            continue
        left, top, right, bottom = coords
        if left >= right or top >= bottom:
            skipped("empty_geometry")
            continue
        bounds = (max(0, math.floor(left * image.width) - 5), max(0, math.floor(top * image.height) - 4),
                  min(image.width, math.ceil(right * image.width) + 5), min(image.height, math.ceil(bottom * image.height) + 4))
        crop = image.crop(bounds)
        if crop.width < 4 or crop.height < 4 or crop.width > 1400 or crop.height > 160:
            skipped("crop_size_limit")
            continue
        matches = 0
        contradicted = False
        # Tiny HUD crops need a surrounding canvas for text detection; feeding
        # a 20px-high strip makes some OCR detectors resize/segment it badly.
        for scale in (2, 3, 4):
            check_current(cancelled)
            resized = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.BICUBIC)
            sample = Image.new("RGB", (resized.width + 32, resized.height + 32), (24, 24, 24))
            sample.paste(resized, (16, 16))
            line_reader = getattr(backend, "recognize_line", None)
            if callable(line_reader):
                lines = line_reader(resized)
                boxes = [dict(text=t, score=s, left=0, top=0, bottom=resized.height) for t, s in lines]
            else:
                _, values = backend.extract_text_with_boxes(sample)
                boxes = [_box_value(b) for b in values]
            # Missing/weak boxes cannot be silently dropped to manufacture a match.
            try:
                minimum = .85 if proof.get("control") is True else .9
                valid = bool(boxes) and all(math.isfinite(float(b.get("score") or 0))
                    and float(b.get("score") or 0) >= minimum for b in boxes)
                boxes.sort(key=lambda b: float(b.get("left") or 0))
                centers = [(float(b.get("top") or 0) + float(b.get("bottom") or 0)) / 2 for b in boxes]
                valid &= max(centers, default=0) - min(centers, default=0) <= sample.height * .4
                actual_text = "".join(str(b.get("text") or "") for b in boxes)
                actual = (label_key(actual_text) if proof.get("validation_scope") == "relic_stat_label"
                          else text_key(actual_text, control=proof.get("control") is True))
            except (ValueError, TypeError):
                valid, actual, actual_text = False, "", ""
            quality_valid = bool(valid)
            numeric_syntax_valid = not NUMBER_LITERAL.fullmatch(expected) or bool(NUMBER_LITERAL.fullmatch(actual))
            valid = valid and numeric_syntax_valid
            if trace is not None:
                trace.append({"stage": "reread_attempt", "key": proof_key(proof),
                              "expected": expected, "actual": actual, "scale": scale, "valid": bool(valid),
                              "actual_raw": actual_text,
                              "quality_valid": quality_valid, "numeric_syntax_valid": numeric_syntax_valid,
                              "bounds": bounds, "image_size": image.size, "sample_size": sample.size,
                              "scores": [round(float(b.get("score") or 0), 3) for b in boxes]})
            if valid and actual != expected:
                contradicted = True
                break
            if valid:
                matches += 1
                if matches == 2:
                    break
        if matches == 2 and not contradicted:
            confirmed.add(proof_key(proof))
        if trace is not None:
            trace.append({"stage": "proof_decision", "key": proof_key(proof), "matches": matches,
                          "reason": "exact_twice" if matches == 2 and not contradicted else
                                    "high_confidence_contradiction" if contradicted else "insufficient_exact_matches"})
    return confirmed
