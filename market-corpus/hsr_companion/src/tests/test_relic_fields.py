"""Relic field binding contracts, not an OCR accuracy benchmark."""
import pytest
from plugin.plugins.hsr_companion.detectors.layout import classify_supported_page
from plugin.plugins.hsr_companion.detectors.scene_preview import scene_preview
from plugin.plugins.hsr_companion.runtime.grounding import build_grounding
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog, facts, visual


def atlas_reading(text, atlas):
    """Simulate a whole glyph at the actual pasted scale, not a 20px stub.

    These tests exercise badge recovery/grammar; half-glyph rejection has its
    own recorded regression. The source fixture is a solid colour rectangle.
    """
    from PIL import Image, ImageChops
    left, top, right, bottom = ImageChops.difference(
        atlas, Image.new("RGB", atlas.size, (25, 32, 46))).getbbox()
    return {"text": text, "score": .99, "left": left + 2, "right": right - 2,
            "top": top + (bottom - top) * .1, "bottom": bottom - (bottom - top) * .1}


def relic_boxes(part="头部", *, x=700, y=100):
    def b(text, dx, dy, width=90, height=20):
        return {"text": text, "left": x + dx, "top": y + dy, "right": x + dx + width,
                "bottom": y + dy + height, "score": .99}
    return [b("任意遗器标题", 0, -35), b(part, 0, 0), b("+15", 0, 25),
            b("生命值", 25, 55), b("705", 220, 55),
            b("攻击力", 25, 90), b("57", 220, 90),
            b("生命值", 25, 125), b("8.2%", 220, 125),
            b("攻击力", 25, 160), b("3.8%", 220, 160),
            b("效果命中", 25, 195), b("7.7%", 220, 195), b("套装效果", 0, 240)]


@pytest.mark.parametrize("part", ["头部", "手部", "躯干", "脚部", "位面球", "连结绳"])
@pytest.mark.parametrize("position", [(80, 80), (700, 300)])
def test_relic_fields_follow_controls_not_item_name_or_right_hand_pixels(part, position):
    boxes = relic_boxes(part, x=position[0], y=position[1]) + [box("速度:999", 1100, 700)]
    result = facts(boxes, "relic_detail")
    values = {(f["kind"], f["label"], f["value"]) for f in result["facts"]}
    assert ("main_stat", "主词条·生命值", "705") in values
    assert ("sub_stat", "副词条·攻击力", "57") in values
    assert len([f for f in result["facts"] if f["kind"] == "sub_stat"]) == 4
    assert not any(f["kind"] == "stat" for f in result["facts"])
    assert ("equipment_name", "遗器名称（画面文字）", "任意遗器标题") in values


def test_icon_suffix_is_not_silently_fixed_or_used_as_a_number():
    boxes = relic_boxes()
    boxes[8]["text"] = "8.2%0"
    result = facts(boxes, "relic_detail")
    assert any(f["kind"] == "unreadable_stat" and f["label"] == "副词条·生命值" for f in result["facts"])
    assert not any(f["value"] == "8.2%0" for f in result["facts"])
    grounded = build_grounding("这件遗器的主词条和副词条是什么", visual(result), catalog())
    assert any("部分装备词条" in message for message in grounded["missing_information"])


def test_missing_main_label_does_not_promote_first_substat():
    boxes = relic_boxes()
    boxes.pop(3)
    result = facts(boxes, "relic_detail")
    assert not any(f["kind"] == "main_stat" for f in result["facts"])
    assert any(f["kind"] == "equipment_binding_missing" for f in result["facts"])


def test_comparison_columns_and_missing_controls_do_not_choose_an_item():
    for boxes in (relic_boxes(x=50) + relic_boxes(x=650), relic_boxes()[:-1]):
        result = facts(boxes, "relic_detail")
        assert not any(f["kind"] in {"main_stat", "sub_stat", "stat"} for f in result["facts"])
        grounded = build_grounding("这件遗器的主词条是什么", visual(result), catalog())
        assert any("尚未可靠区分" in message for message in grounded["missing_information"])


def test_title_must_be_a_heading_not_footer_or_lore_prose():
    for heading in ("遗器替换", "遗器详情"):
        boxes = [box(heading, 20, 40), box("套装效果", 900, 600)]
        text = "\n".join(b["text"] for b in boxes)
        assert classify_supported_page(text, boxes, width=1280, height=800)["page_type"] == "relic_detail"
        assert scene_preview(boxes, width=1280, height=800)["page"]["page_type"] == "relic_detail"
    boxes = [box("遗器替换", 20, 700)]
    assert classify_supported_page("遗器替换", boxes, width=1280, height=800)["page_type"] == "unknown"


def test_badge_detection_requires_round_filled_green_control_after_number():
    from PIL import Image, ImageDraw
    from plugin.plugins.hsr_companion.adapters.relic_values import badge_bounds
    image = Image.new("RGB", (90, 26), (20, 30, 40))
    draw = ImageDraw.Draw(image)
    draw.ellipse((60, 3, 79, 22), fill=(110, 220, 170))
    assert badge_bounds(image)
    draw.rectangle((0, 0, 89, 25), fill=(20, 30, 40))
    draw.ellipse((60, 3, 79, 22), fill=(220, 110, 110))
    assert badge_bounds(image) is None
    draw.rectangle((0, 0, 89, 25), fill=(20, 30, 40))
    draw.rectangle((60, 3, 62, 22), fill=(110, 220, 170))
    assert badge_bounds(image) is None


