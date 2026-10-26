"""Goal-based failure matrix: revoke, query, and submission ownership.

Synthetic images assert contracts, not real-game OCR accuracy or chat speed.
"""

from __future__ import annotations

import asyncio
import importlib
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter
from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity, user_timestamp
from plugin.plugins.hsr_companion.runtime.live_refresh import frame_changed, frame_stamp, refresh_for_query
from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp, regions_changed
from plugin.plugins.hsr_companion.runtime.speech import SpeechArbiter
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_experience_fixes import box, catalog
from plugin.sdk.plugin import Ok, unwrap


def test_pause_revokes_before_settings_io_or_worker_drain_and_cannot_auto_restore(tmp_path):
    owner, values, sent = plugin_fixture(tmp_path)
    owner._assistant_process = SimpleNamespace(stop=lambda: None)

    async def run():
        await owner._apply_local_analysis(analysis(scene="menu"), image_fingerprint="old", source="assistant_local_ocr")
        entered, release = asyncio.Event(), asyncio.Event()
        original = owner._read_observer_settings

        async def slow_settings():
            entered.set()
            await release.wait()
            return await original()

        owner._read_observer_settings = slow_settings
        owner._observer_task = asyncio.create_task(asyncio.Event().wait())
        pending = asyncio.create_task(owner.stop_auto_observation())
        await entered.wait()
        assert not pending.done()  # Persistence/cleanup is deliberately stuck.
        evidence = await owner._current_visual_evidence()
        assert evidence["status"] == "stopped" and not evidence["fresh"]
        assert evidence["characters"] == [] and evidence["screen_facts"]["facts"] == []
        assert sent[-1]["metadata"]["status"] == "stopped"
        owner._start_observer_task(restored=True)
        assert owner._observer_status["enabled"] is False
        release.set()
        assert unwrap(await pending)["observer"]["state"] == "stopped"
        assert values["current_game_state"] == {}

    asyncio.run(run())


