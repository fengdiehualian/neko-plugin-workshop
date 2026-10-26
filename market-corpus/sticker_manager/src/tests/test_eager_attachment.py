"""Exercise reply observation, model selection and actual image submission together."""

from __future__ import annotations

import asyncio
import json
import tomllib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import PNG_BYTES, FakeBus, FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.attachment_selection import conversation_turn, parse_selection, selection_candidates
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.services import eager_attachment
from sticker_manager.services.turn_end import TurnEndLogs


def _setup(tmp_path, monkeypatch, tier="eager", image=PNG_BYTES, turns=1):
    clock = [1000.0]
    monkeypatch.setattr(eager_attachment.time, "time", lambda: clock[0])
    monkeypatch.setattr(eager_attachment, "CHECK_INTERVAL_SEC", 0.001)
    host = FakeHostContext(
        data_root=tmp_path, bus=FakeBus(),
        config=FakeConfig({"sticker_manager": {
            "enabled": True, "send": {
                "eagerness": tier, "cooldown_sec": 0, "recent_dedup_count": 0,
                "eager_attachment_turns": turns,
            },
        }}),
    )
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings.from_config(host.config.data)
    assert plugin._library.load().ok
    sticker, error = plugin._library.add(data=image, desc="轻松表示赞同", tags=["赞同"])
    assert not error
    logfile = tmp_path / "N.E.K.O_Main_20261017.log"
    logfile.write_text("2026-10-17 15:00:00 - N.E.K.O.Main - INFO - initial\n", encoding="utf-8")
    plugin._sender._turn_end = TurnEndLogs(plugin, [tmp_path])
    requests = []

    async def create(**request):
        requests.append(request)
        return _response(sticker.id)

    async def get_client():
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    host.models = SimpleNamespace(get_client=get_client)

    def local_json(url, *, timeout):
        assert url.startswith("http://127.0.0.1:") and timeout == 0.5
        if url.endswith("/api/characters/current_catgirl"):
            return {"current_catgirl": "YUI"}
        assert url.endswith("/api/debug/health")
        return {"current": {"is_responding": {"YUI": False}}}

    monkeypatch.setattr(eager_attachment, "read_local_json", local_json)
    return plugin, host, sticker, clock, requests, logfile


def _response(sid="", *, decision="send", reason="selected", finish="stop", content=None):
    if content is None:
        content = json.dumps({"decision": decision, "sticker_id": sid, "reason": reason})
    return SimpleNamespace(choices=[
        SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content)),
    ])


def _reply(host, clock, number, *, text="好呀，听你的！", user="陪我聊聊吧", role="YUI"):
    clock[0] += 3
    start = clock[0] - 1
    for kind, at, body, suffix in (
        ("user_message", start - 1, user, "user"),
        ("assistant_message", start, text, "assistant"),
    ):
        host.bus.conversations.records.append({
            "conversation_id": f"reply-{number}-{suffix}", "source": "main_logic.core",
            "timestamp": clock[0] - (0.2 if suffix == "user" else 0.1),
            "content": body, "lanlan_name": role, "turn_type": kind,
            "metadata": {"ts": at, **({"ts_end": clock[0]} if suffix == "assistant" else {})},
        })
    host.bus.memory.records.append({
        "type": "user_message", "lanlan": role, "_ts": start - 1, "content": user,
    })


def _append(logfile, message):
    with logfile.open("a", encoding="utf-8") as stream:
        stream.write(f"2026-10-17 15:00:01 - N.E.K.O.Main.main_logic.core - INFO - {message}\n")


