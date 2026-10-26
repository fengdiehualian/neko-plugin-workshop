"""Acceptance for recorded failure conditions; no model/host success inferred."""
from io import BytesIO

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.adapters.relic_values import read_relic_table, refine_relic_values
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key, verify_text_regions
from plugin.plugins.hsr_companion.detectors.scene_preview import preview_regions
from plugin.plugins.hsr_companion.runtime.live_refresh import stable_screen_content
from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
from plugin.plugins.hsr_companion.tests.test_relic_fields import relic_boxes
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog


def facts(boxes, width=1280, height=800):
    return extract_screen_facts(boxes, width=width, height=height,
        page={"page_type": "relic_detail"}, catalog=catalog())


@pytest.mark.parametrize("touch", [0, -1])
@pytest.mark.parametrize("duplicate", [False, True])
def test_level_edge_jitter_and_same_frame_duplicates_do_not_erase_five_rows(touch, duplicate):
    boxes = relic_boxes()
    boxes[1]["bottom"] = boxes[2]["top"] - touch
    if duplicate:
        boxes.append({**boxes[2], "left": boxes[2]["left"] + 1, "top": boxes[2]["top"] + 1})
    assert len([f for f in facts(boxes)["facts"] if f["kind"] in {"main_stat", "sub_stat"}]) == 5


@pytest.mark.parametrize("changed", ["level_conflict", "missing_main", "two_columns"])
def test_ambiguous_objects_and_missing_main_stay_blocked(changed):
    boxes = relic_boxes()
    if changed == "level_conflict":
        boxes.append({**boxes[2], "text": "+14"})
    elif changed == "missing_main":
        boxes.pop(3)
    else:
        boxes += relic_boxes(x=50)
    assert not any(f["kind"] == "main_stat" for f in facts(boxes)["facts"])


def test_table_reread_does_not_delete_same_frame_independent_level():
    boxes = relic_boxes()
    class Backend:
        def extract_text_with_boxes(self, crop):
            return "", [{**b, "left": b["left"] - 695, "right": b["right"] - 695,
                          "top": b["top"] - 120, "bottom": b["bottom"] - 120} for b in boxes[3:-1]]
    updated, details = read_relic_table(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend())
    assert details["accepted"]
    assert len([f for f in facts(updated)["facts"] if f["kind"] in {"main_stat", "sub_stat"}]) == 5


def test_numeric_atlas_returns_actual_detected_geometry_in_original_frame():
    boxes = relic_boxes()
    boxes.pop(6)
    class Backend:
        def extract_text_with_boxes(self, atlas):
            # Full-height glyph, not the former artificial half-row fragment.
            return "16", [{"text": "16", "score": .99, "left": 100, "top": 21, "right": 130, "bottom": 60}]
    updated, details = refine_relic_values(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend(), table_bounds=[695, 120, 1020, 340])
    assert details["confirmed"] == 1
    number = next(b for b in updated if b.get("numeric_recheck", {}).get("method") == "same_frame_badge_separated_ocr")
    # Source search is x873..1020, h26; enlarged to271x48 at atlas(16,16).
    assert number["left"] == pytest.approx(873 + 84 * 147 / 271)
    assert number["right"] == pytest.approx(873 + 114 * 147 / 271)
    assert number["right"] - number["left"] < 20
    assert number["numeric_recheck"]["search_crop"] != number["numeric_recheck"]["crop"]
    assert any(f["value"] == "16" for f in facts(updated)["facts"])


def png(image):
    result = BytesIO()
    image.save(result, "PNG")
    return result.getvalue()


def test_control_decoration_normalization_is_not_applied_to_literal_values():
    class Backend:
        def recognize_line(self, image):
            return [(":套装效果", .934)]
    proof = {"text": "套装效果", "region": dict(left=.4, top=.7, right=.8, bottom=.8)}
    payload = png(Image.new("RGB", (200, 100)))
    control = {**proof, "control": True}
    assert proof_key(control) in verify_text_regions(Backend(), payload, [control])
    assert not verify_text_regions(Backend(), payload, [proof])
    assert proof_key(control) != proof_key(proof)


def test_same_box_literal_failure_does_not_overwrite_control_proof():
    from plugin.plugins.hsr_companion.adapters.text_verification import text_key
    original = Image.new("RGB", (200, 100), (20, 20, 20))
    latest = original.copy()
    ImageDraw.Draw(latest).rectangle((80, 70, 150, 79), fill="white")
    footer = {"text": "套装效果", "region": dict(left=.4, top=.7, right=.8, bottom=.8)}
    value = {"scene": {"verified": True}, "page": {"page_type": "relic_detail"},
             "recognition": {"scene_regions": [footer]}, "screen_facts": {"facts": [], "visible_text": [footer]}}
    class Backend:
        def recognize_line(self, image):
            return [(":套装效果", .934)]
    checked = stable_screen_content(value, png(original), png(latest),
        verifier=lambda p, rows, **kw: verify_text_regions(Backend(), p, rows, **kw))
    assert checked["recognition"]["page_controls_revalidated"]
    assert not checked["screen_facts"]["visible_text"]  # Literal remains exact, no forged text.
    assert text_key(":套装效果", control=True) == "套装效果"


def test_equipment_binding_and_fields_use_distinct_boundary_and_literal_roles():
    boxes = relic_boxes()
    raw = facts(boxes)
    stat = next(f for f in raw["facts"] if f["kind"] == "main_stat")
    footer = next(p for p in stat["evidence"] if p["text"] == "套装效果")
    assert footer["validation_scope"] == "control_presence"
    assert any(p["text"] == "+15" and p["validation_scope"] == "literal" for p in stat["evidence"])
    assert any(p["text"] == "任意遗器标题" for p in stat["evidence"])
    original = Image.new("RGB", (1280, 800), (20, 20, 20))
    latest = original.copy()
    end = boxes[-1]
    ImageDraw.Draw(latest).rectangle((end["left"], end["top"], end["right"], end["bottom"]), fill="white")
    value = {"scene": {"verified": True}, "page": {"page_type": "relic_detail"},
             "recognition": {}, "screen_facts": raw}
    checked = stable_screen_content(value, png(original), png(latest), verifier=lambda *a, **k: set())
    assert len([f for f in checked["screen_facts"]["facts"] if f["kind"] in {"main_stat", "sub_stat"}]) == 5
    # Changing object title must still withdraw all of this item's values.
    latest = original.copy()
    title = boxes[0]
    ImageDraw.Draw(latest).rectangle((title["left"], title["top"], title["right"], title["bottom"]), fill="white")
    checked = stable_screen_content(value, png(original), png(latest), verifier=lambda *a, **k: set())
    assert not any(f["kind"] in {"main_stat", "sub_stat"} for f in checked["screen_facts"]["facts"])


def test_page_controls_and_fact_proofs_share_one_coordinate_precision():
    boxes = relic_boxes() + [{"text": "遗器替换", "score": .99, "left": 130.66666, "top": 64,
                             "right": 245.33333, "bottom": 101.33333}]
    raw = facts(boxes, 2560, 1600)
    controls = preview_regions({"page_type": "relic_detail"}, boxes, width=2560, height=1600)
    end = next(c for c in controls if c["text"] == "套装效果")
    proof = next(p for f in raw["facts"] for p in f["evidence"] if p["text"] == "套装效果")
    assert end["region"] == proof["region"]
