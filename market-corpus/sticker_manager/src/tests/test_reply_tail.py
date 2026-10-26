"""Plugin-only reply ordering, using isolated logs and strict SDK transport."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from conftest import GIF_BYTES, PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.services import sender as sender_module
from sticker_manager.services.turn_end import TurnEndLogs, log_directories


def _end(role="K"):
    return (
        f"2026-10-01 22:12:23 - N.E.K.O.Main.main_logic.cross_server - INFO - "
        f"[{role}] turn_end analyze check: history=14 recent=6 has_user=True "
        "had_input=True agent_callback_turn=False avatar_drop_turn=False\n"
    ).encode("utf-8")


def _input(role="K", length=3):
    return (
        f"2026-10-01 22:12:24 - N.E.K.O.Main.main_logic.core - INFO - "
        f"[{role}] voice user_transcript session=OmniOfflineClient "
        f"ws_connected=True len={length}\n"
    ).encode("utf-8")


def _append(path, data):
    with path.open("ab") as stream:
        stream.write(data)


class TestLogs(TurnEndLogs):
    __test__ = False

    def __init__(self, plugin, directories):
        super().__init__(plugin, directories)
        self.states = {"K": False, "A": False, "B": False}
        self.health_reads = 0

    def busy_states(self):
        self.health_reads += 1
        return dict(self.states)


def _setup(tmp_path, *, data=GIF_BYTES, cooldown=20, buffer=0.0):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path / "data"))
    plugin._settings = StickerManagerSettings(
        enabled=True, send=SendSettings(
            recent_dedup_count=0, cooldown_sec=cooldown, reply_tail_display_buffer_sec=buffer,
            defer_tool_sends=True,
        )
    )
    library = plugin._library
    library.load(force=True)
    sticker, _ = library.add(data=data, desc="test", tags=[])
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log = log_dir / "N.E.K.O_Main_20260930.log"  # Still active after midnight.
    log.write_bytes(_end())
    signals = TestLogs(plugin, [log_dir])
    plugin._sender._turn_end = signals
    return plugin, host, sticker, log, signals


def _send(plugin, sticker, *, role="K", source="tool", text=""):
    return plugin._sender.send(
        sticker, lanlan=role, settings=plugin._settings, source=source, text=text
    )


def _drain(plugin):
    return plugin._sender.drain(settings=plugin._settings)


@pytest.mark.parametrize("busy", [True, False, None])
@pytest.mark.parametrize("missing_log", [True, False])
def test_default_tool_submission_survives_new_input_and_tail_ticks(
    tmp_path, run_async, busy, missing_log,
):
    plugin, host, sticker, log, signals = _setup(tmp_path, buffer=7.0)
    plugin._settings = replace(
        plugin._settings, send=replace(plugin._settings.send, defer_tool_sends=False),
    )
    signals.states = {} if busy is None else {"K": busy}
    if missing_log:
        log.unlink()
    result = run_async(_send(plugin, sticker))
    assert result.ok and not result.queued
    assert len(host.push.calls) == 1
    assert host.push.calls[0]["ai_behavior"] == "blind"
    assert signals.health_reads == 0
    _append(log, _input() + _end())
    for _ in range(3):
        run_async(plugin.on_sticker_tail())
    assert len(host.push.calls) == 1
    assert plugin._library.get(sticker.id).use_count == 1
    usage = plugin._library.read_usage()
    assert len(usage) == 1 and usage[0]["ok"] is True
    assert not plugin._sender._pending


@pytest.fixture
def monotonic_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(sender_module.time, "monotonic", lambda: clock[0])
    return clock


def test_new_end_releases_after_reply_and_preserves_gif_and_caption(tmp_path, run_async):
    plugin, host, sticker, log, signals = _setup(tmp_path)
    result = run_async(_send(plugin, sticker, text="private caption"))
    assert result.ok and result.queued and not result.text_submitted
    assert not host.push.calls
    assert not plugin._library.read_usage()
    assert not plugin._sender._last_sent
    assert not run_async(_drain(plugin))  # Old marker at EOF is not replayed.
    assert signals.health_reads == 0  # Idle health alone cannot release a send.
    _append(log, _end())
    ((source, delivered),) = run_async(_drain(plugin))
    assert source == "tool" and delivered.ok and not delivered.queued and delivered.text_submitted
    caption_call, image_call = host.push.calls
    assert caption_call["parts"] == [{"type": "text", "text": "private caption"}]
    assert image_call["parts"] == [{"type": "image", "data": GIF_BYTES, "mime": "image/gif"}]
    for call in host.push.calls:
        assert call["visibility"] == ["chat"] and call["ai_behavior"] == "blind"
        assert call["target_lanlan"] == "K"
    assert not host.images.calls
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] and usage["text_len"] == len("private caption")
    assert "private caption" not in json.dumps(usage)
    assert plugin._library.get(sticker.id).use_count == 1
    assert not run_async(_drain(plugin))
    assert len(host.push.calls) == 2


@pytest.mark.parametrize("extra", [
    _end("other"), b"user_message: [K] turn_end analyze check: history=1\n",
    _end().replace(b"main_logic.cross_server", b"other_logger"),
    _end().replace(b" - INFO - ", b" - DEBUG - "),
])
def test_wrong_role_or_log_record_cannot_release(tmp_path, run_async, extra):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, extra)
    assert not run_async(_drain(plugin))
    assert not host.push.calls
    _append(log, _end())
    assert run_async(_drain(plugin))[0][1].ok


def test_partial_marker_waits_for_complete_line(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    _append(log, _end()[:-1])
    assert not run_async(_drain(plugin))
    assert not host.push.calls
    _append(log, b"\n")
    assert run_async(_drain(plugin))[0][1].ok


def test_arm_during_existing_partial_line_does_not_replay_it(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    log.write_bytes(_end()[:-1])
    run_async(_send(plugin, sticker))
    _append(log, b"\n")
    assert not run_async(_drain(plugin))
    assert not host.push.calls
    _append(log, _end())
    assert run_async(_drain(plugin))[0][1].ok


def test_independent_roles_and_pending_reservation(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path, cooldown=0)
    assert run_async(_send(plugin, sticker, role="A")).queued
    assert run_async(_send(plugin, sticker, role="B")).queued
    blocked = run_async(_send(plugin, sticker, role="A", source="panel"))
    assert not blocked.ok and blocked.code == "send_cooldown"
    _append(log, _end("B"))
    assert len(run_async(_drain(plugin))) == 1
    assert host.push.calls[0]["target_lanlan"] == "B"
    assert "A" in plugin._sender._pending
    _append(log, _end("A"))
    assert len(run_async(_drain(plugin))) == 1
    assert host.push.calls[1]["target_lanlan"] == "A"


def test_panel_remains_immediate_without_log(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    log.unlink()
    result = run_async(_send(plugin, sticker, source="panel"))
    assert result.ok and not result.queued and len(host.push.calls) == 1
    assert host.push.calls[0]["ai_behavior"] == "read"


def test_missing_log_fails_closed_and_does_not_hold_reservation(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    log.unlink()
    result = run_async(_send(plugin, sticker))
    assert not result.ok and result.code == "turn_end_unavailable"
    assert not host.push.calls and not plugin._sender._inflight


def test_rotation_reads_old_tail_then_new_active_file(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    _append(log, b"non-marker\n")
    log.rename(log.with_name(log.name + ".1"))
    log.write_bytes(_end())
    assert not run_async(_drain(plugin))  # Exhaust the renamed old file first.
    assert run_async(_drain(plugin))[0][1].ok
    assert len(host.push.calls) == 1


def test_end_in_rotated_backup_is_not_lost(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    _append(log, _end())
    log.rename(log.with_name(log.name + ".1"))
    log.write_bytes(b"new active\n")
    assert not run_async(_drain(plugin))  # Inspect the new active file before releasing.
    assert run_async(_drain(plugin))[0][1].ok
    assert len(host.push.calls) == 1


@pytest.mark.parametrize("change", ["truncate", "remove", "replace", "rewrite"])
def test_lost_cursor_cancels_without_early_push(tmp_path, run_async, change):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    if change == "truncate":
        log.write_bytes(b"")
    elif change == "replace":
        log.rename(log.with_name("discarded.log"))
        log.write_bytes(_end())
    elif change == "rewrite":
        log.write_bytes(b"x" * log.stat().st_size + _end())
    else:
        log.unlink()
    result = run_async(_drain(plugin))[0][1]
    assert not result.ok and result.code == "turn_end_lost"
    assert not host.push.calls and not plugin._sender._pending


def test_unknown_health_does_not_block_valid_log_completion(tmp_path, run_async):
    plugin, host, sticker, log, signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    signals.states = {}
    assert not run_async(_drain(plugin))
    assert not host.push.calls
    _append(log, _end())
    assert run_async(_drain(plugin))[0][1].ok


def test_new_response_busy_cancels_obsolete_sticker(tmp_path, run_async):
    plugin, host, sticker, log, signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    _append(log, _end())
    signals.states = {"K": True}
    result = run_async(_drain(plugin))[0][1]
    assert not result.ok and result.code == "turn_superseded"
    assert not host.push.calls


def test_multiple_ends_cancel_ambiguous_ticket(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    _append(log, _end() + _end())
    result = run_async(_drain(plugin))[0][1]
    assert not result.ok and result.code == "turn_end_lost"
    assert not host.push.calls


def test_timeout_drops_instead_of_sending_early(tmp_path, run_async, monkeypatch):
    plugin, host, sticker, _log, _signals = _setup(tmp_path)
    clock = [10.0]
    monkeypatch.setattr(sender_module.time, "monotonic", lambda: clock[0])
    run_async(_send(plugin, sticker))
    clock[0] += 301
    result = run_async(_drain(plugin))[0][1]
    assert not result.ok and result.code == "turn_end_timeout"
    assert not host.push.calls and not plugin._sender._last_sent
    assert plugin._library.read_usage()[0]["ok"] is False


@pytest.mark.parametrize("edit,code", [
    ("disable_plugin", "not_enabled"), ("disable_sticker", "sticker_disabled"),
    ("remove_sticker", "sticker_file_missing"),
    ("remove_file", "sticker_file_missing"),
])
def test_pending_is_revalidated_before_delivery(tmp_path, run_async, edit, code):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    if edit == "disable_plugin":
        plugin._settings = replace(plugin._settings, enabled=False)
    elif edit == "disable_sticker":
        plugin._library.update(sticker.id, disabled=True)
    elif edit == "remove_file":
        plugin._library.image_path(sticker).unlink()
    else:
        plugin._library.remove(sticker.id)
    _append(log, _end())
    result = run_async(_drain(plugin))[0][1]
    assert not result.ok and result.code == code
    assert not host.push.calls and not plugin._sender._last_sent


@pytest.mark.parametrize("raises", [False, True])
def test_failed_deferred_transport_does_not_count_success(tmp_path, run_async, raises):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    if raises:
        def push(**kwargs):
            raise RuntimeError("transport")
        host.push_message = push
    else:
        host.push.reject_reason = "backpressure"
    _append(log, _end())
    result = run_async(_drain(plugin))[0][1]
    assert not result.ok
    assert not plugin._sender._last_sent and not plugin._sender._inflight
    assert plugin._library.get(sticker.id).use_count == 0
    assert plugin._library.read_usage()[0]["ok"] is False


def test_tool_receipt_and_timer_count_real_delivery_once(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    result = run_async(plugin.tool_sticker_send(
        sticker_id=sticker.id, text="caption", _ctx={"lanlan_name": "K"},
    ))
    assert result["ok"] and result["queued"] == sticker.id and not result["sent"]
    assert plugin._runstats.tool_calls == 1 and plugin._runstats.sent == 0
    assert not host.push.calls
    _append(log, _end())
    run_async(plugin.on_sticker_tail())
    run_async(plugin.on_sticker_tail())
    assert plugin._runstats.sent == 1 and plugin._runstats.tool_calls == 1
    assert len(host.push.calls) == 2
    assert plugin._library.get(sticker.id).use_count == 1
    assert len(plugin._library.read_usage()) == 1


def test_disable_then_reenable_does_not_replay_pending(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    host.config.data = {"sticker_manager": {"enabled": False}}
    run_async(plugin.on_config_change())
    assert not plugin._sender._pending and plugin._runstats.refused == 1
    host.config.data = {"sticker_manager": {"enabled": True}}
    run_async(plugin.on_config_change())
    _append(log, _end())
    run_async(plugin.on_sticker_tail())
    assert not host.push.calls


def test_shutdown_drops_queue_and_prevents_new_sends(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    run_async(plugin.on_shutdown())
    _append(log, _end())
    run_async(plugin.on_sticker_tail())
    assert not host.push.calls and plugin._runstats.refused == 1
    assert run_async(_send(plugin, sticker)).code == "send_stopped"


def test_disable_during_upload_invalidates_preparing_send(tmp_path, run_async):
    plugin, host, sticker, _log, _signals = _setup(tmp_path, data=PNG_BYTES + b"0" * (300 * 1024))

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def upload(*args, **kwargs):
            started.set()
            await release.wait()
            return {"type": "image", "url": "https://stub.local/image.jpg"}

        host.images.upload = upload
        task = asyncio.create_task(_send(plugin, sticker))
        await started.wait()
        plugin._sender.cancel_pending(settings=plugin._settings)
        release.set()
        return await task

    assert run_async(scenario()).code == "not_enabled"
    assert not host.push.calls and not plugin._sender._pending


def test_drain_lock_prevents_duplicate_delivery_across_threads(tmp_path, run_async):
    plugin, host, sticker, log, signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    _append(log, _end())
    entered, release = threading.Event(), threading.Event()

    def states():
        entered.set()
        assert release.wait(5)
        return {"K": False}

    signals.busy_states = states
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(run_async, _drain(plugin))
        try:
            assert entered.wait(5)
            assert not run_async(_drain(plugin))
        finally:
            release.set()
        assert first.result(timeout=5)[0][1].ok
    assert len(host.push.calls) == 1


@pytest.mark.parametrize("busy,queued", [(False, False), (True, True), (None, True)])
def test_agent_defers_only_when_reply_active_or_unknown(tmp_path, run_async, busy, queued):
    plugin, host, sticker, log, signals = _setup(tmp_path)
    signals.states = {} if busy is None else {"K": busy}
    result = run_async(_send(plugin, sticker, source="agent"))
    assert result.ok and result.queued is queued
    assert bool(host.push.calls) is not queued
    if queued:
        _append(log, _end())
        signals.states = {"K": False}
        assert run_async(_drain(plugin))[0][1].ok
    assert host.push.calls[0]["ai_behavior"] == "read"


def test_queue_is_bounded(tmp_path, run_async):
    plugin, host, sticker, _log, _signals = _setup(tmp_path)
    for n in range(8):
        assert run_async(_send(plugin, sticker, role=str(n))).queued
    result = run_async(_send(plugin, sticker, role="overflow"))
    assert not result.ok and result.code == "send_queue_full"
    assert len(plugin._sender._pending) == 8 and not host.push.calls


def test_storage_environment_has_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    assert log_directories(tmp_path / "data")[0] == tmp_path / "logs"


def test_real_plugin_assembly_enables_tail_adapter(tmp_path):
    from conftest import register_plugin_package

    plugin = register_plugin_package().StickerManagerPlugin(FakeHostContext(data_root=tmp_path))
    assert isinstance(plugin._sender._turn_end, TurnEndLogs)


@pytest.mark.parametrize("payload,expected", [
    ({"current": {"is_responding": {"K": False, "A": True, "bad": 0}}}, {"K": False, "A": True}),
    ({"is_responding": {"K": False}}, {}),
    ({"current": {"is_responding": None}}, {}),
    ({"current": {"is_responding": {"K": "false"}}}, {}),
    ([], {}),
])
def test_health_uses_existing_envelope_and_explicit_booleans(tmp_path, monkeypatch, payload, expected):
    from sticker_manager.services import turn_end

    plugin, _host = build_plugin(FakeHostContext(data_root=tmp_path))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            assert limit == 1024 * 1024 + 1
            return json.dumps(payload).encode("utf-8")

    class Opener:
        def open(self, url, *, timeout):
            assert url == "http://127.0.0.1:48911/api/debug/health"
            assert timeout == 0.6
            return Response()

    monkeypatch.setattr(turn_end.urllib.request, "build_opener", lambda *args: Opener())
    assert TurnEndLogs(plugin, []).busy_states() == expected


def test_large_partial_lines_do_not_grow_unbounded(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    run_async(_send(plugin, sticker))
    _append(log, b"x" * (300 * 1024) + b"\n" + _end())
    assert not run_async(_drain(plugin))
    assert len(plugin._sender._pending["K"].ticket.partial) <= 64 * 1024
    assert run_async(_drain(plugin))[0][1].ok
    assert len(host.push.calls) == 1


def test_queue_and_release_do_not_roll_probability_twice(tmp_path, run_async, monkeypatch):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    plugin._settings = replace(
        plugin._settings, send=replace(plugin._settings.send, probability=0.5, probability_reuse_sec=0)
    )
    rolls = []

    class Random:
        def random(self):
            rolls.append(1)
            return 0.0

    monkeypatch.setattr(sender_module, "_RNG", Random())
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end())
    assert run_async(_drain(plugin))[0][1].ok
    assert len(rolls) == 1 and len(host.push.calls) == 1


def test_cooldown_starts_at_delivery_not_queue_time(tmp_path, run_async, monkeypatch):
    plugin, _host, sticker, log, _signals = _setup(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(sender_module.time, "time", lambda: clock[0])
    assert run_async(_send(plugin, sticker)).queued
    clock[0] += 30.0
    _append(log, _end())
    assert run_async(_drain(plugin))[0][1].ok
    assert plugin._sender.cooldown_remaining("K", plugin._settings, now=1030.0) == 20.0


def test_display_buffer_default_is_two_seconds():
    assert SendSettings().reply_tail_display_buffer_sec == 2.0


def test_buffer_waits_seven_seconds_from_first_observed_end_without_reset(
    tmp_path, run_async, monotonic_clock,
):
    plugin, host, sticker, log, signals = _setup(tmp_path, buffer=7.0)
    assert run_async(_send(plugin, sticker, text="caption")).queued
    monotonic_clock[0] = 110.0
    _append(log, _end())
    assert not run_async(_drain(plugin))
    assert plugin._sender._pending["K"].end_observed_at == 110.0
    assert not host.push.calls and not plugin._sender._last_sent
    assert not plugin._library.read_usage()
    for tick in [111.0, 112.0, 116.999]:
        monotonic_clock[0] = tick
        assert not run_async(_drain(plugin))
        assert plugin._sender._pending["K"].end_observed_at == 110.0
        assert not host.push.calls
    monotonic_clock[0] = 117.0
    ((source, result),) = run_async(_drain(plugin))
    assert source == "tool" and result.ok and not result.queued and result.text_submitted
    assert signals.health_reads == 5
    assert host.push.calls[0]["parts"] == [{"type": "text", "text": "caption"}]
    assert host.push.calls[1]["parts"] == [
        {"type": "image", "data": GIF_BYTES, "mime": "image/gif"},
    ]
    assert all(call["ai_behavior"] == "blind" for call in host.push.calls)
    assert not plugin._sender._pending and not plugin._sender._inflight
    assert len(plugin._library.read_usage()) == 1
    assert not run_async(_drain(plugin))
    assert len(host.push.calls) == 2


def test_zero_buffer_releases_on_first_observed_end(tmp_path, run_async, monotonic_clock):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=0.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end())
    assert run_async(_drain(plugin))[0][1].ok
    assert len(host.push.calls) == 1


@pytest.mark.parametrize("before_end", [False, True])
def test_new_same_role_input_cancels_and_releases_reservation(
    tmp_path, run_async, monotonic_clock, before_end,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    assert run_async(_send(plugin, sticker, text="caption")).queued
    if not before_end:
        _append(log, _end())
        assert not run_async(_drain(plugin))
        monotonic_clock[0] += 1.0
    _append(log, _input())
    ((source, result),) = run_async(_drain(plugin))
    assert source == "tool" and result.code == "turn_superseded" and not result.ok
    assert not result.text_submitted
    assert not host.push.calls and not plugin._sender._last_sent
    assert not plugin._sender._inflight and not plugin._sender._pending
    assert plugin._library.get(sticker.id).use_count == 0
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is False and usage["code"] == "turn_superseded"
    assert "text_submitted" not in usage
    _append(log, _end())
    assert not run_async(_drain(plugin))
    assert run_async(_send(plugin, sticker)).queued


def test_end_and_new_input_in_one_poll_cannot_release_even_with_zero_buffer(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=0.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end() + _input())
    assert run_async(_drain(plugin))[0][1].code == "turn_superseded"
    assert not host.push.calls


@pytest.mark.parametrize("extra", [
    _input("other"), _input(length=0),
    _input().replace(b" - INFO - ", b" - DEBUG - "),
    _input().replace(b"main_logic.core", b"other_logger"),
    b"user text contains [K] voice user_transcript session=X ws_connected=True len=3\n",
])
def test_other_role_or_untrusted_input_does_not_cancel_buffer(
    tmp_path, run_async, monotonic_clock, extra,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end())
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 1.0
    _append(log, extra)
    assert not run_async(_drain(plugin))
    assert not host.push.calls and "K" in plugin._sender._pending
    monotonic_clock[0] += 6.0
    assert run_async(_drain(plugin))[0][1].ok
    assert len(host.push.calls) == 1


def test_partial_new_input_is_consumed_on_later_buffer_tick(
    tmp_path, run_async, monotonic_clock,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end() + _input()[:-1])
    assert not run_async(_drain(plugin))
    _append(log, b"\n")
    assert run_async(_drain(plugin))[0][1].code == "turn_superseded"
    assert not host.push.calls


def test_buffer_cancels_on_later_end_without_restarting_it(
    tmp_path, run_async, monotonic_clock,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end())
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 2.0
    _append(log, _end())
    assert run_async(_drain(plugin))[0][1].code == "turn_end_lost"
    assert not host.push.calls and not plugin._sender._inflight


def test_known_busy_state_during_buffer_cancels_without_waiting(
    tmp_path, run_async, monotonic_clock,
):
    plugin, host, sticker, log, signals = _setup(tmp_path, buffer=7.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end())
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 1.0
    signals.states["K"] = True
    assert run_async(_drain(plugin))[0][1].code == "turn_superseded"
    assert not host.push.calls and not plugin._sender._last_sent


@pytest.mark.parametrize("edit,code", [
    ("disable_plugin", "not_enabled"), ("disable_sticker", "sticker_disabled"),
    ("remove_sticker", "sticker_file_missing"), ("remove_file", "sticker_file_missing"),
    ("change_zone", "sticker_zone_changed"),
])
def test_pending_buffer_is_revalidated_each_tick(
    tmp_path, run_async, monotonic_clock, edit, code,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end())
    assert not run_async(_drain(plugin))
    if edit == "disable_plugin":
        plugin._settings = replace(plugin._settings, enabled=False)
    elif edit == "disable_sticker":
        plugin._library.update(sticker.id, disabled=True)
    elif edit == "remove_sticker":
        plugin._library.remove(sticker.id)
    elif edit == "remove_file":
        plugin._library.image_path(sticker).unlink()
    else:
        zone, error = plugin._library.create_zone("other")
        assert zone and not error
        assert plugin._library.activate_zone(zone) == (True, "")
    monotonic_clock[0] += 1.0
    assert run_async(_drain(plugin))[0][1].code == code
    assert not host.push.calls and not plugin._sender._inflight


def test_buffer_does_not_reroll_probability(tmp_path, run_async, monotonic_clock, monkeypatch):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    plugin._settings = replace(
        plugin._settings, send=replace(plugin._settings.send, probability=0.5, probability_reuse_sec=0),
    )
    rolls = []

    class Random:
        def random(self):
            rolls.append(1)
            return 0.0

    monkeypatch.setattr(sender_module, "_RNG", Random())
    assert run_async(_send(plugin, sticker, text="caption")).queued
    _append(log, _end())
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 3.0
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 4.0
    assert run_async(_drain(plugin))[0][1].ok
    assert len(rolls) == 1 and len(host.push.calls) == 2


def test_force_bypasses_probability_before_and_after_buffer(
    tmp_path, run_async, monotonic_clock, monkeypatch,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    plugin._settings = replace(
        plugin._settings, send=replace(plugin._settings.send, probability=0.0, probability_reuse_sec=0),
    )

    class Random:
        def random(self):
            raise AssertionError("force must not roll")

    monkeypatch.setattr(sender_module, "_RNG", Random())
    assert run_async(plugin._sender.send(
        sticker, lanlan="K", settings=plugin._settings, source="tool", force=True,
    )).queued
    _append(log, _end())
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 7.0
    assert run_async(_drain(plugin))[0][1].ok
    assert len(host.push.calls) == 1


def test_new_input_beyond_bounded_read_is_seen_before_zero_buffer_release(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=0.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end() + b"padding\n" * (40 * 1024) + _input())
    assert not run_async(_drain(plugin))
    assert not host.push.calls
    assert run_async(_drain(plugin))[0][1].code == "turn_superseded"
    assert not host.push.calls


def test_new_input_in_active_file_after_rotation_cancels_backup_end(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=0.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end())
    log.rename(log.with_name(log.name + ".1"))
    log.write_bytes(_input())
    assert not run_async(_drain(plugin))
    assert run_async(_drain(plugin))[0][1].code == "turn_superseded"
    assert not host.push.calls


def test_error_log_is_never_armed_instead_of_active_main_log(tmp_path, run_async):
    plugin, host, sticker, log, signals = _setup(tmp_path)
    error = log.with_name("N.E.K.O_Main_error.log")
    error.write_bytes(b"new error diagnostic\n")
    assert signals.arm("K").path == log
    assert run_async(_send(plugin, sticker)).queued
    _append(error, _end())
    assert not run_async(_drain(plugin))
    _append(log, _end())
    assert run_async(_drain(plugin))[0][1].ok
    assert len(host.push.calls) == 1


def test_only_error_log_fails_closed_without_holding_reservation(tmp_path, run_async):
    plugin, host, sticker, log, _signals = _setup(tmp_path)
    log.unlink()
    log.with_name("N.E.K.O_Main_error.log").write_bytes(_end())
    result = run_async(_send(plugin, sticker))
    assert result.code == "turn_end_unavailable" and not result.ok
    assert not host.push.calls and not plugin._sender._inflight


@pytest.mark.parametrize("source,busy,queued", [
    ("panel", True, False), ("agent", False, False), ("agent", True, True),
])
def test_buffered_configuration_preserves_panel_and_agent_read_contract(
    tmp_path, run_async, monotonic_clock, source, busy, queued,
):
    plugin, host, sticker, log, signals = _setup(tmp_path, buffer=7.0)
    signals.states["K"] = busy
    result = run_async(_send(plugin, sticker, source=source, text="caption"))
    assert result.ok and result.queued is queued
    if queued:
        assert not host.push.calls
        signals.states["K"] = False
        _append(log, _end())
        assert not run_async(_drain(plugin))
        monotonic_clock[0] += 7.0
        assert run_async(_drain(plugin))[0][1].ok
    assert len(host.push.calls) == 2
    assert all(call["ai_behavior"] == "read" for call in host.push.calls)
    assert host.push.calls[0]["parts"] == [{"type": "text", "text": "caption"}]
    assert [part["type"] for part in host.push.calls[1]["parts"]] == ["image"]


def test_timer_counts_buffered_supersession_once_not_as_success(
    tmp_path, run_async, monotonic_clock,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    assert result["ok"] and result["queued"] == sticker.id
    _append(log, _end())
    run_async(plugin.on_sticker_tail())
    assert plugin._runstats.sent == 0 and plugin._runstats.refused == 0
    _append(log, _input())
    run_async(plugin.on_sticker_tail())
    run_async(plugin.on_sticker_tail())
    assert plugin._runstats.sent == 0 and plugin._runstats.refused == 1
    assert plugin._runstats.tool_calls == 1
    assert not host.push.calls


def test_partial_input_at_buffer_deadline_cannot_release_until_complete(
    tmp_path, run_async, monotonic_clock,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=7.0)
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _end())
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 7.0
    _append(log, _input()[:-1])
    assert not run_async(_drain(plugin))
    assert not host.push.calls and "K" in plugin._sender._pending
    _append(log, b"\n")
    assert run_async(_drain(plugin))[0][1].code == "turn_superseded"
    assert not host.push.calls and not plugin._sender._inflight


@pytest.mark.parametrize("input_gap,observed_lag,handler_delay", [
    (5.0, 0.8, 0.2), (8.0, 1.0, 0.4), (8.0, 0.2, 0.6),
])
def test_default_buffer_delivers_before_logged_five_or_eight_second_next_input(
    tmp_path, run_async, monotonic_clock, input_gap, observed_lag, handler_delay,
):
    plugin, host, sticker, log, signals = _setup(
        tmp_path, buffer=SendSettings().reply_tail_display_buffer_sec,
    )
    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    assert result["ok"] and result["queued"] == sticker.id and not result["sent"]
    assert not host.push.calls and plugin._runstats.sent == 0
    run_async(plugin.on_sticker_tail())  # A timer tick before the end cannot send.

    def delayed_states():
        signals.health_reads += 1
        monotonic_clock[0] += handler_delay
        return dict(signals.states)

    signals.busy_states = delayed_states
    ended_at = 110.0
    _append(log, _end())
    monotonic_clock[0] = ended_at + observed_lag
    run_async(plugin.on_sticker_tail())
    assert not host.push.calls
    assert plugin._sender._pending["K"].end_observed_at == ended_at + observed_lag

    delivered_at = None
    for _ in range(3):
        # The host waits one second after completing each timer handler.
        monotonic_clock[0] += 1.0
        run_async(plugin.on_sticker_tail())
        if host.push.calls and delivered_at is None:
            delivered_at = monotonic_clock[0]
    assert delivered_at is not None and delivered_at < ended_at + input_gap
    assert len(host.push.calls) == 1 and host.push.calls[0]["ai_behavior"] == "blind"
    assert plugin._runstats.sent == 1 and plugin._runstats.refused == 0
    assert plugin._library.get(sticker.id).use_count == 1
    assert not plugin._sender._pending and not plugin._sender._inflight
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is True

    monotonic_clock[0] = ended_at + input_gap
    _append(log, _input())
    signals.states["K"] = True
    run_async(plugin.on_sticker_tail())
    assert len(host.push.calls) == 1
    assert plugin._runstats.sent == 1 and plugin._runstats.refused == 0
    assert len(plugin._library.read_usage()) == 1


def test_two_second_default_still_cancels_very_fast_new_input(
    tmp_path, run_async, monotonic_clock,
):
    plugin, host, sticker, log, _signals = _setup(
        tmp_path, buffer=SendSettings().reply_tail_display_buffer_sec,
    )
    result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    assert result["queued"] == sticker.id
    _append(log, _end())
    run_async(plugin.on_sticker_tail())
    monotonic_clock[0] += 0.5
    _append(log, _input())
    run_async(plugin.on_sticker_tail())
    assert not host.push.calls and not plugin._sender._last_sent
    assert not plugin._sender._inflight and not plugin._sender._pending
    assert plugin._runstats.sent == 0 and plugin._runstats.refused == 1
    assert plugin._library.get(sticker.id).use_count == 0
    assert run_async(_send(plugin, sticker)).queued


def test_tail_timing_logs_once_with_captured_buffer_and_without_caption(
    tmp_path, run_async, monotonic_clock, caplog,
):
    plugin, _host, sticker, log, _signals = _setup(tmp_path, buffer=2.0)
    caplog.set_level("INFO", logger="sticker_manager.test")
    assert run_async(_send(plugin, sticker, text="private caption")).queued
    plugin._settings = replace(
        plugin._settings, send=replace(plugin._settings.send, reply_tail_display_buffer_sec=7.0),
    )
    _append(log, _end())
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 0.5
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 1.25
    _append(log, _input())
    assert run_async(_drain(plugin))[0][1].code == "turn_superseded"
    queued = [message for message in caplog.messages if message.startswith("sticker queued:")]
    observed = [message for message in caplog.messages if message.startswith("sticker tail observed:")]
    rejected = [message for message in caplog.messages if message.startswith("sticker deferred rejected:")]
    assert queued == [f"sticker queued: id={sticker.id} source=tool buffer_sec=2"]
    assert observed == [f"sticker tail observed: id={sticker.id} buffer_sec=2"]
    assert rejected == [
        f"sticker deferred rejected: id={sticker.id} reason=turn_superseded end_wait_sec=1.75",
    ]
    assert "private caption" not in caplog.text


def test_cancellation_before_end_logs_no_end_wait(tmp_path, run_async, caplog):
    plugin, _host, sticker, log, _signals = _setup(tmp_path, buffer=2.0)
    caplog.set_level("INFO", logger="sticker_manager.test")
    assert run_async(_send(plugin, sticker)).queued
    _append(log, _input())
    assert run_async(_drain(plugin))[0][1].code == "turn_superseded"
    assert not any(message.startswith("sticker tail observed:") for message in caplog.messages)
    assert any(
        message == (
            f"sticker deferred rejected: id={sticker.id} "
            "reason=turn_superseded end_wait_sec=no-end"
        ) for message in caplog.messages
    )


@pytest.mark.parametrize("raises", [False, True])
def test_deferred_pair_partial_failure_logs_and_does_not_retry(
    tmp_path, run_async, monotonic_clock, caplog, raises,
):
    plugin, host, sticker, log, _signals = _setup(tmp_path, buffer=2.0)
    caplog.set_level("INFO", logger="sticker_manager.test")
    queued = run_async(_send(plugin, sticker, text="private caption"))
    assert queued.queued and not queued.text_submitted
    calls = []

    def push(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"submitted": True}
        if raises:
            raise RuntimeError("transport")
        return {"submitted": False, "reason": "backpressure"}

    host.push_message = push
    _append(log, _end())
    assert not run_async(_drain(plugin))
    monotonic_clock[0] += 2.0
    ((source, result),) = run_async(_drain(plugin))
    assert source == "tool" and not result.ok and result.text_submitted
    assert result.code == ("transport_unavailable" if raises else "backpressure")
    assert len(calls) == 2 and all(call["ai_behavior"] == "blind" for call in calls)
    assert not plugin._sender._pending and not plugin._sender._inflight
    assert not plugin._sender._last_sent
    assert plugin._library.get(sticker.id).use_count == 0
    (usage,) = plugin._library.read_usage()
    assert usage["ok"] is False and usage["code"] == result.code and usage["text_submitted"]
    assert "private caption" not in json.dumps(usage)
    assert any(
        message == (
            f"sticker deferred rejected: id={sticker.id} reason={result.code} "
            "end_wait_sec=2.00 text_submitted=True"
        ) for message in caplog.messages
    )
    assert "private caption" not in caplog.text
    _append(log, _end())
    assert not run_async(_drain(plugin))
    assert len(calls) == 2 and len(plugin._library.read_usage()) == 1
