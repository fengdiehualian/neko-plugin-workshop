"""Stable page reuse, small text changes and page switches, not OCR accuracy."""

import asyncio
import copy
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.runtime.live_refresh import frame_changed, frame_stamp, refresh_for_query
from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp, regions_changed, remember_reusable, try_reuse
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture

REGIONS = [{"region": {"left": .05, "top": y, "right": .5, "bottom": y + .06}} for y in (.02, .2, .3)]


def picture(*, task="OBJECTIVE A", page=False, noise=False):
    image = Image.new("RGB", (640, 400), "white" if page else "black")
    draw = ImageDraw.Draw(image)
    for y, text in zip((.02, .2, .3), ("PLACE A", "QUEST A", task)):
        draw.text((35, int(y * 400) + 3), text, fill="white")
    if noise:
        draw.rectangle((500, 300, 510, 310), fill="white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def frame(payload):
    return SimpleNamespace(image_bytes=payload, timestamp=time.time(), game={"pid": 1, "running": True, "minimized": False},
                           capture={"state": "capturing", "isolated_window": True})


def result():
    value = analysis(roles=False, scene="exploration")
    value["page"] = {"page_type": "exploration_hud", "label": "探索与任务追踪"}
    value["recognition"] = {"navigation_only_query": True, "scene_only_query": True}
    value["screen_facts"] = {"facts": [
        {"kind": kind, "scope": scope, "value": text, "evidence": [REGIONS[i]]}
        for i, (kind, scope, text) in enumerate((
            ("current_location", "location", "地点甲"), ("tracked_quest", "quest", "任务甲"),
            ("quest_objective", "quest", "目标甲")))], "unclassified_text": []}
    return value


async def prepare(owner, payload=None):
    payload = payload or picture()
    owner._next_frame_stamp = frame_stamp(payload)
    controls = [{"region": {"left": .75, "top": .01, "right": .95, "bottom": .08}}]
    owner._next_frame_page_regions = controls
    owner._next_frame_query_guard = ([*controls, *REGIONS], region_stamp(payload, [*controls, *REGIONS]))
    owner._analysis_game_pid = 1
    await owner._apply_local_analysis(result(), image_fingerprint="original", source="assistant_local_ocr")
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))


@pytest.mark.parametrize("navigation", [True, False])
def test_unchanged_page_reuses_prequery_result_without_new_ocr_or_cancellation(tmp_path, monkeypatch, navigation):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        # This is a reuse contract, not a scheduler-speed benchmark. Keep the
        # simulated capture fresh and observer alive until explicit teardown;
        # the separate expired-capture tests still exercise production limits.
        import plugin.plugins.hsr_companion.runtime.query_reuse as reuse_module

        sample_time = time.time()
        monkeypatch.setattr(reuse_module, "time", SimpleNamespace(time=lambda: sample_time))
        original_fetch = owner._assistant_client.fetch_frame

        def stable_frame(seq):
            captured = original_fetch(seq)
            captured.timestamp = sample_time
            return captured

        owner._assistant_client.fetch_frame = stable_frame
        owner._query_refresh_after = 123
        owner._observer_task = asyncio.create_task(asyncio.Event().wait())
        serial = owner._observation_serial
        reused = await refresh_for_query(owner, require_characters=False, require_navigation=navigation)
        assert reused is not None, owner._last_refresh_diagnostics
        assert reused["screen_facts"]["facts"][0]["value"] == "地点甲"
        assert reused["reuse"]["ocr_repeated"] is False
        assert owner._observation_serial == serial and owner._query_refresh_after == 123
        assert not owner._observer_task.done()
        assert owner._last_refresh_diagnostics["outcome"] == "reused"
        assert not reused["characters"] and reused["read_scope"] == ("navigation" if navigation else "scene")
        owner._observer_task.cancel()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["page", "task"])
def test_changed_page_or_one_letter_task_cannot_reuse_old_answer(tmp_path, change):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        changed = picture(page=True) if change == "page" else picture(task="OBJECTIVE B")
        if change == "task":
            assert not frame_changed(frame_stamp(picture()), frame_stamp(changed))
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed))
        # Test revocation independently of the thread pool meeting a 20ms
        # query budget. Timeout/latency is covered by separate refresh tests.
        assert await asyncio.wait_for(try_reuse(owner, navigation=True), timeout=1) is None
        assert owner._committed_observation
        current = await owner._current_visual_evidence()
        assert not any(f["kind"] == "quest_objective" for f in current["screen_facts"]["facts"])
        if change == "page":
            assert owner._observation_pending_reason == "verification_pending_control"
            assert not current["fresh"] and owner._reusable_observations
        else:
            assert any(f["kind"] == "current_location" for f in current["screen_facts"]["facts"])

    asyncio.run(run())


