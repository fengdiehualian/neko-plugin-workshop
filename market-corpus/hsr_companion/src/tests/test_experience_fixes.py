from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.data import ExternalKnowledgeCatalog
from plugin.plugins.hsr_companion.detectors import analyze_layout
from plugin.plugins.hsr_companion.runtime import CompanionRuntime
from plugin.plugins.hsr_companion.runtime.contracts import CompanionEvent
from plugin.plugins.hsr_companion.runtime.delivery import build_event_delivery

ROOT = Path(__file__).resolve().parents[1]


def catalog(tmp_path):
    registry = json.loads((ROOT / "resources/character_registry.json").read_text(encoding="utf-8"))
    return ExternalKnowledgeCatalog(registry=registry, root=tmp_path)


def box(text, x, y, score=0.99):
    return {"text": text, "score": score, "left": x, "top": y, "right": x + 60, "bottom": y + 20}


def scene(name="combat", roles=None, verified=True):
    return {"scene": {"primary_state": name, "confidence": "high", "verified": verified}, "characters": roles or []}


ROLE = {"entity_id": "character.yaoguang", "name": "爻光", "confidence": "high"}


def test_combat_and_exploration_hud_labels_are_not_skipped(tmp_path):
    db = catalog(tmp_path)
    combat = analyze_layout("敌方行动中\n爻光", [box("爻光", 850, 250)], db, width=1000, height=600)
    assert combat["scene"]["primary_state"] == "combat"
    assert combat["characters"][0]["name"] == "爻光"
    exploration = analyze_layout("秘技\n任务追踪\n火花", [box("火花", 850, 250)], db, width=1000, height=600)
    assert exploration["characters"][0]["name"] == "火花"
    assert exploration["scene"]["primary_state"] == "exploration"


def test_dialogue_mention_is_not_identified_as_visible_character(tmp_path):
    result = analyze_layout(
        "对话记录\n我昨天遇到爻光了", [box("我昨天遇到爻光了", 100, 400)], catalog(tmp_path), width=1000, height=600
    )
    assert result["characters"] == []


def test_upper_right_detail_name_is_not_filtered_out(tmp_path):
    result = analyze_layout("角色详情\n遗器\n爻光", [box("爻光", 820, 90)], catalog(tmp_path), width=1000, height=600)
    assert result["characters"][0]["name"] == "爻光"


@pytest.mark.parametrize(
    "label",
    ["银狼LV.", "银狼 Lv. 80", "银狼ＬＶ．８０", "银狼等级：80", "银狼80级", "刃Lv.80", "火花Lv.80", "花火Lv.80"],
)
def test_level_suffix_is_not_part_of_character_identity(tmp_path, label):
    result = analyze_layout("敌方行动中\n" + label, [box(label, 850, 250)], catalog(tmp_path), width=1000, height=600)
    from plugin.plugins.hsr_companion.detectors.layout import character_label

    expected, _ = character_label(label)
    assert result["characters"][0]["name"] == expected
    assert result["characters"][0]["level_suffix_removed"] is True
    assert result["characters"][0]["identity_scope"] == "hud_label"


def test_observed_exploration_ocr_without_literal_ui_anchors(tmp_path):
    # These words come from the reported live OCR result; positions below are
    # reconstructed, NOT an image/OCR accuracy benchmark. No UID is retained.
    text = "生研院\n剧场及其复象\n向虚照追问现况\n请前往千星城中心城区\n银狼LV."
    result = analyze_layout(text, [box("银狼LV.", 1100, 200)], catalog(tmp_path), width=1280, height=800)
    assert result["page"]["page_type"] == "unknown"
    assert result["scene"]["verified"] is False
    assert [item["name"] for item in result["characters"]] == ["银狼"]
    assert result["recognition"]["scene_required"] is False
    assert result["recognition"]["portrait_model"] is False


def test_unknown_scene_can_use_adjacent_separate_level_box(tmp_path):
    result = analyze_layout(
        "银狼\nLv.80", [box("银狼", 850, 250), box("Lv.80", 915, 250)], catalog(tmp_path), width=1000, height=600
    )
    assert [item["name"] for item in result["characters"]] == ["银狼"]


@pytest.mark.parametrize(
    "label,x,y",
    [
        ("我昨天遇到银狼了Lv.80", 850, 250),
        ("银狼Lv.80很可爱", 850, 250),
        ("不存在的角色Lv.80", 850, 250),
    ],
)
def test_unknown_page_does_not_turn_arbitrary_prose_into_characters(tmp_path, label, x, y):
    result = analyze_layout(label, [box(label, x, y)], catalog(tmp_path), width=1000, height=600)
    assert result["characters"] == []


