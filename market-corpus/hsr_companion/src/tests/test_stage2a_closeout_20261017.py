"""Long-lived facts remain candidates; only current scoped proof permits answers."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone

import pytest
from plugin.plugins.hsr_companion.runtime import live_refresh
from plugin.plugins.hsr_companion.runtime.equipment import equipment_card
from plugin.plugins.hsr_companion.runtime.observation_store import ObservationSnapshot, ObservationStore
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, commit_raw, raw_page
from plugin.plugins.hsr_companion.tests.test_review_policy_20261017 import QUERY, resident_owner, select


def age_candidates(owner, seconds):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    owner._committed_observation["observed_at"] = stamp
    for snapshot in owner._reusable_observations.values():
        snapshot["evidence"]["observed_at"] = stamp
    return stamp


@pytest.mark.parametrize("path", ["navigation", "full", "direct"])
@pytest.mark.parametrize("age", [39, 90, 600])
def test_old_live_candidate_is_checked_before_deciding_it_is_unusable(tmp_path, path, age):
    async def run():
        owner, _, _, calls = await resident_owner(tmp_path)
        original = age_candidates(owner, age)
        selected = await select(owner, path)
        assert selected is not None
        assert len(selected["screen_facts"]["facts"]) == 3
        assert selected["observed_at"] == original  # No fake OCR/capture timestamp.
        assert selected["reuse"]["validated_at"] != original
        assert not calls  # Same native patches, no full OCR and no text reread.
        current = await owner._current_visual_evidence()
        assert current["fresh"] and len(current["screen_facts"]["facts"]) == 3
        assert current["observed_at"] == original
    asyncio.run(run())


def test_scoped_check_does_not_renew_unchecked_quest(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        age_candidates(owner, 90)
        from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
        selected = await try_reuse(owner, navigation=True, plan=build_query_plan("现在在哪"))
        assert selected is not None
        current = await owner._current_visual_evidence()
        assert any(f["kind"] == "current_location" for f in current["screen_facts"]["facts"])
        assert not any(f["kind"] == "quest_objective" for f in current["screen_facts"]["facts"])
    asyncio.run(run())


def test_query_timeout_is_not_a_fact_lifecycle_boundary(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        before = copy.deepcopy(owner._committed_observation)
        owner._assistant_client.fetch_frame = lambda seq: None
        assert await live_refresh.refresh_for_query(owner, timeout=.02, require_characters=False,
            require_navigation=True, query_plan=build_query_plan(QUERY)) is None
        assert owner._committed_observation == before
        assert owner._reusable_observations and not getattr(owner, "_observation_invalidated", False)
    asyncio.run(run())


def test_checked_equipment_does_not_authorize_different_item_in_same_page(tmp_path):
    async def run():
        owner, _, _ = audit_owner(tmp_path)
        await commit_raw(owner, raw_page())
        plan = build_query_plan("这件遗器的主副词条是什么")
        old = await live_refresh.validated_delivery(owner, await owner._current_visual_evidence(), plan=plan)
        assert old and equipment_card(old)["complete"]
        new_raw = raw_page(subs=(("攻击力", "11.1%"), ("效果抵抗", "8.6%"), ("速度", "8"), ("生命值", "120")))
        new_raw["screen_facts"]["facts"][1].update(label="主词条·防御力", value="54.0%")
        new_raw["screen_facts"]["facts"][1]["evidence"][0]["text"] = "防御力54.0%"
        await commit_raw(owner, new_raw)
        assert not ObservationSnapshot.select(owner, old, fields=plan.fields).matches(owner)
        current = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么")
        card = current["equipment_answer"]
        assert card["complete"] and card["main_stat"]["value"] == "54.0%"
        assert "12.3%" not in card["exact_text"]
    asyncio.run(run())


def test_old_candidate_needs_fresh_capture_even_when_values_are_preserved(tmp_path):
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        age_candidates(owner, 600)
        before = copy.deepcopy(owner._committed_observation)
        owner._assistant_client.fetch_frame = lambda seq: None
        assert await select(owner, "navigation") is None
        assert owner._committed_observation == before
        assert not (await owner._current_visual_evidence())["fresh"]
        ObservationStore(owner).suspend("capture_error")
        assert owner._reusable_observations
    asyncio.run(run())


def test_resident_watcher_renews_only_unchanged_regions_on_real_fresh_frame(tmp_path):
    async def run():
        import time

        from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import picture
        owner, _, _, _ = await resident_owner(tmp_path)
        original = age_candidates(owner, 90)
        owner._watch_frame_timestamp = time.time()
        assert not await live_refresh.watch_latest_frame(owner, picture(), helper=True)
        current = await owner._current_visual_evidence()
        assert current["fresh"] and len(current["screen_facts"]["facts"]) == 3
        assert current["observed_at"] == original and current["last_verified_at"]
    asyncio.run(run())


def test_resident_dynamic_control_uses_literal_review_before_global_withholding(tmp_path):
    async def run():
        import time
        from types import SimpleNamespace

        from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame
        from plugin.plugins.hsr_companion.tests.test_review_policy_20261017 import changed_pixels
        owner, _, _, calls = await resident_owner(tmp_path)
        payload = changed_pixels(control=True)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        owner._watch_frame_timestamp = time.time()
        before = copy.deepcopy(owner._committed_observation)
        assert not await live_refresh.watch_latest_frame(owner, payload, helper=True)
        assert calls and owner._committed_observation == before
        assert (await owner._current_visual_evidence())["fresh"]
    asyncio.run(run())


@pytest.mark.parametrize("missing", ["all", "some"])
def test_weak_same_page_update_keeps_one_complete_candidate_with_original_guards(tmp_path, missing):
    async def run():
        owner, _, _ = audit_owner(tmp_path)
        await commit_raw(owner, raw_page())
        old = copy.deepcopy(owner._committed_observation)
        guard = copy.deepcopy(owner._accepted_frame_delivery_guard)
        weaker = raw_page()
        weaker["screen_facts"]["facts"] = [] if missing == "all" else weaker["screen_facts"]["facts"][:2]
        await commit_raw(owner, weaker)
        assert owner._committed_observation == old
        assert owner._accepted_frame_delivery_guard == guard
        selected = await owner.hsr_companion_prepare(query="这件遗器的主副词条是什么")
        assert selected["equipment_answer"]["complete"]
    asyncio.run(run())


def test_missing_same_page_read_cannot_hide_a_real_changed_value(tmp_path):
    async def run():
        owner, _, _ = audit_owner(tmp_path)
        await commit_raw(owner, raw_page())
        changed = raw_page(subs=())
        changed["screen_facts"]["facts"][1].update(label="主词条·防御力", value="54.0%")
        changed["screen_facts"]["facts"][1]["evidence"][0]["text"] = "防御力54.0%"
        await commit_raw(owner, changed)
        current = equipment_card(await owner._current_visual_evidence())
        assert current["main_stat"]["value"] == "54.0%" and not current["sub_stats"]
    asyncio.run(run())


def test_background_weak_literal_review_preserves_candidate_for_recovery(tmp_path):
    async def run():
        from types import SimpleNamespace

        from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
        from plugin.plugins.hsr_companion.runtime.review_policy import review_current
        from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame
        from plugin.plugins.hsr_companion.tests.test_review_policy_20261017 import changed_pixels
        owner, _, _, _ = await resident_owner(tmp_path)
        old = copy.deepcopy(owner._committed_observation)
        payload = changed_pixels(objective=True)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
        owner._vision.verify_text_regions = lambda data, proofs, **kw: set()
        assert await review_current(owner, allow_partial=False) is None
        assert owner._committed_observation == old and owner._reusable_observations
        current = await owner._current_visual_evidence()
        assert not any(f["kind"] == "quest_objective" for f in current["screen_facts"]["facts"])
        owner._vision.verify_text_regions = lambda data, proofs, **kw: {proof_key(p) for p in proofs}
        recovered = await select(owner, "navigation")
        assert recovered and len(recovered["screen_facts"]["facts"]) == 3
        assert owner._committed_observation == old
    asyncio.run(run())


@pytest.mark.parametrize("superseded_review", [False, True])
def test_cache_selection_consumes_new_producer_receipt_before_another_capture(tmp_path, monkeypatch, superseded_review):
    async def run():
        from plugin.plugins.hsr_companion.runtime import query_reuse
        from plugin.plugins.hsr_companion.tests.test_dynamic_handoff_20261017 import dynamic_owner, produce

        owner, _, _, _ = dynamic_owner(tmp_path)
        query = asyncio.create_task(owner.hsr_companion_prepare(query="现在是什么页面？都有谁？"))
        await asyncio.sleep(.03)
        await produce(owner)
        await produce(owner)
        await query
        proof = owner._producer_validation
        assert proof and proof["checked_frame_at"]
        def forbidden(*args):
            raise AssertionError("new producer proof must precede any redundant frame transfer")
        owner._assistant_client.fetch_frame = forbidden
        if superseded_review:
            owner._producer_validation = None
            async def superseded(*args, **kwargs):
                owner._producer_validation = proof
                return query_reuse._SUPERSEDED
            monkeypatch.setattr(query_reuse, "_try_reuse_once", superseded)
        selected = await query_reuse.try_reuse(owner, navigation=False, full=True,
            plan=build_query_plan("现在是什么页面？都有谁？"))
        assert selected and selected["reuse"]["producer_proof"]
        assert [a["name"] for a in selected["characters"]] == ["银狼"]
    asyncio.run(run())
