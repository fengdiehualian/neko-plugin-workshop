"""Live-query, transition, UI-action and name-icon regressions (not accuracy)."""

from __future__ import annotations

import asyncio
import threading
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.adapters.text_reader import read_native_text
from plugin.plugins.hsr_companion.detectors import analyze_layout
from plugin.plugins.hsr_companion.runtime.live_refresh import (
    frame_changed,
    frame_stamp,
    guarded_analysis,
    refresh_for_query,
)
from plugin.plugins.hsr_companion.runtime.observation import asks_screen_identity
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog


def png(color="black", region=None):
    image = Image.new("RGB", (640, 400), color)
    if region:
        ImageDraw.Draw(image).rectangle(region, fill="white")
    data = BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


@pytest.mark.parametrize("query", ["现在呢", "你再看看呢？（）", "看到了吗", "还少一个吧", "现在是什么界面？"])
def test_short_followups_enter_strict_current_screen_mode(query):
    assert asks_screen_identity(query)


def test_warp_footer_trial_button_is_not_trial_page(tmp_path):
    boxes = [box("跃迁", 30, 20), box("Fate联动跃迁", 160, 65), box("角色试用", 240, 365)]
    result = analyze_layout("\n".join(item["text"] for item in boxes), boxes, catalog(tmp_path), width=640, height=400)
    assert result["page"]["page_type"] == "warp"
    assert "界面锚点：角色试用" not in result["page"]["evidence"]


def test_trial_footer_alone_cannot_classify_unknown_page(tmp_path):
    result = analyze_layout("角色试用", [box("角色试用", 240, 365)], catalog(tmp_path), width=640, height=400)
    assert result["page"]["page_type"] == "unknown"


def test_trial_heading_still_classifies_trial_page(tmp_path):
    result = analyze_layout("角色试用", [box("角色试用", 30, 20)], catalog(tmp_path), width=640, height=400)
    assert result["page"]["page_type"] == "trial"


@pytest.mark.parametrize("name", ["镜流", "远坂凛", "姬子"])
@pytest.mark.parametrize("retry_succeeds", [True, False])
def test_icon_prefix_requires_new_pixel_ocr_not_string_stripping(tmp_path, name, retry_succeeds):
    calls = []

    def extract(image):
        calls.append(image.size)
        if len(calls) == 1:
            return "Q" + name, [box("Q" + name, 100, 100)]
        return (name, [box(name, 24, 24)]) if retry_succeeds else ("", [])

    db = catalog(tmp_path)
    text, boxes, details = read_native_text(
        SimpleNamespace(extract_text_with_boxes=extract), Image.new("RGB", (640, 400)), catalog=db
    )
    result = analyze_layout(text, boxes, db, width=640, height=400)
    assert [item["name"] for item in result["characters"]] == ([name] if retry_succeeds else [])
    assert details["icon_name_rereads"] == 1


def test_change_guard_accepts_identical_or_small_animation_but_rejects_cut():
    assert not frame_changed(frame_stamp(png()), frame_stamp(png()))
    assert not frame_changed(frame_stamp(png((30, 30, 30))), frame_stamp(png((32, 32, 32))))
    assert frame_changed(frame_stamp(png()), frame_stamp(png("white")))


