"""Latency contract tests, not a claim of real-game recognition accuracy."""

from __future__ import annotations

import asyncio
import importlib
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.adapters.text_reader import check_current, read_native_text
from plugin.plugins.hsr_companion.detectors.scene_preview import scene_preview
from plugin.plugins.hsr_companion.runtime.live_refresh import (
    drain_analysis_worker,
    frame_stamp,
    guarded_analysis,
    refresh_for_query,
    validate_before_delivery,
    watch_latest_frame,
)
from plugin.plugins.hsr_companion.runtime.observation import asks_scene_only, asks_screen_identity
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog
from plugin.plugins.hsr_companion.tests.test_live_demo_fixes import png


@pytest.mark.parametrize("query", ["现在是什么场景？", "我现在在哪个界面", "我在做什么"])
def test_scene_question_requests_fresh_scene_without_waiting_for_roster(query):
    assert asks_screen_identity(query)
    assert asks_scene_only(query)


@pytest.mark.parametrize("query", ["现在是什么场景，角色是谁？", "现在呢", "再看看呢", "现在有哪些队员"])
def test_identity_or_ambiguous_question_still_waits_for_names(query):
    assert not asks_scene_only(query)


@pytest.mark.parametrize("title,page", [("跃迁", "warp"), ("队伍编成", "team_setup"), ("角色详情", "character_detail")])
def test_early_scene_probe_before_native_tiles_does_not_replace_coverage(tmp_path, title, page):
    sizes, previews = [], []

    def extract(image):
        sizes.append(image.size)
        return title, [box(title, 20, 20)]

    def progress(boxes):
        preview = scene_preview(boxes, width=2560, height=1600)
        if preview:
            previews.append((len(sizes), preview))

    _, _, details = read_native_text(
        SimpleNamespace(extract_text_with_boxes=extract), Image.new("RGB", (2560, 1600)),
        catalog=catalog(tmp_path), on_progress=progress,
    )
    assert sizes[0] == (768, 480)
    assert previews[0][0] == 1 and previews[0][1]["page"]["page_type"] == page
    assert previews[0][1]["characters"] == []
    assert details["tiles_completed"] == details["tiles_total"] == 4
    assert details["full_frame_covered"] and details["scene_probe_seconds"] is not None


def test_custom_team_title_does_not_block_scene_specific_footer_control():
    preview = scene_preview([box("快速编队", 500, 365)], width=640, height=400)
    assert preview["page"]["page_type"] == "team_setup"
    assert preview["characters"] == []


def test_merged_footer_needs_two_independent_ui_anchors_not_one_prose_keyword():
    preview = scene_preview([box("常用队伍快速编队已启用", 100, 365)], width=640, height=400)
    assert preview["page"]["page_type"] == "team_setup"
    assert scene_preview([box("我们试试快速编队吧", 100, 365)], width=640, height=400) is None


def test_same_capture_native_ocr_does_not_erase_verified_scene_probe(tmp_path):
    calls = []
    def extract(image):
        calls.append(True)
        if len(calls) == 1:
            return "常用队伍快速编队已启用", [box("常用队伍快速编队已启用", 100, 365)]
        return "", []
    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=extract)
    previews = []
    result = adapter.analyze_live(png(), cancelled=lambda: False, on_scene=previews.append)
    assert previews[0]["page"]["page_type"] == "team_setup"
    assert result["page"]["page_type"] == "team_setup"
    assert result["scene"]["verified"] and result["characters"] == []


