"""A review runs in the producer thread between native calls, never alongside."""
import threading
import asyncio
import time
import pytest


def test_review_runs_at_checkpoint_in_producer_thread_without_restarting():
    from plugin.plugins.hsr_companion.adapters.ocr_work_queue import OCRWorkQueue
    queue = OCRWorkQueue()
    entered, review_queued, reviewed, release = (threading.Event() for _ in range(4))
    threads, order = [], []
    def producer():
        queue.begin()
        try:
            order.append("native_one")
            entered.set()
            assert review_queued.wait(2)
            queue.pump()
            order.append("native_two")
            reviewed.set()
            assert release.wait(2)
        finally:
            queue.end()
    thread = threading.Thread(target=producer)
    thread.start()
    try:
        assert entered.wait(2)
        def review():
            threads.append(threading.get_ident())
            order.append("review")
            return "same evidence"
        future = queue.submit(review)
        assert future is not None
        assert queue.submit(lambda: None) is None  # Bounded to one outstanding job.
        review_queued.set()
        assert future.result(2) == "same evidence"
        assert reviewed.wait(2)
        assert threads == [thread.ident]
        assert order == ["native_one", "review", "native_two"]
    finally:
        review_queued.set()
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert queue.submit(lambda: None) is None


def test_cancelled_or_unstarted_review_never_runs_after_producer_exit():
    from plugin.plugins.hsr_companion.adapters.ocr_work_queue import OCRWorkQueue
    queue = OCRWorkQueue()
    called = []
    queue.begin()
    first = queue.submit(lambda: called.append(1))
    assert first.cancel()
    queue.pump()
    second = queue.submit(lambda: called.append(2))
    queue.end()
    assert second.cancelled() and called == []


def test_failed_review_does_not_abort_or_unlock_the_producer():
    from plugin.plugins.hsr_companion.adapters.ocr_work_queue import OCRWorkQueue
    queue = OCRWorkQueue()
    queue.begin()
    def fail():
        raise ValueError("read failed")
    future = queue.submit(fail)
    queue.pump()
    assert isinstance(future.exception(), ValueError)
    next_job = queue.submit(lambda: 42)
    queue.pump()
    assert next_job.result() == 42
    queue.end()


def test_formal_cache_review_finishes_while_resident_full_read_is_still_running(tmp_path):
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter, serial_ocr
    from plugin.plugins.hsr_companion.adapters.text_reader import check_current
    from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
    from plugin.plugins.hsr_companion.runtime import live_refresh
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
    from plugin.plugins.hsr_companion.tests.test_review_policy_20261017 import resident_owner, changed_pixels, QUERY
    from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import picture, frame, text_result
    from types import SimpleNamespace
    entered, release = threading.Event(), threading.Event()
    threads, calls = [], []
    class Vision(LocalVisionAdapter):
        @serial_ocr(cooperative=True)
        def analyze(self, payload, *, cancelled=None, **kwargs):
            threads.append(threading.get_ident())
            calls.append("analysis_started")
            entered.set()
            while not release.wait(.005):
                check_current(cancelled)  # Native-call boundary; never parallel inference.
            return text_result()
        @serial_ocr
        def verify_text_regions(self, payload, proofs, **kwargs):
            threads.append(threading.get_ident())
            calls.append("review")
            return {proof_key(p) for p in proofs}
    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._vision = Vision(None)
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(changed_pixels(control=True)))
        producer = asyncio.create_task(live_refresh.guarded_analysis(owner, picture(), helper=True))
        assert await asyncio.to_thread(entered.wait, 1)
        try:
            selected = await asyncio.wait_for(try_reuse(owner, navigation=True, plan=build_query_plan(QUERY)), 1.5)
            assert selected is not None
            assert not producer.done()  # No wait for all tiles / no cancellation/restart.
            assert calls == ["analysis_started", "review"]
            assert len(set(threads)) == 1
        finally:
            release.set()
            await producer
            await live_refresh.drain_analysis_worker(owner)
    asyncio.run(run())


@pytest.mark.parametrize("actual_text_changed", [False, True])
def test_checkpoint_review_selects_changed_fields_from_execution_frame(tmp_path, actual_text_changed):
    """Queue-time changes are not the changes in a later checkpoint frame.

    The OCR stub proves unchanged TEXT despite different pixels; it does not
    authorize an unselected literal. Reproduces confirmed -> deferred at the
    handoff when a second region changes while the review waits.
    """
    from plugin.plugins.hsr_companion.adapters.local_vision import LocalVisionAdapter, serial_ocr
    from plugin.plugins.hsr_companion.adapters.text_reader import check_current
    from plugin.plugins.hsr_companion.adapters.text_verification import proof_key
    from plugin.plugins.hsr_companion.runtime import live_refresh
    from plugin.plugins.hsr_companion.runtime.query_plan import build_query_plan
    from plugin.plugins.hsr_companion.runtime.query_reuse import try_reuse
    from plugin.plugins.hsr_companion.tests.test_review_policy_20261017 import resident_owner, changed_pixels, QUERY
    from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import picture, frame, text_result
    from types import SimpleNamespace
    entered, release = threading.Event(), threading.Event()
    reviewed = []

    class Vision(LocalVisionAdapter):
        @serial_ocr(cooperative=True)
        def analyze(self, payload, *, cancelled=None, **kwargs):
            entered.set()
            while not release.wait(.005):
                check_current(cancelled)
            return text_result()

        @serial_ocr
        def verify_text_regions(self, payload, proofs, **kwargs):
            reviewed.extend(proofs)
            return {proof_key(p) for p in proofs if not actual_text_changed or not p.get("text", "").endswith("目标甲")}

    async def run():
        owner, _, _, _ = await resident_owner(tmp_path)
        owner._vision = Vision(None)
        frames = iter([changed_pixels(control=True)])
        owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq:
            frame(next(frames, changed_pixels(objective=True))))
        producer = asyncio.create_task(live_refresh.guarded_analysis(owner, picture(), helper=True))
        assert await asyncio.to_thread(entered.wait, 1)
        try:
            result = await asyncio.wait_for(try_reuse(owner, navigation=True, plan=build_query_plan(QUERY)), 1.5)
            assert result is not None
            values = {f["value"] for f in result["screen_facts"]["facts"]}
            if actual_text_changed:
                assert "目标甲" not in values and "地点甲" in values
            else:
                assert values == {"地点甲", "任务甲", "目标甲"}
            assert any(p.get("text", "").endswith("目标甲") for p in reviewed)
            assert not producer.done()
        finally:
            release.set()
            await producer
            await live_refresh.drain_analysis_worker(owner)
    asyncio.run(run())
