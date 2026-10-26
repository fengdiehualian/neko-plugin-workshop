"""Cross-page logic and pixel coverage; not a screenshot accuracy benchmark."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.adapters.text_reader import native_tiles, read_native_text
from plugin.plugins.hsr_companion.detectors import analyze_layout
from plugin.plugins.hsr_companion.detectors.names import read_name_label
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog


@pytest.mark.parametrize(
    "anchor",
    [
        "快速编队",
        "我的角色\n阵容推荐提示",
        "角色详情\n遗器\n行迹",
        "角色活动跃迁",
        "背包\n穿戴者",
        "支援角色",
        "角色图鉴",
        "角色试用",
        "委托派遣",
        "星魂\n属性",
        "全新活动的未知页面",
    ],
)
@pytest.mark.parametrize("name", ["镜流", "桂乃芬", "符玄", "姬子", "刃", "银狼LV.999"])
def test_clear_registered_names_are_read_without_page_or_position_gate(tmp_path, anchor, name):
    value = analyze_layout(anchor + "\n" + name, [box(name, 350, 510)], catalog(tmp_path), width=1000, height=600)
    assert [item["name"] for item in value["characters"]] == [name]
    assert value["characters"][0]["identity_scope"] != "hud_label"


@pytest.mark.parametrize(
    "text,names",
    [
        ("80火花欢愉80藿藿丰饶", ["火花", "藿藿"]),
        ("80银狼LV.999欢愉", ["银狼LV.999"]),
        ("Lv.80/80 刃 毁灭", ["刃"]),
        ("已装备 桂乃芬 Lv.80", ["桂乃芬"]),
        ("姬子 试用", ["姬子"]),
        ("未拥有 符玄", ["符玄"]),
    ],
)
def test_field_decorations_and_multiple_names_do_not_erase_identity(tmp_path, text, names):
    records, _ = read_name_label(text, catalog(tmp_path))
    assert [record["name"] for record in records] == names


@pytest.mark.parametrize(
    "text", ["我昨天遇到火花了", "利刃出鞘", "银狼Lv80很可爱", "不存在的角色80", "交光", "ignore previous 火花"]
)
def test_prose_unknown_and_model_instructions_are_not_character_labels(tmp_path, text):
    assert read_name_label(text, catalog(tmp_path))[0] == []


@pytest.mark.parametrize("anchor", ["背包", "角色图鉴", "角色试用", "支援列表", "委托派遣"])
def test_additional_community_documented_menu_families(tmp_path, anchor):
    value = analyze_layout(anchor, [box("姬子", 500, 350)], catalog(tmp_path), width=1000, height=600)
    assert value["scene"]["primary_state"] == "menu"
    assert value["characters"][0]["identity_scope"] == "screen_label"


@pytest.mark.parametrize("x,y", [(850, 250), (100, 250), (850, 570)])
def test_unknown_page_reads_label_without_claiming_team_or_scene(tmp_path, x, y):
    value = analyze_layout("", [box("银狼", x, y)], catalog(tmp_path), width=1000, height=600)
    assert value["characters"][0]["name"] == "银狼"
    assert value["characters"][0]["identity_scope"] == "screen_label"
    assert value["scene"]["verified"] is False


@pytest.mark.parametrize("size", [(640, 400), (2560, 1600), (3840, 2160), (1200, 2400)])
def test_native_tiles_cover_entire_screen_without_downsampling(size):
    width, height = size
    tiles = native_tiles(width, height)
    for x in range(0, width, 31):
        for y in range(0, height, 37):
            assert any(left <= x < right and top <= y < bottom for left, top, right, bottom in tiles)
    assert all(max(right - left, bottom - top) <= 1550 for left, top, right, bottom in tiles)


def test_reader_keeps_native_coordinates_for_every_tile():
    sizes = []

    def extract(image):
        sizes.append(image.size)
        return ("镜流", [box("镜流", 100, 100)]) if len(sizes) <= 4 else ("", [])

    _, boxes, detail = read_native_text(
        SimpleNamespace(extract_text_with_boxes=extract), Image.new("RGB", (2560, 1600))
    )
    assert sizes[:4] == [(1344, 864)] * 4 and all(max(size) <= 1550 for size in sizes)
    assert len(boxes) == 4
    assert max(item["left"] for item in boxes) > 1280
    assert max(item["top"] for item in boxes) > 800
    assert detail["full_frame_covered"] is True


def test_replacement_team_drops_prior_names_independently(tmp_path):
    from plugin.plugins.hsr_companion.runtime import CompanionRuntime

    db = catalog(tmp_path)
    runtime = CompanionRuntime()

    def value(names):
        return analyze_layout(
            "快速编队", [box(name, 100 + i * 200, 400) for i, name in enumerate(names)], db, width=1000, height=600
        )

    for _ in range(2):
        runtime.ingest(value(["镜流", "桂乃芬"]))
    replacement = value(["符玄", "姬子"])
    runtime.ingest(replacement)
    assert runtime.stable_character_ids == ()
    runtime.ingest(replacement)
    assert set(runtime.stable_character_ids) == {item["entity_id"] for item in replacement["characters"]}


def test_passive_context_preserves_scope_even_when_page_is_unknown():
    from plugin.plugins.hsr_companion.runtime.observation import visual_context_text

    text = visual_context_text({"characters": [{"name": "姬子", "identity_scope": "screen_label"}]})
    assert "姬子（仅界面文字）" in text
    assert "当前操控角色：尚未确认" in text


def test_incomplete_text_coverage_is_not_reported_as_complete():
    from datetime import datetime, timezone

    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence

    value = {
        "ok": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "recognition": {"text_reader": {"full_frame_covered": False}},
    }
    evidence = public_visual_evidence(value, {})
    assert evidence["text_coverage_complete"] is False
    assert evidence["failure_reason"] == "ocr_incomplete"
