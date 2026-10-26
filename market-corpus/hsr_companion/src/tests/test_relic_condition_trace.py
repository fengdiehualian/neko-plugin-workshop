"""Observability must explain rejection without changing answer permission."""
import copy
from io import BytesIO

from PIL import Image, ImageDraw
import pytest

from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
from plugin.plugins.hsr_companion.runtime.live_refresh import stable_screen_content
from plugin.plugins.hsr_companion.adapters.text_verification import verify_text_regions, proof_key
from plugin.plugins.hsr_companion.tests.test_relic_fields import relic_boxes
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog


@pytest.mark.parametrize("failure", [None, "missing_level", "misaligned_level", "overlapping_rows", "missing_main"])
def test_parser_trace_matches_unchanged_output_and_identifies_binding_gate(failure):
    boxes = relic_boxes()
    if failure == "missing_level":
        boxes.pop(2)
    elif failure == "misaligned_level":
        boxes[2].update(left=780, right=830)
    elif failure == "overlapping_rows":
        boxes[5].update(top=170)
    elif failure == "missing_main":
        boxes.pop(3)
    kwargs = dict(width=1280, height=800, page={"page_type": "relic_detail"}, catalog=catalog())
    plain = extract_screen_facts(copy.deepcopy(boxes), **kwargs)
    trace = []
    traced = extract_screen_facts(copy.deepcopy(boxes), trace=trace, **kwargs)
    assert plain == traced
    binding = next(e for e in trace if e["stage"] == "relic_binding")
    assert binding["binding_accepted"] == (failure is None)
    if failure in {"missing_level", "misaligned_level"}:
        assert not binding["conditions"]["unique_upgrade_above_first_row_aligned"]
        assert len(binding["rows"]) == 5  # OCR saw rows; parser correctly remains unbound.
    if failure == "missing_main":
        assert not binding["conditions"]["upgrade_gap_0_to_1_5_heading_heights"]


def png(image):
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_speed_rejection_is_attributed_to_exact_proof_not_whole_field_set():
    region = dict(left=.2, top=.2, right=.4, bottom=.3)
    speed = {"text": "4", "region": region, "validation_scope": "literal"}
    original = Image.new("RGB", (200, 100), (20, 20, 20))
    latest = original.copy()
    ImageDraw.Draw(latest).rectangle((40, 20, 70, 29), fill=(240, 240, 240))
    value = {"page": {"page_type": "relic_detail"}, "scene": {"verified": True},
             "recognition": {}, "screen_facts": {"facts": [
                 {"kind": "sub_stat", "scope": "equipment", "label": "副词条·速度", "value": "4", "evidence": [speed]}]}}
    class Backend:
        def recognize_line(self, image):
            return [("42", .99)]
    rereads = []
    def verifier(payload, proofs, **kwargs):
        return verify_text_regions(Backend(), payload, proofs, trace=rereads, **kwargs)
    trace = []
    result = stable_screen_content(value, png(original), png(latest), verifier=verifier, trace=trace)
    assert result == stable_screen_content(value, png(original), png(latest), verifier=verifier)
    admission = next(e for e in trace if e["stage"] == "fact_admission")
    assert not admission["accepted"] and admission["proofs"][0]["required"]
    assert not admission["proofs"][0]["pixels_stable"]
    assert next(e for e in rereads if e["stage"] == "proof_decision")["reason"] == "high_confidence_contradiction"
    assert proof_key(speed) not in verify_text_regions(Backend(), png(latest), [speed])


def test_invalid_crop_trace_and_two_exact_matches_do_not_change_verification():
    class Backend:
        def recognize_line(self, image):
            return [("4", .99)]
    payload = png(Image.new("RGB", (200, 100)))
    proof = {"text": "4", "region": dict(left=.2, top=.2, right=.4, bottom=.3)}
    trace = []
    assert verify_text_regions(Backend(), payload, [proof], trace=trace) == {proof_key(proof)}
    assert trace[-1]["reason"] == "exact_twice"
    bad = {**proof, "region": dict(left=-1, top=0, right=1, bottom=1)}
    trace.clear()
    assert not verify_text_regions(Backend(), payload, [bad], trace=trace)
    assert trace[-1]["reason"] == "invalid_coordinates"