@pytest.mark.parametrize("recognized", ["57", "broken", "error"])
@pytest.mark.parametrize("original", ["572", "57²"])
def test_badge_number_is_reread_not_trimmed_or_left_as_valid_572(recognized, original):
    from PIL import Image, ImageDraw
    from plugin.plugins.hsr_companion.adapters.relic_values import refine_relic_values
    image = Image.new("RGB", (1280, 800), (20, 30, 40))
    boxes = relic_boxes()
    boxes[6]["text"] = original
    draw = ImageDraw.Draw(image)
    draw.ellipse((972, 193, 989, 210), fill=(110, 220, 170))
    class Backend:
        calls = 0
        def extract_text_with_boxes(self, atlas):
            self.calls += 1
            if recognized == "error":
                raise RuntimeError("test unavailable")
            return recognized, [atlas_reading(recognized, atlas)]
    backend = Backend()
    updated, details = refine_relic_values(image, boxes, page={"page_type": "relic_detail"}, backend=backend)
    assert backend.calls == 1 and details["candidates"] == 1
    values = facts(updated, "relic_detail")["facts"]
    assert not any(f["value"] == "572" for f in values)
    if recognized == "57":
        assert any(f["value"] == "57" and f.get("value_verification") for f in values)
    else:
        assert any(f["kind"] == "unreadable_stat" for f in values)


def test_merged_stat_label_and_number_is_not_dropped_from_relic_rows():
    from PIL import Image, ImageDraw
    from plugin.plugins.hsr_companion.adapters.relic_values import refine_relic_values
    image = Image.new("RGB", (1280, 800), (20, 30, 40))
    boxes = relic_boxes()
    boxes[5].update(text="攻击力57²", right=1010)
    boxes.pop(6)
    ImageDraw.Draw(image).ellipse((972, 193, 989, 210), fill=(110, 220, 170))
    class Backend:
        def extract_text_with_boxes(self, atlas):
            return "攻击力57", [atlas_reading("攻击力57", atlas)]
    updated, details = refine_relic_values(image, boxes, page={"page_type": "relic_detail"}, backend=Backend())
    assert details["confirmed"] == 1
    values = facts(updated, "relic_detail")["facts"]
    assert any(f["kind"] == "sub_stat" and f["label"] == "副词条·攻击力" and f["value"] == "57" for f in values)
    assert len([f for f in values if f["kind"] == "sub_stat"]) == 4


@pytest.mark.parametrize("omit_row", [False, True])
def test_whole_table_read_masks_count_controls_and_rejects_less_complete_reading(omit_row):
    from PIL import Image, ImageDraw
    from plugin.plugins.hsr_companion.adapters.relic_values import read_relic_table
    image = Image.new("RGB", (1280, 800), (20, 30, 40))
    boxes = relic_boxes()
    boxes[6]["text"] = "57²"
    ImageDraw.Draw(image).ellipse((972, 193, 989, 210), fill=(110, 220, 170))
    class Backend:
        def extract_text_with_boxes(self, crop):
            # The control's center is actually erased before recognition.
            assert crop.getpixel((285, 80)) == (25, 32, 46)
            output = []
            for index, b in enumerate(relic_boxes()):
                if index < 2 or index == 13 or (omit_row and index == 5):
                    continue
                output.append({**b, "left": b["left"] - 695, "right": b["right"] - 695,
                               "top": b["top"] - 120, "bottom": b["bottom"] - 120})
            return "", output
    updated, details = read_relic_table(image, boxes, page={"page_type": "relic_detail"}, backend=Backend())
    assert details["masked_badges"] == 1
    assert details["accepted"] is not omit_row
    if omit_row:
        assert updated == boxes
    else:
        values = facts(updated, "relic_detail")["facts"]
        assert any(f["label"] == "副词条·攻击力" and f["value"] == "57" for f in values)
        assert len([f for f in values if f["kind"] == "sub_stat"]) == 4


@pytest.mark.parametrize("missing", [False, True])
def test_number_retry_can_recover_missing_or_non_numeric_text_without_guessing(missing):
    from PIL import Image
    from plugin.plugins.hsr_companion.adapters.relic_values import refine_relic_values
    boxes = relic_boxes()
    if missing:
        boxes.pop(6)
    else:
        boxes[6]["text"] = "I6"
    class Backend:
        def extract_text_with_boxes(self, atlas):
            return "16", [atlas_reading("16", atlas)]
    updated, details = refine_relic_values(Image.new("RGB", (1280, 800)), boxes,
        page={"page_type": "relic_detail"}, backend=Backend(), table_bounds=[695, 120, 1020, 340])
    assert details["candidates"] == details["confirmed"] == 1
    values = facts(updated, "relic_detail")["facts"]
    assert any(f["label"] == "副词条·攻击力" and f["value"] == "16" for f in values)


@pytest.mark.parametrize("label", ["物理属性伤害提高", "火属性伤害提高", "冰属性伤害提高", "雷属性伤害提高",
                                  "风属性伤害提高", "量子属性伤害提高", "虚数属性伤害提高", "能量恢复效率", "治疗量加成"])
def test_other_main_stat_types_not_limited_to_two_live_samples(label):
    boxes = relic_boxes("位面球")
    boxes[3]["text"] = label
    boxes[4]["text"] = "38.8%"
    values = facts(boxes, "relic_detail")["facts"]
    assert any(f["kind"] == "main_stat" and f["label"] == "主词条·" + label and f["value"] == "38.8%" for f in values)
