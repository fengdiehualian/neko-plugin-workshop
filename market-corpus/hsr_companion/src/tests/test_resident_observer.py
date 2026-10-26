"""Resident-worker/query scheduling contracts, not live OCR accuracy claims."""
import asyncio
import copy
import logging
import threading
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.runtime.live_refresh import frame_stamp, refresh_for_query
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp, try_reuse
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_query_reuse import REGIONS, frame, picture, prepare, result


def sample(payload, *, seq=1, age=0):
    value = frame(payload)
    value.frame_seq = seq
    value.timestamp -= age
    value.signature = "test-frame"
    value.mime = "image/png"
    return value


def configure_loop(owner, client):
    owner._assistant_client = client
    owner._assistant_frame_seq = 0
    owner._assistant_process = SimpleNamespace(snapshot=lambda: {})
    owner._observer_wake_event = asyncio.Event()
    owner._announce_game_presence = lambda window: None
    owner.logger = logging.getLogger("hsr-resident-unit")


def health(seq=1):
    return {"connected": True, "frame_seq": seq,
            "game": {"running": True, "pid": 1, "minimized": False},
            "capture": {"state": "capturing", "isolated_window": True, "frame_semantics": "every_capture"}}


def test_navigation_cache_does_not_depend_on_unasked_floating_entity(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    from io import BytesIO
    entity_region = {"region": {"left": .75, "right": .95, "top": .7, "bottom": .8}}
    def payload(text):
        stream = BytesIO(picture())
        image = Image.open(stream).convert("RGB")
        ImageDraw.Draw(image).text((485, 285), text, fill="white")
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    async def run():
        original = payload("NPC A")
        value = result()
        value["recognition"] = {}  # A real resident full result, not a navigation-only fixture.
        value["screen_facts"]["facts"].append({"kind": "entity_label", "scope": "displayed_label", "value": "NPC A",
            "evidence": [{"text": "NPC A", **entity_region}]})
        value["screen_facts"]["visible_text"] = [{"text": "NPC A", **entity_region}]
        owner._next_frame_stamp = frame_stamp(original)
        all_regions = [*REGIONS, entity_region]
        owner._next_frame_query_guard = (all_regions, region_stamp(original, all_regions))
        owner._next_frame_page_regions = []
        owner._analysis_game_pid = 1
        await owner._apply_local_analysis(value, image_fingerprint="full", source="assistant_local_ocr")
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: sample(payload("NPC B")))
        selected = await try_reuse(owner, navigation=True)
        assert selected is not None  # Old implementation revoked all nav due to an unrelated NPC label.
        assert {f["kind"] for f in selected["screen_facts"]["facts"]} == {
            "current_location", "tracked_quest", "quest_objective"}
        assert not selected["screen_facts"].get("visible_text")
    asyncio.run(run())


@pytest.mark.parametrize("suspended", [False, True])
def test_resident_snapshot_recovers_after_old_transfer_without_repeating_ocr(tmp_path, suspended):
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        await prepare(owner)
        epoch = getattr(owner, "_observation_epoch", 0)
        if suspended:
            await owner._invalidate_current_observation("capture_unavailable_after_analysis")
            owner._observer_status["state"] = "error"
        reads = []
        def capture(seq):
            reads.append(seq)
            return sample(picture(), seq=len(reads), age=5 if len(reads) == 1 else 0)
        owner._assistant_client = SimpleNamespace(fetch_frame=capture)
        owner._observer_task = asyncio.create_task(asyncio.Event().wait())  # Already resident, never started by query.
        try:
            selected = await refresh_for_query(owner, timeout=1, require_characters=False, require_navigation=True)
            assert selected is not None, owner._last_refresh_diagnostics
            assert selected["reuse"]["ocr_repeated"] is False
            assert ObservationSnapshot.select(owner, selected).matches(owner)
            assert len(reads) >= 2
            assert getattr(owner, "_observation_epoch", 0) == epoch
            if suspended:
                # The query has a validated SCOPED handoff; unasked parts of
                # the global full observation must stay masked until a commit.
                assert owner._observation_pending_reason
                assert not (await owner._current_visual_evidence())["fresh"]
            else:
                assert not getattr(owner, "_observation_pending_reason", "")
        finally:
            owner._observer_task.cancel()
    asyncio.run(run())


def test_recovery_frame_with_changed_task_still_rejects_old_cache(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        await prepare(owner)
        await owner._invalidate_current_observation("capture_error")
        owner._observer_status["state"] = "error"
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: sample(picture(task="OBJECTIVE B")))
        assert await try_reuse(owner, navigation=True) is None
        assert not (await owner._current_visual_evidence())["fresh"]
        # A failed check quarantines delivery, not the successful candidate.
        # Keeping it must never authorize the now-changed objective.
        assert owner._reusable_observations
        assert await try_reuse(owner, navigation=True) is None
        assert not (await owner._current_visual_evidence())["fresh"]
    asyncio.run(run())


