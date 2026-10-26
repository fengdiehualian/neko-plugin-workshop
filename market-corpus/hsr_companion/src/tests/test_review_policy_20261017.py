"""Common admission invariants across scopes, with explicit synthetic OCR.

No game input, installed plugin, host messages or actual model calls.
Live resident verification is a separate, recorded acceptance step.
"""
import asyncio
import copy
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from plugin.plugins.hsr_companion.adapters.text_reader import ObservationSuperseded
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime import live_refresh
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, commit_raw, navigation_raw
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame, picture

QUERY = "现在在哪，接下来这个任务要做什么？"


def changed_pixels(*, control=False, objective=False):
    with Image.open(BytesIO(picture())) as opened:
        image = opened.convert("RGB")
    region = (20, 15, 28, 23) if control else (310, 108, 320, 115) if objective else (310, 64, 320, 72)
    ImageDraw.Draw(image).rectangle(region, fill="white")
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


async def resident_owner(tmp_path):
    owner, values, sent = audit_owner(tmp_path)
    await commit_raw(owner, navigation_raw())
    await commit_raw(owner, navigation_raw())
    calls = []
    def verify(data, proofs, **kwargs):
        calls.append(copy.deepcopy(proofs))
        return {proof_key(p) for p in proofs}
    owner._vision.verify_text_regions = verify
    return owner, values, sent, calls


async def select(owner, path):
    trace = getattr(owner, "_active_refresh_trace", None)
    if trace is None:
        owner._active_refresh_trace = trace = {"started": time.monotonic(), "stages": []}
    plan = build_query_plan(QUERY)
    if path == "direct":
        result = await live_refresh.validated_delivery(owner, await owner._current_visual_evidence(), plan=plan)
    else:
        result = await try_reuse(owner, navigation=path == "navigation", full=path == "full", plan=plan)
    if result is None:
        print("review_test_rejection", trace["stages"])
    return result


@pytest.mark.parametrize("path", ["navigation", "full", "direct"])
@pytest.mark.parametrize("control", [False, True])
def test_same_literals_under_dynamic_pixels_have_one_rule(tmp_path, path, control):
    async def run():
        owner, _, _, calls = await resident_owner(tmp_path)
        epoch = getattr(owner, "_observation_epoch", 0)
        original = copy.deepcopy(owner._committed_observation)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed_pixels(control=control)))
        result = await select(owner, path)
        assert result is not None
        assert {f["value"] for f in result["screen_facts"]["facts"]} == {"地点甲", "任务甲", "目标甲"}
        assert calls  # No pixel-only approval of changed literals.
        assert getattr(owner, "_observation_epoch", 0) == epoch
        assert owner._committed_observation == original
    asyncio.run(run())


@pytest.mark.parametrize("path", ["navigation", "full", "direct"])
def test_real_objective_change_keeps_location_but_never_old_objective(tmp_path, path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed_pixels(objective=True)))
        owner._vision.verify_text_regions = lambda data, proofs, **kw: set()
        result = await select(owner, path)
        assert result is not None
        facts = result["screen_facts"]["facts"]
        assert any(f["kind"] == "current_location" and f["value"] == "地点甲" for f in facts)
        assert not any(f["kind"] == "quest_objective" for f in facts)
        assert owner._committed_observation["screen_facts"]["facts"][-1]["value"] == "目标甲"
    asyncio.run(run())


@pytest.mark.parametrize("path", ["navigation", "full", "direct"])
def test_busy_review_preserves_candidate_for_later_exact_recovery(tmp_path, path):
    async def run():
        owner, _, _, calls = await resident_owner(tmp_path)
        keys = set(owner._reusable_observations)
        epoch = getattr(owner, "_observation_epoch", 0)
        def busy(*args, **kwargs):
            raise ObservationSuperseded("native busy")
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed_pixels()))
        owner._vision.verify_text_regions = busy
        assert await select(owner, path) is None
        assert set(owner._reusable_observations) == keys
        assert getattr(owner, "_observation_epoch", 0) == epoch
        owner._vision.verify_text_regions = lambda data, proofs, **kw: calls.append(proofs) or {proof_key(p) for p in proofs}
        result = await select(owner, path)
        assert result is not None and len(result["screen_facts"]["facts"]) == 3
    asyncio.run(run())