def test_unknown_scene_character_event_keeps_hud_scope(tmp_path):
    db = catalog(tmp_path)
    analysis = analyze_layout("银狼LV.", [box("银狼LV.", 850, 250)], db, width=1000, height=600)
    runtime = CompanionRuntime()
    runtime.ingest(analysis, now=10)
    event, _ = runtime.ingest(analysis, now=13)
    assert event.kind == "characters_identified"
    assert runtime.stable_scene == "unknown"
    assert event.payload["identities"][0]["identity_scope"] == "hud_label"
    plan = build_event_delivery(event, catalog_revision="test", proactive_enabled=True)
    assert "不证明当前操控角色" in plan.text
    assert "账号拥有关系" in plan.text
    assert plan.ai_behavior == "read"


@pytest.mark.parametrize("proactive_enabled", [True, False])
def test_recognized_roster_updates_context_without_spontaneous_reply(proactive_enabled):
    event = CompanionEvent("roster", "characters_identified", 6, 10,
                           payload={"names": ["爻光"]}, proactive=True)
    plan = build_event_delivery(event, catalog_revision="test", proactive_enabled=proactive_enabled)
    assert "爻光" in plan.text
    assert plan.ai_behavior == "read"
    assert plan.coalesce_key == "hsr_local_observation"
    assert plan.metadata["delivery_intent"] == "passive_context"
    assert plan.metadata["proactive_requested"] is False


@pytest.mark.parametrize("enabled,behavior", [(True, "respond"), (False, "read")])
def test_scene_proactive_delivery_is_not_disabled_by_roster_policy(enabled, behavior):
    event = CompanionEvent("scene", "scene_changed", 6, 10,
                           payload={"current": "combat", "previous": "exploration"}, proactive=True)
    plan = build_event_delivery(event, catalog_revision="test", proactive_enabled=enabled)
    assert plan.ai_behavior == behavior
    assert plan.metadata["requires_current_observation"] is enabled


def test_native_tile_maps_back_to_original_region(tmp_path):
    from PIL import Image

    payload = BytesIO()
    Image.new("RGB", (2560, 1600), "white").save(payload, format="PNG")
    responses = iter([("", []), ("爻光", [box("爻光", 100, 80)]), ("", []), ("", [])])
    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._backend = SimpleNamespace(extract_text_with_boxes=lambda image: next(responses, ("", [])))
    result = adapter.analyze(payload.getvalue())
    assert result["ok"] is True
    assert result["characters"][0]["name"] == "爻光"
    assert result["characters"][0]["region"]["left"] == pytest.approx(1316 / 2560, abs=0.0001)
    assert result["recognition"]["text_reader"]["full_frame_covered"] is True


@pytest.mark.parametrize("quest_in_crop", [True, False])
def test_native_tiles_keep_quest_position_validation(tmp_path, quest_in_crop):
    from PIL import Image

    payload = BytesIO()
    Image.new("RGB", (2560, 1600), "white").save(payload, format="PNG")
    label = box("银狼LV.999", 1000, 306)
    quest = box("请前往千星城中心城区", 150, 348)
    responses = iter(
        [
            (quest["text"], [quest]) if quest_in_crop else ("", []),
            (label["text"], [label]),
            ("", []),
            ("", []),
        ]
    )
    sizes = []

    def extract(image):
        sizes.append(image.size)
        return next(responses, ("", []))

    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._backend = SimpleNamespace(extract_text_with_boxes=extract)
    result = adapter.analyze(payload.getvalue())
    assert result["ok"] is True
    assert sizes[:4] == [(1344, 864)] * 4
    assert all(max(size) <= 1550 for size in sizes)
    assert [item["name"] for item in result["characters"]] == ["银狼LV.999"]
    assert result["scene"]["verified"] is quest_in_crop
    assert result["scene"]["primary_state"] == ("exploration" if quest_in_crop else "unknown")


@pytest.mark.parametrize("label", ["银狼LV.999", "银狼LV999", "银狼LV.999 Lv.80"])
def test_complete_registered_name_is_not_erased_as_a_level_suffix(tmp_path, label):
    result = analyze_layout("敌方行动中\n" + label, [box(label, 1100, 300)], catalog(tmp_path), width=1280, height=800)
    assert [item["name"] for item in result["characters"]] == ["银狼LV.999"]
    assert result["characters"][0]["entity_id"] != "character.silverwolf"
    assert result["characters"][0]["level_suffix_removed"] is ("Lv.80" in label)


def test_confirming_exploration_rechecks_all_team_labels(tmp_path):
    boxes = [box("请前往千星城中心城区", 40, 180)]
    boxes += [box(name, 1100, 230 + i * 65) for i, name in enumerate(("爻光", "银狼LV.999", "藿藿", "火花"))]
    result = analyze_layout("\n".join(item["text"] for item in boxes), boxes, catalog(tmp_path), width=1280, height=800)
    assert result["scene"]["primary_state"] == "exploration"
    assert {item["name"] for item in result["characters"]} == {"爻光", "银狼LV.999", "藿藿", "火花"}
    assert all(item["identity_scope"] == "hud_label" for item in result["characters"])
    boxes[0] = box("请前往千星城中心城区", 40, 700)
    unknown = analyze_layout(
        "\n".join(item["text"] for item in boxes), boxes, catalog(tmp_path), width=1280, height=800
    )
    assert unknown["scene"]["primary_state"] == "unknown"
    assert {item["name"] for item in unknown["characters"]} == {"爻光", "银狼LV.999", "藿藿", "火花"}
    assert all(
        item["identity_scope"] == "screen_label" for item in unknown["characters"] if item["name"] != "银狼LV.999"
    )