def test_change_guard_does_not_require_desktop_missing_pillow_modules(monkeypatch):
    import builtins

    original = builtins.__import__

    def restricted(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"PIL.ImageStat", "PIL.ImageChops"} or (
            name == "PIL" and any(x in {"ImageStat", "ImageChops"} for x in fromlist)
        ):
            raise ImportError("not bundled in desktop")
        return original(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", restricted)
    assert frame_changed(frame_stamp(png()), frame_stamp(png("white")))


def test_change_in_small_known_name_region_revokes_old_identity():
    role = {"region": {"left": 0.4, "right": 0.5, "top": 0.4, "bottom": 0.45}}
    assert frame_changed(frame_stamp(png()), frame_stamp(png(region=(256, 160, 320, 180))), [role])


@pytest.mark.parametrize("during", [True, False])
def test_same_game_changing_page_during_ocr_discards_result(during):
    release = threading.Event()

    async def run():
        owner = SimpleNamespace(
            _observation_epoch=0,
            _assistant_client=SimpleNamespace(fetch_frame=lambda seq: SimpleNamespace(
                image_bytes=png("white"), timestamp=time.time(), game={"running": True, "pid": 1},
                capture={"state": "capturing", "isolated_window": True})),
        )
        invalidations = []

        async def invalidate(reason):
            invalidations.append(reason)
            owner._observation_epoch += 1
            release.set()

        owner._invalidate_current_observation = invalidate
        owner._vision = SimpleNamespace(analyze=lambda payload: (release.wait(3) if during else None) or analysis())
        result = await guarded_analysis(owner, png(), helper=True)
        assert result is None
        assert invalidations == ["page_changed_during_analysis"]

    asyncio.run(run())


def test_identical_independent_frame_can_complete_analysis():
    async def run():
        payload = png()
        owner = SimpleNamespace(
            _vision=SimpleNamespace(analyze=lambda payload: analysis()),
            _assistant_client=SimpleNamespace(fetch_frame=lambda seq: SimpleNamespace(
                image_bytes=payload, timestamp=time.time(), game={"running": True, "pid": 1},
                capture={"state": "capturing", "isolated_window": True})),
        )
        assert (await guarded_analysis(owner, payload, helper=True))["ok"]

    asyncio.run(run())


def test_query_waits_for_new_confirmed_capture_not_previous_cache(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        old = analysis()
        old["runtime"] = {"stable_character_ids": [old["characters"][0]["entity_id"]]}
        values["last_local_vision"] = old

        async def produce():
            await asyncio.sleep(0.02)
            for _ in range(2):
                fresh = analysis()
                fresh["characters"] = [
                    {"entity_id": "character.himeko", "name": "姬子", "identity_scope": "screen_label"}
                ]
                await owner._apply_local_analysis(fresh, image_fingerprint="new", source="assistant_local_ocr")

        owner._observer_task = asyncio.create_task(produce())
        await refresh_for_query(owner, timeout=1)
        assert [item["name"] for item in (await owner._current_visual_evidence())["characters"]] == ["姬子"]
        await owner._observer_task

    asyncio.run(run())


def test_query_timeout_does_not_fall_back_to_old_page(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        old = analysis(scene="menu")
        old["runtime"] = {"stable_character_ids": [old["characters"][0]["entity_id"]], "stable_scene": "menu"}
        values["last_local_vision"] = old
        owner._observer_task = asyncio.create_task(asyncio.sleep(0.1))
        await refresh_for_query(owner, timeout=0.02)
        value = await owner._current_visual_evidence()
        assert value["status"] == "updating"
        assert value["characters"] == [] and value["scene"]["primary_state"] == "unknown"
        await owner._observer_task

    asyncio.run(run())


@pytest.mark.parametrize("names", [["那刻夏", "风堇", "大黑塔", "缇宝"], ["火花", "藿藿", "银狼LV.999", "爻光"]])
def test_uncovered_demo_rules_cannot_generate_team_defects(tmp_path, names):
    owner, _, _ = plugin_fixture(tmp_path)
    value = asyncio.run(owner._analysis_payload(names))
    assert len(value["recognized_characters"]) == 4
    assert value["issues"] == [] and value["strengths"] == [] and value["recommended_next_steps"] == []
    assert value["analysis_available"] is False and value["analysis_scope"] == "identity_only"


def test_answering_visual_question_does_not_trigger_parallel_proactive_reply(tmp_path):
    owner, _, sent = plugin_fixture(tmp_path)

    async def run():
        owner._visual_query_waiters = 1
        for _ in range(2):
            await owner._apply_local_analysis(
                analysis(scene="team_setup"), image_fingerprint="new", source="assistant_local_ocr"
            )
        assert sent and all(item["ai_behavior"] == "read" for item in sent)

    asyncio.run(run())
