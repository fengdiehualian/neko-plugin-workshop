"""Cross-layout and freshness contracts, not a claimed live-game accuracy rate."""

from __future__ import annotations

import asyncio
import importlib
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.detectors.scene_preview import scene_preview
from plugin.plugins.hsr_companion.runtime.grounding import asks_navigation_only, build_grounding
from plugin.plugins.hsr_companion.runtime.live_refresh import guarded_analysis, refresh_for_query
from plugin.plugins.hsr_companion.runtime.navigation import navigation_answer
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box
from plugin.plugins.hsr_companion.tests.test_exploration_fields import classify, extract, hud
from plugin.plugins.hsr_companion.tests.test_screen_facts import catalog, visual


def interior(place="地点甲", objective="寻找线索甲", destination="区域乙"):
    return [*hud(place=place, objective=objective, destination=destination)[:1],
            *hud(place=place, objective=objective, destination=destination)[2:], box("Enter", 45, 760)]


def png():
    stream = BytesIO()
    Image.new("RGB", (1280, 800)).save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("width,height", [(1280, 800), (1920, 1080), (2560, 1600)])
@pytest.mark.parametrize("place,objective,destination", [
    ("某处车站", "向向导询问消息", "另一区域"), ("陌生室内", "寻找下一条线索", "区域丙"),
])
def test_no_team_interior_layout_not_whitelisted_by_place(width, height, place, objective, destination):
    from plugin.plugins.hsr_companion.detectors.layout import classify_supported_page
    from plugin.plugins.hsr_companion.runtime.screen_facts import extract_screen_facts

    boxes = [{**b, **{key: b[key] * scale for key, scale in (
        ("left", width / 1280), ("right", width / 1280), ("top", height / 800), ("bottom", height / 800))}}
             for b in interior(place, objective, destination)]
    page = classify_supported_page("\n".join(b["text"] for b in boxes), boxes, width=width, height=height)
    assert page["page_type"] == "exploration_hud"
    assert scene_preview(boxes, width=width, height=height)["scene"]["verified"]
    result = extract_screen_facts(boxes, width=width, height=height, page=page, catalog=catalog())
    values = {(f["kind"], f["value"]) for f in result["facts"]}
    assert {("current_location", place), ("quest_objective", objective), ("quest_destination", destination)} <= values


@pytest.mark.parametrize("missing", [0, 1, 2, 3, 4])
def test_social_interior_needs_all_independent_controls(missing):
    boxes = interior()
    boxes.pop(missing)
    assert classify(boxes)["page_type"] == "unknown"
    assert not any(f["kind"] == "current_location" for f in extract(boxes)["facts"])


def test_chat_key_elsewhere_and_menu_overlay_are_not_exploration():
    boxes = interior()
    boxes[-1]["top"], boxes[-1]["bottom"] = 400, 420
    assert classify(boxes)["page_type"] == "unknown"
    boxes = interior() + [box("队伍编成", 400, 20)]
    assert classify(boxes)["page_type"] == "team_setup"
    assert not any(f["kind"] == "current_location" for f in extract(boxes)["facts"])


def test_native_and_thumbnail_task_rows_allow_one_icon_indent_not_wrong_title():
    # Layout/score replay from the live failure, with names replaced. Native
    # and thumbnail OCR disagree on title prefix and box-left position.
    boxes = [
        {**box("·地点丁", 26, 9), "right": 100, "bottom": 29, "score": .9966},
        {**box("噪任务标题", 27, 186), "right": 153, "bottom": 209, "score": .8983},
        {**box("任务标题", 52.5, 189), "right": 149.5, "bottom": 206, "score": .9973},
        {**box("向向导询问消息", 54.5, 214), "right": 153.5, "bottom": 229, "score": .9980},
        {**box("请前往区域乙", 78, 240), "right": 267, "bottom": 254.5, "score": .9710},
        box("Enter", 52, 713),
    ]
    result = extract(boxes)
    values = {(f["kind"], f["value"]) for f in result["facts"]}
    assert {("current_location", "地点丁"), ("tracked_quest", "任务标题"),
            ("quest_objective", "向向导询问消息"), ("quest_destination", "区域乙")} <= values
    assert "噪任务标题" not in {f["value"] for f in result["facts"]}


