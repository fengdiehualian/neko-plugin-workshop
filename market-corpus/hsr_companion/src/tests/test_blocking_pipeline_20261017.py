"""No relaxed deadlines/guards: eliminate repeated work on the same evidence."""
from collections import Counter
import asyncio

import pytest

from plugin.plugins.hsr_companion.runtime import live_refresh, query_reuse
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import TEXT, picture, text_result


def test_identical_nonempty_patch_does_not_scan_every_pixel(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Identical nonempty bytes already prove pixel equality")
    monkeypatch.setattr(query_reuse, "text_pixel_differences", forbidden)
    patch = ((.1, .2, .8, .3), (512, 64), bytes(32768))
    assert not query_reuse.regions_changed((patch,), (patch,))


@pytest.mark.parametrize("other", [
    ((.2, .2, .8, .3), (2, 2), bytes(4)),
    ((.1, .2, .8, .3), (4, 1), bytes(4)),
    ((.1, .2, .8, .3), (2, 2), b"\xff" * 4),
])
def test_equality_shortcut_does_not_ignore_geometry_or_glyph_changes(other):
    patch = ((.1, .2, .8, .3), (2, 2), bytes(4))
    assert query_reuse.regions_changed((patch,), (other,))


def test_empty_patch_is_not_proof_of_equality():
    patch = ((.1, .2, .8, .3), (0, 0), b"")
    assert query_reuse.regions_changed((patch,), (patch,))


def test_exact_pixel_fast_path_is_equivalent_to_original_heuristic():
    import random
    rng = random.Random(20261017)
    def original(before, after):
        diffs = query_reuse.text_pixel_differences(before, after)
        return (not diffs or sum(diffs) / len(diffs) / 255 > .015
                or sum(d > 24 for d in diffs) / len(diffs) > .02
                or sum(d > 64 for d in diffs) >= 3 or sum(d > 24 for d in diffs) >= 8)
    for index in range(200):
        before = bytes(rng.randrange(256) for _ in range(64))
        after = before if index % 2 else bytes(rng.randrange(256) for _ in range(64))
        a, b = ((0., 0., 1., 1.), (8, 8), before), ((0., 0., 1., 1.), (8, 8), after)
        assert query_reuse.regions_changed((a,), (b,)) == bool(original(before, after))


def test_each_literal_rectangle_compared_once_per_validation(monkeypatch):
    counts = Counter()
    original = live_refresh.regions_changed
    def counted(before, after):
        counts[before[0][0]] += 1
        return original(before, after)
    monkeypatch.setattr(live_refresh, "regions_changed", counted)
    raw = text_result()
    raw["screen_facts"] = {"facts": [
        {"scope": "equipment", "kind": "sub_stat", "label": "速度", "value": "2", "evidence": [{**TEXT, "text": "速度2"}]}
        for _ in range(4)], "visible_text": [{**TEXT, "text": "速度2"}]}
    result = live_refresh.stable_screen_content(raw, picture(), picture())
    assert len(result["screen_facts"]["facts"]) == 4
    assert counts and max(counts.values()) == 1, counts


def test_ui_only_comparison_does_not_compute_ignored_world_difference(monkeypatch):
    class Surface:
        size = (320, 200)
        def tobytes(self):
            raise AssertionError("World pixels are not consulted in UI-only mode without controls")
    assert not live_refresh.frame_changed(((640, 400), Surface()), ((640, 400), Surface()), ui_only=True)
    assert live_refresh.frame_changed(((640, 400), Surface()), ((800, 600), Surface()), ui_only=True)


def test_slow_newly_committed_candidate_enters_cache_before_live_review(tmp_path):
    """Recognition age cannot silently prevent first insertion into live cache."""
    from datetime import datetime, timedelta, timezone
    from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, commit_raw, navigation_raw
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    async def run():
        owner, _, _ = audit_owner(tmp_path)
        raw = navigation_raw()
        await commit_raw(owner, raw)
        owner._reusable_observations = {}
        old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        await owner._apply_local_analysis(raw, observed_at=old, image_fingerprint="slow-read",
            source="assistant_local_ocr", deliver_event=False)
        assert owner._reusable_observations.get("navigation") is not None
        result = await query_reuse.try_reuse(owner, navigation=True,
            plan=build_query_plan("现在在哪，接下来任务做什么"))
        assert result is not None and result["observed_at"] == old
        assert result["reuse"]["validated_at"] != old  # Actual fresh capture proof.
        owner._assistant_client.fetch_frame = lambda seq: None
        assert await query_reuse.try_reuse(owner, navigation=True) is None
    asyncio.run(run())


def test_new_roster_and_partial_navigation_do_not_erase_richer_guarded_navigation(tmp_path):
    from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, commit_raw, navigation_raw
    from plugin.plugins.hsr_companion.tests.test_current_observation import ROLE
    from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    from plugin.plugins.hsr_companion.runtime.query_coordinator import satisfied_fields
    async def run():
        owner, _, _ = audit_owner(tmp_path)
        await commit_raw(owner, navigation_raw())
        incoming = navigation_raw()
        incoming["characters"] = [{**ROLE, **CONTROL, "identity_scope": "team_selection"}]
        incoming["screen_facts"]["facts"] = incoming["screen_facts"]["facts"][1:2]
        await commit_raw(owner, incoming)
        plan = build_query_plan("现在在哪，接下来任务做什么")
        selected = await query_reuse.try_reuse(owner, navigation=True, plan=plan)
        assert selected is not None and plan.fields <= satisfied_fields(plan, selected)
        # Detached candidate only: current projection remains this partial read.
        assert len(owner._committed_observation["screen_facts"]["facts"]) == 1
    asyncio.run(run())


@pytest.mark.parametrize("scope", ["navigation", "equipment", "characters"])
def test_waiting_question_retries_same_revision_after_resident_review(tmp_path, monkeypatch, scope):
    """Producer retains richer commit; watcher recovers it, no new serial/event."""
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    from plugin.plugins.hsr_companion.tests.test_review_policy_20261017 import resident_owner, QUERY
    from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
    async def run():
        if scope == "navigation":
            owner, _, _, _ = await resident_owner(tmp_path)
            plan = build_query_plan(QUERY)
        else:
            from plugin.plugins.hsr_companion.tests.test_chain_audit_20261017 import audit_owner, commit_raw, raw_page
            from plugin.plugins.hsr_companion.tests.test_current_observation import ROLE
            from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL
            owner, _, _ = audit_owner(tmp_path)
            raw = raw_page("team_setup" if scope == "characters" else "relic_detail")
            if scope == "characters":
                raw["characters"] = [{**ROLE, **CONTROL, "identity_scope": "team_selection"}]
                raw["screen_facts"]["facts"] = []
            await commit_raw(owner, raw)
            await commit_raw(owner, raw)
            plan = build_query_plan("现在是什么页面？都有谁？" if scope == "characters" else "这件遗器主副词条是什么？")
        owner._observer_task = asyncio.create_task(asyncio.Event().wait())
        original_serial = owner._observation_serial
        available = asyncio.Event()
        async def once_busy(target, **kwargs):
            if not available.is_set():
                live_refresh.trace_refresh(target, "delivery_deferred", reason="busy")
                return None
            return await try_reuse(target, **kwargs)
        monkeypatch.setattr(live_refresh, "try_reuse", once_busy)
        async def recovered():
            await asyncio.sleep(.05)
            available.set()  # A same-revision review is not a new OCR commit.
        task = asyncio.create_task(recovered())
        try:
            answer = await live_refresh.refresh_for_query(owner, timeout=1.5, query_plan=plan, **plan.refresh_args())
            assert answer is not None, owner._last_refresh_diagnostics
            assert owner._observation_serial == original_serial
            from plugin.plugins.hsr_companion.runtime.query_coordinator import satisfied_fields
            assert plan.fields <= satisfied_fields(plan, answer)
        finally:
            owner._observer_task.cancel()
            await asyncio.gather(owner._observer_task, task, return_exceptions=True)
    asyncio.run(run())
