"""Query tracing must explain waits without changing freshness or storing pixels."""

import asyncio
from types import SimpleNamespace

from plugin.plugins.hsr_companion.runtime.live_refresh import refresh_for_query, trace_refresh
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture


def test_trace_is_bounded_and_noop_outside_query():
    owner = SimpleNamespace()
    trace_refresh(owner, "ignored")
    owner._active_refresh_trace = {"started": 0, "stages": []}
    for _ in range(100):
        trace_refresh(owner, "bounded")
    assert len(owner._active_refresh_trace["stages"]) == 48


def test_timeout_records_wait_without_reusing_old_frame(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    values["last_local_vision"] = analysis(scene="warp")

    async def run():
        owner._observer_task = asyncio.create_task(asyncio.sleep(1))
        await refresh_for_query(owner, timeout=.02)
        assert (await owner._current_visual_evidence())["status"] == "updating"
        assert owner._last_refresh_diagnostics["outcome"] == "timeout"
        assert owner._last_refresh_diagnostics["wait_seconds"] >= .02
        assert owner._active_refresh_trace is None
        owner._observer_task.cancel()

    asyncio.run(run())


def test_success_includes_commit_and_finish_without_text(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)

    async def run():
        async def produce():
            await asyncio.sleep(.01)
            await owner._apply_local_analysis(analysis(roles=False, scene="warp"), image_fingerprint="test", source="assistant_local_ocr")
        owner._observer_task = asyncio.create_task(produce())
        await refresh_for_query(owner, timeout=1, require_characters=False)
        diagnostics = owner._last_refresh_diagnostics
        assert diagnostics["outcome"] == "ready"
        assert [step["stage"] for step in diagnostics["stages"] if step["stage"] != "vision_turn_acquired"] == ["query_started", "reuse_miss", "observation_committed", "query_finished"]
        assert all(step["queue_seconds"] >= 0 for step in diagnostics["stages"] if step["stage"] == "vision_turn_acquired")
        assert all("text" not in step and "image" not in step for step in diagnostics["stages"])
        assert owner._active_refresh_trace is None

    asyncio.run(run())