@pytest.mark.parametrize("turns", [0, 1])
def test_each_unillustrated_reply_selects_once_and_pushes_only_image(tmp_path, monkeypatch, run_async, turns):
    plugin, host, _, clock, requests, _ = _setup(tmp_path, monkeypatch, turns=turns)

    async def scenario():
        assert (await plugin.on_eager_attachment()).value["status"] == "ready"
        for n in range(1, 5):
            _reply(host, clock, n)
            result = (await plugin.on_eager_attachment()).value
            assert result["status"] == "submitted"
            await plugin.on_eager_attachment()
            assert len(requests) == len(host.push.calls) == n
            assert plugin._eager_attachment.snapshot()["reply_gap"] == 0
        assert plugin._runstats.sent == 4
        assert plugin._runstats.tool_calls == 0
        assert all(call["visibility"] == ["chat"] and call["ai_behavior"] == "blind" for call in host.push.calls)
        assert all([part["type"] for part in call["parts"]] == ["image"] for call in host.push.calls)
        assert all(row["source"] == "eager" and row["text_len"] == 0 for row in plugin._library.read_usage())
        assert all(request["model"] == "eager_attachment" and not request.get("tools") for request in requests)

    run_async(scenario())


@pytest.mark.parametrize("turns", range(2, 11))
def test_configured_gap_waits_then_resets_after_submission(tmp_path, monkeypatch, run_async, turns):
    plugin, host, _, clock, requests, _ = _setup(tmp_path, monkeypatch, turns=turns)

    async def scenario():
        await plugin.on_eager_attachment()
        for n in range(1, turns * 2 + 1):
            _reply(host, clock, n)
            result = (await plugin.on_eager_attachment()).value
            assert result["status"] == ("submitted" if n % turns == 0 else "waiting")
            await plugin.on_eager_attachment()
            assert len(requests) == len(host.push.calls) == n // turns
            assert plugin._eager_attachment.snapshot()["reply_gap"] == n % turns

    run_async(scenario())


@pytest.mark.parametrize("turns", [0, 1, 2, 10])
def test_no_match_retries_only_for_next_reply_without_waiting_another_full_gap(
    tmp_path, monkeypatch, run_async, turns,
):
    plugin, host, sticker, clock, _, _ = _setup(tmp_path, monkeypatch, turns=turns)
    calls = []

    async def select(payload):
        calls.append(payload["reply"])
        return _response(decision="skip", reason="no_match") if len(calls) == 1 else _response(sticker.id)

    monkeypatch.setattr(plugin._eager_attachment, "_model_select", select)

    async def scenario():
        await plugin.on_eager_attachment()
        for n in range(1, max(1, turns) + 1):
            _reply(host, clock, n, text=f"reply {n}")
            result = await plugin.on_eager_attachment()
        assert result.value["status"] == "no_match"
        await plugin.on_eager_attachment()
        assert len(calls) == 1 and not host.push.calls
        _reply(host, clock, 20, text="latest reply")
        assert (await plugin.on_eager_attachment()).value["status"] == "submitted"
        assert calls[-1] == "latest reply" and len(calls) == 2
        assert len(host.push.calls) == 1

    run_async(scenario())


@pytest.mark.parametrize("tier,enabled", [("natural", True), ("reserved", True), ("eager", False)])
def test_other_tiers_and_disabled_do_not_read_or_call(tmp_path, monkeypatch, run_async, tier, enabled):
    plugin, host, _, clock, requests, _ = _setup(tmp_path, monkeypatch, tier)
    plugin._settings = replace(plugin._settings, enabled=enabled)
    _reply(host, clock, 1)
    assert run_async(plugin.on_eager_attachment()).value["status"] == "inactive"
    assert not host.bus.conversations.calls and not host.bus.memory.calls
    assert not requests and not host.push.calls


def test_bootstrap_does_not_send_for_retained_history(tmp_path, monkeypatch, run_async):
    plugin, host, _, clock, requests, _ = _setup(tmp_path, monkeypatch)
    for n in range(1, 7):
        _reply(host, clock, n)
    assert run_async(plugin.on_eager_attachment()).value["status"] == "ready"
    assert not requests and not host.push.calls
    _reply(host, clock, 7)
    assert run_async(plugin.on_eager_attachment()).value["status"] == "submitted"
    assert len(requests) == len(host.push.calls) == 1


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("turns", [0, 1])
def test_normal_tool_delivery_or_pending_send_prevents_fallback(tmp_path, monkeypatch, run_async, pending, turns):
    plugin, host, sticker, clock, requests, _ = _setup(tmp_path, monkeypatch, turns=turns)

    async def scenario():
        await plugin.on_eager_attachment()
        _reply(host, clock, 1)
        if pending:
            plugin._sender._inflight.add("YUI")
        else:
            result = await plugin._sender.send(
                sticker, lanlan="YUI", settings=plugin._settings, source="tool",
            )
            assert result.ok
        await plugin.on_eager_attachment()
        assert not requests
        if pending:
            assert plugin._eager_attachment.snapshot()["reply_gap"] == 1
        else:
            assert plugin._eager_attachment.snapshot()["reply_gap"] == 0

    run_async(scenario())