def test_changed_page_accepts_new_capture_not_previous_fields(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        payload = picture(page=True)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        async def produce():
            while not getattr(owner, "_query_refresh_after", 0):
                await asyncio.sleep(.001)
            next_result = result()
            next_result["screen_facts"]["facts"][0]["value"] = "地点乙"
            next_result["screen_facts"]["facts"][2]["value"] = "目标乙"
            owner._next_frame_stamp = frame_stamp(payload)
            regions = [*owner._next_frame_page_regions, *REGIONS]
            owner._next_frame_query_guard = (regions, region_stamp(payload, regions))
            await owner._apply_local_analysis(next_result, image_fingerprint="new", source="assistant_local_ocr")
        owner._observer_task = asyncio.create_task(produce())
        answer = await refresh_for_query(owner, timeout=1, require_characters=False, require_navigation=True)
        evidence = answer or await owner._current_visual_evidence()
        assert evidence["screen_facts"]["facts"][0]["value"] == "地点乙"
        assert owner._last_refresh_diagnostics["outcome"] == "ready"

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["future_snapshot", "stale_frame", "minimized", "different_process", "missing_capture", "invalidated", "paused"])
def test_invalid_snapshot_or_capture_never_reuses(tmp_path, failure):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        latest = frame(picture())
        if failure == "future_snapshot":
            # Old recognition is re-checkable; impossible future timestamps
            # remain invalid. Long-lived positive cases have their own suite.
            for snapshot in owner._reusable_observations.values():
                snapshot["evidence"]["observed_at"] = (datetime.now(timezone.utc) + timedelta(seconds=31)).isoformat()
        elif failure == "stale_frame":
            latest.timestamp -= 5
        elif failure == "minimized":
            latest.game["minimized"] = True
        elif failure == "different_process":
            latest.game["pid"] = 2
        elif failure == "missing_capture":
            latest = None
        elif failure == "invalidated":
            await owner._invalidate_current_observation("test")
        elif failure == "paused":
            owner._observer_status["state"] = "paused"
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: latest)
        owner._observer_task = asyncio.create_task(asyncio.sleep(1))
        assert await refresh_for_query(owner, timeout=.02, require_characters=False, require_navigation=True) is None
        assert owner._last_refresh_diagnostics["outcome"] == "timeout"
        owner._observer_task.cancel()

    asyncio.run(run())


@pytest.mark.parametrize("content", [True, False])
def test_scoped_cache_cannot_answer_character_or_content_query(tmp_path, content):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        owner._observer_task = asyncio.create_task(asyncio.sleep(1))
        assert await refresh_for_query(owner, timeout=.02, require_characters=True, require_content=content) is None
        assert owner._last_refresh_diagnostics["outcome"] == "timeout"
        owner._observer_task.cancel()

    asyncio.run(run())


