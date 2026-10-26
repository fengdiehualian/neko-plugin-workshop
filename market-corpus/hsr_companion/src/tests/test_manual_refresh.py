"""Manual observation is coalesced, silent and must not resume a paused game."""
import asyncio
import json
from types import SimpleNamespace

from plugin.plugins.hsr_companion.runtime import manual_refresh
from plugin.plugins.hsr_companion.tests.test_current_observation import ROOT, plugin_fixture
from plugin.plugins.hsr_companion.tests.test_delivery_races import commit, picture
from plugin.sdk.plugin import PluginI18n


def test_manual_refresh_coalesces_and_requires_new_full_commit(tmp_path):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._observer_task = SimpleNamespace(done=lambda: False)
    owner._observer_wake_event = asyncio.Event()
    owner._visual_update_event = asyncio.Event()

    async def run():
        await commit(owner, picture())
        owner._last_full_commit_serial = owner._observation_serial
        first = manual_refresh.request_manual_refresh(owner)
        task = owner._manual_refresh_task
        assert first["state"] == "running"
        manual_refresh.request_manual_refresh(owner)
        assert owner._manual_refresh_task is task and owner._observer_wake_event.is_set()
        await asyncio.sleep(0)
        assert not task.done()
        owner._observation_serial += 1  # A field withdrawal is NOT a new capture.
        owner._visual_update_event.set()
        await asyncio.sleep(0)
        assert manual_refresh.public_manual_refresh(owner)["state"] == "running"
        await commit(owner, picture(), "20", complete=True)
        owner._last_full_commit_serial = owner._observation_serial
        owner._visual_update_event.set()
        await task
        assert manual_refresh.public_manual_refresh(owner)["state"] == "completed"

    asyncio.run(run())
    assert not any(message.get("ai_behavior") == "respond" for message in sent)


def test_manual_refresh_does_not_start_or_clear_paused_observer(tmp_path):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._observer_status.update(enabled=False, state="paused")
    owner._committed_observation = {"retained": "history"}
    result = manual_refresh.request_manual_refresh(owner)
    assert result["state"] == "unavailable"
    assert owner._committed_observation == {"retained": "history"}
    assert not getattr(owner, "_manual_refresh_task", None) and not sent


def test_manual_refresh_timeout_keeps_history_and_does_not_speak(tmp_path, monkeypatch):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._observer_task = SimpleNamespace(done=lambda: False)
    owner._last_successful_observation = {"retained": "history"}
    monkeypatch.setattr(manual_refresh, "MANUAL_TIMEOUT_SECONDS", .01)

    async def run():
        manual_refresh.request_manual_refresh(owner)
        await owner._manual_refresh_task
        assert manual_refresh.public_manual_refresh(owner)["state"] == "timeout"
        assert owner._last_successful_observation == {"retained": "history"}

    asyncio.run(run())
    assert not sent


def test_pause_cancels_manual_refresh_task(tmp_path):
    owner, _, _ = plugin_fixture(tmp_path)
    owner._observer_task = SimpleNamespace(done=lambda: False)

    async def run():
        manual_refresh.request_manual_refresh(owner)
        task = owner._manual_refresh_task
        manual_refresh.cancel_manual_refresh(owner)
        await asyncio.gather(task, return_exceptions=True)
        assert manual_refresh.public_manual_refresh(owner)["state"] == "cancelled"

    asyncio.run(run())


def test_manual_entry_returns_readable_operation_and_ui_has_action(tmp_path):
    owner, values, _ = plugin_fixture(tmp_path)
    messages = json.loads((ROOT / "i18n/zh-CN.json").read_text(encoding="utf-8"))
    owner.i18n = PluginI18n({"zh-CN": messages}, default_locale="zh-CN")
    owner._observer_status.update(enabled=False, state="paused")

    async def run():
        await owner.refresh_current_observation()
        operation = values["last_user_operation"]
        assert operation["message"] == messages["manualRefresh.unavailable"]
        assert operation["state"] == "error"

    asyncio.run(run())
    panel = (ROOT / "ui/panel_v09.tsx").read_text(encoding="utf-8")
    assert 'call("refresh_current_observation"' in panel


def test_manual_refresh_reports_reader_failure_without_chat(tmp_path):
    owner, _, sent = plugin_fixture(tmp_path)
    owner._observer_task = SimpleNamespace(done=lambda: False)

    async def broken():
        raise RuntimeError("private reader details")

    owner._current_visual_evidence = broken

    async def run():
        manual_refresh.request_manual_refresh(owner)
        await owner._manual_refresh_task
        public = manual_refresh.public_manual_refresh(owner)
        assert public["state"] == "failed"
        assert "private" not in str(public)

    asyncio.run(run())
    assert not sent
