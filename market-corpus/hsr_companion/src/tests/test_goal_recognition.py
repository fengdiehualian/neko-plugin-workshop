"""Goal-based contract matrix. Fake OCR is NOT a game accuracy benchmark."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.adapters.text_reader import split_label_geometry, verify_name_regions
from plugin.plugins.hsr_companion.detectors import analyze_layout
from plugin.plugins.hsr_companion.detectors.scene_preview import scene_preview
from plugin.plugins.hsr_companion.runtime import CompanionRuntime
from plugin.plugins.hsr_companion.runtime.live_refresh import (
    frame_changed,
    frame_stamp,
    guarded_analysis,
    refresh_for_query,
)
from plugin.plugins.hsr_companion.runtime.observation import asks_scene_only, asks_screen_identity
from plugin.plugins.hsr_companion.runtime.storage import migrate_legacy_store
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog
from plugin.plugins.hsr_companion.tests.test_live_demo_fixes import png
from plugin.sdk.plugin import PluginStore, unwrap_or
from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods


@pytest.mark.parametrize("query", ["现在在哪", "现在在哪？", "现在在哪儿呢", "我现在在哪里", "我们在哪", "当前在哪", "咱们到哪了", "你看看现在在哪", "现在在哪（）"])
def test_natural_location_requests_current_scene(query):
    assert asks_scene_only(query) and asks_screen_identity(query)


@pytest.mark.parametrize("query", ["爻光在哪获得", "这个材料在哪里刷", "银狼现在在哪个卡池", "现在有哪些角色", "现在是什么场景，角色是谁", "还少一个吧", "现在呢"])
def test_knowledge_and_identity_are_not_scene_only(query):
    assert not asks_scene_only(query)


def test_location_tool_has_scene_answer_not_misleading_missing_role_reply(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        await owner._apply_local_analysis(analysis(scene="team_setup", roles=False), image_fingerprint="a", source="assistant_local_ocr")
        result = await owner.hsr_companion_prepare(query="现在在哪", _ctx={"sentinel": "must_not_enter_facts"})
        assert result["screen_scene_answer"]["requested"] and result["screen_scene_answer"]["allowed"]
        assert result["screen_scene_answer"]["label"] == "队伍编成 / 挑战准备"
        assert not result["screen_identity_answer"]["requested"]
        assert result["screen_identity_answer"]["unavailable_reply"] == ""
        assert result["knowledge_results"] == []
        assert "原样保留已确认的地点和任务名称" in result["answer_policy"]["direct_scene_navigation_reply"]
        assert "must_not_enter_facts" not in str(result)
    asyncio.run(run())


def test_every_llm_entry_accepts_host_run_context_without_echoing_it(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    tools = collect_llm_tool_methods(owner)
    assert len(tools) == 11
    for meta, method in tools:
        args = {key: None for key in meta.parameters.get("required", [])}
        # Bind only: do not modify player profiles or records to test a signature.
        inspect.signature(method).bind(**args, _ctx={"run_id": "signature-test"})
        assert "_ctx" not in meta.parameters.get("properties", {})


def test_plugin_store_config_refresh_cannot_silently_skip_own_state(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    owner.store.enabled = False
    original_get, original_set = owner.store.get, owner.store.set
    async def get(key):
        return await original_get(key) if owner.store.enabled else None
    async def set_value(key, value):
        return await original_set(key, value) if owner.store.enabled else None
    owner.store.get, owner.store.set = get, set_value
    async def run():
        await owner._store_value("observer_settings", {"enabled": True})
        assert values["observer_settings"]["enabled"]
        owner.store.enabled = False  # Simulate later effective-config refresh.
        assert (await owner._read_observer_settings())["enabled"]
    asyncio.run(run())


def test_fresh_query_restores_player_enabled_observer_without_open_panel(tmp_path, monkeypatch):
    import importlib
    owner, values, _ = plugin_fixture(tmp_path)
    values["observer_settings"] = {"enabled": True}
    owner._observer_task = None
    restored = []
    owner._start_observer_task = lambda **kwargs: restored.append(kwargs)
    async def refresh(*args, **kwargs):
        assert restored == [{"restored": True}]
    module = importlib.import_module(type(owner).__module__)
    monkeypatch.setattr(module, "refresh_for_query", refresh)
    asyncio.run(owner.hsr_companion_prepare(query="现在在哪"))


def test_external_sdk_store_migration_preserves_existing_data_and_restart(tmp_path):
    legacy = PluginStore(plugin_id="hsr_companion", plugin_dir=tmp_path / "old", enabled=True)
    target = PluginStore(plugin_id="hsr_companion", plugin_dir=tmp_path / "external", enabled=True)
    async def run():
        await legacy.set("observer_settings", {"enabled": True})
        await legacy.set("player_context", {"favorite_characters": ["镜流"]})
        await target.set("player_context", {"favorite_characters": ["爻光"]})
        assert await migrate_legacy_store(legacy, target) == 1
        assert unwrap_or(await target.get("player_context"), {}) == {"favorite_characters": ["爻光"]}
        assert await migrate_legacy_store(legacy, target) == 0
        await target.close()
        restarted = PluginStore(plugin_id="hsr_companion", plugin_dir=tmp_path / "external", enabled=True)
        assert unwrap_or(await restarted.get("observer_settings"), {})["enabled"]
        await restarted.close()
        await legacy.close()
    asyncio.run(run())


@pytest.mark.parametrize("title", ["角色详情", "队伍编成", "角色活动跃迁", "角色图鉴", "角色试用", "支援列表", "委托派遣", "背包", "敌方行动中", "对话记录", "正在加载", "挑战成功"])
@pytest.mark.parametrize("name", ["镜流", "远坂凛", "火花", "爻光", "银狼LV.999", "刃"])
def test_canonical_labels_corroborate_across_pages_and_names(tmp_path, title, name):
    db = catalog(tmp_path)
    original = box(name, 200, 200)
    backend = SimpleNamespace(extract_text_with_boxes=lambda image: (name, [box(name, 24, 24)]))
    boxes, stats = verify_name_regions(backend, Image.new("RGB", (1000, 600)), [original], db)
    layout = analyze_layout(title + "\n" + name, [box(title, 10, 10), *boxes], db, width=1000, height=600)
    runtime = CompanionRuntime()
    runtime.ingest(layout)
    assert stats["confirmed"] == 1
    assert layout["characters"][0]["name"] == name
    assert list(runtime.stable_character_ids) == [db.resolve_character(name)["id"]]
    # This only proves the printed label. Never claim a 3D/controlled actor.
    assert "controlled_character" not in layout


@pytest.mark.parametrize("reread,score", [("花火", .99), ("火花", .70), ("", .99), ("火花很强", .99)])
def test_conflicting_uncertain_or_prose_reread_cannot_bypass_confirmation(tmp_path, reread, score):
    db = catalog(tmp_path)
    backend = SimpleNamespace(extract_text_with_boxes=lambda image: (reread, [box(reread, 24, 24, score)]))
    boxes, stats = verify_name_regions(backend, Image.new("RGB", (1000, 600)), [box("火花", 200, 200)], db)
    layout = analyze_layout("火花", boxes, db, width=1000, height=600)
    runtime = CompanionRuntime()
    runtime.ingest(layout)
    assert not stats["confirmed"] and not runtime.stable_character_ids


def test_correction_cannot_reuse_proof_for_a_different_entity(tmp_path):
    db = catalog(tmp_path)
    role = analyze_layout("火花", [box("火花", 200, 200)], db, width=1000, height=600)["characters"][0]
    other = db.resolve_character("花火")
    role["name_validation"] = {"entity_id": other["id"], "method": "native_and_enlarged_label_agree", "scores": [.99, .99]}
    runtime = CompanionRuntime()
    runtime.ingest({"characters": [role]})
    assert not runtime.stable_character_ids


def test_exploration_scene_does_not_need_recognized_names():
    boxes = [box("请前往中心城区", 30, 80)]
    for i in range(4):
        boxes.append({**box(str(i + 1), 975, 130 + 65 * i), "right": 995})
    preview = scene_preview(boxes, width=1000, height=600)
    assert preview["page"]["page_type"] == "exploration_hud"
    assert preview["characters"] == []
    assert scene_preview(boxes[:-2], width=1000, height=600) is None
    assert scene_preview(boxes[1:], width=1000, height=600) is None
    # A title for a different page wins; shortcuts alone do not override it.
    preview = scene_preview([box("队伍编成", 20, 20), *boxes], width=1000, height=600)
    assert preview["page"]["page_type"] == "team_setup"


def test_compatibility_capture_publishes_scene_before_full_recognition(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    release = threading.Event()
    async def publish(value):
        await original(value)
        release.set()
    original = owner._publish_scene_preview
    owner._publish_scene_preview = publish
    def analyze_live(payload, *, cancelled, on_scene):
        on_scene(scene_preview([box("跃迁", 20, 20)], width=640, height=400))
        assert release.wait(2), "scene preview was delayed behind full OCR"
        return analysis(roles=False, scene="warp")
    owner._vision = SimpleNamespace(analyze_live=analyze_live)
    async def run():
        result = await guarded_analysis(owner, png(), helper=False)
        assert result is not None
        assert owner._scene_preview["scene"]["primary_state"] == "warp"
    asyncio.run(run())


def test_pipeline_timings_count_every_backend_call_without_text(tmp_path):
    db = catalog(tmp_path)
    calls = []
    def extract(image):
        calls.append(image.size)
        if len(calls) == 1:
            return "队伍编成", [box("队伍编成", 20, 20)]
        if len(calls) == 2:
            return "镜流", [box("镜流", 200, 200)]
        return "镜流", [box("镜流", 24, 24)]
    adapter = LocalVisionAdapter(db)
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=extract)
    result = adapter.analyze_live(png(), cancelled=lambda: False, on_scene=lambda p: None)
    timing = result["recognition"]["text_reader"]
    assert [call["stage"] for call in timing["backend_calls"]] == ["scene_probe", "native_tile", "name_verification"]
    assert all("text" not in call and call["backend_call_seconds"] >= 0 for call in timing["backend_calls"])
    assert timing["name_verification"]["confirmed"] == 1
    runtime = CompanionRuntime()
    runtime.ingest(result)
    assert runtime.stable_scene == "team_setup"
    assert runtime.stable_character_ids == (db.resolve_character("镜流")["id"],)


def test_conflicting_new_capture_drops_previous_names_not_accumulating_roster(tmp_path):
    db = catalog(tmp_path)
    runtime = CompanionRuntime()
    for name in ("镜流", "远坂凛", "火花", "爻光", "银狼LV.999", "刃"):
        boxes, _ = verify_name_regions(
            SimpleNamespace(extract_text_with_boxes=lambda image: (name, [box(name, 24, 24)])),
            Image.new("RGB", (1000, 600)), [box(name, 200, 200)], db,
        )
        layout = analyze_layout("队伍编成\n" + name, boxes, db, width=1000, height=600)
        runtime.ingest(layout)
        assert runtime.stable_character_ids == (db.resolve_character(name)["id"],)
    runtime.invalidate_observation()
    assert not runtime.stable_character_ids and runtime.stable_scene == "unknown"


@pytest.mark.parametrize("label,name", [("镜流Lv.80", "镜流"), ("银狼LV.999等级80", "银狼LV.999"), ("火花欢愉80", "火花")])
def test_complete_canonical_name_with_ui_fields_can_be_corroborated(tmp_path, label, name):
    db = catalog(tmp_path)
    backend = SimpleNamespace(extract_text_with_boxes=lambda image: (label, [box(label, 24, 24)]))
    boxes, stats = verify_name_regions(backend, Image.new("RGB", (1000, 600)), [box(label, 200, 200)], db)
    result = analyze_layout(label, boxes, db, width=1000, height=600)
    runtime = CompanionRuntime()
    runtime.ingest(result)
    assert stats["confirmed"] == 1
    assert runtime.stable_character_ids == (db.resolve_character(name)["id"],)


def test_precise_retry_not_hidden_by_earlier_merged_name_row(tmp_path):
    db = catalog(tmp_path)
    merged = {**box("镜流毁灭80火花欢愉80", 100, 200), "right": 900}
    precise = box("镜流", 100, 200, .95)
    boxes, _ = verify_name_regions(SimpleNamespace(extract_text_with_boxes=lambda image: ("镜流", [box("镜流", 24, 24)])),
                                  Image.new("RGB", (1000, 600)), [precise], db)
    result = analyze_layout(merged["text"] + "\n镜流", [merged, *boxes], db, width=1000, height=600)
    character = next(c for c in result["characters"] if c["name"] == "镜流")
    assert character["region_precision"] == "label" and character["name_validation"]
    assert character["region"]["right"] == .16


def test_retry_geometry_uses_blank_space_not_fixed_character_locations():
    from PIL import ImageDraw
    image = Image.new("RGB", (1000, 600), "black")
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 200, 180, 220), fill="white")
    draw.rectangle((700, 200, 790, 220), fill="white")
    parts = split_label_geometry(image, {**box("镜流火花", 100, 200), "right": 900})
    assert len(parts) == 2
    assert parts[0][0] <= 120 < parts[0][2] < 700
    assert parts[1][0] <= 700 < parts[1][2]
    assert split_label_geometry(Image.new("RGB", (1000, 600), "black"), {**box("镜流火花", 100, 200), "right": 900}) == []


@pytest.mark.parametrize("other,allowed", [("Lv.80", True), ("欢愉", True), ("花火", False), ("银狼", False), ("很强", False)])
def test_separate_ui_field_not_a_split_name_or_another_person(tmp_path, other, allowed):
    backend = SimpleNamespace(extract_text_with_boxes=lambda image: ("", [box("银狼LV.999", 24, 24), box(other, 150, 24)]))
    _, stats = verify_name_regions(backend, Image.new("RGB", (1000, 600)), [box("银狼LV.999Lv80", 200, 200)], catalog(tmp_path))
    assert bool(stats["confirmed"]) is allowed


@pytest.mark.parametrize("prose", ["挑战成功后就能领取奖励", "我们试试快速编队吧", "获得角色以后再养成", "请查看战技点与终结技的详细说明"])
def test_native_prose_is_not_a_scene_control(tmp_path, prose):
    result = analyze_layout(prose, [box(prose, 20, 20)], catalog(tmp_path), width=1000, height=600)
    assert result["scene"]["primary_state"] == "unknown"
    assert scene_preview([box(prose, 20, 20)], width=1000, height=600) is None


def test_challenge_preparation_controls_do_not_mean_reward_or_victory(tmp_path):
    boxes = [box("可遭遇", 650, 200), box("快捷补充拓力", 650, 400), box("挑战次数1", 700, 450), box("挑战", 850, 530)]
    result = analyze_layout("\n".join(b["text"] for b in boxes), boxes, catalog(tmp_path), width=1000, height=600)
    assert result["scene"]["primary_state"] == "team_setup"
    assert result["page"]["page_type"] == "challenge_setup"
    assert scene_preview(boxes, width=1000, height=600)["page"]["page_type"] == "challenge_setup"


def test_scene_only_reader_uses_one_call_and_no_character_facts(tmp_path):
    calls = []
    def extract(image):
        calls.append(image.size)
        return "队伍编成\n火花", [box("队伍编成", 20, 20), box("火花", 200, 200)]
    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=extract)
    result = adapter.analyze_scene(png())
    assert len(calls) == 1 and result["characters"] == []
    assert result["scene"]["primary_state"] == "team_setup"
    assert result["recognition"]["scene_regions"]


def test_scene_ui_guard_ignores_world_motion_but_not_changed_control():
    from io import BytesIO

    from PIL import ImageDraw
    def stamp(world, control):
        image = Image.new("RGB", (640, 400), world)
        ImageDraw.Draw(image).rectangle((0, 0, 160, 70), fill=control)
        stream = BytesIO()
        image.save(stream, format="PNG")
        return frame_stamp(stream.getvalue())
    region = [{"region": {"left": 0, "top": 0, "right": .25, "bottom": .175}}]
    a, b = stamp("black", "gray"), stamp("white", "gray")
    assert frame_changed(a, b)
    assert not frame_changed(a, b, region, ui_only=True)
    assert frame_changed(a, stamp("black", "white"), region, ui_only=True)


def test_scene_query_routes_single_worker_to_scene_not_roster(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    calls = []
    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=lambda image: ("队伍编成", [box("队伍编成", 20, 20)]))
    def read_scene(payload, *, cancelled):
        calls.append("scene")
        return adapter.analyze_scene(payload, cancelled=cancelled)
    def read_full(*args, **kwargs):
        pytest.fail("scene query was queued behind full roster analysis")
    owner._vision = SimpleNamespace(analyze_scene=read_scene, analyze_live=read_full)
    owner._visual_query_waiters = owner._scene_query_waiters = 1
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: SimpleNamespace(
        image_bytes=png(), timestamp=time.time(), game={"running": True, "pid": 1},
        capture={"state": "capturing", "isolated_window": True}))
    async def run():
        result = await guarded_analysis(owner, png(), helper=True)
        assert result is not None and result["scene"]["verified"]
        assert owner._next_frame_scene_only
        await owner._apply_local_analysis(result, image_fingerprint="a", source="assistant_local_ocr")
        evidence = await owner._current_visual_evidence()
        assert evidence["scene"]["verified"] and evidence["characters"] == []
    asyncio.run(run())
    assert calls == ["scene"]


def test_completed_unknown_scene_does_not_wait_for_character_analysis(tmp_path):
    from plugin.plugins.hsr_companion.runtime.observation import public_visual_evidence
    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=lambda image: ("", []))
    result = adapter.analyze_scene(png())
    result["observed_at"] = analysis()["observed_at"]
    result["runtime"] = {"stable_scene": "unknown", "stable_character_ids": []}
    evidence = public_visual_evidence(result, {"state": "observing"})
    assert evidence["status"] == "scene_not_recognized"
    assert not evidence["scene"]["verified"] and not evidence["characters"]
    assert evidence["failure_reason"] == "no_validated_scene_control"


@pytest.mark.parametrize("initial", ["waiting", "expired"])
def test_first_query_waits_past_startup_or_expired_frame(tmp_path, initial):
    owner, _, _ = plugin_fixture(tmp_path)
    calls = []
    owner._observer_status.update(enabled=True, state="waiting")
    owner._push_current_visual_context = lambda evidence: None
    async def evidence():
        calls.append(1)
        if len(calls) < 3:
            return {"status": initial, "scene": {"verified": False}}
        return {"status": "scene_not_recognized", "scene": {"verified": False}}
    owner._current_visual_evidence = evidence
    async def run():
        owner._observer_task = asyncio.create_task(asyncio.sleep(10))
        await refresh_for_query(owner, timeout=2, require_characters=False)
        owner._observer_task.cancel()
        assert len(calls) >= 3 and owner._scene_query_waiters == 0
    asyncio.run(run())


def test_exploration_actions_do_not_depend_on_quest_wording():
    boxes = [{**box(str(i + 1), 975, 130 + 65 * i), "right": 995} for i in range(4)]
    actions = [box("疾跑", 820, 500), box("普攻", 900, 550)]
    assert scene_preview([*boxes, *actions], width=1000, height=600)["page"]["page_type"] == "exploration_hud"
    assert scene_preview([*boxes, actions[1]], width=1000, height=600) is None
    assert scene_preview(actions, width=1000, height=600) is None
    assert scene_preview([*boxes, box("试试疾跑和普攻", 820, 500)], width=1000, height=600) is None


def test_isolated_model_requires_complete_hashes(tmp_path, monkeypatch):
    from hashlib import sha256

    from plugin.plugins.hsr_companion.adapters import upright_ocr
    monkeypatch.setattr(upright_ocr, "MODEL_FILES", {"test.onnx": sha256(b"verified").hexdigest()})
    assert not upright_ocr.verified_models(tmp_path)
    (tmp_path / "test.onnx").write_bytes(b"verified")
    assert upright_ocr.verified_models(tmp_path)
    (tmp_path / "test.onnx").write_bytes(b"corrupt")
    assert not upright_ocr.verified_models(tmp_path)


def test_upright_backend_uses_public_api_without_direction_model(monkeypatch):
    import sys

    from plugin.plugins.hsr_companion.adapters.upright_ocr import UprightOCRBackend
    calls = []
    class Engine:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def __call__(self, image, **kwargs):
            assert kwargs == {"use_cls": False}
            return [[[[10, 20], [50, 20], [50, 40], [10, 40]], "镜流", .99]], None
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=Engine))
    backend = UprightOCRBackend()
    text, boxes = backend.extract_text_with_boxes(Image.new("RGB", (100, 100)))
    assert text == "镜流" and boxes[0]["left"] == 10
    assert calls == [{"use_cls": False, "intra_op_num_threads": 2, "inter_op_num_threads": 1}]
    backend.close()
    assert not backend.is_available()
