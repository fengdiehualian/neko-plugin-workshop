"""Overworld field semantics without keyboard hints or character names."""
from __future__ import annotations

from io import BytesIO

import pytest
from plugin.plugins.hsr_companion.data.catalog import StructuredCatalog
from plugin.plugins.hsr_companion.detectors.layout import analyze_layout, classify_supported_page
from plugin.plugins.hsr_companion.detectors.scene_preview import scene_preview
from plugin.plugins.hsr_companion.runtime.grounding import build_grounding
from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog, visual


def hud(place="地点甲", title="任务甲", objective="寻找线索甲", destination="区域乙"):
    return [box(place, 38, 10), box("状态效果", 1180, 195), box(title, 50, 185),
            box(objective, 52, 214), box("请前往" + destination, 52, 245)]


def classify(boxes):
    return classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=1280, height=800)


def extract(boxes, page=None, db=None):
    return extract_screen_facts(boxes, width=1280, height=800, page=page or classify(boxes), catalog=db or catalog())


@pytest.mark.parametrize("place,title,objective,destination", [
    ("地点甲", "任务甲", "寻找线索甲", "区域乙"), ("地点丙", "任务乙", "与向导交谈", "区域丁"),
])
def test_overworld_is_independent_of_names_levels_and_keyboard_shortcuts(place, title, objective, destination):
    boxes = hud(place, title, objective, destination)
    layout = analyze_layout("\n".join(b["text"] for b in boxes), boxes, catalog(), width=1280, height=800)
    assert layout["page"]["page_type"] == "exploration_hud" and not layout["characters"]
    assert scene_preview(boxes, width=1280, height=800)["page"]["page_type"] == "exploration_hud"
    values = {(f["kind"], f["value"]) for f in extract(boxes)["facts"]}
    assert ("current_location", place) in values and ("quest_destination", destination) in values
    assert ("tracked_quest", title) in values and ("quest_objective", objective) in values
    assert ("current_location", destination) not in values


@pytest.mark.parametrize("missing", [0, 1, 4])
def test_missing_independent_control_does_not_make_corner_name_a_location(missing):
    boxes = hud()
    boxes.pop(missing)
    assert classify(boxes)["page_type"] == ("exploration_hud" if missing == 0 else "unknown")
    assert not any(f["kind"] == "current_location" for f in extract(boxes)["facts"])


def test_menu_title_overrides_background_overworld_controls():
    boxes = hud() + [box("队伍编成", 400, 30)]
    assert classify(boxes)["page_type"] == "team_setup"
    assert not any(f["kind"] == "current_location" for f in extract(boxes)["facts"])


def test_ambiguous_header_and_near_equal_objective_retries_are_not_picked():
    boxes = hud() + [box("地点乙", 150, 10)]
    assert classify(boxes)["page_type"] == "exploration_hud"
    assert not any(f["kind"] == "current_location" for f in extract(boxes)["facts"])
    assert any(f["kind"] == "quest_objective" for f in extract(boxes)["facts"])
    boxes = hud() + [box("另一任务目标", 52, 214, score=.98)]
    result = extract(boxes)
    assert not any(f["value"] in {"寻找线索甲", "另一任务目标"} for f in result["facts"])
    # Do not promote the preceding title to objective after a conflict.
    assert not any(f["kind"] == "quest_objective" and f["value"] == "任务甲" for f in result["facts"])


def test_clearly_better_retry_replaces_corrupted_navigation_and_objective():
    boxes = hud() + [box("请前往错误残片", 52, 244, score=.86), box("寻找错字", 51, 213, score=.9)]
    values = {f["value"] for f in extract(boxes)["facts"]}
    assert "区域乙" in values and "寻找线索甲" in values
    assert "错误残片" not in values and "寻找错字" not in values


