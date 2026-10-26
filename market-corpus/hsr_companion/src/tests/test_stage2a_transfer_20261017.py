"""Native-guard monitoring must not retransfer images it never consults."""
import asyncio
import copy
import threading
from types import SimpleNamespace

import pytest

from plugin.plugins.hsr_companion.adapters.text_reader import check_current
from plugin.plugins.hsr_companion.runtime.live_refresh import guarded_analysis
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_fix_20261012 import frame, picture, text_result
from plugin.plugins.hsr_companion.tests.test_delivery_races import complete_commit


async def configured(tmp_path, *, native=True, failure=None, changed=False):
    owner, _, _ = plugin_fixture(tmp_path)
    await complete_commit(owner, picture())
    if native:
        owner._accepted_frame_delivery_guard = owner._accepted_frame_query_guard
    reads, polls = [], []
    release = threading.Event()
    def status():
        polls.append(True)
        game = {"running": True, "pid": 1, "minimized": False}
        capture = {"state": "capturing", "isolated_window": True}
        if failure and len(polls) >= 2:
            if failure == "closed":
                game["running"] = False
            elif failure == "minimized":
                game["minimized"] = True
            elif failure == "pid":
                game["pid"] = 2
            elif failure == "capture":
                capture["state"] = "stopped"
        return {"connected": True, "frame_seq": len(polls) + 1, "game": game, "capture": capture}
    def capture(seq):
        reads.append(seq)
        return frame(picture("page" if changed else "none"))
    def analyze(payload, *, cancelled, **kwargs):
        while not release.wait(.005):
            check_current(cancelled)
        check_current(cancelled)
        return text_result()
    owner._assistant_frame_seq = 1
    owner._assistant_client = SimpleNamespace(health=status, fetch_frame=capture)
    owner._vision = SimpleNamespace(analyze_live=analyze)
    return owner, reads, polls, release


@pytest.mark.parametrize("native", [False, True])
def test_only_unproven_pages_need_in_progress_image_transfers(tmp_path, native):
    async def run():
        owner, reads, polls, release = await configured(tmp_path, native=native)
        task = asyncio.create_task(guarded_analysis(owner, picture(), helper=True))
        try:
            await asyncio.sleep(.65)
            release.set()
            assert await asyncio.wait_for(task, 3) is not None
            assert len(polls) >= 2
            assert len(reads) == 1 if native else len(reads) >= 3
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["closed", "minimized", "pid", "capture"])
def test_health_failure_still_stops_producer_without_deleting_candidate(tmp_path, failure):
    async def run():
        owner, _, polls, release = await configured(tmp_path, failure=failure)
        before = copy.deepcopy(owner._committed_observation)
        try:
            assert await asyncio.wait_for(guarded_analysis(owner, picture(), helper=True), 3) is None
            assert len(polls) >= 2
            assert owner._committed_observation == before
            assert not (await owner._current_visual_evidence())["fresh"]
        finally:
            release.set()
    asyncio.run(run())


def test_end_of_producer_still_rejects_a_changed_page(tmp_path):
    async def run():
        owner, reads, _, release = await configured(tmp_path, changed=True)
        before = copy.deepcopy(owner._committed_observation)
        release.set()
        assert await guarded_analysis(owner, picture(), helper=True) is None
        assert reads  # A health response was NOT used as the final image proof.
        assert owner._committed_observation == before
        assert not (await owner._current_visual_evidence())["fresh"]
    asyncio.run(run())
