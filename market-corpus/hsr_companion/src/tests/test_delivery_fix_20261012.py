"""Next-candidate delivery contracts: real pixel guards, synthetic OCR.

NOT real-game OCR accuracy, live IPC reliability or cat playback acceptance.
The frozen test plan/run is unchanged; this candidate has its own contracts.
"""
import asyncio
import time
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from plugin.plugins.hsr_companion.runtime import followup
from plugin.plugins.hsr_companion.runtime.chat_activity import latest_user_activity
from plugin.plugins.hsr_companion.runtime.live_refresh import guarded_analysis, query_pending, refresh_for_query, validate_before_delivery, validated_delivery
from plugin.plugins.hsr_companion.tests.test_current_observation import analysis, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_page_followup import followup_owner, png, request

CONTROL = {"region": {"left": .01, "top": .01, "right": .3, "bottom": .1}}
TEXT = {"region": {"left": .05, "top": .3, "right": .9, "bottom": .4}}


def picture(change="none"):
    image = Image.new("RGB", (640, 400), "black")
    if change == "page":
        ImageDraw.Draw(image).rectangle((0, 0, 200, 45), fill="white")
    elif change == "text":
        ImageDraw.Draw(image).rectangle((55, 125, 65, 137), fill="white")
    return png(image)


def frame(payload, *, stale=False):
    return SimpleNamespace(image_bytes=payload, timestamp=time.time() - (10 if stale else 0),
                           game={"running": True, "pid": 1}, capture={"state": "capturing", "isolated_window": True})


def text_result():
    value = analysis(roles=False, scene="menu")
    value["page"] = {"page_type": "battle_pass", "label": "无名勋礼"}
    value["recognition"]["scene_regions"] = [CONTROL]
    value["screen_facts"] = {"facts": [], "visible_text": [{"text": "规则说明", **TEXT}]}
    return value


def test_prequestion_capture_completes_once_and_reaches_text_query(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    calls = []
    payload = picture()
    owner._observer_task = SimpleNamespace(done=lambda: False)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(payload))
    owner._vision = SimpleNamespace(analyze=lambda data: calls.append(data) or text_result())

    async def run():
        captured = datetime.now(timezone.utc) - timedelta(seconds=.4)
        async def produce():
            await asyncio.sleep(.02)
            value = await guarded_analysis(owner, payload, helper=True)
            assert value is not None
            await owner._apply_local_analysis(value, image_fingerprint="memory-only", source="assistant_local_ocr",
                                              observed_at=captured.isoformat())
        producer = asyncio.create_task(produce())
        result = await refresh_for_query(owner, timeout=2, require_characters=True, require_content=True)
        await producer
        assert result is not None, owner._last_refresh_diagnostics
        assert result["fresh"] and result["page_type"] == "battle_pass"
        assert result["screen_facts"]["visible_text"][0]["text"] == "规则说明"
        assert result["observed_at"] == captured.isoformat()  # No fabricated capture time.
        assert len(calls) == 1
        assert (await owner._current_visual_evidence())["fresh"]
    asyncio.run(run())


@pytest.mark.parametrize("change", ["page", "stale"])
def test_same_title_does_not_authorize_changed_text_or_unavailable_capture(tmp_path, change):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._content_query_waiters = 1
    owner._vision = SimpleNamespace(analyze=lambda data: text_result())
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(
        picture(change), stale=change == "stale"))
    async def run():
        assert await guarded_analysis(owner, picture(), helper=True) is None
        assert getattr(owner, "_query_validated_read", None) is None
    asyncio.run(run())


