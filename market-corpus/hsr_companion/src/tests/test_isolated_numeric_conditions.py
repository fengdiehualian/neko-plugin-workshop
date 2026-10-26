"""Acceptance for isolated real-run conditions, not a host-speech claim.

Coordinates/text are reconstructed from the v3 diagnostic log; images here
are synthetic. Original failure reports remain unchanged.
"""
from io import BytesIO
import unicodedata

from PIL import Image
import pytest

from plugin.plugins.hsr_companion.adapters.relic_values import atlas_fragment_bounds, read_relic_table, refine_relic_values
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key, verify_text_regions
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card, equipment_readiness
from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
from plugin.plugins.hsr_companion.tests.test_relic_fields import relic_boxes
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog


def test_table_ui_bullet_does_not_discard_other_four_correct_rows():
    boxes = relic_boxes()
    rows = [{**b, "left": b["left"] - 695, "right": b["right"] - 695,
             "top": b["top"] - 120, "bottom": b["bottom"] - 120} for b in boxes[3:-1]]
    rows[0]["text"] = "·生命值"

    class Backend:
        def extract_text_with_boxes(self, sample):
            return "", rows

    _, details = read_relic_table(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend())
    assert details["accepted"]


def test_atlas_small_detector_margin_does_not_discard_correct_numeric_reading():
    boxes = relic_boxes()
    boxes[8]["text"] = "8.2%0"

    class Backend:
        def extract_text_with_boxes(self, sample):
            # The real run read 15.5% at (12,12)..(125,67) for paste origin
            # (16,16), height48. This checks geometry, not a guessed suffix.
            return "8.2%", [{"text": "8.2%", "score": .995,
                "left": 12, "top": 12, "right": 125, "bottom": 67}]

    _, details = refine_relic_values(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend())
    assert details["candidates"] == 1
    assert details["confirmed"] == 1


def test_invalid_numeric_reread_is_unknown_not_definite_contradiction():
    class Backend:
        def recognize_line(self, sample):
            return [(".16", .926)]

    encoded = BytesIO()
    Image.new("RGB", (200, 100)).save(encoded, format="PNG")
    trace = []
    proof = {"text": "16", "region": dict(left=.2, top=.2, right=.4, bottom=.3)}
    assert not verify_text_regions(Backend(), encoded.getvalue(), [proof], trace=trace)
    # Must remain unconfirmed; no stripping dot or replacing the actual read.
    assert trace[-1]["reason"] == "insufficient_exact_matches"


def test_unreadable_substat_does_not_revoke_confirmed_main_stat_readiness():
    visual = {"fresh": True, "screen_facts": {"facts": [
        {"scope": "equipment", "kind": "equipment_slot", "value": "连结绳"},
        {"scope": "equipment", "kind": "equipment_level", "value": "+15"},
        {"scope": "equipment", "kind": "main_stat", "label": "主词条·能量恢复效率", "value": "19.4%"},
        {"scope": "equipment", "kind": "unreadable_stat", "label": "副词条·生命值", "value": "数值未可靠识别"}],
        "field_checks": [{"scope": "equipment", "kind": "main_stat", "state": "confirmed"},
                         {"scope": "equipment", "kind": "unreadable_stat", "state": "confirmed"}]}}
    result = equipment_readiness(visual, {"main_stat", "sub_stat"})
    assert result["confirmed"] == ["main_stat"]
    assert result["missing"] == ["sub_stat"]


@pytest.mark.parametrize("marker", ["·", "•", ":", "："])
@pytest.mark.parametrize("inline", [False, True])
def test_marker_parsing_keeps_raw_literal_and_main_sub_binding(marker, inline):
    boxes = relic_boxes()
    boxes[3]["text"] = marker + "生命值" + (":705" if inline else "")
    if inline:
        boxes.pop(4)
    result = extract_screen_facts(boxes, width=1280, height=800,
        page={"page_type": "relic_detail"}, catalog=catalog())
    main = next(f for f in result["facts"] if f["kind"] == "main_stat")
    assert main["value"] == "705"
    # Existing safe_text applies NFKC (full-width colon -> colon). The
    # ornament must stay in evidence, not necessarily its Unicode width.
    assert any(p["text"].startswith(unicodedata.normalize("NFKC", marker)) for p in main["evidence"])
    assert len([f for f in result["facts"] if f["kind"] == "sub_stat"]) == 4


def test_overlapping_decorated_label_retries_are_one_row_but_conflicting_labels_are_not():
    boxes = relic_boxes()
    boxes.append({**boxes[9], "text": "·攻击力", "top": boxes[9]["top"] + 1})
    kwargs = dict(width=1280, height=800, page={"page_type": "relic_detail"}, catalog=catalog())
    assert len([f for f in extract_screen_facts(boxes, **kwargs)["facts"] if f["kind"] == "sub_stat"]) == 4
    boxes[-1]["text"] = "·防御力"
    assert not any(f["kind"] == "main_stat" for f in extract_screen_facts(boxes, **kwargs)["facts"])