def test_modern_helper_without_new_sequence_does_not_spawn_compatibility_capture(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    compat = []
    def forbidden(*args):
        compat.append(True)
        raise RuntimeError("a healthy helper should remain the only capture owner")
    monkeypatch.setattr("plugin.plugins.hsr_companion.find_hsr_window", forbidden)
    configure_loop(owner, SimpleNamespace(health=health, fetch_frame=lambda seq: None))
    owner._visual_query_waiters = 1
    async def done(delay):
        raise asyncio.CancelledError()
    owner._wait_observer_tick = done
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await owner._observer_loop()
    asyncio.run(run())
    assert not compat
    assert owner._observer_status["state"] != "error"


def test_modern_helper_stale_frame_waits_without_second_capture_session(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    compat = []
    monkeypatch.setattr("plugin.plugins.hsr_companion.find_hsr_window", lambda: compat.append(True))
    configure_loop(owner, SimpleNamespace(health=health, fetch_frame=lambda seq: sample(picture(), age=5)))
    owner._visual_query_waiters = 1
    async def done(delay):
        raise asyncio.CancelledError()
    owner._wait_observer_tick = done
    async def run():
        with pytest.raises(asyncio.CancelledError):
            await owner._observer_loop()
    asyncio.run(run())
    assert not compat
    assert owner._observer_status["state"] == "updating"


def test_lightweight_health_polls_do_not_retransfer_the_same_native_frame(tmp_path):
    from plugin.plugins.hsr_companion.runtime.live_refresh import guarded_analysis
    owner, _, _ = plugin_fixture(tmp_path)
    reads, polls = [], []
    def capture(seq):
        reads.append(seq)
        return sample(picture())
    def status():
        polls.append(True)
        return health()
    configure_loop(owner, SimpleNamespace(health=status, fetch_frame=capture))
    owner._assistant_frame_seq = 1
    owner._analysis_game_pid = 1
    release = threading.Event()
    def full(data, **kwargs):
        assert release.wait(2)
        return result()
    owner._vision = SimpleNamespace(analyze_live=full)
    async def run():
        task = asyncio.create_task(guarded_analysis(owner, picture(), helper=True))
        await asyncio.sleep(.6)
        release.set()
        assert await task is not None
    try:
        asyncio.run(run())
    finally:
        release.set()
    assert len(polls) >= 2
    assert reads == [0]  # Final fresh validation, no repeated in-progress PNG transfers.


def test_pause_during_recovery_cannot_be_undone_by_a_late_frame(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    async def run():
        await prepare(owner)
        await owner._invalidate_current_observation("capture_error")
        def capture(seq):
            owner._stop_observation_now()
            return sample(picture())
        owner._assistant_client = SimpleNamespace(fetch_frame=capture)
        assert await try_reuse(owner, navigation=True) is None
        assert not owner._observer_status["enabled"]
        assert not (await owner._current_visual_evidence())["fresh"]
    asyncio.run(run())


def test_transient_cache_miss_retries_while_resident_full_worker_is_still_busy(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    started, release = threading.Event(), threading.Event()
    reads = []
    async def recent(target, **kwargs):
        reads.append(True)
        return None if len(reads) == 1 else sample(picture())
    monkeypatch.setattr("plugin.plugins.hsr_companion.runtime.live_refresh.fetch_recent_frame", recent)
    async def run():
        await prepare(owner)
        configure_loop(owner, SimpleNamespace(health=health, fetch_frame=lambda seq: sample(picture())))
        def full(data, **kwargs):
            started.set()
            assert release.wait(2)
            return result()
        owner._vision = SimpleNamespace(analyze_live=full)
        owner._observer_task = asyncio.create_task(owner._observer_loop())
        try:
            assert await asyncio.to_thread(started.wait, 1)
            selected = await refresh_for_query(owner, timeout=.5, require_characters=False, require_navigation=True)
            assert selected is not None, owner._last_refresh_diagnostics
            assert selected["reuse"]["ocr_repeated"] is False
            assert len(reads) >= 2
            assert not release.is_set()  # The answer did NOT wait for full recognition to finish.
        finally:
            release.set()
            owner._observer_stop_requested = True
            owner._observer_task.cancel()
            try:
                await owner._observer_task
            except asyncio.CancelledError:
                pass
            from plugin.plugins.hsr_companion.runtime.live_refresh import drain_analysis_worker
            await drain_analysis_worker(owner)
    asyncio.run(run())


@pytest.mark.parametrize("cache", [True, False])
def test_question_during_actual_resident_full_worker_does_not_restart_it(tmp_path, cache):
    owner, _, _ = plugin_fixture(tmp_path)
    started, release = threading.Event(), threading.Event()
    calls = []
    async def run():
        await prepare(owner)
        if not cache:
            owner._reusable_observations = {}
        payload = picture()
        configure_loop(owner, SimpleNamespace(health=health, fetch_frame=lambda seq: sample(payload)))
        def full(data, **kwargs):
            calls.append(getattr(owner, "_visual_query_waiters", 0))
            started.set()
            assert release.wait(2)
            value = copy.deepcopy(result())
            value["recognition"]["scene_regions"] = REGIONS
            return value
        owner._vision = SimpleNamespace(analyze_live=full)
        owner._observer_task = asyncio.create_task(owner._observer_loop())
        try:
            assert await asyncio.to_thread(started.wait, 1)
            assert calls == [0]  # Actual production observer has begun before the query exists.
            async def finish():
                await asyncio.sleep(.2)
                release.set()
            finisher = asyncio.create_task(finish())
            selected = await refresh_for_query(owner, timeout=1.5, require_characters=False, require_navigation=True)
            await finisher
            assert selected is not None, owner._last_refresh_diagnostics
            assert selected["page_type"] == "exploration_hud"
            assert calls == [0]  # Neither cancellation/restart nor parallel query OCR.
            if cache:
                assert selected["reuse"]["ocr_repeated"] is False
            else:
                assert any(s["stage"] == "query_joined_worker" for s in owner._last_refresh_diagnostics["stages"])
        finally:
            release.set()
            owner._observer_stop_requested = True
            owner._observer_task.cancel()
            try:
                await owner._observer_task
            except asyncio.CancelledError:
                pass
            from plugin.plugins.hsr_companion.runtime.live_refresh import drain_analysis_worker
            await drain_analysis_worker(owner)
    asyncio.run(run())
