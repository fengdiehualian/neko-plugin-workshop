"""Logic/transport regressions, not a claim of real-image accuracy."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.detectors.team import assign_team_slots, infer_complete_team_rows, locate_team_rows
from plugin.plugins.hsr_companion.runtime import CompanionRuntime
from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture


def role(entity_id, slot):
    return {"entity_id": entity_id, "name": entity_id, "slot": slot, "identity_scope": "hud_label"}


def test_each_position_confirms_independently_without_waiting_for_whole_team():
    runtime = CompanionRuntime(global_rate_seconds=0)
    a, b, c = role("a", 1), role("b", 2), role("c", 3)
    runtime.ingest({"characters": [a, b]})
    runtime.ingest({"characters": [a, b, c]})
    assert runtime.stable_character_ids == ("a", "b")
    runtime.ingest({"characters": [a, c]})
    assert runtime.stable_character_ids == ("a", "c")
    runtime.ingest({"characters": [role("d", 1), c]})
    assert runtime.stable_character_ids == ("c",)
    runtime.invalidate_observation()
    runtime.ingest({"characters": [c]})
    assert runtime.stable_character_ids == ()


def test_name_and_avatar_evidence_can_confirm_same_position():
    runtime = CompanionRuntime(global_rate_seconds=0)
    runtime.ingest({"characters": [role("a", 1)]})
    runtime.ingest({"characters": [{**role("a", 1), "identity_scope": "hud_portrait"}]})
    assert runtime.stable_character_ids == ("a",)


def test_native_ocr_accepts_slotted_boxes_and_reads_original_pixels(tmp_path):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
    from plugin.plugins.hsr_companion.tests.test_experience_fixes import catalog

    @dataclass(slots=True)
    class Box:
        text: str = "银狼LV.999"
        score: float = 0.99
        left: float = 1106
        top: float = 306
        right: float = 1180
        bottom: float = 330

    image = Image.new("RGB", (2560, 1600), "black")
    image.putpixel((2445, 777), (255, 254, 253))
    payload = BytesIO()
    image.save(payload, format="PNG")
    sizes = []

    def extract(crop):
        sizes.append(crop.size)
        return ("银狼LV.999", [Box()]) if len(sizes) == 1 else ("", [])

    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._backend = SimpleNamespace(extract_text_with_boxes=extract)
    result = adapter.analyze(payload.getvalue())
    assert result["ok"] is True
    assert result["characters"][0]["name"] == "银狼LV.999"
    assert result["characters"][0]["region"]["left"] == pytest.approx(1106 / 2560, abs=0.0001)
    assert result["recognition"]["source_size"] == [2560, 1600]
    assert sizes[:4] == [(1344, 864)] * 4
    assert all(max(size) <= 1550 for size in sizes)


def test_helper_preserves_native_pixels_before_ocr_crops(monkeypatch):
    module = importlib.import_module("plugin.plugins.hsr_companion.assistant_runtime.server")
    image = Image.new("RGB", (2560, 1600), (17, 29, 41))
    image.putpixel((2445, 777), (255, 254, 253))
    monkeypatch.setattr(module, "assert_capturable_window", lambda window: None)
    monkeypatch.setattr(module, "capture_isolated_window", lambda window: (image, 123.0))
    data, mime, timestamp = module._capture(SimpleNamespace())
    with Image.open(BytesIO(data)) as received:
        assert received.size == image.size
        assert received.getpixel((2445, 777)) == (255, 254, 253)
    assert mime == "image/png" and timestamp == 123


def test_hotkey_layout_requires_three_ordered_right_edge_keys():
    boxes = [
        {"text": str(i), "score": 0.99, "left": 975, "right": 990, "top": 200 + i * 70, "bottom": 216 + i * 70}
        for i in range(1, 5)
    ]
    rows = locate_team_rows(boxes, width=1000, height=800)
    assert len(rows) == 4
    assert locate_team_rows(boxes[:2], width=1000, height=800) == []
    assert locate_team_rows([{**item, "left": 200, "right": 220} for item in boxes], width=1000, height=800) == []
    characters = [{**role("a", 1), "region": {"top": (rows[2] - 8) / 800, "bottom": (rows[2] + 8) / 800}}]
    assert assign_team_slots(characters, rows, height=800)[0]["slot"] == 3


def test_partial_team_never_guesses_missing_positions():
    characters = [
        {**role(str(i), i), "region": {"left": 0.9, "top": 0.2 + i * 0.09, "bottom": 0.24 + i * 0.09}} for i in range(4)
    ]
    assert len(infer_complete_team_rows(characters, height=800)) == 4
    assert infer_complete_team_rows(characters[1:], height=800) == []


@pytest.mark.parametrize("query", ["我现在用的是谁", "我现在用谁", "我正在操控哪个角色", "场景中站着的角色是谁"])
def test_team_names_cannot_answer_controlled_character_question(tmp_path, query):
    import asyncio

    plugin, values, _ = plugin_fixture(tmp_path)
    value = analysis(scene="exploration")
    value["runtime"] = {"stable_character_ids": [value["characters"][0]["entity_id"]], "stable_scene": "exploration"}
    values["last_local_vision"] = value
    from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
    ObservationStore(plugin).commit(value)  # Classify a current roster, never resurrect a disk-only answer.
    result = asyncio.run(plugin.hsr_companion_prepare(query=query))
    assert result["current_visual_evidence"]["characters"]
    assert result["screen_identity_answer"]["scope"] == "controlled_character"
    assert result["screen_identity_answer"]["names"] == []
    assert result["screen_identity_answer"]["allowed"] is False
    assert result["character_resolution"]["accepted"] == []


def test_team_position_contract_does_not_expose_unconfirmed_or_paused_names():
    value = analysis(scene="exploration")
    value["page"]["page_type"] = "exploration_hud"
    value["characters"][0]["slot"] = 2
    value["recognition"]["team_rows"] = [0.2, 0.3, 0.4, 0.5]
    value["runtime"] = {"stable_character_ids": [value["characters"][0]["entity_id"]], "stable_scene": "exploration"}
    visual = public_visual_evidence(value, {"state": "observing"})
    assert visual["team"][1]["character"]["name"] == "银狼"
    assert visual["team"][0]["character"] is None
    assert visual["controlled_character"] is None
    assert public_visual_evidence(value, {"state": "paused"})["team"] == []
