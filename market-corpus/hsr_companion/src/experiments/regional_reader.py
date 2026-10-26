"""PoC only: same model/parser, acquire bounded page-dependent native regions.

No remembered names/items, game input or fallback full reader. Unknown is an
experiment failure, not permission to infer a page from the user's question.
"""
from io import BytesIO
import math
import time

from ..adapters.text_reader import _box_value, check_current
from ..detectors.layout import classify_supported_page
from ..detectors.scene_preview import scene_preview, preview_regions
from ..detectors.exploration import navigation_boxes
from ..runtime.screen_facts import extract_screen_facts, _boxes, _relic_panel, safe_text


def translate_boxes(values, *, left=0, top=0, sx=1, sy=1):
    result = []
    for value in values:
        b = _box_value(value)
        result.append({**b, "left": b["left"] * sx + left, "right": b["right"] * sx + left,
                       "top": b["top"] * sy + top, "bottom": b["bottom"] * sy + top})
    return result


def select_regions(page, boxes, width, height):
    """Geometry only; meaning must still be independently proved by OCR."""
    def bounds(left, top, right, bottom):
        return (max(0, math.floor(left)), max(0, math.floor(top)),
                min(width, math.ceil(right)), min(height, math.ceil(bottom)))
    if page == "exploration_hud":
        navs = navigation_boxes(boxes, width=width, height=height)
        if len(navs) == 1:
            nav = navs[0]
            h = nav["bottom"] - nav["top"]
            task = bounds(max(0, nav["left"] - h * 2), max(height * .08, nav["top"] - h * 8),
                          min(width * .55, max(nav["right"] + h * 4, width * .35)), nav["bottom"] + h)
        else:
            # Explicit documented acquisition envelope, not an answer/layout
            # assumption. Missing rows within it remain missing.
            task = bounds(0, height * .08, width * .5, height * .55)
        return [("location", bounds(0, 0, width * .3, height * .07)), ("task", task),
                ("hud_control", bounds(width * .70, height * .10, width, height * .28))]
    if page == "relic_detail":
        panel = _relic_panel(_boxes(boxes, width, height))
        if not panel:
            return []  # Comparison/ambiguous columns cannot choose an item.
        part, end = panel
        h = part["bottom"] - part["top"]
        return [("relic_object", bounds(part["left"] - h * 2, part["top"] - h * 4,
                                         part["left"] + width * .4, end["bottom"] + h))]
    return []


class RegionalReader:
    def __init__(self, backend, catalog):
        self.backend, self.catalog = backend, catalog

    def analyze(self, payload, *, cancelled=None, on_scene=None, trace=None):
        from PIL import Image, ImageOps
        started = time.perf_counter()
        calls = []
        def snapshot(stage, values, **details):
            if trace is not None:
                trace.append({"stage": stage, **details, "boxes": [
                    {**b, "text": safe_text(b.get("text"))} for b in values[:240]]})
        def read(image, stage):
            check_current(cancelled)
            before = time.perf_counter()
            _, values = self.backend.extract_text_with_boxes(image)
            calls.append({"stage": stage, "size": list(image.size),
                          "backend_call_seconds": round(time.perf_counter() - before, 4)})
            check_current(cancelled)
            return values
        with Image.open(BytesIO(payload)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
        if image.width * image.height > 16_000_000:
            raise ValueError("PoC image exceeds existing pixel bound")
        probe = image.copy()
        probe.thumbnail((960, 960))
        boxes = translate_boxes(read(probe, "page_probe"), sx=image.width / probe.width, sy=image.height / probe.height)
        snapshot("page_probe", boxes)
        def classify():
            early = scene_preview(boxes, width=image.width, height=image.height)
            return (early or {}).get("page") or classify_supported_page(
                "\n".join(b["text"] for b in boxes), boxes, width=image.width, height=image.height)
        page = classify()
        # Unknown small probes may miss small HUD anchors. One bounded left
        # envelope plus independent status control ACQUIRES the page; it does
        # not authorize navigation from location coordinates alone.
        acquisition = []
        if page.get("page_type") == "unknown":
            acquisition = select_regions("exploration_hud", boxes, image.width, image.height)[1:]
        def acquire(regions):
            nonlocal boxes
            for stage, rect in regions:
                left, top, right, bottom = rect
                if right <= left or bottom <= top:
                    continue
                native = translate_boxes(read(image.crop(rect), stage), left=left, top=top)
                snapshot("native_crop", native, region_name=stage, bounds=list(rect))
                boxes = [b for b in boxes if not (left <= b["left"] < b["right"] <= right
                                                   and top <= b["top"] < b["bottom"] <= bottom)] + native
        acquire(acquisition)
        page = classify()
        selected = select_regions(page.get("page_type"), boxes, image.width, image.height)
        already = {rect for _, rect in acquisition}
        if page.get("confidence") == "high" and selected:
            acquire([(stage, rect) for stage, rect in selected if rect not in already])
            page = classify()
        if page.get("page_type") == "relic_detail" and selected:
            from ..adapters.relic_values import read_relic_table, refine_relic_values
            snapshot("before_table_reread", boxes)
            boxes, table = read_relic_table(image, boxes, page=page, backend=self.backend, cancelled=cancelled)
            snapshot("after_table_reread", boxes, table=table)
            boxes, numeric = refine_relic_values(image, boxes, page=page, backend=self.backend, cancelled=cancelled,
                table_bounds=table.get("crop") if table.get("accepted") else None)
            snapshot("after_numeric_reread", boxes, numeric=numeric)
            calls.extend(table["backend_calls"] + numeric["backend_calls"])
        supported = page.get("page_type") in {"exploration_hud", "relic_detail"}
        confirmed = bool(supported and selected and page.get("confidence") == "high")
        controls = preview_regions(page, boxes, width=image.width, height=image.height) if confirmed else []
        confirmed = confirmed and bool(controls)
        result = {"ok": True, "page": page,
            "scene": {"primary_state": page.get("scene", "unknown") if confirmed else "unknown",
                      "verified": confirmed, "confidence": "high" if confirmed else "low",
                      "substate": page.get("page_type", "unknown"), "evidence": page.get("evidence", [])},
            "characters": [], "recognition": {"experiment": "regional_poc_v1", "scene_regions": controls,
                "native_crops": True, "source_size": list(image.size),
                "text_reader": {"full_frame_covered": False, "backend_calls": calls},
                "processing_seconds": round(time.perf_counter() - started, 4)},
            "screen_facts": {"facts": [], "visible_text": []}}
        if confirmed:
            result["screen_facts"] = extract_screen_facts(boxes, width=image.width, height=image.height,
                page=page, catalog=self.catalog, trace=trace)
        return result