@pytest.mark.parametrize("destination", ["区域丙", "区域乙中心", "另一个导航名称"])
def test_destination_retry_conflict_does_not_erase_other_independent_fields(destination):
    boxes = hud() + [box("请前往" + destination, 52, 245, score=.96)]
    result = extract(boxes)
    values = {(f["kind"], f["value"]) for f in result["facts"]}
    assert ("current_location", "地点甲") in values
    assert ("tracked_quest", "任务甲") in values
    assert ("quest_objective", "寻找线索甲") in values
    assert not any(f["kind"] == "quest_destination" for f in result["facts"])


def test_conflicting_objective_only_revokes_that_value_not_location():
    boxes = hud() + [box("不同目标", 52, 214, score=.98)]
    result = extract(boxes)
    assert any(f["kind"] == "current_location" for f in result["facts"])
    assert not any(f["kind"] == "quest_objective" for f in result["facts"])


@pytest.mark.parametrize("prefix", ["图", "符V号", "看店V童"])
def test_navigation_icon_glyphs_allow_location_not_cleaned_destination(prefix):
    boxes = hud()
    boxes[-1]["text"] = prefix + boxes[-1]["text"]
    assert classify(boxes)["page_type"] == "exploration_hud"
    result = extract(boxes)
    assert any(f["kind"] == "current_location" and f["value"] == "地点甲" for f in result["facts"])
    assert not any(f["kind"] in {"quest_destination", "tracked_quest", "quest_objective"} for f in result["facts"])


def test_navigation_phrase_in_paragraph_does_not_establish_hud_control():
    boxes = hud()
    boxes[-1]["text"] = "这里有一段文字提醒请前往区域乙"
    assert classify(boxes)["page_type"] == "unknown"


def test_quest_query_keeps_destination_but_does_not_call_it_current_location():
    result = build_grounding("当前任务是什么", visual(extract(hud())), catalog())
    assert any(f["kind"] == "quest_destination" and f["value"] == "区域乙" for f in result["facts"])
    assert not any(f["kind"] == "current_location" for f in result["facts"])


def test_thumbnail_missing_objective_does_not_promote_title_even_with_large_navigation_box():
    boxes = hud()
    boxes.pop(3)
    boxes[-1]["bottom"] = 300
    result = extract(boxes)
    assert any(f["kind"] == "current_location" for f in result["facts"])
    assert not any(f["kind"] in {"quest_objective", "tracked_quest"} for f in result["facts"])


@pytest.mark.parametrize("page", ["unknown", "exploration_hud", "team_setup", "character_detail", "warp"])
def test_known_character_label_not_also_grounded_to_same_named_item_or_foreign_trace(page):
    db = StructuredCatalog({"meta": {"schema_version": 2, "data_revision": "test"}, "records": [
        {"id": "character.a", "name": "同名甲", "entity_type": "character", "source_refs": ["test"]},
        {"id": "item.a", "name": "同名甲", "entity_type": "item", "source_refs": ["test"]},
        {"id": "character_trace.a", "name": "同名甲", "entity_type": "character_trace", "owner_ids": ["character.other"], "source_refs": ["test"]},
    ]}, {"sources": [{"source_id": "test", "source_url": "https://example.com/test-only"}]})
    assert db.resolve_character("同名甲")
    result = extract([box("同名甲", 1150, 300)], {"page_type": page}, db)
    assert not any(f["kind"] == "entity_label" for f in result["facts"])


def test_scene_path_reads_native_location_without_character_pipeline():
    from PIL import Image
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    class Backend:
        calls = 0
        def extract_text_with_boxes(self, image):
            self.calls += 1
            return "", hud()
    backend = Backend()
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: backend
    payload = BytesIO()
    Image.new("RGB", (2560, 1600)).save(payload, format="PNG")
    result = adapter.analyze_scene(payload.getvalue())
    # Even page/location questions must not publish only a thumbnail reading
    # of the small place heading. Native left HUD is checked on the same frame.
    assert backend.calls == 2
    assert result["scene"]["verified"] and not result["characters"]
    assert any(f["kind"] == "current_location" and f["value"] == "地点甲" for f in result["screen_facts"]["facts"])
