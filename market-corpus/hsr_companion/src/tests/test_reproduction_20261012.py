"""Regression contracts derived from the frozen-build diagnostic run.

Real SDK/core implementations with a controlled transport, not a live IPC
connection. Original failure assertions/results remain in the frozen run log;
these assertions describe the next repair candidate, not live-chat acceptance.
"""
import asyncio
import importlib
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from plugin.core.bus.memory import MemoryClient
from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity
from plugin.plugins.hsr_companion.runtime.grounding import asks_navigation_only, asks_screen_content
from plugin.plugins.hsr_companion.runtime.live_refresh import refresh_for_query
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.sdk.plugin import NekoPluginBase


class ControlledTransport:
    def __init__(self, delay):
        self.delay = delay
        self.on_loop = None

    def request(self, request, timeout):
        # Model a sync wait longer than the wrapper's async budget. No real IPC.
        self.on_loop = asyncio.get_running_loop()
        threading.Event().wait(self.delay)
        return {"result": {"history": []}}


def real_sdk_bus(delay):
    transport = ControlledTransport(delay)
    raw_context = SimpleNamespace(plugin_id="hsr_companion", _plugin_comm_queue=object(),
                                  _zmq_ipc_client=transport)
    owner = object.__new__(NekoPluginBase)
    owner.ctx = SimpleNamespace(bus=SimpleNamespace(memory=MemoryClient(raw_context)))
    return owner.bus, transport


def test_real_sdk_memory_wait_does_not_block_plugin_loop_and_obeys_budget():
    bus, transport = real_sdk_bus(.55)

    async def run():
        heartbeats = []
        async def heartbeat():
            await asyncio.sleep(.02)
            heartbeats.append(time.monotonic())
        ticker = asyncio.create_task(heartbeat())
        started = time.monotonic()
        outcome = await latest_user_activity(bus, target="reproduction-only")
        elapsed = time.monotonic() - started
        blocked = not heartbeats
        await ticker
        print({"sdk_real_transport_controlled": True, "elapsed": round(elapsed, 3),
               "wrapper_budget": .35, "heartbeat_blocked": blocked, "outcome": outcome})
        assert transport.on_loop is not asyncio.get_running_loop()
        assert not blocked and elapsed < .5
        assert outcome == ("unavailable", None)
        # Drain the controlled worker so it cannot affect another test.
        await asyncio.sleep(.25)
    asyncio.run(run())


def test_reproduce_page_text_query_enters_character_full_path(tmp_path, monkeypatch):
    owner, _, _ = plugin_fixture(tmp_path)
    module = importlib.import_module(type(owner).__module__)
    calls = []
    async def observed(owner, **kwargs):
        calls.append(kwargs)
        owner._last_refresh_diagnostics = {"outcome": "ready"}
        return None
    monkeypatch.setattr(module, "refresh_for_query", observed)
    asyncio.run(owner.hsr_companion_prepare(query="现在是什么页面？页面文字写了什么？"))
    print({"page_text_refresh_requirements": calls})
    assert calls[0]["require_content"] is True
    assert calls[0]["require_characters"] is True


def test_reproduce_rephrased_followup_loses_content_intent():
    original = "现在是什么页面？页面文字写了什么？"
    rewritten = "再确认一下现在是哪里？"
    assert asks_screen_content(original) and not asks_navigation_only(original)
    assert asks_navigation_only(rewritten)


@pytest.mark.parametrize("captured_before_question", [True, False])
def test_reproduce_committed_text_result_rejected_only_by_capture_timestamp(tmp_path, captured_before_question):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._observer_task = SimpleNamespace(done=lambda: False)

    async def run():
        before = datetime.now(timezone.utc) - timedelta(seconds=.2)
        async def producer():
            await asyncio.sleep(.02)
            value = analysis(roles=False, scene="menu")
            value["page"] = {"page_type": "battle_pass", "label": "无名勋礼"}
            value["screen_facts"] = {"facts": [], "visible_text": [{"text": "规则说明"}]}
            stamp = before if captured_before_question else datetime.now(timezone.utc)
            await owner._apply_local_analysis(value, image_fingerprint="identical-memory-frame",
                                              source="assistant_local_ocr", observed_at=stamp.isoformat())
        worker = asyncio.create_task(producer())
        result = await refresh_for_query(owner, timeout=.15, require_characters=True, require_content=True)
        await worker
        current = await owner._current_visual_evidence()
        print({"captured_before_question": captured_before_question, "result_returned": result is not None,
               "current_status": current["status"], "committed_serial": owner._observation_serial})
        if captured_before_question:
            assert result is None and current["fresh"]
            # The answer remains blocked without a live check, but a query's
            # timestamp is no longer a global panel-data revocation.
            # Inspect the same committed evidence without the query-time gate.
            owner._query_refresh_after = 0
            committed = await owner._current_visual_evidence()
            assert committed["fresh"] and committed["scene"]["verified"]
            assert committed["screen_facts"]["visible_text"] == [{"text": "规则说明"}]
        else:
            assert result is not None and result["scene"]["verified"]
    asyncio.run(run())


def test_persistent_real_sdk_transport_failure_safely_cancels_after_bounded_retries(monkeypatch):
    bus, transport = real_sdk_bus(0)
    transport.request = lambda *args, **kwargs: None  # Controlled IPC failure, not a live failure claim.
    sent = []
    owner = SimpleNamespace(bus=bus, _observer_status={"enabled": True, "state": "observing"},
                            _target_character=lambda: "reproduction-only", push_message=lambda **kw: sent.append(kw))
    now = time.monotonic()
    req = followup.AnswerRequest("repro-failure", "页面文字写了什么", "reproduction-only", time.time(), now,
                                 now + 1, "unknown", False, False, True, False)
    owner._pending_answer = req
    monkeypatch.setattr(followup, "FOLLOWUP_GRACE_SECONDS", 0)
    asyncio.run(followup.deliver_followup(owner, req))
    print({"controlled_transport_failure": True, "state": owner._followup_status, "messages_sent": len(sent)})
    assert owner._followup_status["reason"] == "activity_unavailable_after_retries" and not sent