def test_changed_literal_row_is_withheld_without_revoking_independent_page(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._content_query_waiters = 1
    value = text_result()
    value["screen_facts"]["visible_text"].append({"text": "仍可读的规则", "region": {"left": .05, "top": .5, "right": .9, "bottom": .6}})
    owner._vision = SimpleNamespace(analyze=lambda data: value)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
    async def run():
        selected = await guarded_analysis(owner, picture(), helper=True)
        assert selected is not None and selected["page"]["page_type"] == "battle_pass"
        assert [row["text"] for row in selected["screen_facts"]["visible_text"]] == ["仍可读的规则"]
        assert selected["recognition"]["unstable_text_rows_removed"] == 1
        assert selected["screen_facts"]["unclassified_text"] == []
    asyncio.run(run())


def test_content_changes_after_commit_rejected_at_final_handoff(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._content_query_waiters = 1
    owner._vision = SimpleNamespace(analyze=lambda data: text_result())
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
    async def run():
        value = await guarded_analysis(owner, picture(), helper=True)
        await owner._apply_local_analysis(value, image_fingerprint="memory-only", source="assistant_local_ocr")
        owner._assistant_client.fetch_frame = lambda seq: frame(picture("text"))
        assert not await validate_before_delivery(owner)
        # New candidate: reject the changed literal row, not its independent
        # page. The old frozen package/test-run records are not rewritten.
        current = await owner._current_visual_evidence()
        assert current["fresh"] and current["page_type"] == "battle_pass"
        assert current["screen_facts"]["visible_text"] == []
    asyncio.run(run())


def test_changed_fact_is_withdrawn_without_revoking_independent_page(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._content_query_waiters = 1
    value = text_result()
    value["screen_facts"]["facts"] = [{"kind": "resource_count", "scope": "resource", "value": "100",
        "evidence": [TEXT]}, {"kind": "resource_count", "scope": "resource", "value": "200",
        "evidence": [{"region": {"left": .1, "top": .5, "right": .4, "bottom": .6}}]}]
    owner._vision = SimpleNamespace(analyze=lambda data: value)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture("text")))
    async def run():
        selected = await guarded_analysis(owner, picture(), helper=True)
        assert selected is not None and selected["page"]["page_type"] == "battle_pass"
        assert [fact["value"] for fact in selected["screen_facts"]["facts"]] == ["200"]
        assert selected["recognition"]["unstable_facts_removed"] == 1
    asyncio.run(run())


def test_missing_fact_pixel_proof_cannot_be_revalidated(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    value = text_result()
    value["screen_facts"]["facts"] = [{"kind": "quest_objective", "value": "unproven", "evidence": []}]
    owner._vision = SimpleNamespace(analyze=lambda data: value)
    owner._assistant_client = SimpleNamespace(fetch_frame=lambda seq: frame(picture()))
    async def run():
        selected = await guarded_analysis(owner, picture(), helper=True)
        assert selected["screen_facts"]["facts"] == []
    asyncio.run(run())


def test_live_validation_token_cannot_cross_new_query_or_observation_epoch():
    owner = SimpleNamespace(_query_refresh_after=time.time(), _query_refresh_serial=1,
                            _query_refresh_generation=3, _observation_epoch=4, _query_validated_read=(3, 4, 2))
    value = {"observation_serial": 2, "observed_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}
    assert not query_pending(owner, value)
    owner._query_refresh_generation += 1
    assert query_pending(owner, value)
    owner._query_refresh_generation = 3
    owner._observation_epoch += 1
    assert query_pending(owner, value)


def test_final_handoff_rejects_same_epoch_replacement(monkeypatch):
    from plugin.plugins.hsr_companion.runtime import live_refresh
    owner = SimpleNamespace(_observation_epoch=1, _observation_serial=2)
    async def validate(owner):
        await asyncio.sleep(0)
        owner._observation_serial = 3
        return True
    monkeypatch.setattr(live_refresh, "validate_before_delivery", validate)
    value = {"fresh": True, "screen_facts": {"observation_serial": 2}}
    assert asyncio.run(validated_delivery(owner, value)) is None


@pytest.mark.parametrize("failure_at", ["first", "final"])
def test_transient_activity_failure_retries_and_delivers_exactly_once(monkeypatch, failure_at):
    owner, sent = followup_owner(monkeypatch)
    reads = []
    async def activity(*args, **kwargs):
        reads.append(1)
        return ("unavailable", None) if len(reads) == (1 if failure_at == "first" else 2) else ("available", None)
    monkeypatch.setattr(followup, "latest_user_activity", activity)
    req = request()
    owner._pending_answer = req
    asyncio.run(followup.deliver_followup(owner, req))
    assert len(sent) == 1 and owner._followup_status["state"] == "submitted"
    assert len(reads) == 3


def test_new_user_message_seen_during_activity_retry_cancels(monkeypatch):
    owner, sent = followup_owner(monkeypatch)
    req = request()
    reads = []
    async def activity(*args, **kwargs):
        reads.append(1)
        return ("unavailable", None) if len(reads) == 1 else ("available", req.asked_at + .1)
    monkeypatch.setattr(followup, "latest_user_activity", activity)
    owner._pending_answer = req
    asyncio.run(followup.deliver_followup(owner, req))
    assert not sent and owner._followup_status["reason"] == "new_user_message"


def test_cancelled_activity_retry_does_not_cancel_replacement(monkeypatch):
    owner, sent = followup_owner(monkeypatch)
    req = request()
    replacement = replace(req, request_id="replacement")
    async def activity(*args, **kwargs):
        owner._pending_answer = replacement
        return "unavailable", None
    monkeypatch.setattr(followup, "latest_user_activity", activity)
    owner._pending_answer = req
    asyncio.run(followup.deliver_followup(owner, req))
    assert owner._pending_answer is replacement and not sent


def test_activity_read_cancellation_keeps_loop_responsive():
    async def get(**kwargs):
        await asyncio.sleep(.08)
        return []
    async def run():
        task = asyncio.create_task(latest_user_activity(SimpleNamespace(memory=SimpleNamespace(get=get)), target="cat"))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Wait only for the controlled test transport; production never blocks
        # the plugin loop draining this worker on a query timeout.
        await asyncio.sleep(.1)
    asyncio.run(run())


def test_slow_activity_transport_has_one_worker_and_no_timeout_backlog():
    release = threading.Event()
    entered = threading.Event()
    calls = []
    def get(**kwargs):
        calls.append(kwargs["bucket_id"])
        entered.set()
        release.wait(2)
        return []
    bus = SimpleNamespace(memory=SimpleNamespace(get=get))
    async def run():
        first = asyncio.create_task(latest_user_activity(bus, target="first"))
        await asyncio.to_thread(entered.wait, 1)
        for _ in range(12):
            assert await latest_user_activity(bus, target="other") == ("unavailable", None)
        assert await first == ("unavailable", None)
        assert calls == ["first"]
        release.set()
        await asyncio.sleep(.05)
        assert await latest_user_activity(bus, target="after") == ("available", None)
        assert calls == ["first", "after"]
    try:
        asyncio.run(run())
    finally:
        release.set()