@pytest.mark.parametrize("changed", [dict(left=-10), dict(top=60, bottom=85), dict(score=.89),
                                   dict(score=float("nan")), dict(left=float("nan")), dict(right=250)])
def test_atlas_margin_does_not_allow_outside_weak_or_invalid_proofs(changed):
    box = dict(left=20, top=20, right=100, bottom=60, score=.99)
    box.update(changed)
    assert atlas_fragment_bounds(box, (16, 16, 150, 48)) is None


def test_atlas_margin_is_clipped_before_mapping_back_to_original_frame():
    box = dict(left=12, top=12, right=125, bottom=67, score=.995)
    assert atlas_fragment_bounds(box, (16, 16, 150, 48)) == (16, 16, 125., 64)


@pytest.mark.parametrize("readings", [[("16", .99), ("16", .99)], [("1", .99), ("6", .99)]])
def test_conflicting_overlapping_numeric_detections_do_not_create_concatenated_value(readings):
    boxes = relic_boxes()
    boxes[8]["text"] = "8.2%0"

    class Backend:
        def extract_text_with_boxes(self, sample):
            return "", [dict(text=t, score=s, left=20, top=20, right=90, bottom=50) for t, s in readings]

    _, details = refine_relic_values(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend())
    assert details["confirmed"] == 0


@pytest.mark.parametrize("expected,actual", [("16", "17"), ("110", "1.10"), ("16%", "16"), ("5.1%", "51%")])
def test_legal_conflicting_numbers_and_units_are_still_rejected(expected, actual):
    class Backend:
        def recognize_line(self, sample):
            return [(actual, .99)]

    encoded = BytesIO()
    Image.new("RGB", (200, 100)).save(encoded, format="PNG")
    proof = {"text": expected, "region": dict(left=.2, top=.2, right=.4, bottom=.3)}
    trace = []
    assert not verify_text_regions(Backend(), encoded.getvalue(), [proof], trace=trace)
    assert trace[-1]["reason"] == "high_confidence_contradiction"


def test_invalid_read_is_not_a_vote_and_requires_two_later_exact_readings():
    values = iter([(".16", .926), ("16", .99), ("16", .99)])

    class Backend:
        def recognize_line(self, sample):
            return [next(values)]

    encoded = BytesIO()
    Image.new("RGB", (200, 100)).save(encoded, format="PNG")
    proof = {"text": "16", "region": dict(left=.2, top=.2, right=.4, bottom=.3)}
    trace = []
    assert verify_text_regions(Backend(), encoded.getvalue(), [proof], trace=trace) == {proof_key(proof)}
    assert trace[0]["quality_valid"] and not trace[0]["numeric_syntax_valid"]
    assert trace[-1]["matches"] == 2


@pytest.mark.parametrize("uncertainty", ["equipment_binding_missing", "equipment_name", "equipment_level"])
def test_object_uncertainty_still_blocks_both_main_and_sub(uncertainty):
    visual = {"fresh": True, "screen_facts": {"facts": [
        {"kind": "main_stat", "scope": "equipment", "label": "主词条·生命值", "value": "705"},
        {"kind": "sub_stat", "scope": "equipment", "label": "副词条·速度", "value": "4"}],
        "field_checks": [{"kind": uncertainty, "scope": "equipment", "state": "pending"}]}}
    if uncertainty == "equipment_binding_missing":
        visual["screen_facts"]["facts"].append({"kind": uncertainty, "scope": "equipment"})
    assert equipment_card(visual)["state"] != "confirmed"
    assert equipment_readiness(visual, {"main_stat", "sub_stat"})["confirmed"] == []


def test_same_frame_label_column_consensus_excludes_main_row_icon_and_keeps_grade():
    boxes = relic_boxes()
    boxes[3]["left"] = 695  # Real failure: icon+label box extends far left.
    narrow = relic_boxes()[3:-1]

    class Backend:
        def extract_text_with_boxes(self, sample):
            origin = 1020 - sample.width
            assert origin == 723  # Four other labels locate their own column.
            return "", [{**b, "left": b["left"] - origin, "right": b["right"] - origin,
                          "top": b["top"] - 120, "bottom": b["bottom"] - 120} for b in narrow]

    updated, details = read_relic_table(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend())
    assert details["accepted"] and details["crop_avoids_label_icon"]
    assert any(b["text"] == "+15" for b in updated)
    assert not any(b["text"] == "生命值" and b["left"] == 695 for b in updated)
    result = extract_screen_facts(updated, width=1280, height=800,
        page={"page_type": "relic_detail"}, catalog=catalog())
    assert len([f for f in result["facts"] if f["kind"] in {"main_stat", "sub_stat"}]) == 5


@pytest.mark.parametrize("actual", [":16", "·16", ".16"])
def test_numeric_atlas_does_not_apply_label_ornament_normalization_to_numbers(actual):
    boxes = relic_boxes()
    boxes[8]["text"] = "8.2%0"

    class Backend:
        def extract_text_with_boxes(self, sample):
            return actual, [dict(text=actual, score=.99, left=20, top=20, right=90, bottom=50)]

    _, details = refine_relic_values(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend())
    assert details["confirmed"] == 0