def test_pause_revokes_while_ocr_cleanup_remains_in_flight(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._observer_task = None
    owner._assistant_process = SimpleNamespace(stop=lambda: None)

    async def run():
        await owner._apply_local_analysis(analysis(scene="menu"), image_fingerprint="old", source="assistant_local_ocr")
        release = asyncio.Event()
        owner._draining_vision_worker = asyncio.create_task(release.wait())
        pending = asyncio.create_task(owner._cancel_observer_task())
        await asyncio.sleep(0)
        assert not pending.done()
        assert not (await owner._current_visual_evidence())["fresh"]
        release.set()
        await pending

    asyncio.run(run())


def test_invalidation_is_effective_before_database_reads_finish(tmp_path):
    owner, _, sent = plugin_fixture(tmp_path)

    async def run():
        await owner._apply_local_analysis(analysis(scene="menu"), image_fingerprint="old", source="assistant_local_ocr")
        entered, release = asyncio.Event(), asyncio.Event()
        original = owner._get_store

        async def slow_get(key):
            if key == "last_local_vision":
                entered.set()
                await release.wait()
            return await original(key)

        owner._get_store = slow_get
        task = asyncio.create_task(owner._invalidate_current_observation("test_transition"))
        await entered.wait()
        assert owner._observation_invalidated
        assert sent[-1]["metadata"]["status"] == "invalidated"
        # Bypass the intentionally blocked store read to check the volatile gate.
        owner._get_store = original
        assert not (await owner._current_visual_evidence())["fresh"]
        release.set()
        await task

    asyncio.run(run())


def test_late_commit_cannot_clear_revocation_after_await(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        original = owner._store_value

        async def slow_store(key, value):
            if key == "last_local_vision":
                entered.set()
                await release.wait()
            await original(key, value)

        owner._store_value = slow_store
        task = asyncio.create_task(owner._apply_local_analysis(analysis(scene="menu"), image_fingerprint="old", source="assistant_local_ocr"))
        await entered.wait()
        owner._stop_observation_now()
        release.set()
        assert (await task)["delivery"]["reason"] == "observation_revoked"
        assert owner._observation_invalidated
        assert not (await owner._current_visual_evidence())["fresh"]

    asyncio.run(run())


@pytest.mark.parametrize("change", ["other_target", "plugin_message", "old", "future", "nan", "missing", "boolean"])
def test_chat_activity_ignores_wrong_target_nonuser_and_invalid_time(change):
    raw = {"type": "user_message", "lanlan": "cat", "_ts": 100, "content": "private"}
    if change == "other_target":
        raw["lanlan"] = "other"
    elif change == "plugin_message":
        raw["type"] = "plugin_message"
    else:
        raw["_ts"] = {"old": -30, "future": 102, "nan": float("nan"), "missing": None, "boolean": True}[change]
    assert user_timestamp(raw, target="cat", now=101) is None


@pytest.mark.parametrize("shape", ["plain", "sdk", "sdk_value"])
def test_chat_activity_reads_only_target_latest_via_public_bus(shape):
    calls = []
    raw = {"type": "user_message", "lanlan": "cat", "_ts": 100, "content": "private"}

    async def get(**kwargs):
        calls.append(kwargs)
        record = raw if shape == "plain" else SimpleNamespace(dump=lambda: {"raw": raw})
        if shape == "sdk_value":
            record = SimpleNamespace(dump=lambda: {"value": SimpleNamespace(dump=lambda: {"raw": raw})})
        return Ok([record])

    status, timestamp = asyncio.run(latest_user_activity(SimpleNamespace(memory=SimpleNamespace(get=get)), target="cat", now=101))
    assert (status, timestamp) == ("available", 100)
    assert calls == [{"bucket_id": "cat", "limit": 1, "timeout": .25}]


def test_user_arriving_during_bus_read_is_not_rejected_as_future():
    async def get(**kwargs):
        await asyncio.sleep(.002)
        return [{"type": "user_message", "lanlan": "cat", "_ts": time.time()}]

    status, timestamp = asyncio.run(latest_user_activity(SimpleNamespace(memory=SimpleNamespace(get=get)), target="cat"))
    assert status == "available" and timestamp is not None


@pytest.mark.parametrize("when", ["before_validation", "during_validation", "read_failure", "empty", "old"])
def test_automatic_submission_observes_chat_without_waiting_for_tool(tmp_path, monkeypatch, when):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._speech_arbiter = SpeechArbiter(hold_seconds=0)
    raw = []
    reads = []

    async def get(**kwargs):
        reads.append(kwargs)
        if when == "read_failure":
            raise TimeoutError()
        return raw

    def speak():
        raw[:] = [{"type": "user_message", "lanlan": "cat", "_ts": time.time(), "content": "SECRET_CHAT"}]

    owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=SimpleNamespace(get=get)), _current_lanlan="cat")
    if when == "before_validation":
        speak()
    elif when == "old":
        raw[:] = [{"type": "user_message", "lanlan": "cat", "_ts": time.time() - 60}]

    async def validate(_):
        if when == "during_validation":
            speak()
        return True

    monkeypatch.setattr(importlib.import_module(type(owner).__module__), "validate_before_delivery", validate)

    async def run():
        await owner._apply_local_analysis(analysis(roles=False, scene="team_setup"), image_fingerprint="new", source="assistant_local_ocr")
        await owner._speech_task
        responding = [item for item in sent if item["ai_behavior"] == "respond"]
        assert len(responding) == (1 if when in {"empty", "old"} else 0)
        assert reads
        assert "SECRET_CHAT" not in str(owner._get_speech_arbiter().snapshot())
        assert owner._get_speech_arbiter().pending is None

    asyncio.run(run())


def test_connection_resume_is_passive_not_a_second_welcome(tmp_path):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._announced_game_pid, owner._game_presence_attempt_at = 0, -1e9
    owner._announce_game_presence({"pid": 1})
    owner._announce_game_presence({"pid": 1})
    assert len(sent) == 1 and sent[0]["ai_behavior"] == "read"
    assert sent[0]["metadata"]["proactive_requested"] is False


