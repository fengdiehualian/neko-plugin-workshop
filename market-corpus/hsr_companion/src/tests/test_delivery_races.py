"""Synthetic resident-observer races, not a replay of the user's screenshots."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime import live_refresh
from plugin.plugins.hsr_companion.runtime.query_reuse import region_stamp, try_reuse
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL, TEXT, frame, picture, text_result

OTHER = {"region": {"left": .05, "top": .5, "right": .9, "bottom": .6}}


async def commit(owner, payload, value="10", *, complete=False, page_control=None):
    data = text_result()
    data["page"] = {"page_type": "relic_detail", "label": "遗器详情"}
    data["screen_facts"] = {"facts": [
        {"kind": "main_stat", "scope": "equipment", "label": "主词条·防御力", "value": value,
         "evidence": [{**TEXT, "text": value}]},
        {"kind": "sub_stat", "scope": "equipment", "label": "副词条·速度", "value": "2",
         "evidence": [{**OTHER, "text": "2"}]},
    ], "visible_text": []}
    page_control = page_control or CONTROL
    regions = [page_control, TEXT, OTHER]
    if complete:
        # Opt-in complete row set for tests whose subject is a READY handoff.
        # Keep the original partial fixture for withdrawal/partial contracts.
        for index, (label, number) in enumerate((("暴击率", "3%"), ("生命值", "80"), ("效果抵抗", "5%"))):
            proof = {"region": {"left": .05, "right": .9, "top": .62 + index * .1, "bottom": .68 + index * .1},
                     "text": label + number}
            data["screen_facts"]["facts"].append({"kind": "sub_stat", "scope": "equipment",
                "label": "副词条·" + label, "value": number, "evidence": [proof]})
            regions.append(proof)
    owner._next_frame_stamp = live_refresh.frame_stamp(payload)
    owner._next_frame_page_regions = [page_control]
    owner._next_frame_scene_regions = regions
    owner._next_frame_query_guard = (regions, region_stamp(payload, regions))
    owner._next_frame_full_query_guard = owner._next_frame_query_guard
    owner._next_frame_delivery_guard = ([TEXT, OTHER], region_stamp(payload, [TEXT, OTHER]))
    owner._analysis_game_pid = 1
    await owner._apply_local_analysis(data, image_fingerprint="synthetic", source="assistant_local_ocr")


async def complete_commit(owner, payload, value="10", **kwargs):
    return await commit(owner, payload, value, complete=True, **kwargs)


def test_old_reuse_check_cannot_revoke_new_background_commit(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    changed = picture("text")
    fetches = []

    async def run():
        await commit(owner, picture())
        owner._assistant_client = SimpleNamespace()
        epoch = getattr(owner, "_observation_epoch", 0)

        async def fetch(*args, **kwargs):
            if not fetches:
                await commit(owner, changed, "20")  # New result arrives while the OLD check awaits.
            fetches.append(1)
            return frame(changed)

        monkeypatch.setattr(live_refresh, "fetch_recent_frame", fetch)
        selected = await try_reuse(owner, navigation=False, full=True)
        assert getattr(owner, "_observation_epoch", 0) == epoch
        assert (await owner._current_visual_evidence())["fresh"]
        assert selected is not None, getattr(owner, "_last_refresh_diagnostics", {})
        assert selected["screen_facts"]["facts"][0]["value"] == "20"

    asyncio.run(run())


def test_changed_literal_revokes_only_affected_fact_not_worker_epoch(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        epoch = getattr(owner, "_observation_epoch", 0)
        assert await try_reuse(owner, navigation=False, full=True) is None
        current = await owner._current_visual_evidence()
        assert current["fresh"] and current["page_type"] == "relic_detail"
        assert [fact["value"] for fact in current["screen_facts"]["facts"]] == ["2"]
        assert getattr(owner, "_observation_epoch", 0) == epoch

    asyncio.run(run())


def test_same_literal_recheck_allows_full_reuse_without_revoking(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    checks = []

    def verify(payload, proofs, **kwargs):
        checks.append(len(proofs))
        return {proof_key(p) for p in proofs}

    async def run():
        await commit(owner, picture())
        owner._vision = SimpleNamespace(verify_text_regions=verify)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
        before = copy.deepcopy((await owner._current_visual_evidence())["screen_facts"]["facts"])
        selected = await try_reuse(owner, navigation=False, full=True)
        assert selected is not None
        assert selected["screen_facts"]["facts"] == before
        assert checks

    asyncio.run(run())


def test_accepted_pixel_stamp_matches_validated_frame_not_original_capture(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._vision = SimpleNamespace(analyze=lambda payload: text_result(),
                                   verify_text_regions=lambda payload, proofs, **kwargs: {proof_key(p) for p in proofs})
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))

    async def run():
        assert await live_refresh.guarded_analysis(owner, picture(), helper=True) is not None
        assert owner._next_frame_stamp[1].tobytes() == live_refresh.frame_stamp(picture("text"))[1].tobytes()

    asyncio.run(run())


def test_changed_page_pixels_quarantine_answers_without_destroying_candidate(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        await commit(owner, picture())
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("page")))
        epoch = getattr(owner, "_observation_epoch", 0)
        assert await try_reuse(owner, navigation=False, full=True) is None
        assert getattr(owner, "_observation_epoch", 0) == epoch
        assert owner._committed_observation
        assert not (await owner._current_visual_evidence())["fresh"]

    asyncio.run(run())


@pytest.mark.parametrize("path", ["reuse", "delivery", "watch"])
def test_new_commit_during_pixel_extraction_is_not_revoked(tmp_path, monkeypatch, path):
    owner, _, _ = plugin_fixture(tmp_path)
    original = asyncio.to_thread
    touched = []

    async def run():
        await commit(owner, picture())
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("page")))
        epoch = getattr(owner, "_observation_epoch", 0)

        async def delayed(function, *args, **kwargs):
            result = await original(function, *args, **kwargs)
            if function is live_refresh.frame_stamp and not touched:
                touched.append(True)
                await commit(owner, picture("page"), "20")
            return result

        monkeypatch.setattr(asyncio, "to_thread", delayed)
        if path == "reuse":
            await try_reuse(owner, navigation=False, full=True)
        elif path == "delivery":
            assert not await live_refresh.validate_before_delivery(owner)
        else:
            assert not await live_refresh.watch_latest_frame(owner, picture("page"), helper=True)
        assert touched
        assert getattr(owner, "_observation_epoch", 0) == epoch
        assert (await owner._current_visual_evidence())["fresh"]

    asyncio.run(run())