@pytest.mark.parametrize("kind", ["text_only", "serious_task", "no_match", "invalid_id", "truncated"])
def test_skip_invalid_and_truncated_results_never_send(tmp_path, monkeypatch, run_async, kind):
    plugin, host, _, clock, _, _ = _setup(tmp_path, monkeypatch)

    async def select(_payload):
        if kind in {"text_only", "serious_task"}:
            return _response(decision="ineligible", reason=kind)
        if kind == "no_match":
            return _response(decision="skip", reason=kind)
        return _response("not-a-candidate", finish="length" if kind == "truncated" else "stop")

    monkeypatch.setattr(plugin._eager_attachment, "_model_select", select)

    async def scenario():
        await plugin.on_eager_attachment()
        _reply(host, clock, 1)
        result = await plugin.on_eager_attachment()
        assert result.value["status"] == (kind if kind in {"text_only", "serious_task", "no_match"} else "invalid_result")
        assert not host.push.calls
        assert plugin._eager_attachment.snapshot()["reply_gap"] == (0 if kind in {"text_only", "serious_task"} else 1)

    run_async(scenario())


@pytest.mark.parametrize("mutation", [
    "new_input", "session_end", "tier", "disable", "turns", "zone", "shutdown", "tool", "caption", "deleted",
])
def test_inflight_selection_is_cancelled_or_discarded(tmp_path, monkeypatch, run_async, mutation):
    plugin, host, sticker, clock, _, logfile = _setup(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def select(_payload):
        entered.set()
        await release.wait()
        return _response(sticker.id)

    monkeypatch.setattr(plugin._eager_attachment, "_model_select", select)

    async def scenario():
        await plugin.on_eager_attachment()
        _reply(host, clock, 1)
        polling = asyncio.create_task(plugin.on_eager_attachment())
        await entered.wait()
        if mutation == "new_input":
            host.bus.memory.records.append({
                "type": "user_message", "lanlan": "YUI", "_ts": clock[0] + 1, "content": "换话题",
            })
        elif mutation == "session_end":
            _append(logfile, "Voice lifecycle received action=end_session")
        elif mutation in {"tier", "disable", "turns"}:
            if mutation == "tier":
                host.config.data["sticker_manager"]["send"]["eagerness"] = "natural"
            elif mutation == "turns":
                assert (await plugin.set_eager_attachment_turns_entry(turns=10)).is_ok()
            else:
                host.config.data["sticker_manager"]["enabled"] = False
            await plugin.on_config_change()
        elif mutation == "zone":
            zone, error = plugin._library.create_zone("another")
            assert not error
            plugin._library.activate_zone(zone)
        elif mutation == "shutdown":
            plugin._eager_attachment.invalidate(closing=True)
        elif mutation == "tool":
            assert (await plugin._sender.send(
                sticker, lanlan="YUI", settings=plugin._settings, source="tool",
            )).ok
        elif mutation == "caption":
            plugin._library.update(sticker.id, desc="different intended recipient")
        elif mutation == "deleted":
            plugin._library.remove(sticker.id)
        release.set()
        result = await polling
        assert result.value["status"] in {
            "superseded", "cancelled", "reply_attachment_superseded", "candidate_unavailable",
        }
        assert len(host.push.calls) == (1 if mutation == "tool" else 0)
        assert plugin._eager_attachment.snapshot()["sent"] == 0

    run_async(scenario())


def test_input_during_image_upload_is_checked_at_submission(tmp_path, monkeypatch, run_async):
    plugin, host, _, clock, _, logfile = _setup(tmp_path, monkeypatch, image=PNG_BYTES + b"x" * (400 * 1024))

    async def upload(data, *, mime=None, timeout=10.0):
        _append(logfile, "[YUI] voice user_transcript session=OmniOfflineClient ws_connected=True len=3")
        return {"type": "image", "url": "https://stub.local/image.jpg", "mime": "image/jpeg"}

    monkeypatch.setattr(host.images, "upload", upload)

    async def scenario():
        await plugin.on_eager_attachment()
        _reply(host, clock, 1)
        result = await plugin.on_eager_attachment()
        assert result.value["status"] == "reply_attachment_superseded"
        assert not host.push.calls and plugin._runstats.sent == 0

    run_async(scenario())


def test_missing_binding_is_visible_and_backs_off(tmp_path, monkeypatch, run_async):
    plugin, host, _, clock, _, _ = _setup(tmp_path, monkeypatch)
    calls = []

    class Unbound(RuntimeError):
        body = {"error": {"code": "model_usage_not_bound"}}

    async def select(_payload):
        calls.append(1)
        raise Unbound("must not log this private payload")

    monkeypatch.setattr(plugin._eager_attachment, "_model_select", select)

    async def scenario():
        await plugin.on_eager_attachment()
        for n in (1, 2, 3):
            _reply(host, clock, n)
            await plugin.on_eager_attachment()
        assert calls == [1] and not host.push.calls
        assert plugin._eager_attachment.snapshot()["status"] == "model_unbound"

    run_async(scenario())


def test_timeout_releases_request_and_poll_lock(tmp_path, monkeypatch, run_async):
    plugin, host, _, clock, _, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(eager_attachment, "MODEL_TIMEOUT_SEC", 0.02)
    cancelled = []

    async def select(_payload):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(plugin._eager_attachment, "_model_select", select)

    async def scenario():
        await plugin.on_eager_attachment()
        _reply(host, clock, 1)
        result = await plugin.on_eager_attachment()
        assert result.value["status"] == "model_timeout" and cancelled == [True]
        assert not plugin._eager_attachment._poll_lock.locked()
        assert not host.push.calls

    run_async(scenario())


def test_declared_optional_text_usage():
    manifest = tomllib.loads((Path(__file__).resolve().parents[1] / "plugin.toml").read_text(encoding="utf-8"))
    assert manifest["plugin"]["models"]["eager_attachment"]["required"] is False
    assert manifest["plugin"]["models"]["eager_attachment"]["capabilities"] == ["text"]


@pytest.mark.parametrize("content", [
    "{}", "[]", '{"decision":"send","sticker_id":"x","reason":"selected","text":"echo"}',
    '{"decision":"skip","sticker_id":"x","reason":"no_match"}',
    '{"decision":"send","sticker_id":null,"reason":"selected"}',
    '```json\n{"decision":"send","sticker_id":"x","reason":"selected"}\n```',
])
def test_result_parser_is_strict(content):
    assert parse_selection(content, [{"id": "x"}]) == ("", "invalid_result")


def test_candidates_keep_full_descriptions_and_exclude_disabled_recent():
    from sticker_manager.core.catalog import Sticker

    caption = "完整适用条件" * 35
    pool = [
        Sticker(id="a", file="a.png", desc="笑", caption=caption),
        Sticker(id="b", file="b.png", desc="笑", disabled=True),
        Sticker(id="c", file="c.png", desc="笑"),
    ]
    rows = selection_candidates(pool, "好呀", {"c"}, {})
    assert [row["id"] for row in rows] == ["a"]
    assert rows[0]["caption"] == caption


def test_unfinished_or_untrusted_records_are_not_replies():
    assert conversation_turn({
        "source": "main_logic.core", "lanlan_name": "YUI", "turn_type": "assistant_message",
        "conversation_id": "x", "timestamp": 10, "content": "unfinished", "metadata": {"ts": 9},
    }) is None


@pytest.mark.parametrize("gate", ["probability", "recent", "cooldown"])
def test_fallback_respects_existing_gates_without_resetting_gap(tmp_path, monkeypatch, run_async, gate):
    plugin, host, sticker, clock, _, _ = _setup(tmp_path, monkeypatch)
    send = plugin._settings.send
    if gate == "probability":
        plugin._settings = replace(plugin._settings, send=replace(send, probability=0))
    elif gate == "recent":
        plugin._settings = replace(plugin._settings, send=replace(send, recent_dedup_count=1))
        plugin._library.append_usage({"id": sticker.id, "lanlan": "YUI", "ok": True, "at": 990}, keep=200)
    else:
        plugin._settings = replace(plugin._settings, send=replace(send, cooldown_sec=100))
        plugin._sender._last_sent["YUI"] = 990

    async def scenario():
        await plugin.on_eager_attachment()
        _reply(host, clock, 1)
        result = await plugin.on_eager_attachment()
        assert result.value["status"] == {
            "probability": "probability_declined", "recent": "empty_candidates", "cooldown": "send_cooldown",
        }[gate]
        assert plugin._eager_attachment.snapshot()["reply_gap"] == 1
        assert not host.push.calls

    run_async(scenario())


def test_new_input_cancels_hung_model_without_waiting_for_model_result(tmp_path, monkeypatch, run_async):
    plugin, host, _, clock, _, _ = _setup(tmp_path, monkeypatch)
    entered, cancelled = asyncio.Event(), []

    async def select(_payload):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(plugin._eager_attachment, "_model_select", select)

    async def scenario():
        await plugin.on_eager_attachment()
        _reply(host, clock, 1)
        polling = asyncio.create_task(plugin.on_eager_attachment())
        await entered.wait()
        host.bus.memory.records.append({
            "type": "user_message", "lanlan": "YUI", "_ts": clock[0] + 1, "content": "new input",
        })
        assert (await asyncio.wait_for(polling, 1)).value["status"] == "superseded"
        assert cancelled == [True] and not host.push.calls

    run_async(scenario())


def test_raw_host_metadata_and_sdk_conversation_records(tmp_path, monkeypatch, run_async):
    plugin, host, _, clock, requests, _ = _setup(tmp_path, monkeypatch)

    async def scenario():
        await plugin.on_eager_attachment()
        for n in (1, 2):
            _reply(host, clock, n)
            for row in host.bus.conversations.records:
                for key in ("conversation_id", "lanlan_name", "turn_type"):
                    if key in row:
                        row["metadata"][key] = row.pop(key)
            await plugin.on_eager_attachment()
        assert len(requests) == len(host.push.calls) == 2

    run_async(scenario())


def test_session_restart_rebaselines_retained_history(tmp_path, monkeypatch, run_async):
    plugin, host, _, clock, requests, logfile = _setup(tmp_path, monkeypatch)

    async def scenario():
        _reply(host, clock, 1)
        assert (await plugin.on_eager_attachment()).value["status"] == "ready"
        _append(logfile, "Voice lifecycle received action=start_session")
        _reply(host, clock, 2)
        assert (await plugin.on_eager_attachment()).value["status"] == "ready"
        assert not requests
        _reply(host, clock, 3)
        assert (await plugin.on_eager_attachment()).value["status"] == "submitted"
        assert len(requests) == len(host.push.calls) == 1

    run_async(scenario())


def test_busy_host_can_become_ready_without_losing_or_repeating_selection(tmp_path, monkeypatch, run_async):
    plugin, host, _, clock, requests, _ = _setup(tmp_path, monkeypatch)
    available = [False]
    monkeypatch.setattr(plugin._eager_attachment, "_ready", lambda role: _ready())

    async def _ready():
        return available[0]

    async def scenario():
        await plugin.on_eager_attachment()
        for n in (1, 2):
            _reply(host, clock, n)
            await plugin.on_eager_attachment()
        assert not requests
        available[0] = True
        assert (await plugin.on_eager_attachment()).value["status"] == "submitted"
        await plugin.on_eager_attachment()
        assert len(requests) == len(host.push.calls) == 1

    run_async(scenario())
