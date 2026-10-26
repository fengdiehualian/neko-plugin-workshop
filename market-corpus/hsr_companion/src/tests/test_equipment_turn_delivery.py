"""Recorded control overcoupling / half-height numeral, not live accuracy."""
from io import BytesIO

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.adapters.relic_values import refine_relic_values
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.detectors.layout import classify_supported_page
from plugin.plugins.hsr_companion.runtime.live_refresh import stable_screen_content
from plugin.plugins.hsr_companion.tests import test_guidance as guidance_cases
from plugin.plugins.hsr_companion.tests.test_recording_flow import choose
from plugin.plugins.hsr_companion.tests.test_relic_fields import relic_boxes

book = guidance_cases.book
catalog = guidance_cases.catalog


def image(color):
    out = BytesIO()
    Image.new("RGB", (1280, 800), color).save(out, "PNG")
    return out.getvalue()


def control(text, y, x=.05):
    return {"text": text, "control": True,
            "region": {"left": x, "top": y, "right": x + .15, "bottom": y + .04}}


def test_redundant_weak_tab_cannot_veto_a_separately_proved_page_heading():
    header, tab = control("角色详情", .03), control("光锥", .2)
    value = {"page": {"page_type": "character_detail"}, "scene": {"verified": True},
             "recognition": {"scene_regions": [header, tab]}, "screen_facts": {"facts": [], "visible_text": []}}
    def verify(payload, proofs, **kwargs):
        return {proof_key(p) for p in proofs if p["text"] == "角色详情"}
    result = stable_screen_content(value, image("black"), image("white"), verifier=verify)
    assert result["recognition"]["page_controls_revalidated"]
    assert result["recognition"]["scene_regions"] == [header]


def test_one_remaining_generic_tab_is_not_enough_to_guess_old_page():
    controls = [control("角色详情", .03), control("光锥", .2), control("遗器", .35)]
    value = {"page": {"page_type": "character_detail"}, "scene": {"verified": True},
             "recognition": {"scene_regions": controls}, "screen_facts": {"facts": [], "visible_text": []}}
    def verify(payload, proofs, **kwargs):
        return {proof_key(p) for p in proofs if p["text"] == "光锥"}
    result = stable_screen_content(value, image("black"), image("white"), verifier=verify)
    assert not result["recognition"]["page_controls_revalidated"]


def test_two_independent_tabs_follow_existing_classifier_not_all_anchor_rule():
    controls = [control("光锥", .2), control("行迹", .3), control("遗器", .4)]
    value = {"page": {"page_type": "character_detail"}, "scene": {"verified": True},
             "recognition": {"scene_regions": controls}, "screen_facts": {"facts": [], "visible_text": []}}
    def verify(payload, proofs, **kwargs):
        return {proof_key(p) for p in proofs if p["text"] != "光锥"}
    result = stable_screen_content(value, image("black"), image("white"), verifier=verify)
    assert result["recognition"]["page_controls_revalidated"]
    assert [p["text"] for p in result["recognition"]["scene_regions"]] == ["行迹", "遗器"]


def test_proved_page_does_not_authorize_changed_equipment_literal():
    header, tab = control("角色详情", .03), control("光锥", .2)
    number = control("16", .5, x=.75)
    number.pop("control")
    value = {"page": {"page_type": "character_detail"}, "scene": {"verified": True},
             "recognition": {"scene_regions": [header, tab]}, "screen_facts": {"visible_text": [], "facts": [
                 {"kind": "sub_stat", "scope": "equipment", "label": "攻击力", "value": "16", "evidence": [number]}]}}
    def verify(payload, proofs, **kwargs):
        return {proof_key(p) for p in proofs if p["text"] == "角色详情"}
    result = stable_screen_content(value, image("black"), image("white"), verifier=verify)
    assert result["recognition"]["page_controls_revalidated"]
    assert not result["screen_facts"]["facts"]
    assert result["screen_facts"]["field_checks"][0]["state"] == "pending"


@pytest.mark.parametrize("heading", ["角色光锥", "角色遗器"])
def test_actual_equipment_section_heading_is_a_heading_not_three_menu_tabs(heading):
    boxes = [{"text": heading, "score": .99, "left": 20, "right": 200, "top": 20, "bottom": 50},
             {"text": "遗器", "score": .99, "left": 20, "right": 100, "top": 300, "bottom": 330}]
    page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=1280, height=800)
    assert page["page_type"] == "character_detail"
    boxes[0].update(top=700, bottom=730)
    assert classify_supported_page("\n".join(b["text"] for b in boxes), boxes,
                                   width=1280, height=800)["page_type"] == "unknown"


@pytest.mark.parametrize("fragment_height,expected", [(19, False), (37, True)])
def test_numeric_recovery_requires_full_row_glyph_not_a_confident_lower_half(fragment_height, expected):
    # Real failed source ratio: 18px recovered fragment / 35px stat label.
    boxes = relic_boxes()
    boxes.pop(6)  # Missing first numeric row; table recovery is allowed.
    for b in boxes:
        for key in ("left", "right", "top", "bottom"):
            b[key] *= 2
    class Backend:
        def extract_text_with_boxes(self, sample):
            return "6", [{"text": "6", "score": .999, "left": 110, "right": 135,
                           "top": 22, "bottom": 22 + fragment_height}]
    updated, details = refine_relic_values(Image.new("RGB", (2560, 1600)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend(), table_bounds=[1390, 240, 2040, 680])
    assert bool(details["confirmed"]) is expected
    assert any(b.get("numeric_recheck", {}).get("confirmed") is expected for b in updated)


@pytest.mark.parametrize("original,retried,expected", [(".16", "6", 0), (".16", "16", 1), ("8.2%0", "8.2%", 1)])
def test_full_height_retry_cannot_drop_a_previously_detected_leading_digit(original, retried, expected):
    boxes = relic_boxes()
    boxes[6]["text"] = original
    class Backend:
        def extract_text_with_boxes(self, sample):
            from plugin.plugins.hsr_companion.tests.test_relic_fields import atlas_reading
            return retried, [atlas_reading(retried, sample)]
    updated, details = refine_relic_values(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend(), table_bounds=[695, 120, 1020, 340])
    assert details["confirmed"] == expected
    if not expected:
        assert not any(b["text"] == "6" for b in updated)


def test_cone_fit_requires_an_entity_not_relic_rows(book, catalog):
    from plugin.plugins.hsr_companion.runtime.query_coordinator import satisfied_fields
    _, demand = choose("这个光锥适合绯英吗？", book, catalog)
    assert not demand.required_equipment_kinds
    visual = {"fresh": True, "read_scope": "full", "scene": {"verified": True},
              "screen_facts": {"facts": [{"kind": "entity_label", "scope": "displayed_label",
                  "entity_type": "light_cone", "entity_id": "light_cone.23055", "value": "献给明日的色彩"}]}}
    assert demand.fields <= satisfied_fields(demand, visual)
