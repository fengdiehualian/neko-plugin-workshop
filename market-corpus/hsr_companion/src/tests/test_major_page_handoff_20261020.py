"""Cross-page handoff regressions; synthetic OCR, not live game acceptance.

Keep the original two-second review slice and test the ownership of unfinished
work. No installed observer, helper, host messages or player data are touched.
"""
import asyncio
import copy
import threading
import time
from types import SimpleNamespace

import pytest
from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter, serial_ocr
from plugin.plugins.hsr_companion.adapters.text_reader import check_current
from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
from plugin.plugins.hsr_companion.runtime import live_refresh
from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import CONTROL, frame, picture
from plugin.plugins.hsr_companion.tests.test_review_policy_20261017 import (
    QUERY,
    changed_pixels,
    resident_owner,
)


def test_review_longer_than_one_slice_is_received_without_restarting_it(tmp_path):
    """An unfinished, still-owned read is not a wrong value or a cancelled question."""
    calls, stopped = [], []

    def verify(payload, proofs, *, cancelled=None):
        calls.append(1)
        time.sleep(2.08)  # Longer than the unchanged production slice, below the query budget.
        stopped.append(bool(cancelled and cancelled()))
        check_current(cancelled)
        return {proof_key(p) for p in proofs}

    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._answer_query_generation = 1
        owner._vision.verify_text_regions = verify
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed_pixels()))
        plan = build_query_plan(QUERY)
        first = await try_reuse(owner, navigation=True, plan=plan)
        # Independent task fields may already be returned as a PARTIAL answer.
        # Only the changed location needs the unfinished native review.
        assert first is None or not any(f['kind'] == 'current_location' for f in first['screen_facts']['facts'])
        await asyncio.sleep(.15)
        second = await try_reuse(owner, navigation=True, plan=plan)
        assert second is not None, "The completed first review was abandoned instead of consumed"
        assert len(calls) == 1, "A completed review must not restart the same native work"
        assert stopped == [False]
        assert len(second['screen_facts']['facts']) == 3

    asyncio.run(run())


@pytest.mark.parametrize('transition', ['epoch', 'query', 'stopped'])
def test_unfinished_review_cannot_authorize_a_new_page_or_question(tmp_path, transition):
    release = threading.Event()

    def verify(payload, proofs, *, cancelled=None):
        release.wait(3)
        check_current(cancelled)
        return {proof_key(p) for p in proofs}

    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._answer_query_generation = 1
        owner._vision.verify_text_regions = verify
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed_pixels()))
        plan = build_query_plan(QUERY)
        first = await try_reuse(owner, navigation=True, plan=plan)
        assert first is None or not any(f['kind'] == 'current_location' for f in first['screen_facts']['facts'])
        if transition == 'epoch':
            owner._observation_epoch = getattr(owner, '_observation_epoch', 0) + 1
        elif transition == 'query':
            owner._answer_query_generation += 1
        else:
            owner._observer_stop_requested = True
        release.set()
        await asyncio.sleep(.05)
        worker = getattr(owner, '_content_revalidation_worker', None)
        if worker is not None:
            try:
                await worker
            except Exception:
                pass
        # A changed-page/new-question caller must perform its own fresh review.
        owner._vision.verify_text_regions = lambda *a, **kw: set()
        selected = await try_reuse(owner, navigation=True, plan=plan)
        if selected:
            assert not any(f['kind'] == 'current_location' for f in selected['screen_facts']['facts'])

    try:
        asyncio.run(run())
    finally:
        release.set()


@pytest.mark.parametrize('next_page', ['character_detail', 'challenge', 'exploration_hud'])
def test_new_question_checks_live_page_before_finishing_old_full_pass(tmp_path, next_page):
    """A native page guard must not make a pre-question producer blind to real switching."""
    entered, release = threading.Event(), threading.Event()
    calls = []
    changed = picture('page')

    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        original = copy.deepcopy(owner._committed_observation)
        epoch = getattr(owner, '_observation_epoch', 0)

        class Vision(LocalVisionAdapter):
            @serial_ocr(cooperative=True)
            def analyze(self, payload, *, cancelled=None, on_scene=None):
                on_scene(original)
                calls.append('old_full_started')
                entered.set()
                while not release.wait(.005):
                    check_current(cancelled)
                calls.append('old_full_completed')
                return copy.deepcopy(original)

            @serial_ocr
            def probe_scene(self, payload, *, cancelled=None):
                check_current(cancelled)
                calls.append('page_checkpoint')
                return {'ok': True, 'page': {'page_type': next_page, 'label': next_page},
                        'scene': {'primary_state': 'exploration' if next_page == 'exploration_hud' else 'menu',
                                  'verified': True, 'confidence': 'high', 'evidence': ['synthetic native page proof']},
                        'recognition': {'scene_regions': [{**CONTROL, 'text': next_page, 'control': True}]},
                        'characters': [], 'screen_facts': {'facts': [], 'visible_text': []}}

            @serial_ocr
            def verify_text_regions(self, payload, proofs, **kwargs):
                return {proof_key(p) for p in proofs}

        owner._vision = Vision(None)
        active = {'changed': False}
        owner._assistant_client = SimpleNamespace(
            health=lambda: {'connected': True, 'frame_seq': 2 if active['changed'] else 1,
                            'game': {'running': True, 'pid': 1},
                            'capture': {'state': 'capturing', 'isolated_window': True}},
            fetch_frame=lambda seq: frame(changed if active['changed'] else picture()))
        producer = asyncio.create_task(live_refresh.guarded_analysis(owner, picture(), helper=True))
        assert await asyncio.to_thread(entered.wait, 1)
        active['changed'] = True
        owner._visual_query_waiters = 1
        owner._query_refresh_deadline = time.monotonic() + 2
        owner._query_refresh_generation = 1  # A real new query arrives while the old pass is resident.
        try:
            until = time.monotonic() + 1.4
            while not producer.done() and 'page_checkpoint' not in calls and time.monotonic() < until:
                await asyncio.sleep(.01)
            assert 'page_checkpoint' in calls, 'New question waited behind the entire old-page read'
            if next_page != 'exploration_hud':
                for _ in range(80):
                    if producer.done():
                        break
                    await asyncio.sleep(.01)
                assert producer.done(), 'Confirmed page transition did not retire the old producer'
                assert await producer is None
                assert owner._scene_preview['page']['page_type'] == next_page
                assert owner._observation_epoch == epoch + 1
                assert 'old_full_completed' not in calls
            else:
                assert not producer.done()  # Background/camera changes do not retire an unchanged page.
                assert getattr(owner, '_observation_epoch', 0) == epoch
        finally:
            release.set()
            await producer
            await live_refresh.drain_analysis_worker(owner)

    asyncio.run(run())