def test_scene_query_does_not_wait_for_pending_identity_confirmation(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    async def run():
        async def produce():
            await asyncio.sleep(.01)
            await owner._apply_local_analysis(analysis(scene="team_setup"), image_fingerprint="new", source="assistant_local_ocr")
            await asyncio.sleep(.1)
        owner._observer_task = asyncio.create_task(produce())
        await refresh_for_query(owner, timeout=.08, require_characters=False)
        assert not owner._observer_task.done()
        evidence = await owner._current_visual_evidence()
        assert evidence["status"] == "confirming" and evidence["scene"]["verified"]
        await owner._observer_task
    asyncio.run(run())


def test_new_scene_preview_after_restart_not_hidden_by_persisted_larger_serial(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    values["last_local_vision"] = {**analysis(scene="menu"), "observation_serial": 900}
    async def run():
        await owner._publish_scene_preview(scene_preview([box("跃迁", 20, 20)], width=640, height=400))
        evidence = await owner._current_visual_evidence()
        assert evidence["scene"]["primary_state"] == "warp"
        assert evidence["characters"] == []
    asyncio.run(run())


@pytest.mark.parametrize("frame_status", ["unchanged", "stale", "wrong_pid"])
def test_query_recaptures_static_released_helper_immediately_and_keeps_live_guard(tmp_path, monkeypatch, frame_status):
    owner, values, _ = plugin_fixture(tmp_path)
    module = importlib.import_module(type(owner).__module__)
    game = {"pid": 1, "running": True, "foreground": False}
    window = SimpleNamespace(minimized=False, public=lambda: {**game, "pid": 2} if frame_status == "wrong_pid" else game)
    owner._assistant_frame_seq = 5
    owner._observer_last_push_monotonic = time.monotonic()
    owner._visual_query_waiters = 1
    owner._assistant_client = SimpleNamespace(
        health=lambda: {"connected": True, "frame_seq": 5, "game": game, "capture": {"state": "capturing", "isolated_window": True}},
        fetch_frame=lambda seq: None if frame_status == "unchanged" else SimpleNamespace(
            image_bytes=png(), mime="image/png", timestamp=time.time() - 10, frame_seq=6, signature="old",
            game=game, capture={"state": "capturing", "isolated_window": True}),
    )
    owner._assistant_process = SimpleNamespace(snapshot=lambda: {})
    owner._announce_game_presence = lambda value: None
    captured, guards = [], []
    def capture(window):
        captured.append(True)
        return b"independent-new-capture", "image/png", b"signature"
    async def guarded(plugin, payload, *, helper):
        guards.append(helper)
        return analysis(roles=False)
    async def stop(delay):
        raise asyncio.CancelledError()
    monkeypatch.setattr(module, "find_hsr_window", lambda: window)
    monkeypatch.setattr(module, "capture_game_window", capture)
    monkeypatch.setattr(module, "guarded_analysis", guarded)
    monkeypatch.setattr(module, "visual_fingerprint", lambda value: "new")
    monkeypatch.setattr(module, "asyncio", SimpleNamespace(to_thread=asyncio.to_thread, sleep=stop, CancelledError=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(owner._observer_loop())
    if frame_status == "wrong_pid":
        assert captured == [] and guards == []
        assert not (values.get("last_local_vision") or {}).get("characters")
    else:
        assert captured == [True] and guards == [True]
        assert values["last_local_vision"]["source"] == "plugin_compatibility_local_ocr"


def test_footer_or_conflicting_heading_never_publishes_early_guess():
    assert scene_preview([box("角色试用", 20, 370)], width=640, height=400) is None
    assert scene_preview([box("角色试用", 20, 20), box("跃迁", 250, 20)], width=640, height=400) is None


@pytest.mark.parametrize("require_characters", [True, False])
def test_query_scene_preview_wakes_scene_question_but_not_identity_question(tmp_path, require_characters):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        async def produce():
            await asyncio.sleep(.01)
            await owner._publish_scene_preview(scene_preview([box("跃迁", 20, 20)], width=640, height=400))
            await asyncio.sleep(.04)
            await owner._apply_local_analysis(analysis(roles=False, scene="warp"), image_fingerprint="new", source="assistant_local_ocr")
        owner._observer_task = asyncio.create_task(produce())
        await refresh_for_query(owner, timeout=1, require_characters=require_characters)
        result = await owner._current_visual_evidence()
        assert (result["status"] == "recognizing_characters") is (not require_characters)
        assert result["scene"]["primary_state"] == "warp"
        await owner._observer_task
    asyncio.run(run())


@pytest.mark.parametrize("latest", [None, "white", "black"])
def test_handoff_validates_latest_frame_and_revokes_changed_or_unavailable_capture(tmp_path, latest):
    owner, values, _ = plugin_fixture(tmp_path)
    owner._accepted_frame_stamp = frame_stamp(png())
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: None if latest is None else SimpleNamespace(
        image_bytes=png(latest), timestamp=time.time(), game={"running": True},
        capture={"state": "capturing", "isolated_window": True}))
    async def run():
        values["last_local_vision"] = analysis(scene="warp")
        assert await validate_before_delivery(owner) is (latest == "black")
        if latest != "black":
            assert not (await owner._current_visual_evidence())["fresh"]
            if latest is None:
                assert owner._observation_pending_reason == "capture_unavailable_before_delivery"
            else:
                assert values["last_local_vision"]["invalidated"]
    asyncio.run(run())


def test_changed_frame_during_observer_gap_revokes_current_scene(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    owner._accepted_frame_stamp = frame_stamp(png())
    values["last_local_vision"] = analysis(scene="warp")
    async def run():
        assert await watch_latest_frame(owner, png("white"), helper=True)
        assert (await owner._current_visual_evidence())["scene"]["primary_state"] == "unknown"
    asyncio.run(run())


def test_live_ocr_cooperatively_exits_before_remaining_calls_after_transition():
    calls = []
    release = threading.Event()
    def analyze(payload, *, cancelled, on_scene):
        for i in range(8):
            check_current(cancelled)
            calls.append(i)
            if i == 0:
                release.wait(2)
        return analysis()
    async def run():
        owner = SimpleNamespace(
            _vision=SimpleNamespace(analyze_live=analyze),
            _assistant_client=SimpleNamespace(fetch_frame=lambda seq: SimpleNamespace(image_bytes=png("white"))),
        )
        async def invalidate(reason):
            owner._observation_epoch = getattr(owner, "_observation_epoch", 0) + 1
            release.set()
        owner._invalidate_current_observation = invalidate
        assert await guarded_analysis(owner, png(), helper=True) is None
        assert calls == [0]
    asyncio.run(run())


def test_new_title_preview_does_not_cancel_its_own_remaining_ocr(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    values["last_local_vision"] = analysis(scene="menu")
    def analyze(payload, *, cancelled, on_scene):
        on_scene(scene_preview([box("跃迁", 20, 20)], width=640, height=400))
        # Let the event loop publish the new title before full OCR completes.
        release.wait(1)
        check_current(cancelled)
        return analysis(scene="warp")
    release = threading.Event()
    original = owner._publish_scene_preview
    async def publish(value):
        await original(value)
        release.set()
    owner._publish_scene_preview = publish
    owner._vision = SimpleNamespace(analyze_live=analyze)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: SimpleNamespace(
        image_bytes=png(), timestamp=time.time(), game={"running": True, "pid": 1},
        capture={"state": "capturing", "isolated_window": True}))
    async def run():
        result = await guarded_analysis(owner, png(), helper=True)
        assert result is not None and result["scene"]["primary_state"] == "warp"
        assert owner._scene_preview["characters"] == []
    asyncio.run(run())


@pytest.mark.parametrize("change", ["timestamp", "generation_same_timestamp"])
def test_new_query_joins_prequery_capture_and_validates_for_latest_request(change):
    started, release = threading.Event(), threading.Event()
    calls = []
    def analyze(payload, *, cancelled, on_scene):
        calls.append(0)
        started.set()
        release.wait(2)
        check_current(cancelled)
        calls.append(1)
        return analysis()
    async def run():
        owner = SimpleNamespace(
            _query_refresh_after=1,
            _vision=SimpleNamespace(analyze_live=analyze),
            _assistant_client=SimpleNamespace(fetch_frame=lambda seq: SimpleNamespace(
                image_bytes=png(), timestamp=time.time(), game={"running": True, "pid": 1},
                capture={"state": "capturing", "isolated_window": True})),
        )
        async def invalidate(reason):
            raise AssertionError(f"Unchanged page must not be revoked: {reason}")
        owner._invalidate_current_observation = invalidate
        task = asyncio.create_task(guarded_analysis(owner, png(), helper=True))
        await asyncio.to_thread(started.wait, 1)
        if change == "timestamp":
            owner._query_refresh_after = 2
        else:
            owner._query_refresh_generation = 1
        await asyncio.sleep(.35)
        assert calls == [0]  # Still the original worker, not a second OCR pass.
        release.set()
        result = await task
        assert result is not None
        assert result["_live_validation"]["query_generation"] == getattr(owner, "_query_refresh_generation", 0)
        assert calls == [0, 1]
    asyncio.run(run())


def test_confirmed_literal_name_not_repeated_in_retry_atlas(tmp_path):
    calls = []
    def extract(image):
        calls.append(image.size)
        return "姬子", [box("姬子", 100, 100)]
    _, _, details = read_native_text(
        SimpleNamespace(extract_text_with_boxes=extract), Image.new("RGB", (640, 400)), catalog=catalog(tmp_path),
    )
    assert len(calls) == 1 and details["line_rereads"] == 0
    assert details["full_frame_covered"]


def test_observer_cancel_drains_cooperative_worker_before_backend_reuse():
    release, started = threading.Event(), threading.Event()
    def analyze(payload, *, cancelled, on_scene):
        started.set()
        release.wait(2)
        check_current(cancelled)
        return analysis()
    async def run():
        owner = SimpleNamespace(_vision=SimpleNamespace(analyze_live=analyze))
        task = asyncio.create_task(guarded_analysis(owner, png(), helper=False))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner._draining_vision_worker is not None
        release.set()
        await drain_analysis_worker(owner)
        assert owner._draining_vision_worker is None
    asyncio.run(run())
