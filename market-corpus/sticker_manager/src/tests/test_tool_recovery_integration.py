"""Offline regression coverage for registry recovery before awareness cues."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from conftest import (
    PNG_BYTES,
    FakeBus,
    FakeConfig,
    FakeHostContext,
    build_plugin,
    conversation_record,
    user_message_record,
)
from sticker_manager.core.configuration import (
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
    StorageSettings,
)


def _settings(**awareness: Any) -> StickerManagerSettings:
    return StickerManagerSettings(
        enabled=True,
        send=SendSettings(),
        storage=StorageSettings(),
        awareness=AwarenessSettings(
            inject_mode="every_user_message",
            min_interval_sec=0.0,
            event_gated=False,
            **awareness,
        ),
    )


def _build(tmp_path: Any, *, records: list[dict[str, Any]] | None = None, sticker: bool = True):
    bus = FakeBus(
        [conversation_record("conversation-k", 1.0, "K")],
        memory_records=records if records is not None else [user_message_record(100.0, "hello", "K")],
    )
    host = FakeHostContext(
        config=FakeConfig(data={"sticker_manager": {"enabled": True}}),
        data_root=tmp_path,
        bus=bus,
    )
    plugin, host = build_plugin(host)
    if sticker:
        plugin._library.add(data=PNG_BYTES, desc="wave", tags=[])
    return plugin, host


def _replace_checker(monkeypatch: Any, plugin: Any, checker: Any) -> None:
    monkeypatch.setattr(plugin._tool_watch, "before_reminder", checker, raising=False)


def test_awareness_uses_the_plugin_shared_watch(tmp_path):
    plugin, _host = _build(tmp_path)
    assert plugin._awareness._tool_watch is plugin._tool_watch


def test_target_preflight_does_not_hide_other_role_gap(tmp_path, run_async):
    plugin, _host = _build(tmp_path)
    plugin._tool_watch._role_states = {
        "YUI": {
            "status": "healthy",
            "scope": "registry",
            "role": "YUI",
            "checked_at": 100.0,
            "age_sec": 0.0,
            "missing": [],
            "confirmed": True,
            "cached": False,
        },
    }
    plugin._tool_watch._last_status = "repair_pending"
    plugin._tool_watch._last_checked_at = 100.0
    plugin._tool_watch._last_missing_by_role = {"ATLS": ["sticker_send"]}
    result = run_async(plugin._tool_watch.before_reminder(role="YUI", now=101.0))
    assert result["status"] == "healthy" and result["cached"] is True
    snapshot = plugin._tool_watch.snapshot(now=101.0)
    assert snapshot["status"] == "repair_pending"
    assert snapshot["missing_by_role"] == {"ATLS": ["sticker_send"]}


def test_watch_timer_polls_every_ten_seconds(tmp_path):
    plugin, _host = _build(tmp_path)
    metadata = plugin.on_watch.__neko_stub_meta__
    timer = next(item["kwargs"] for item in metadata if item["kwargs"].get("id") == "watch")
    assert timer["seconds"] == 10


def test_reminder_awaits_registry_check_before_silent_push(tmp_path, monkeypatch, run_async):
    plugin, host = _build(tmp_path)
    events: list[tuple[Any, ...]] = []
    checked = {"status": "healthy", "scope": "registry", "role": "K", "confirmed": True}

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()

        async def before_reminder(*, role: str, now: float):
            events.append(("check_started", role, now))
            started.set()
            await release.wait()
            events.append(("check_finished", role))
            return dict(checked)

        def push_message(**kwargs: Any):
            events.append(("push", kwargs["target_lanlan"]))
            return host.push.push_message(**kwargs)

        _replace_checker(monkeypatch, plugin, before_reminder)
        monkeypatch.setattr(host, "push_message", push_message)
        task = asyncio.create_task(plugin._awareness.maybe_run(settings=_settings(), now=200.0))
        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert host.push.calls == []
            assert not task.done()
        finally:
            release.set()
        return await asyncio.wait_for(task, timeout=1.0)

    result = run_async(exercise())
    assert events == [("check_started", "K", 200.0), ("check_finished", "K"), ("push", "K")]
    assert result["status"] == "injected"
    assert result["trigger"] == "count"
    assert result["tool_watch"] == checked
    call = host.push.calls[0]
    assert call["ai_behavior"] == "read"
    assert call["visibility"] == []
    assert call["coalesce_key"] == "sticker_manager:awareness:K"
    assert all(part["type"] == "text" for part in call["parts"])
    snapshot_tool = plugin._awareness.snapshot(settings=_settings(), now=201.0)["tool_watch"]
    assert {key: value for key, value in snapshot_tool.items() if key != "age_sec"} == checked


@pytest.mark.parametrize("watch_status", ["unreachable", "reissue_failed", "role_missing"])
def test_unconfirmed_registry_does_not_suppress_due_reminder(
    tmp_path, monkeypatch, run_async, watch_status
):
    plugin, host = _build(tmp_path)
    checked = {"status": watch_status, "scope": "registry", "role": "K", "confirmed": False}

    async def before_reminder(*, role: str, now: float):
        assert (role, now) == ("K", 200.0)
        return checked

    _replace_checker(monkeypatch, plugin, before_reminder)
    result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=200.0))
    assert result["status"] == "injected"
    assert result["trigger"] == "count"
    assert result["tool_watch"] == checked
    assert len(host.push.calls) == 1
    assert plugin._awareness._last_injected == {"K": 200.0}
    assert plugin._awareness._turns.turns_since("K") == 0


def test_checker_exception_is_visible_without_losing_the_reminder(tmp_path, monkeypatch, run_async):
    plugin, host = _build(tmp_path)

    async def before_reminder(*, role: str, now: float):
        raise RuntimeError("offline recovery failure")

    _replace_checker(monkeypatch, plugin, before_reminder)
    result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=200.0))
    assert result["status"] == "injected"
    assert result["tool_watch"] == {"status": "watch_failed", "scope": "registry", "role": "K"}
    assert len(host.push.calls) == 1


@pytest.mark.parametrize("response", [None, ["healthy"], True])
def test_malformed_checker_result_is_unknown_not_a_delivery_gate(
    tmp_path, monkeypatch, run_async, response
):
    plugin, host = _build(tmp_path)

    async def before_reminder(*, role: str, now: float):
        return response

    _replace_checker(monkeypatch, plugin, before_reminder)
    result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=200.0))
    assert result["status"] == "injected"
    assert result["tool_watch"] == {"status": "unknown", "scope": "registry", "role": "K"}
    assert len(host.push.calls) == 1


@pytest.mark.parametrize(
    "gate",
    ["disabled", "awareness_disabled", "no_target", "empty_library", "idle", "not_due", "waiting", "floor"],
)
def test_closed_awareness_gates_do_not_call_the_checker(tmp_path, monkeypatch, run_async, gate):
    records = [] if gate in {"idle", "no_target"} else None
    plugin, host = _build(tmp_path, records=records, sticker=gate != "empty_library")
    settings = _settings()
    now = 200.0
    expected = gate
    if gate == "disabled":
        settings = replace(settings, enabled=False)
    elif gate == "awareness_disabled":
        settings = replace(settings, awareness=replace(settings.awareness, enabled=False))
        expected = "disabled"
    elif gate == "no_target":
        host.bus.conversations.records = []
    elif gate == "not_due":
        settings = replace(
            settings,
            awareness=replace(settings.awareness, inject_mode="interval_n", inject_interval_n=3),
        )
    elif gate == "waiting":
        host.bus.memory.error = True
        plugin._awareness._last_injected["K"] = 190.0
    elif gate == "floor":
        settings = replace(settings, awareness=replace(settings.awareness, min_interval_sec=60.0))
        plugin._awareness._last_injected["K"] = 190.0
        expected = "not_due"
    checked: list[tuple[str, float]] = []

    async def before_reminder(*, role: str, now: float):
        checked.append((role, now))
        return {"status": "healthy"}

    _replace_checker(monkeypatch, plugin, before_reminder)
    result = run_async(plugin._awareness.maybe_run(settings=settings, now=now))
    assert result["status"] == expected
    assert checked == []
    assert host.push.calls == []


def test_rejected_push_preserves_registry_result_and_retry_clock(tmp_path, monkeypatch, run_async):
    plugin, host = _build(tmp_path)
    checked = {"status": "healthy", "scope": "registry", "role": "K", "confirmed": True}
    checks: list[tuple[str, float]] = []

    async def before_reminder(*, role: str, now: float):
        checks.append((role, now))
        return dict(checked)

    _replace_checker(monkeypatch, plugin, before_reminder)
    host.push.reject_reason = "payload_too_large"
    rejected = run_async(plugin._awareness.maybe_run(settings=_settings(), now=200.0))
    assert rejected["status"] == "push_rejected"
    assert rejected["reason"] == "payload_too_large"
    assert rejected["tool_watch"] == checked
    assert plugin._awareness._last_injected == {}
    assert plugin._awareness._turns.turns_since("K") == 1
    host.bus.memory.records.append(user_message_record(101.0, "another turn", "K"))
    retried = run_async(plugin._awareness.maybe_run(settings=_settings(), now=201.0))
    assert retried["status"] == "injected"
    assert retried["tool_watch"] == checked
    assert checks == [("K", 200.0), ("K", 201.0)]
    assert len(host.push.calls) == 2
    assert plugin._awareness._last_injected == {"K": 201.0}
    assert plugin._awareness._turns.turns_since("K") == 0


def test_manual_reminder_checks_its_explicit_role(tmp_path, monkeypatch, run_async):
    plugin, host = _build(tmp_path)
    checks: list[tuple[str, float]] = []

    async def before_reminder(*, role: str, now: float):
        checks.append((role, now))
        return {"status": "healthy", "scope": "registry", "role": role, "confirmed": True}

    _replace_checker(monkeypatch, plugin, before_reminder)
    result = run_async(plugin._awareness.inject_now(settings=_settings(), lanlan="M", now=200.0))
    assert result["status"] == "injected"
    assert result["target"] == "M"
    assert result["tool_watch"]["role"] == "M"
    assert checks == [("M", 200.0)]
    assert host.push.calls[0]["target_lanlan"] == "M"


def test_optional_checker_keeps_legacy_awareness_behavior(tmp_path, run_async):
    plugin, host = _build(tmp_path)
    plugin._awareness._tool_watch = None
    result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=200.0))
    assert result["status"] == "injected"
    assert result["tool_watch"] == {}
    assert len(host.push.calls) == 1


def test_snapshot_recomputes_registry_age_without_mutating_last_check(tmp_path, monkeypatch, run_async):
    plugin, _host = _build(tmp_path)
    checked = {
        "status": "healthy",
        "scope": "registry",
        "role": "K",
        "confirmed": True,
        "checked_at": 200.0,
    }

    async def before_reminder(*, role: str, now: float):
        return dict(checked)

    _replace_checker(monkeypatch, plugin, before_reminder)
    result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=200.0))
    assert result["tool_watch"] == checked
    assert plugin._awareness.last_tool_check == checked

    snapshot = plugin._awareness.snapshot(settings=_settings(), now=207.5)
    assert snapshot["tool_watch"]["age_sec"] == 7.5
    assert snapshot["tool_watch"]["checked_at"] == 200.0
    assert plugin._awareness.last_tool_check == checked
