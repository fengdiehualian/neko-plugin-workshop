"""PoC methodology/geometry, NOT model accuracy or installed-host acceptance."""
from io import BytesIO
from types import SimpleNamespace
import threading

import pytest
from PIL import Image

from plugin.plugins.hsr_companion.experiments.regional_reader import RegionalReader, select_regions, translate_boxes
from plugin.plugins.hsr_companion.scripts.benchmark_regional_poc import arm_order, read_pair, focused
from plugin.plugins.hsr_companion.tests.test_relic_fields import relic_boxes
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog


def b(text, left, top, right, bottom):
    return dict(text=text, score=.99, left=left, top=top, right=right, bottom=bottom)


def test_coordinate_mapping_preserves_native_geometry():
    original = b("文本", 2, 3, 12, 13)
    scaled = translate_boxes([original], left=100, top=200, sx=2, sy=3)[0]
    assert (scaled["left"], scaled["top"], scaled["right"], scaled["bottom"]) == (104, 209, 124, 239)
    assert original["left"] == 2


@pytest.mark.parametrize("scale", [.75, 1, 2])
@pytest.mark.parametrize("x", [80, 700])
def test_relic_column_follows_unique_anchor_at_different_positions_and_sizes(scale, x):
    boxes = translate_boxes(relic_boxes(x=x), sx=scale, sy=scale)
    regions = select_regions("relic_detail", boxes, round(1280*scale), round(800*scale))
    assert len(regions) == 1
    left, top, right, bottom = regions[0][1]
    assert left <= boxes[0]["left"] < right and top <= boxes[0]["top"] < bottom
    assert bottom >= boxes[-1]["bottom"]
    assert right <= 1280*scale and bottom <= 800*scale


def test_comparison_columns_and_unknown_page_do_not_choose_an_item():
    assert not select_regions("relic_detail", relic_boxes(x=50)+relic_boxes(x=650), 1280, 800)
    assert not select_regions("warp", relic_boxes(), 1280, 800)


def test_native_exploration_fields_share_one_capture_without_full_pipeline():
    width, height = 1920, 1080
    boxes = [b("长乐天", 20, 20, 200, 48), b("剧场及其复象", 30, 180, 250, 204),
             b("向虚照追问现况", 30, 215, 270, 239), b("前往任务目标", 30, 250, 230, 274),
             b("状态效果", 1500, 180, 1650, 204)]
    rects = select_regions("exploration_hud", boxes, width, height)
    queues = [translate_boxes(boxes, sx=.5, sy=.5)]
    sizes = [(960, 540)]
    for _, (left, top, right, bottom) in rects:
        queues.append(translate_boxes([value for value in boxes
            if left <= value["left"] < value["right"] <= right and top <= value["top"] < value["bottom"] <= bottom],
            left=-left, top=-top))
        sizes.append((right-left, bottom-top))
    class Backend:
        def extract_text_with_boxes(self, image):
            assert image.size == sizes.pop(0)
            values = queues.pop(0)
            return "", values
    payload = BytesIO()
    Image.new("RGB", (width, height)).save(payload, "PNG")
    result = RegionalReader(Backend(), catalog()).analyze(payload.getvalue())
    assert result["scene"]["verified"]
    assert {(f["kind"], f["value"]) for f in result["screen_facts"]["facts"]} >= {
        ("current_location", "长乐天"), ("tracked_quest", "剧场及其复象"), ("quest_objective", "向虚照追问现况")}
    assert len(result["recognition"]["text_reader"]["backend_calls"]) == 4
    assert not queues and not sizes


def test_same_input_and_serial_counterbalanced_order():
    payload = b"one immutable captured image"
    calls = []
    def reader(name):
        def read(value):
            assert value is payload
            calls.append((name, threading.get_ident()))
            return {"page": {}, "screen_facts": {"facts": []}}
        return read
    readers = {name: reader(name) for name in ("full", "regional")}
    for index in range(3):
        read_pair(readers, payload, index, SimpleNamespace(calls=[]), "exploration_hud")
    assert [name for name, _ in calls] == [name for i in range(3) for name in arm_order(i)]
    assert len({tid for _, tid in calls}) == 1


def test_scoring_filters_unasked_characters_and_equipment_from_navigation():
    raw = {"characters": [{"name": "角色"}], "recognition": {"scene_regions": []},
           "screen_facts": {"facts": [{"scope": "location"}, {"scope": "equipment"}], "visible_text": []}}
    selected = focused(raw, "exploration_hud")
    assert selected["characters"] == []
    assert selected["screen_facts"]["facts"] == [{"scope": "location"}]
    assert len(raw["screen_facts"]["facts"]) == 2