def test_unknown_frames_break_consecutive_character_confirmation():
    runtime = CompanionRuntime()
    runtime.ingest(scene(roles=[ROLE]), now=10)
    runtime.ingest(scene("unknown", verified=False), now=11)
    runtime.ingest(scene(roles=[ROLE]), now=12)
    assert runtime.stable_character_ids == ()
    assert runtime.pending_character_count == 1


def test_deferred_character_cue_is_retried_after_scene_cooldown():
    runtime = CompanionRuntime(global_rate_seconds=18)
    runtime.ingest(scene(), now=10)
    runtime.ingest(scene(roles=[ROLE]), now=20)
    selected, decisions = runtime.ingest(scene(roles=[ROLE]), now=21)
    assert selected is None
    assert any(item["reason"] == "global_rate" for item in decisions)
    selected, _ = runtime.ingest(scene(roles=[ROLE]), now=29)
    assert selected.kind == "characters_identified"


def test_deferred_character_cue_is_discarded_when_character_disappears():
    runtime = CompanionRuntime(global_rate_seconds=18)
    runtime.ingest(scene(), now=10)
    runtime.ingest(scene(roles=[ROLE]), now=20)
    runtime.ingest(scene(roles=[ROLE]), now=21)
    selected, _ = runtime.ingest(scene(), now=29)
    assert selected is None


def test_old_exploration_cannot_trigger_chatter_on_unknown_screen():
    runtime = CompanionRuntime(idle_chatter_seconds=30)
    runtime.ingest(scene("exploration"), now=10)
    selected, _ = runtime.ingest(scene("unknown", verified=False), now=50)
    assert selected is None


def test_natural_question_contains_character_skill_and_rank_details(tmp_path):
    from plugin.plugins.hsr_companion.data.pack import UPSTREAM_TABLES

    db = catalog(tmp_path)
    tables = {name: {} for name in UPSTREAM_TABLES}
    tables["characters"] = {
        "1502": {"name": "爻光", "skills": ["skill1"], "ranks": ["rank1"], "path": "Elation", "element": "Physical"}
    }
    tables["character_skills"] = {"skill1": {"name": "测试战技", "desc": "测试效果，不是攻略事实"}}
    tables["character_ranks"] = {"rank1": {"name": "测试星魂", "desc": "测试效果"}}
    status = db.install_pinned(
        json_fetcher=lambda url: tables[url.rsplit("/", 1)[-1][:-5]], text_fetcher=lambda url: "AGPL test license"
    )
    assert status["meta"]["game_version"] == "4.6.0"
    result = db.search("爻光的战技到底是什么机制？", limit=1)
    assert result[0]["name"] == "爻光"
    assert result[0]["forms"][0]["skills"][0]["name"] == "测试战技"
    assert result[0]["forms"][0]["ranks"][0]["name"] == "测试星魂"


def test_proactive_test_does_not_claim_character_replied(monkeypatch):
    from plugin.plugins.hsr_companion import HsrCompanionPlugin
    from plugin.sdk.plugin import NekoPluginBase

    plugin = object.__new__(HsrCompanionPlugin)
    plugin.ctx = SimpleNamespace(_current_lanlan="测试猫娘")
    plugin._proactive_status = {"request_count": 0}
    plugin._observer_status = {"proactive_enabled": True}
    sent = []
    monkeypatch.setattr(
        NekoPluginBase, "push_message", lambda self, **kwargs: sent.append(kwargs) or {"submitted": True}
    )
    monkeypatch.setattr(plugin, "_message_path_ready", lambda: {"available": True})

    async def save_operation(operation, state, message):
        return {"operation": operation, "state": state, "message": message}

    plugin._set_user_operation = save_operation
    result = asyncio.run(plugin.test_proactive_message())
    assert result.value["last_operation"]["state"] == "info"
    assert "已提交" in result.value["last_operation"]["message"]
    assert sent[0]["target_lanlan"] == "测试猫娘"
    assert plugin._proactive_status["state"] == "submitted"


def test_missing_message_service_does_not_claim_submission(monkeypatch):
    from plugin.plugins.hsr_companion import HsrCompanionPlugin

    plugin = object.__new__(HsrCompanionPlugin)
    plugin.ctx = SimpleNamespace(_current_lanlan="测试猫娘")
    plugin._proactive_status = {"request_count": 0}
    monkeypatch.setattr(plugin, "_message_path_ready", lambda: {"available": False, "message": "消息服务未运行"})
    result = plugin.push_message(ai_behavior="respond", parts=[])
    assert result["submitted"] is False
    assert plugin._proactive_status["state"] == "error"
    assert plugin._proactive_status["message"] == "消息服务未运行"