@pytest.mark.parametrize("query", ["现在在哪，接下来这个任务要做什么？", "当前任务是什么", "接下来去哪", "这个任务目标是什么"])
def test_location_task_questions_use_navigation_only(query):
    assert asks_navigation_only(query)


@pytest.mark.parametrize("query", ["现在在哪", "这个技能怎么样", "现在角色是谁", "当前任务队伍用谁", "这个遗器适合谁", "材料哪里获得"])
def test_other_queries_do_not_enter_navigation_only(query):
    assert not asks_navigation_only(query)


def test_typed_answer_separates_place_destination_objective_and_unknown_progress():
    answer = build_grounding("现在在哪，接下来这个任务要做什么？", visual(extract(interior())), catalog())["navigation"]
    assert answer["requested"]
    assert answer["current_location"]["value"] == "地点甲"
    assert answer["destination"]["value"] == "区域乙"
    assert answer["objective"]["value"] == "寻找线索甲"
    assert answer["game_task_completion"] == {"status": "unknown", "verified": False}
    assert not answer["missing_fields"]


def test_destination_alone_does_not_fill_objective_or_current_location():
    screen = {"facts": [{"kind": "quest_destination", "value": "区域乙"}],
              "unclassified_text": [{"text": "猜测地点甲"}]}
    answer = navigation_answer(visual(screen), requested=True)
    assert answer["destination"]["known"]
    assert not answer["objective"]["known"] and not answer["current_location"]["known"]
    assert answer["confirmed_parts"] == ["导航目的地：区域乙"]


def test_conflicting_or_stale_fields_and_unknown_imperatives_never_fill_slots():
    screen = {"facts": [{"kind": "current_location", "value": name} for name in ("地点甲", "地点乙")]}
    answer = navigation_answer(visual(screen), requested=True)
    assert not answer["current_location"]["known"] and answer["current_location"]["ambiguous"]
    answer = navigation_answer({**visual(screen), "fresh": False}, requested=True)
    assert not answer["confirmed_parts"]
    unknown = extract([box("请前往某地", 52, 245)], {"page_type": "unknown"})
    assert not unknown["facts"]  # Not mislabeled as an objective on unknown pages.


def test_navigation_adapter_uses_two_serial_calls_no_portrait_or_name_reader():
    calls = []
    boxes = interior()
    def read(image):
        calls.append(image.size)
        return "", [boxes[0], boxes[-1]] if len(calls) == 1 else boxes[:-1]
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=read)
    result = adapter.analyze_navigation(png())
    assert calls == [(1280, 800), (704, 480)]
    assert result["characters"] == [] and result["scene"]["verified"]
    assert result["recognition"]["navigation_only_query"]
    assert len(result["screen_facts"]["facts"]) == 4


def test_complete_thumbnail_still_checks_native_text_without_promoting_weak_title():
    boxes = interior()
    boxes[1]["score"] = .88
    boxes[1]["text"] = "弱读数不应当作任务名"
    calls = []
    def read(image):
        calls.append(image.size)
        return "", boxes
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=read)
    result = adapter.analyze_navigation(png())
    assert calls == [(1280, 800), (704, 480)]
    values = {f["kind"] for f in result["screen_facts"]["facts"]}
    assert {"current_location", "quest_destination", "quest_objective"} <= values
    assert "tracked_quest" not in values