def test_missing_task_keeps_scene_but_cannot_finish_navigation_query(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        owner._reusable_observations = {}
        partial = copy.deepcopy(values["last_local_vision"])
        partial["screen_facts"]["facts"] = partial["screen_facts"]["facts"][:1]
        remember_reusable(owner, partial)
        assert "scene" in owner._reusable_observations and "navigation" not in owner._reusable_observations

    asyncio.run(run())


def test_tool_uses_validated_snapshot_even_when_background_pass_has_no_task(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        # A later background read may be incomplete, but never gets to invent
        # or overwrite the separately validated navigation answer.
        values["last_local_vision"] = analysis(roles=False)
        owner._observer_task = asyncio.create_task(asyncio.sleep(1))
        answer = await owner.hsr_companion_prepare(query="现在在哪，接下来这个任务要做什么？")
        assert answer["query_timing"]["refresh"]["outcome"] == "reused"
        assert answer["screen_context_answer"]["navigation"]["current_location"]["value"] == "地点甲"
        assert answer["screen_context_answer"]["navigation"]["objective"]["value"] == "目标甲"
        assert not answer["current_visual_evidence"]["characters"]
        owner._observer_task.cancel()

    asyncio.run(run())


def test_unrelated_small_world_animation_does_not_force_ocr(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture(noise=True)))
        owner._observer_task = asyncio.create_task(asyncio.sleep(1))
        assert await refresh_for_query(owner, require_characters=False, require_navigation=True) is not None
        owner._observer_task.cancel()

    asyncio.run(run())


def test_newly_completed_read_can_be_revalidated_even_if_capture_precedes_question(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        payload = picture()
        captured_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        owner._analysis_game_pid = 1
        calls = []
        async def produce():
            await asyncio.sleep(.01)
            calls.append("single_completed_read")
            owner._next_frame_stamp = frame_stamp(payload)
            owner._next_frame_query_guard = (REGIONS, region_stamp(payload, REGIONS))
            await owner._apply_local_analysis(result(), image_fingerprint="new", source="assistant_local_ocr", observed_at=captured_at)
        owner._observer_task = asyncio.create_task(produce())
        answer = await refresh_for_query(owner, timeout=1, require_characters=False, require_navigation=True)
        assert answer["observed_at"] == captured_at
        assert calls == ["single_completed_read"]
        assert owner._last_refresh_diagnostics["outcome"] == "ready"
        assert "completed_read_revalidated" in [step["stage"] for step in owner._last_refresh_diagnostics["stages"]]

    asyncio.run(run())


def test_scene_reuse_checks_only_scene_and_location_not_task_text(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        owner._next_frame_query_scene_guard = (REGIONS[:1], region_stamp(picture(), REGIONS[:1]))
        remember_reusable(owner, values["last_local_vision"])
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture(task="OBJECTIVE B")))
        owner._observer_task = asyncio.create_task(asyncio.sleep(1))
        answer = await refresh_for_query(owner, require_characters=False)
        assert [f["kind"] for f in answer["screen_facts"]["facts"]] == ["current_location"]
        assert owner._last_refresh_diagnostics["outcome"] == "reused"
        owner._observer_task.cancel()

    asyncio.run(run())


def test_navigation_question_on_new_menu_finishes_one_validated_read_without_old_quest(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        await owner._invalidate_current_observation("new_menu")
        payload = picture(page=True)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        captured_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        async def produce():
            await asyncio.sleep(.01)
            menu = analysis(roles=False, scene="team_setup")
            menu["page"] = {"page_type": "team_setup", "label": "队伍编成"}
            menu["recognition"] = {"scene_only_query": True, "navigation_only_query": True}
            owner._next_frame_stamp = frame_stamp(payload)
            owner._next_frame_query_guard = (REGIONS, region_stamp(payload, REGIONS))
            await owner._apply_local_analysis(menu, image_fingerprint="new", source="assistant_local_ocr", observed_at=captured_at)
        owner._observer_task = asyncio.create_task(produce())
        answer = await refresh_for_query(owner, timeout=1, require_characters=False, require_navigation=True)
        assert answer["page_type"] == "team_setup" and answer["screen_facts"]["facts"] == []
        assert "completed_read_revalidated" in [s["stage"] for s in owner._last_refresh_diagnostics["stages"]]

    asyncio.run(run())


def test_transient_error_cannot_finish_scene_query_before_recovery(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)

    async def run():
        values["last_local_vision"] = {**analysis(roles=False), "ok": False}
        async def produce():
            await asyncio.sleep(.02)
            await owner._apply_local_analysis(analysis(roles=False, scene="team_setup"), image_fingerprint="new", source="assistant_local_ocr")
        owner._observer_task = asyncio.create_task(produce())
        await refresh_for_query(owner, timeout=1, require_characters=False)
        assert (await owner._current_visual_evidence())["scene"]["verified"]

    asyncio.run(run())


def test_reuse_invalidation_cannot_complete_with_precheck_navigation_evidence(tmp_path, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import live_refresh

    owner, _, _ = plugin_fixture(tmp_path)
    real_reuse = live_refresh.try_reuse
    revoked = asyncio.Event()

    async def revoke_once(target, *, navigation):
        if target._reusable_observations and not revoked.is_set():
            await target._invalidate_current_observation("pixels_changed_at_handoff")
            revoked.set()
            return None
        return await real_reuse(target, navigation=navigation)

    monkeypatch.setattr(live_refresh, "try_reuse", revoke_once)

    async def run():
        owner._reusable_observations = {}
        payload = picture()
        owner._analysis_game_pid = 1
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        async def produce():
            while not getattr(owner, "_query_refresh_after", 0):
                await asyncio.sleep(.001)
            owner._next_frame_stamp = frame_stamp(payload)
            owner._next_frame_query_guard = (REGIONS, region_stamp(payload, REGIONS))
            before_question = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            await owner._apply_local_analysis(result(), image_fingerprint="old", source="assistant_local_ocr", observed_at=before_question)
            await revoked.wait()
            await asyncio.sleep(.02)
            next_result = result()
            next_result["screen_facts"]["facts"][2]["value"] = "重新确认的目标"
            await owner._apply_local_analysis(next_result, image_fingerprint="new", source="assistant_local_ocr")
        owner._observer_task = asyncio.create_task(produce())
        answer = await refresh_for_query(owner, timeout=1, require_characters=False, require_navigation=True)
        evidence = answer or await owner._current_visual_evidence()
        assert revoked.is_set() and evidence["fresh"]
        assert evidence["screen_facts"]["facts"][2]["value"] == "重新确认的目标"

    asyncio.run(run())


def test_live_delivery_rejects_revoked_observation_even_when_stamp_was_cleared(tmp_path):
    from plugin.plugins.hsr_companion.runtime.live_refresh import validate_before_delivery

    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await prepare(owner)
        await owner._invalidate_current_observation("pixels_changed_at_handoff")
        assert owner._accepted_frame_stamp is None
        assert await validate_before_delivery(owner) is False

    asyncio.run(run())


def test_region_check_tolerates_few_weak_pixels_but_rejects_local_glyph_change():
    before = ((tuple([0, 0, 1, 1]), (64, 8), bytes(512)),)
    weak = bytearray(512)
    weak[:5] = bytes([32] * 5)
    assert not regions_changed(before, ((before[0][0], before[0][1], bytes(weak)),))
    weak[:8] = bytes([32] * 8)
    assert regions_changed(before, ((before[0][0], before[0][1], bytes(weak)),))
    strong = bytearray(512)
    strong[:3] = bytes([200] * 3)
    assert regions_changed(before, ((before[0][0], before[0][1], bytes(strong)),))


def test_fresh_postquery_read_uses_normal_delivery_not_cache_recheck(tmp_path, monkeypatch):
    from plugin.plugins.hsr_companion.runtime import live_refresh

    owner, _, _ = plugin_fixture(tmp_path)
    checks = []
    async def cache_miss(target, *, navigation):
        checks.append("cache_check")
        assert len(checks) == 1, "new OCR must not enter the old-cache rejection loop"
        return None
    monkeypatch.setattr(live_refresh, "try_reuse", cache_miss)

    async def run():
        owner._reusable_observations = {}
        payload = picture()
        owner._analysis_game_pid = 1
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        async def produce():
            while not getattr(owner, "_query_refresh_after", 0):
                await asyncio.sleep(.001)
            await prepare(owner, payload)
        owner._observer_task = asyncio.create_task(produce())
        answer = await refresh_for_query(owner, timeout=1, require_characters=False, require_navigation=True)
        assert answer["fresh"] and answer["handoff"]["epoch"] == getattr(owner, "_observation_epoch", 0)
        assert len(checks) == 1 and owner._last_refresh_diagnostics["outcome"] == "ready"

    asyncio.run(run())


def test_exploration_camera_motion_outside_ui_does_not_revoke_scene_or_navigation(tmp_path):
    from plugin.plugins.hsr_companion.runtime.live_refresh import watch_latest_frame

    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        original = picture()
        await prepare(owner, original)
        with Image.open(BytesIO(original)) as source:
            moved = Image.new("RGB", source.size, "white")
            for item in [*owner._next_frame_page_regions, *REGIONS]:
                r = item["region"]
                bounds = tuple(round(r[key] * limit) for key, limit in (
                    ("left", 640), ("top", 400), ("right", 640), ("bottom", 400)))
                moved.paste(source.crop(bounds), bounds[:2])
            stream = BytesIO()
            moved.save(stream, format="PNG")
        payload = stream.getvalue()
        assert frame_changed(frame_stamp(original), frame_stamp(payload))
        assert await watch_latest_frame(owner, payload, helper=True) is False
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        owner._observer_task = asyncio.create_task(asyncio.sleep(1))
        answer = await refresh_for_query(owner, require_characters=False, require_navigation=True)
        assert answer["page_type"] == "exploration_hud" and answer["screen_facts"]["facts"][2]["value"] == "目标甲"
        assert owner._last_refresh_diagnostics["outcome"] == "reused"
        owner._observer_task.cancel()

    asyncio.run(run())