def text_picture(*, shade=0, text="OBJECTIVE A"):
    image = Image.new("RGB", (640, 400), (shade, shade, shade))
    ImageDraw.Draw(image).text((35, 82), text, fill="white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("shade", [16, 32, 48, 64])
def test_translucent_text_region_background_light_change_not_a_changed_task(shade):
    region = [{"region": {"left": .05, "top": .2, "right": .5, "bottom": .26}}]
    before = text_picture()
    after = text_picture(shade=shade)
    assert not regions_changed(region_stamp(before, region), region_stamp(after, region))
    assert not frame_changed(frame_stamp(before), frame_stamp(after), region, ui_only=True)
    changed = text_picture(shade=shade, text="OBJECTIVE B")
    assert regions_changed(region_stamp(before, region), region_stamp(changed, region))


@pytest.mark.parametrize("title", ["队伍编成", "跃迁", "角色详情"])
def test_navigation_question_on_confirmed_menu_does_not_read_exploration_crop(tmp_path, title):
    calls = []

    def extract(image):
        calls.append(image.size)
        return title, [box(title, 20, 20)]

    adapter = LocalVisionAdapter(catalog(tmp_path))
    adapter._get_backend = lambda: SimpleNamespace(extract_text_with_boxes=extract)
    result = adapter.analyze_navigation(text_picture())
    assert result["scene"]["verified"]
    assert len(calls) == 1
    assert not result["characters"]
    assert not any(f["kind"] in {"quest_objective", "current_location"} for f in result["screen_facts"]["facts"])


@pytest.mark.parametrize("iteration", range(8))
def test_query_wakes_idle_observer_without_waiting_poll_interval(tmp_path, iteration):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        owner._observer_wake_event = asyncio.Event()
        entered = asyncio.Event()

        async def produce():
            entered.set()
            await owner._wait_observer_tick(10)
            await owner._apply_local_analysis(analysis(roles=False, scene="warp"), image_fingerprint="new", source="assistant_local_ocr")

        owner._observer_task = asyncio.create_task(produce())
        await entered.wait()
        result = await refresh_for_query(owner, timeout=.5, require_characters=False)
        if result is None:
            evidence = await owner._current_visual_evidence()
            pytest.fail(str((owner._last_refresh_diagnostics, owner._query_refresh_after, owner._query_refresh_serial,
                             owner._observation_serial, evidence["observed_at"], evidence["status"])))
        assert result["scene"]["primary_state"] == "warp"
        await owner._observer_task

    asyncio.run(run())


def test_reuse_check_is_in_total_query_deadline(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    module = importlib.import_module("plugin.plugins.hsr_companion.runtime.live_refresh")

    async def slow_reuse(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(module, "try_reuse", slow_reuse)

    async def run():
        owner._observer_task = asyncio.create_task(asyncio.Event().wait())
        assert await asyncio.wait_for(refresh_for_query(owner, timeout=.03, require_characters=False), .4) is None
        assert owner._last_refresh_diagnostics["outcome"] == "timeout"
        assert owner._visual_query_waiters == 0
        owner._observer_task.cancel()

    asyncio.run(run())


def test_query_state_read_timeout_releases_waiters(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def stuck_read():
        await asyncio.Event().wait()

    owner._current_visual_evidence = stuck_read

    async def run():
        owner._observer_task = asyncio.create_task(asyncio.Event().wait())
        assert await asyncio.wait_for(refresh_for_query(owner, timeout=.03, require_characters=True), .4) is None
        assert owner._last_refresh_diagnostics["outcome"] == "timeout"
        assert owner._visual_query_waiters == 0
        owner._observer_task.cancel()

    asyncio.run(run())


def test_pause_wakes_pending_query_without_waiting_for_deadline(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        owner._observer_task = asyncio.create_task(asyncio.Event().wait())
        query = asyncio.create_task(refresh_for_query(owner, timeout=10, require_characters=True))
        while not getattr(owner, "_visual_query_waiters", 0):
            await asyncio.sleep(0)
        owner._stop_observation_now()
        assert await asyncio.wait_for(query, .4) is None
        assert owner._last_refresh_diagnostics["outcome"] == "observer_unavailable"
        assert owner._visual_query_waiters == 0

    asyncio.run(run())