def test_table_reread_preserves_independent_level_after_repair():
    """The old failure remains in the run record; current acceptance protects the anchor."""
    from plugin.plugins.hsr_companion.adapters.relic_values import read_relic_table
    boxes = relic_boxes()
    # Table crop is x=695..1025, y=120..340 in this synthetic geometry.
    table_rows = [{**b, "left": b["left"] - 695, "right": b["right"] - 695,
                   "top": b["top"] - 120, "bottom": b["bottom"] - 120} for b in boxes[3:-1]]
    class Backend:
        def extract_text_with_boxes(self, image):
            return "", table_rows  # Five labels and numbers, but no +15.
    updated, details = read_relic_table(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend())
    assert details["accepted"]
    assert any(b["text"] == "+15" for b in boxes)
    assert any(b["text"] == "+15" for b in updated)
    trace = []
    result = extract_screen_facts(updated, width=1280, height=800,
        page={"page_type": "relic_detail"}, catalog=catalog(), trace=trace)
    binding = next(e for e in trace if e["stage"] == "relic_binding")
    assert len(binding["rows"]) == 5
    assert binding["conditions"]["unique_upgrade_above_first_row_aligned"]
    assert len([f for f in result["facts"] if f["kind"] in {"main_stat", "sub_stat"}]) == 5


def test_level_touching_heading_edge_is_bound_by_centers_after_repair():
    """Live entry/3 geometry remains covered after replacing the strict edge gate."""
    boxes = relic_boxes()
    boxes[1]["bottom"] = boxes[2]["top"]
    trace = []
    extract_screen_facts(boxes, width=1280, height=800,
        page={"page_type": "relic_detail"}, catalog=catalog(), trace=trace)
    materialized = next(e for e in trace if e["stage"] == "relic_materialized")
    binding = next(e for e in trace if e["stage"] == "relic_binding")
    assert any(b["text"] == "+15" for b in materialized["boxes"])
    assert any(b["text"] == "+15" for b in binding["column"])
    assert len(binding["rows"]) == 5 and len(binding["upgrades"]) == 1
    assert binding["binding_accepted"]


def test_correct_unchanged_speed_is_withheld_by_shared_footer_literal():
    original = Image.new("RGB", (200, 100), (20, 20, 20))
    latest = original.copy()
    ImageDraw.Draw(latest).rectangle((80, 70, 150, 79), fill=(240, 240, 240))
    speed = {"text": "2", "region": dict(left=.2, top=.2, right=.4, bottom=.3)}
    footer = {"text": "套装效果", "region": dict(left=.4, top=.7, right=.8, bottom=.8)}
    value = {"page": {"page_type": "relic_detail"}, "scene": {"verified": True},
        "recognition": {}, "screen_facts": {"facts": [{"kind": "sub_stat", "scope": "equipment",
            "label": "副词条·速度", "value": "2", "evidence": [speed, footer]}]}}
    class Backend:
        def recognize_line(self, image):
            return [(":套装效果", .934)]
    rereads, trace = [], []
    def verify(payload, proofs, **kwargs):
        return verify_text_regions(Backend(), payload, proofs, trace=rereads, **kwargs)
    result = stable_screen_content(value, png(original), png(latest), verifier=verify, trace=trace)
    admission = next(e for e in trace if e["stage"] == "fact_admission")
    assert admission["proofs"][0]["pixels_stable"]
    assert not admission["accepted"] and not result["screen_facts"]["facts"]
    assert rereads[-1]["reason"] == "high_confidence_contradiction"


def test_correct_reread_below_confidence_gate_does_not_count_as_exact_vote():
    class Backend:
        def recognize_line(self, image):
            return [("2", .813)]
    proof = {"text": "2", "region": dict(left=.2, top=.2, right=.4, bottom=.3)}
    trace = []
    assert not verify_text_regions(Backend(), png(Image.new("RGB", (200, 100))), [proof], trace=trace)
    attempts = [e for e in trace if e["stage"] == "reread_attempt"]
    assert len(attempts) == 3 and all(e["actual"] == "2" and not e["valid"] for e in attempts)
    assert trace[-1]["matches"] == 0 and trace[-1]["reason"] == "insufficient_exact_matches"
