"""The new host capability is opt-in; uncertain registration never resends."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import GIF_BYTES, PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings

CONTEXT = {
    "version": 1, "reply_id": "reply-A", "request_id": "request-A",
    "role": "K", "call_id": "call-A", "source": "plugin:sticker_manager",
    "token": "host-issued-secret",
}


class TailClient:
    def __init__(self):
        self.calls = []
        self.receipt = {"accepted": True, "status": "registered"}
        self.state = {"accepted": True, "status": "registered"}
        self.cancel_receipt = {"accepted": True, "status": "cancelled", "reason": "plugin_cancelled"}

    async def register(self, context, *, registration_id, parts, ai_behavior="blind", timeout=5.0):
        self.calls.append(("register", dict(context), registration_id, parts, ai_behavior, timeout))
        return dict(self.receipt)

    async def status(self, context, *, registration_id, timeout=5.0):
        self.calls.append(("status", dict(context), registration_id, timeout))
        return dict(self.state)

    async def cancel(self, context, *, registration_id, timeout=5.0):
        self.calls.append(("cancel", dict(context), registration_id, timeout))
        return dict(self.cancel_receipt)


def setup(tmp_path, *, gif=False, **settings):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(
        enabled=True, send=SendSettings(recent_dedup_count=0, **settings),
    )
    client = TailClient()
    plugin._sender._plugin = SimpleNamespace(ctx=SimpleNamespace(
        reply_tail=client, images=host.images, push_message=host.push_message,
    ))
    plugin._library.load(force=True)
    sticker, error = plugin._library.add(data=GIF_BYTES if gif else PNG_BYTES, desc="wave", tags=[])
    assert not error
    return plugin, host, client, sticker


async def send(plugin, sticker, *, context=CONTEXT, source="tool", **kwargs):
    return await plugin._sender.send(
        sticker, lanlan="K", settings=plugin._settings, source=source,
        host_reply=context, **kwargs,
    )


def test_registered_image_is_not_counted_and_tool_returns_promptly(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    result = run_async(asyncio.wait_for(send(plugin, sticker), 1))
    assert result.ok and result.queued and result.detail == "host_reply_tail"
    assert not host.push.calls
    assert client.calls[0][0] == "register" and client.calls[0][4] == "blind"
    assert plugin._library.get(sticker.id).use_count == 0
    assert not plugin._library.read_usage() and not plugin._sender._last_sent
    assert "K" in plugin._sender._inflight


def test_host_submission_counts_once_and_never_pushes_again(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    run_async(send(plugin, sticker))
    assert run_async(plugin._sender.drain(settings=plugin._settings)) == []
    client.state = {"accepted": True, "status": "submitted"}
    outcomes = run_async(plugin._sender.drain(settings=plugin._settings))
    assert len(outcomes) == 1 and outcomes[0][1].ok
    assert run_async(plugin._sender.drain(settings=plugin._settings)) == []
    assert plugin._library.get(sticker.id).use_count == 1
    assert len(plugin._library.read_usage()) == 1
    assert plugin._library.read_usage()[0]["ok"] is True
    assert "K" in plugin._sender._last_sent
    assert not plugin._sender._inflight and not host.push.calls


@pytest.mark.parametrize("receipt", [
    {"accepted": False, "status": "uncertain"},
    None,
])
def test_uncertain_registration_only_queries_original_identity(tmp_path, run_async, receipt):
    plugin, host, client, sticker = setup(tmp_path)
    client.receipt = receipt
    if receipt is None:
        async def fail(*args, **kwargs):
            raise TimeoutError("ACK lost")
        client.register = fail
    result = run_async(send(plugin, sticker))
    assert result.ok and result.queued and result.detail == "host_reply_tail_uncertain"
    client.state = {"accepted": False, "status": "uncertain"}
    assert not run_async(plugin._sender.drain(settings=plugin._settings))
    client.state = {"accepted": True, "status": "submitted"}
    assert run_async(plugin._sender.drain(settings=plugin._settings))[0][1].ok
    assert not host.push.calls
    assert len([row for row in client.calls if row[0] == "register"]) <= 1
    assert all(row[1]["reply_id"] == "reply-A" for row in client.calls)


@pytest.mark.parametrize("state", ["cancelled", "failed"])
def test_rejection_never_falls_back_to_immediate_send(tmp_path, run_async, state):
    plugin, host, client, sticker = setup(tmp_path)
    client.receipt = {"accepted": False, "status": state, "reason": "context_expired"}
    result = run_async(send(plugin, sticker))
    assert not result.ok and result.code == "context_expired"
    assert not host.push.calls and not plugin._sender._host_pending
    assert not plugin._sender._inflight and plugin._library.get(sticker.id).use_count == 0


def test_original_role_is_required_not_latest_role(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    result = run_async(send(plugin, sticker, context={**CONTEXT, "role": "other-role"}))
    assert not result.ok and result.code == "reply_tail_target_mismatch"
    assert not client.calls and not host.push.calls


@pytest.mark.parametrize("mode", ["no_context", "opted_out", "old_sdk", "agent", "panel"])
def test_original_immediate_path_kept(tmp_path, run_async, mode):
    plugin, host, client, sticker = setup(tmp_path, host_reply_tail=mode != "opted_out")
    if mode == "old_sdk":
        plugin._sender._plugin.ctx.reply_tail = None
    source = mode if mode in {"agent", "panel"} else "tool"
    result = run_async(send(
        plugin, sticker, context=None if mode == "no_context" else CONTEXT, source=source,
    ))
    assert result.ok and not result.queued
    assert not client.calls and len(host.push.calls) == 1
    assert host.push.calls[0]["ai_behavior"] == ("blind" if source == "tool" else "read")
    assert plugin._library.get(sticker.id).use_count == 1


def test_gif_bytes_preserved_without_vision_or_upload(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path, gif=True)
    run_async(send(plugin, sticker))
    image = client.calls[0][3][0]
    assert image == {"type": "image", "data": GIF_BYTES, "mime": "image/gif"}
    assert not host.images.calls and not host.push.calls


def test_caption_keeps_normal_plugin_channel_and_image_is_tail_only(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    result = run_async(send(plugin, sticker, text="extra caption"))
    assert result.queued and result.text_submitted
    assert host.push.calls[0]["parts"] == [{"type": "text", "text": "extra caption"}]
    assert host.push.calls[0]["ai_behavior"] == "blind"
    assert len(host.push.calls) == 1
    assert all(part["type"] == "image" for part in client.calls[0][3])


@pytest.mark.parametrize("change", ["disable_plugin", "disable_image", "remove_image", "disable_tail"])
def test_pending_invalidated_resources_request_host_cancellation(tmp_path, run_async, change):
    plugin, host, client, sticker = setup(tmp_path)
    run_async(send(plugin, sticker))
    if change == "disable_plugin":
        plugin._settings = replace(plugin._settings, enabled=False)
    elif change == "disable_tail":
        plugin._settings = replace(plugin._settings, send=replace(plugin._settings.send, host_reply_tail=False))
    elif change == "disable_image":
        updated, error = plugin._library.update(sticker.id, disabled=True)
        assert updated is not None and not error
    else:
        assert not plugin._library.remove(sticker.id)
    outcome = run_async(plugin._sender.drain(settings=plugin._settings))
    assert len(outcome) == 1 and not outcome[0][1].ok
    assert client.calls[-1][0] == "cancel"
    assert not host.push.calls and not plugin._sender._inflight


def test_submission_wins_cancellation_race_is_counted_truthfully(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    run_async(send(plugin, sticker))
    client.cancel_receipt = {"accepted": True, "status": "submitted"}
    plugin._settings = replace(plugin._settings, enabled=False)
    outcome = run_async(plugin._sender.drain(settings=plugin._settings))
    assert outcome[0][1].ok
    assert plugin._library.get(sticker.id).use_count == 1 and not host.push.calls


def test_duplicate_completed_registration_does_not_inflate_usage(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path, cooldown_sec=0)
    client.receipt = {"accepted": True, "status": "submitted", "duplicate": True}
    result = run_async(send(plugin, sticker))
    assert result.ok and result.detail == "host_reply_tail_duplicate"
    assert plugin._library.get(sticker.id).use_count == 0
    assert not plugin._library.read_usage() and not host.push.calls


def test_timeout_and_shutdown_clear_local_state_without_replay(tmp_path, run_async, monkeypatch):
    plugin, host, client, sticker = setup(tmp_path)
    monkeypatch.setattr("sticker_manager.services.sender.time.monotonic", lambda: 100)
    run_async(send(plugin, sticker))
    monkeypatch.setattr("sticker_manager.services.sender.time.monotonic", lambda: 500)
    outcome = run_async(plugin._sender.drain(settings=plugin._settings))
    assert outcome[0][1].code == "reply_tail_timeout"
    assert client.calls[-1][0] == "cancel" and not plugin._sender._host_pending
    assert not host.push.calls


def test_original_host_context_role_wins_over_legacy_hint(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    result = run_async(plugin.tool_sticker_send(
        sticker_id=sticker.id, _ctx={"host_reply": CONTEXT, "lanlan_name": "latest-other-role"},
    ))
    assert result["queued"] == sticker.id and result["sent"] == ""
    assert "全部文字显示后" in result["note"]
    assert client.calls[0][1]["role"] == "K" and not host.push.calls


def test_inflight_registration_observes_shutdown_epoch(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        original = client.register

        async def parked(*args, **kwargs):
            started.set()
            await release.wait()
            return await original(*args, **kwargs)

        client.register = parked
        task = asyncio.create_task(send(plugin, sticker))
        await started.wait()
        plugin._sender.cancel_pending(settings=plugin._settings, closing=True)
        await plugin._sender.cancel_host_pending(settings=plugin._settings)
        release.set()
        return await task

    result = run_async(exercise())
    assert not result.ok and result.code == "send_stopped"
    assert len([call for call in client.calls if call[0] == "cancel"]) == 1
    assert not plugin._sender._inflight and not plugin._sender._host_pending
    assert not host.push.calls


def test_timer_cannot_cancel_a_registration_still_in_progress(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()
        original = client.register

        async def parked(*args, **kwargs):
            started.set()
            await release.wait()
            return await original(*args, **kwargs)

        client.register = parked
        client.state = {"accepted": False, "status": "failed", "reason": "registration_not_found"}
        task = asyncio.create_task(send(plugin, sticker))
        await started.wait()
        assert not await plugin._sender.drain(settings=plugin._settings)
        assert not client.calls
        release.set()
        return await task

    result = run_async(exercise())
    assert result.queued and len(client.calls) == 1 and client.calls[0][0] == "register"
    assert not host.push.calls


def test_uncertain_registration_not_found_is_not_definitive(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    client.receipt = {"accepted": False, "status": "uncertain"}
    run_async(send(plugin, sticker))
    client.state = {"accepted": False, "status": "failed", "reason": "registration_not_found"}
    assert not run_async(plugin._sender.drain(settings=plugin._settings))
    assert plugin._sender._host_pending and not plugin._library.read_usage()
    client.state = {"accepted": True, "status": "submitted"}
    assert run_async(plugin._sender.drain(settings=plugin._settings))[0][1].ok
    assert plugin._library.get(sticker.id).use_count == 1 and not host.push.calls


def test_fresh_attempt_capability_can_register_same_image(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    first = run_async(send(plugin, sticker))
    assert first.queued
    client.state = {"accepted": False, "status": "cancelled", "reason": "reply_discarded"}
    second = run_async(send(plugin, sticker, context={**CONTEXT, "token": "new-attempt-token"}))
    assert second.queued
    registrations = [row for row in client.calls if row[0] == "register"]
    assert len(registrations) == 2 and registrations[0][2] != registrations[1][2]
    assert not host.push.calls and plugin._library.get(sticker.id).use_count == 0


def test_upload_return_after_shutdown_cannot_push_caption(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def upload(data, *, mime, timeout):
            started.set()
            await release.wait()
            return {"type": "image", "url": "http://127.0.0.1:48911/media/test", "mime": mime}

        host.images.upload = upload
        large, error = plugin._library.add(data=PNG_BYTES + b"x" * (1024 * 1024), desc="large", tags=[])
        assert not error
        task = asyncio.create_task(send(plugin, large, text="extra caption"))
        await started.wait()
        plugin._sender.cancel_pending(settings=plugin._settings, closing=True)
        release.set()
        return await task

    result = run_async(exercise())
    assert not result.ok and not result.text_submitted
    assert not host.push.calls and not client.calls


def test_cancelled_tool_registration_remains_reconcilable(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)

    async def interrupted(*args, **kwargs):
        raise asyncio.CancelledError()

    client.register = interrupted
    with pytest.raises(asyncio.CancelledError):
        run_async(send(plugin, sticker))
    assert not plugin._sender._host_pending["K"].registering
    client.state = {"accepted": False, "status": "cancelled", "reason": "reply_interrupted"}
    outcome = run_async(plugin._sender.drain(settings=plugin._settings))
    assert outcome[0][1].code == "reply_interrupted"
    assert not plugin._sender._inflight and not host.push.calls


def test_uncertain_cancellation_stays_pending_until_confirmed(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    run_async(send(plugin, sticker))
    plugin._settings = replace(plugin._settings, enabled=False)
    client.cancel_receipt = {"accepted": False, "status": "uncertain", "reason": "transport_error"}
    assert not run_async(plugin._sender.drain(settings=plugin._settings))
    assert plugin._sender._host_pending and not plugin._library.read_usage()
    client.cancel_receipt = {"accepted": True, "status": "cancelled", "reason": "plugin_cancelled"}
    outcome = run_async(plugin._sender.drain(settings=plugin._settings))
    assert not outcome[0][1].ok and not plugin._sender._inflight
    assert not host.push.calls


def test_cooldown_uses_actual_submission_not_polling_time(tmp_path, run_async, monkeypatch):
    plugin, host, client, sticker = setup(tmp_path)
    monkeypatch.setattr("sticker_manager.services.sender.time.time", lambda: 1000)
    run_async(send(plugin, sticker))
    client.state = {"accepted": True, "status": "submitted", "submitted_at": 995.0}
    run_async(plugin._sender.drain(settings=plugin._settings))
    assert plugin._sender._last_sent["K"] == 995.0
    assert plugin._sender.cooldown_remaining("K", plugin._settings, now=1000) == 15.0
    assert plugin._library.read_usage()[0]["at"] == 995.0 and not host.push.calls


@pytest.mark.parametrize("closing", [False, True])
def test_submission_ack_after_epoch_change_is_still_success(tmp_path, run_async, closing):
    plugin, host, client, sticker = setup(tmp_path)

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def parked(*args, **kwargs):
            started.set()
            await release.wait()
            return {"accepted": True, "status": "submitted", "submitted_at": 995.0}

        client.register = parked
        task = asyncio.create_task(send(plugin, sticker))
        await started.wait()
        plugin._sender.cancel_pending(settings=plugin._settings, closing=closing)
        release.set()
        return await task

    result = run_async(exercise())
    assert result.ok and not result.queued
    assert plugin._library.get(sticker.id).use_count == 1
    assert [row["ok"] for row in plugin._library.read_usage()] == [True]
    assert plugin._sender._last_sent["K"] == 995.0
    assert not plugin._sender._inflight and not plugin._sender._host_pending
    assert not host.push.calls


@pytest.mark.parametrize("status", ["submitted", "cancelled"])
def test_shutdown_settlement_and_late_tool_ack_record_one_outcome(tmp_path, run_async, status):
    plugin, host, client, sticker = setup(tmp_path)

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def settings():
            return plugin._settings

        async def parked(*args, **kwargs):
            started.set()
            await release.wait()
            return {"accepted": status == "submitted", "status": status}

        plugin._reload_settings = settings
        client.register = parked
        client.cancel_receipt = {"accepted": True, "status": status}
        task = asyncio.create_task(plugin.tool_sticker_send(
            sticker_id=sticker.id, _ctx={"host_reply": CONTEXT},
        ))
        await started.wait()
        await plugin.on_shutdown()
        assert plugin._runstats.sent == int(status == "submitted")
        assert plugin._runstats.refused == int(status == "cancelled")
        release.set()
        return await task

    result = run_async(exercise())
    assert result["ok"] == (status == "submitted")
    assert "_delivery_recorded" not in result
    assert plugin._library.get(sticker.id).use_count == int(status == "submitted")
    assert [row["ok"] for row in plugin._library.read_usage()] == [status == "submitted"]
    assert plugin._runstats.tool_calls == 1
    assert plugin._runstats.sticker_send_calls == 1
    assert plugin._runstats.sent == int(status == "submitted")
    assert plugin._runstats.refused == int(status == "cancelled")
    assert not plugin._sender._inflight and not plugin._sender._host_pending
    assert not host.push.calls


@pytest.mark.parametrize("status", ["submitted", "cancelled"])
def test_late_tool_ack_before_deferred_observer_does_not_count_twice(tmp_path, run_async, status):
    plugin, host, client, sticker = setup(tmp_path)

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def parked(*args, **kwargs):
            started.set()
            await release.wait()
            return {"accepted": status == "submitted", "status": status}

        client.register = parked
        client.cancel_receipt = {"accepted": True, "status": status}
        task = asyncio.create_task(plugin.tool_sticker_send(
            sticker_id=sticker.id, _ctx={"host_reply": CONTEXT},
        ))
        await started.wait()
        plugin._sender.cancel_pending(settings=plugin._settings, closing=True)
        outcomes = await plugin._sender.cancel_host_pending(settings=plugin._settings)
        original = outcomes[0][1]
        assert not original.delivery_recorded
        assert original.detail == ""
        release.set()
        result = await task
        assert plugin._runstats.tool_calls == 1
        assert plugin._runstats.sent == plugin._runstats.refused == 0
        assert not original.delivery_recorded and original.detail == ""
        plugin._note_deferred_outcomes(outcomes)
        assert original.delivery_recorded
        plugin._note_deferred_outcomes(outcomes)
        return result

    result = run_async(exercise())
    assert result["ok"] == (status == "submitted")
    assert "_delivery_recorded" not in result
    assert plugin._library.get(sticker.id).use_count == int(status == "submitted")
    assert [row["ok"] for row in plugin._library.read_usage()] == [status == "submitted"]
    assert plugin._runstats.sticker_send_calls == 1
    assert plugin._runstats.sent == int(status == "submitted")
    assert plugin._runstats.refused == int(status == "cancelled")
    assert not plugin._sender._inflight and not plugin._sender._host_pending
    assert not host.push.calls


def test_cancel_ack_that_already_submitted_is_not_reported_as_stopped(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def parked(*args, **kwargs):
            started.set()
            await release.wait()
            return {"accepted": True, "status": "registered"}

        client.register = parked
        client.cancel_receipt = {"accepted": True, "status": "submitted"}
        task = asyncio.create_task(send(plugin, sticker))
        await started.wait()
        plugin._sender.cancel_pending(settings=plugin._settings, closing=True)
        release.set()
        return await task

    result = run_async(exercise())
    assert result.ok and not result.queued
    assert plugin._library.get(sticker.id).use_count == 1
    assert [row["ok"] for row in plugin._library.read_usage()] == [True]
    assert len([call for call in client.calls if call[0] == "cancel"]) == 1
    assert not host.push.calls and not plugin._sender._inflight


def test_late_old_ack_cannot_release_new_attempt_upload_reservation(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)

    async def exercise():
        old_started, old_release = asyncio.Event(), asyncio.Event()
        upload_started, upload_release = asyncio.Event(), asyncio.Event()
        original = client.register
        invocations = 0

        async def parked(*args, **kwargs):
            nonlocal invocations
            invocations += 1
            if invocations == 1:
                old_started.set()
                await old_release.wait()
                return {"accepted": True, "status": "registered"}
            return await original(*args, **kwargs)

        async def upload(data, *, mime, timeout):
            upload_started.set()
            await upload_release.wait()
            return {"type": "image", "url": "http://127.0.0.1:48911/media/new", "mime": mime}

        client.register = parked
        host.images.upload = upload
        old_task = asyncio.create_task(send(plugin, sticker))
        await old_started.wait()
        plugin._sender.cancel_pending(settings=plugin._settings)
        outcomes = await plugin._sender.cancel_host_pending(settings=plugin._settings)
        assert len(outcomes) == 1 and not outcomes[0][1].ok
        large, error = plugin._library.add(data=PNG_BYTES + b"x" * (1024 * 1024), desc="new", tags=[])
        assert not error
        new_task = asyncio.create_task(send(
            plugin, large, context={**CONTEXT, "token": "new-attempt-token"},
        ))
        await upload_started.wait()
        old_release.set()
        old_result = await old_task
        assert not old_result.ok and old_result.failure_recorded
        assert "K" in plugin._sender._inflight
        competing = await send(plugin, sticker)
        assert not competing.ok and competing.code == "send_cooldown"
        upload_release.set()
        new_result = await new_task
        assert new_result.queued
        assert plugin._sender._host_pending["K"].sticker.id == large.id
        client.state = {"accepted": True, "status": "submitted"}
        await plugin._sender.drain(settings=plugin._settings)
        return large

    large = run_async(exercise())
    assert plugin._library.get(sticker.id).use_count == 0
    assert plugin._library.get(large.id).use_count == 1
    assert len(plugin._library.read_usage()) == 2
    assert not plugin._sender._inflight and not plugin._sender._host_pending
    assert not host.push.calls


def test_duplicate_close_before_registration_ack_does_not_count_twice(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path, cooldown_sec=0)
    client.receipt = {"accepted": True, "status": "submitted"}
    assert run_async(send(plugin, sticker)).ok

    async def exercise():
        started, release = asyncio.Event(), asyncio.Event()

        async def parked(*args, **kwargs):
            started.set()
            await release.wait()
            return {"accepted": True, "status": "submitted", "duplicate": True}

        client.register = parked
        client.cancel_receipt = {"accepted": True, "status": "submitted"}
        task = asyncio.create_task(send(plugin, sticker))
        await started.wait()
        plugin._sender.cancel_pending(settings=plugin._settings, closing=True)
        outcomes = await plugin._sender.cancel_host_pending(settings=plugin._settings)
        assert outcomes[0][1].ok and outcomes[0][1].detail == "host_reply_tail_duplicate"
        release.set()
        return await task

    result = run_async(exercise())
    assert result.ok and result.detail == "host_reply_tail_duplicate"
    assert plugin._library.get(sticker.id).use_count == 1
    assert [row["ok"] for row in plugin._library.read_usage()] == [True]
    assert not plugin._sender._inflight and not host.push.calls


def test_accounted_registration_identity_cache_is_bounded(tmp_path, run_async, monkeypatch):
    plugin, host, client, sticker = setup(tmp_path, cooldown_sec=0)
    monkeypatch.setattr("sticker_manager.services.sender._MAX_HOST_ACCOUNTED", 2)
    client.receipt = {"accepted": True, "status": "submitted"}
    for index in range(3):
        assert run_async(send(
            plugin, sticker, context={**CONTEXT, "token": f"capability-{index}"},
        )).ok
    assert len(plugin._sender._host_accounted) == 2
    assert plugin._library.get(sticker.id).use_count == 3
    assert not host.push.calls


def test_uncertain_tool_receipt_does_not_claim_confirmed_registration(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path)
    client.receipt = {"accepted": False, "status": "uncertain"}
    result = run_async(plugin.tool_sticker_send(
        sticker_id=sticker.id, _ctx={"host_reply": CONTEXT},
    ))
    assert result["queued"] == sticker.id and result["sent"] == ""
    assert "暂未确认" in result["note"] and "如宿主接受" in result["note"]
    assert "已登记" not in result["note"]
    assert not plugin._library.read_usage() and not host.push.calls


@pytest.mark.parametrize("status", ["registered", "uncertain", "submitted"])
def test_finished_speech_is_not_reissued_by_tool_or_receipt(tmp_path, run_async, status):
    plugin, host, client, sticker = setup(tmp_path, cooldown_sec=0)
    client.receipt = {"accepted": status != "uncertain", "status": status}
    client.status = AsyncMock(side_effect=AssertionError("tool must not wait for reply completion"))
    result = run_async(plugin.tool_sticker_send(
        sticker_id=sticker.id, _ctx={"host_reply": CONTEXT},
    ))
    assert result["ok"]
    assert "若已说完就结束" in result["note"]
    assert "不复述原话" in result["note"]
    assert "正文只说一次" in result["hint"]
    assert len(client.calls) == 1 and client.calls[0][0] == "register"
    assert all(part["type"] == "image" for part in client.calls[0][3])
    client.status.assert_not_awaited()
    assert not host.push.calls


def test_duplicate_tail_receipt_finishes_speech_without_another_caption(tmp_path, run_async):
    plugin, host, client, sticker = setup(tmp_path, cooldown_sec=0)
    client.receipt = {"accepted": True, "status": "submitted", "duplicate": True}
    result = run_async(plugin.tool_sticker_send(
        sticker_id=sticker.id, _ctx={"host_reply": CONTEXT},
    ))
    assert result["ok"] and result["sent"] == ""
    assert "不必再次发送" in result["note"] and "若已说完就结束" in result["note"]
    assert "不复述原话" in result["note"]
    assert not host.push.calls and not plugin._library.read_usage()