def test_query_waiting_does_not_make_confirmed_current_panel_empty(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._query_refresh_after = time.time() + .01
        owner._query_refresh_serial = owner._observation_serial
        current = await owner._current_visual_evidence()
        assert current["fresh"]
        assert len(current["screen_facts"]["facts"]) == 3
    asyncio.run(run())


def test_soft_background_update_is_not_an_answer_revocation(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._observer_status["state"] = "updating"
        current = await owner._current_visual_evidence()
        assert current["fresh"]
        result = await select(owner, "navigation")
        assert result is not None
    asyncio.run(run())


def test_navigation_can_review_full_candidate_when_optimized_cache_is_missing(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._reusable_observations.pop("navigation", None)
        owner._reusable_observations.pop("scene", None)
        result = await select(owner, "navigation")
        assert result is not None and len(result["screen_facts"]["facts"]) == 3
    asyncio.run(run())


def test_checked_packet_is_consumed_without_second_capture_or_ocr(tmp_path):
    async def run():
        owner, _, _, calls = await resident_owner(tmp_path)
        result = await select(owner, "full")
        assert result is not None
        def unexpected(*args):
            raise AssertionError("already-reviewed packet was sampled again")
        owner._assistant_client.fetch_frame = unexpected
        delivered = await live_refresh.validated_delivery(owner, result, plan=build_query_plan(QUERY))
        assert delivered is not None and not calls
        assert ObservationSnapshot.select(owner, delivered, fields={"location", "quest"}).matches(owner)
    asyncio.run(run())


@pytest.mark.parametrize("action", ["pause", "new_question", "real_page_change"])
def test_review_receipt_does_not_override_lifecycle_revocation(tmp_path, action):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._active_refresh_trace = {"started": time.monotonic(), "stages": []}
        result = await select(owner, "full")
        assert result is not None, owner._active_refresh_trace["stages"]
        if action == "pause":
            owner._observer_stop_requested = True
            owner._observer_status.update(enabled=False, state="paused")
        elif action == "new_question":
            owner._answer_query_generation = getattr(owner, "_answer_query_generation", 0) + 1
        else:
            await owner._invalidate_current_observation("new_page_title")
        assert await live_refresh.validated_delivery(owner, result, plan=build_query_plan(QUERY)) is None
    asyncio.run(run())


@pytest.mark.parametrize("changed_again", [False, True])
def test_slow_verification_carries_only_matching_proofs_to_fresh_frame(tmp_path, changed_again):
    async def run():
        owner, _, _, calls = await resident_owner(tmp_path)
        old = frame(changed_pixels())
        newer = changed_pixels()
        if changed_again:
            with Image.open(BytesIO(newer)) as opened:
                image = opened.convert("RGB")
            ImageDraw.Draw(image).rectangle((310, 108, 320, 115), fill="white")
            out = BytesIO()
            image.save(out, format="PNG")
            newer = out.getvalue()
        fetches = []
        def fetch(seq):
            fetches.append(seq)
            return old if len(fetches) == 1 else frame(newer)
        def verify(data, proofs, **kw):
            calls.append(proofs)
            old.timestamp -= 3  # Deterministic elapsed processing, not wall-clock sleep.
            return {proof_key(p) for p in proofs}
        owner._assistant_client = SimpleNamespace(fetch_frame=fetch)
        owner._vision.verify_text_regions = verify
        selected = await select(owner, "navigation")
        assert selected is not None and len(fetches) == 2 and len(calls) == 1
        facts = selected["screen_facts"]["facts"]
        assert any(f["kind"] == "current_location" for f in facts)
        assert any(f["kind"] == "quest_objective" for f in facts) is not changed_again
    asyncio.run(run())


def test_resident_watcher_challenges_page_pixels_without_destroying_candidate(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        original = copy.deepcopy(owner._committed_observation)
        epoch = getattr(owner, "_observation_epoch", 0)
        payload = changed_pixels(control=True)
        assert await live_refresh.watch_latest_frame(owner, payload, helper=True)
        assert owner._committed_observation == original
        assert getattr(owner, "_observation_epoch", 0) == epoch
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        result = await owner.hsr_companion_prepare(query=QUERY)
        assert result["query_answer"]["state"] == "ready", result["query_timing"]
        assert result["delivery_receipt"]["payload_serial"] == owner._observation_serial
    asyncio.run(run())


def test_confirmed_new_page_still_revokes_old_checked_packet(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        selected = await select(owner, "full")
        from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import raw_page
        preview = raw_page("warp")
        preview["scene"].update(primary_state="menu", verified=True, confidence="high", label="跃迁", evidence=["跃迁页面标题"])
        await owner._publish_scene_preview(preview)
        assert not ObservationSnapshot.select(owner, selected, fields={"location", "quest"}).matches(owner)
        assert await live_refresh.validated_delivery(owner, selected, plan=build_query_plan(QUERY)) is None
    asyncio.run(run())


def test_retained_partial_proof_can_deliver_without_mutating_new_commit(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        await commit_raw(owner, navigation_raw(objective=False))
        newer_commit = copy.deepcopy(owner._committed_observation)
        payload = changed_pixels(objective=True)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        # The old complete candidate retains independent location proof; only
        # its old objective is disproved on the new frame.
        owner._vision.verify_text_regions = lambda data, proofs, **kw: set()
        from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
        from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp
        keys = {p[0] for p in region_stamp(payload, [navigation_raw()["screen_facts"]["facts"][-1]["evidence"][0]])}
        ObservationStore(owner).defer_regions(keys)
        deferred = copy.deepcopy(owner._deferred_verification)
        selected = await select(owner, "navigation")
        assert selected is not None
        assert any(f["kind"] == "current_location" for f in selected["screen_facts"]["facts"])
        assert not any(f["kind"] == "quest_objective" for f in selected["screen_facts"]["facts"])
        assert owner._committed_observation == newer_commit
        assert owner._deferred_verification == deferred  # Detached review never blesses new state globally.
        assert ObservationSnapshot.select(owner, selected, fields={"location", "quest"}).matches(owner)
        ObservationStore(owner).defer_regions({tuple(selected["screen_facts"]["facts"][0]["evidence"][0]["region"][k]
            for k in ("left", "top", "right", "bottom"))})
        assert not ObservationSnapshot.select(owner, selected, fields={"location", "quest"}).matches(owner)
    asyncio.run(run())


def test_new_capture_failure_revokes_checked_receipt(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        selected = await select(owner, "full")
        from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
        ObservationStore(owner).suspend("capture_unavailable_after_review")
        assert not ObservationSnapshot.select(owner, selected, fields={"location", "quest"}).matches(owner)
        assert await live_refresh.validated_delivery(owner, selected, plan=build_query_plan(QUERY)) is None
    asyncio.run(run())


@pytest.mark.parametrize("revoke", [None, "new_question", "challenge", "capture_failure", "stale_proof"])
def test_new_producer_proof_is_consumed_once_without_another_capture(tmp_path, revoke):
    async def run():
        from plugin.plugins.hsr_companion.runtime.observation_store import ObservationStore
        from plugin.plugins.hsr_companion.runtime.review_policy import producer_receipt
        from plugin.plugins.hsr_companion.tests.test_dynamic_handoff_20261017 import dynamic_owner, produce
        owner, _, _, _ = dynamic_owner(tmp_path)
        # Start through the real question coordinator, not injected success
        # flags. Only production completed for an actual question can shortcut.
        query = asyncio.create_task(owner.hsr_companion_prepare(query="现在是什么页面？都有谁？"))
        await asyncio.sleep(.03)
        await produce(owner)
        await produce(owner)
        await query
        value = await owner._current_visual_evidence()
        plan = build_query_plan("现在是什么页面？都有谁？")
        assert owner._producer_validation["checked_frame_at"]
        def unexpected(*args):
            raise AssertionError("producer proof must not start another capture")
        owner._assistant_client.fetch_frame = unexpected
        if revoke == "new_question":
            owner._answer_query_generation = getattr(owner, "_answer_query_generation", 0) + 1
        elif revoke == "challenge":
            region = owner._accepted_frame_page_regions[0]["region"]
            ObservationStore(owner).defer_regions({tuple(region[k] for k in ("left", "top", "right", "bottom"))})
        elif revoke == "capture_failure":
            ObservationStore(owner).suspend("capture_error_after_commit")
        elif revoke == "stale_proof":
            owner._producer_validation["checked_frame_at"] -= 3
        if revoke:
            assert producer_receipt(owner, value, plan=plan) is None
        else:
            selected = await live_refresh.validated_delivery(owner, value, plan=plan)
            assert selected and selected["reuse"]["producer_proof"]
            assert [c["name"] for c in selected["characters"]] == ["银狼"]
    asyncio.run(run())


def test_live_commit_without_native_guards_cannot_use_preview_compatibility(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        for key in ("_accepted_frame_delivery_guard", "_accepted_frame_full_query_guard", "_accepted_frame_query_guard"):
            setattr(owner, key, None)
        assert await live_refresh.validated_delivery(owner, await owner._current_visual_evidence(), plan=build_query_plan(QUERY)) is None
    asyncio.run(run())
