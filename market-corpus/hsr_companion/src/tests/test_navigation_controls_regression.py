"""Frozen live failures plus generated controls; not new blind-test passes."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.detectors.exploration import generic_navigation
from plugin.plugins.hsr_companion.runtime.navigation import navigation_answer
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box
from plugin.plugins.hsr_companion.tests.test_exploration_fields import classify, extract, hud
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog, visual


@pytest.mark.parametrize("label", ["前往任务目标", "V前往任务目标", "前往任务目标2米", "前往任务目标 1.5千米", "前往任务目标205m"])
@pytest.mark.parametrize("social", [True, False])
def test_generic_navigation_binds_task_but_never_becomes_a_destination(label, social):
    boxes = hud(place="未知测试区域", title="新追踪标题", objective="询问向导关于线索的下落")
    boxes[-1]["text"] = label
    if social:
        boxes.pop(1)
        boxes.append(box("Enter", 45, 720))
    assert classify(boxes)["page_type"] == "exploration_hud"
    answer = navigation_answer(visual(extract(boxes)), requested=True)
    assert answer["current_location"]["value"] == "未知测试区域"
    assert answer["quest_title"]["value"] == "新追踪标题"
    assert answer["objective"]["value"] == "询问向导关于线索的下落"
    assert not answer["destination"]["known"]
    assert answer["missing_fields"] == ["destination"]
    assert answer["game_task_completion"]["status"] == "unknown"


@pytest.mark.parametrize("label", ["前往任务目标的意思是", "前往任务目标已完成", "这里有一段说明前往任务目标", "前往任务目标某地"])
def test_button_phrase_in_prose_is_not_a_navigation_control(label):
    assert not generic_navigation(label)
    boxes = hud()
    boxes[-1]["text"] = label
    assert classify(boxes)["page_type"] == "unknown"


def test_generic_button_wrong_region_and_menu_overlay_do_not_publish_location():
    boxes = hud()
    boxes[-1].update(text="前往任务目标", left=900, right=1060)
    assert classify(boxes)["page_type"] == "unknown"
    boxes = hud() + [box("队伍编成", 500, 20)]
    boxes[4]["text"] = "前往任务目标"
    assert classify(boxes)["page_type"] == "team_setup"
    assert not any(f["kind"] == "current_location" for f in extract(boxes)["facts"])


def test_location_failure_does_not_erase_independent_task_fields():
    boxes = hud()
    boxes[0]["score"] = .4
    boxes[-1]["text"] = "前往任务目标"
    assert "左上区域标题" not in classify(boxes)["evidence"]
    answer = navigation_answer(visual(extract(boxes)), requested=True)
    assert not answer["current_location"]["known"]
    assert answer["objective"]["value"] == "寻找线索甲"
    assert answer["quest_title"]["value"] == "任务甲"


def replay(place, title, objective, nav):
    # Actual two-pass geometry: thumbnail merged columns; native read separates
    # the title. Names are parameters, not recognition whitelist entries.
    probe = [
        {**box("噪声区域", 46, 8), "right": 169, "bottom": 30},
        {**box(title + "状态效果", 50, 184), "right": 1258, "bottom": 213},
        {**box(objective, 52, 212), "right": 240, "bottom": 232},
        {**box("V" + nav, 53, 237), "right": 178, "bottom": 257},
        box("Enter", 52, 713),
    ]
    native = [
        {**box(place, 90, 21), "right": 331, "bottom": 54},
        {**box(title, 107, 379), "right": 330, "bottom": 409},
        {**box(objective, 110, 428), "right": 474, "bottom": 458},
        {**box(nav, 155, 477), "right": 1091 if nav.endswith("2米") else 332, "bottom": 510},
    ]
    return probe, native


@pytest.mark.parametrize("place,title,objective,nav", [
    ("千星城中心城区", "剧场及其复象", "向虚照追问现况", "前往任务目标2米"),
    ("永恒圣城奥赫玛", "黑潮时期的爱情", "询问哈托努斯关于宁芙的下落", "前往任务目标"),
    ("独立测试区域", "任意标题", "寻找另一位向导", "前往任务目标"),
])
@pytest.mark.parametrize("size", [(2560, 1600), (1920, 1080), (1280, 800)])
def test_native_region_replaces_thumbnail_copies_not_outside_controls(place, title, objective, nav, size):
    probe, native = replay(place, title, objective, nav)
    stream = BytesIO()
    Image.new("RGB", size).save(stream, format="PNG")
    calls = []
    def read(image):
        calls.append(image.size)
        values = probe if len(calls) == 1 else native
        reference = (1280, 800) if len(calls) == 1 else (2560, 1600)
        # Thumbnail has aspect-preserving actual dimensions; native crop keeps
        # the full source scale, rather than scaling its box to crop size.
        target = image.size if len(calls) == 1 else size
        return "", [{**b, **{key: b[key] * factor for key, factor in (
            ("left", target[0] / reference[0]), ("right", target[0] / reference[0]),
            ("top", target[1] / reference[1]), ("bottom", target[1] / reference[1]))}} for b in values]
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=read)
    result = adapter.analyze_navigation(stream.getvalue())
    assert len(calls) == 2
    assert result["scene"]["verified"] and not result["characters"]
    answer = navigation_answer(visual(result["screen_facts"]), requested=True)
    assert answer["current_location"]["value"] == place
    assert answer["quest_title"]["value"] == title
    assert answer["objective"]["value"] == objective
    assert not answer["destination"]["known"]
    assert "噪声区域" not in str(result["screen_facts"]["facts"])
    assert result["recognition"]["scene_regions"]  # keep latest-frame guards
    assert result["recognition"]["field_reading"]["fact_count"] == 3
    assert result["recognition"]["field_reading"]["revision"] == "navigation_controls_v4"
    assert result["recognition"]["navigation_rows"]


def test_readable_generic_button_does_not_force_an_absent_destination_retry():
    boxes = hud()
    boxes[-1]["text"] = "前往任务目标"
    calls = []
    def read(image):
        calls.append(image.size)
        return "", boxes
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=read)
    stream = BytesIO()
    Image.new("RGB", (1280, 800)).save(stream, format="PNG")
    result = adapter.analyze_navigation(stream.getvalue())
    # Accuracy candidate always verifies native navigation, not an absent
    # destination. A confident thumbnail alone can omit a character/digit.
    assert calls == [(1280, 800), (704, 480)]
    assert not any(f["kind"] == "quest_destination" for f in result["screen_facts"]["facts"])


def test_native_conflict_still_revokes_only_its_own_value():
    probe, native = replay("区域甲", "标题甲", "询问向导甲", "前往任务目标")
    native.append({**native[2], "text": "询问向导乙"})
    assert not any(f["kind"] == "quest_objective" for f in extract([
        *[{**b, **{key: b[key] / 2 for key in ("left", "right", "top", "bottom")}} for b in native],
        box("状态效果", 1180, 195),
    ])["facts"])


@pytest.mark.parametrize("qualifier,name", [("「区域别称」", "陌生区域"), ("『某地称号』", "地点乙")])
@pytest.mark.parametrize("scale", [.5, 1, 1.5])
def test_touching_location_fragments_and_noisy_status_use_independent_social_controls(qualifier, name, scale):
    from plugin.plugins.hsr_companion.detectors.layout import classify_supported_page
    from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts

    boxes = [
        {**box(qualifier, 86, 18), "right": 249, "bottom": 55},
        {**box(name, 246, 21), "right": 333, "bottom": 53},
        {**box("追踪任务甲", 103, 374), "right": 333, "bottom": 414},
        {**box("询问向导关于线索的下落", 107, 424), "right": 476, "bottom": 460},
        {**box("前往任务目标", 152, 473), "right": 333, "bottom": 512},
        box("状态效果0", 2350, 385), box("Enter", 104, 1426),
    ]
    boxes = [{**b, **{key: b[key] * scale for key in ("left", "top", "right", "bottom")}} for b in boxes]
    page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=2560 * scale, height=1600 * scale)
    assert page["page_type"] == "exploration_hud"
    screen = extract_screen_facts(boxes, width=2560 * scale, height=1600 * scale, page=page, catalog=catalog())
    answer = navigation_answer(visual(screen), requested=True)
    assert answer["current_location"]["value"] == qualifier + name
    assert answer["objective"]["value"] == "询问向导关于线索的下落"
    assert not answer["destination"]["known"]


@pytest.mark.parametrize("position", [(210, 21), (246, 60), (350, 21)])
def test_overlapping_or_separated_header_candidates_are_not_joined(position):
    from plugin.plugins.hsr_companion.detectors.exploration import location_headings

    x, y = position
    boxes = [{**box("「区域别称」", 86, 18), "right": 249, "bottom": 55}, box("另一区域", x, y)]
    assert all(b["text"] != "「区域别称」另一区域" for b in location_headings(boxes, width=2560, height=1600))