def test_navigation_query_routes_one_guarded_worker_not_full_roster(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    adapter = LocalVisionAdapter(catalog())
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=lambda image: ("", interior()))
    calls = []
    def navigation(payload, *, cancelled):
        calls.append("navigation")
        return adapter.analyze_navigation(payload, cancelled=cancelled)
    def no_roster(*args, **kwargs):
        pytest.fail("navigation query entered full roster pipeline")
    owner._vision = SimpleNamespace(analyze_navigation=navigation, analyze_scene=no_roster, analyze_live=no_roster)
    owner._visual_query_waiters = owner._scene_query_waiters = owner._navigation_query_waiters = 1
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: SimpleNamespace(
        image_bytes=png(), timestamp=time.time(), game={"running": True, "pid": 1},
        capture={"state": "capturing", "isolated_window": True}))
    async def run():
        result = await guarded_analysis(owner, png(), helper=True)
        assert result is not None and result["scene"]["verified"]
        await owner._apply_local_analysis(result, image_fingerprint="nav", source="assistant_local_ocr")
        evidence = await owner._current_visual_evidence()
        assert evidence["read_scope"] == "navigation" and evidence["fresh"]
    asyncio.run(run())
    assert calls == ["navigation"]


def test_navigation_wait_does_not_finish_on_scene_preview(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        async def produce():
            await asyncio.sleep(.01)
            await owner._publish_scene_preview(scene_preview([box("跃迁", 20, 20)], width=1280, height=800))
            await asyncio.sleep(.025)
            result = analysis(roles=False, scene="warp")
            result["recognition"] = {"navigation_only_query": True, "scene_only_query": True}
            await owner._apply_local_analysis(result, image_fingerprint="nav", source="assistant_local_ocr")
        owner._observer_task = asyncio.create_task(produce())
        await refresh_for_query(owner, timeout=1, require_characters=False, require_navigation=True)
        assert owner._observer_task.done()
        assert (await owner._current_visual_evidence())["read_scope"] == "navigation"
        assert owner._navigation_query_waiters == owner._scene_query_waiters == owner._visual_query_waiters == 0
    asyncio.run(run())


def test_prepare_routes_navigation_and_hides_unclassified_guesses(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    calls = []
    async def refresh(*args, **kwargs):
        calls.append(kwargs)
    async def evidence():
        value = await original()
        value["screen_facts"]["unclassified_text"] = [{"text": "不可当地点的标题"}]
        return value
    original = owner._current_visual_evidence
    owner._current_visual_evidence = evidence
    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "refresh_for_query", refresh)
    result = asyncio.run(owner.hsr_companion_prepare(query="现在在哪，接下来这个任务要做什么？"))
    assert len(calls) == 1
    assert calls[0].pop("query_plan").public() == result["query_plan"]
    assert 0 < calls[0].pop("timeout") <= 8.0  # Coordination consumes the SAME deadline, not free time.
    assert calls == [{"require_characters": False, "require_content": False, "require_navigation": True}]
    assert result["current_visual_evidence"]["screen_facts"]["unclassified_text"] == []
    assert result["screen_context_answer"]["navigation"]["game_task_completion"]["status"] == "unknown"
    reply_policy = result["answer_policy"]["direct_scene_navigation_reply"]
    assert "一到两句" in reply_policy and "不要追加未被问到的角色名单" in reply_policy


@pytest.mark.parametrize("require_content", [True, False])
def test_full_query_does_not_accept_another_queries_navigation_only_result(tmp_path, require_content):
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        async def produce():
            await asyncio.sleep(.01)
            partial = analysis(roles=False, scene="warp")
            partial["recognition"] = {"navigation_only_query": True, "scene_only_query": True}
            await owner._apply_local_analysis(partial, image_fingerprint="nav", source="assistant_local_ocr")
            await asyncio.sleep(.025)
            await owner._apply_local_analysis(analysis(roles=False, scene="warp"), image_fingerprint="full", source="assistant_local_ocr")
        owner._observer_task = asyncio.create_task(produce())
        await refresh_for_query(owner, timeout=1, require_characters=True, require_content=require_content)
        assert owner._observer_task.done()
        assert (await owner._current_visual_evidence())["read_scope"] == "full"
    asyncio.run(run())
