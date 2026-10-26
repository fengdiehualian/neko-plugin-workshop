"""Panel saves release the host channel and never infer success from its cache."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from ..configuration import persist_settings
from .test_plugin import make_plugin


@pytest.mark.asyncio
async def test_ui_save_returns_before_write_and_receipt_survives_lost_initial_reply():
    plugin, _ = make_plugin({})
    release_channel = asyncio.Event()
    update = plugin.config.update

    async def delayed(patch, **kwargs):
        # Like a host acknowledgement that can't arrive until the UI request returns.
        await release_channel.wait()
        return await update(patch, **kwargs)

    plugin.config.update = delayed
    ident = "a" * 32
    args = dict(operation_id=ident, kind="agent", ui_nonce=plugin._settings_action_nonce,
                base_url="https://example.com/v1", model="test-model", api_key="private-test-key")
    receipt = (await asyncio.wait_for(plugin.begin_settings_save(**args), .2)).value
    assert receipt["state"] == "saving" and receipt["persisted"] is None
    assert not plugin._agent_settings.configured
    assert (await plugin.begin_settings_save(**args)).value == receipt
    assert (await plugin.get_settings_save(operation_id=ident)).value == receipt
    release_channel.set()
    await plugin._settings_saves.task
    saved = (await plugin.get_settings_save(operation_id=ident)).value
    assert saved["state"] == "saved" and saved["persisted"] is True and saved["applied"] is True
    assert len(plugin.config.updates) == 1
    dashboard = await plugin.dashboard_ui_context()
    assert dashboard["configuration"]["agent_model"] == "test-model"
    assert "private-test-key" not in json.dumps([saved, dashboard, plugin.logger.records])
    assert plugin._agent_audit.snapshot()["totals"]["model_calls"] == 0
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_readback_confirms_a_persisted_write_after_its_acknowledgement_was_lost():
    patch = {"neko_rimworld": {"agent": {"model": "new", "api_key": "secret"}}}
    reads = []

    class Config:
        async def update(self, value, **kwargs):
            raise TimeoutError("lost acknowledgement")

        async def dump(self, **kwargs):
            pytest.fail("An optimistic cache is not persistence evidence")

    async def read(**kwargs):
        reads.append(kwargs)
        return {"config": {**patch, "other": "preserved"}}

    config = Config()
    config.ctx = SimpleNamespace(get_own_effective_config=read)
    await persist_settings(config, patch)
    assert len(reads) == 1


@pytest.mark.asyncio
async def test_real_sdk_config_facade_readback_does_not_accept_optimistic_memory(tmp_path):
    from plugin.sdk.shared.core.config import PluginConfig

    from .test_plugin import FakeLogger

    path = tmp_path / "config.json"
    target = {"neko_rimworld": {"agent": {"model": "new", "api_key": "fixture-key"}}}

    class HostContext:
        plugin_id = "neko_rimworld"
        logger = FakeLogger()

        async def update_own_config(self, patch, **kwargs):
            # The real SDK refuses this uncertain acknowledgement even though its
            # optimistic cache equals the requested configuration.
            return {"success": False, "persisted": None, "config": patch}

        async def get_own_effective_config(self, profile_name=None, **kwargs):
            return {"config": json.loads(path.read_text(encoding="utf-8"))}

    facade = PluginConfig(HostContext())
    path.write_text(json.dumps({"neko_rimworld": {"agent": {"model": "old"}}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unconfirmed"):
        await persist_settings(facade, target)
    path.write_text(json.dumps(target), encoding="utf-8")
    await persist_settings(facade, target)


@pytest.mark.asyncio
async def test_mismatched_or_unavailable_readback_never_reports_saved():
    plugin, _ = make_plugin({})

    async def wrong(**kwargs):
        return {"config": {"neko_rimworld": {"agent": {"model": "old"}}}}

    plugin.config.ctx = SimpleNamespace(get_own_effective_config=wrong)
    plugin.config.update_error = TimeoutError("api_key=do-not-retain")
    receipt = await plugin.begin_settings_save(operation_id="b" * 32, kind="agent",
        ui_nonce=plugin._settings_action_nonce, base_url="https://example.com/v1", model="new")
    await plugin._settings_saves.task
    saved = (await plugin.get_settings_save(operation_id=receipt.value["operation_id"])).value
    assert saved["state"] == "unconfirmed" and saved["persisted"] is None
    assert saved["applied"] is None and not plugin._agent_settings.configured
    assert "do-not-retain" not in json.dumps([saved, plugin.logger.records])
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_shutdown_marks_pending_write_unconfirmed_and_rejects_parallel_save():
    plugin, _ = make_plugin({})
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    plugin.config.update = blocked
    args = dict(kind="agent", ui_nonce=plugin._settings_action_nonce,
                base_url="https://example.com/v1", model="example")
    await plugin.begin_settings_save(operation_id="c" * 32, **args)
    await started.wait()
    other = await plugin.begin_settings_save(operation_id="d" * 32, **args)
    assert other.error.code == "settings_busy"
    await plugin.shutdown()
    receipt = (await plugin.get_settings_save(operation_id="c" * 32)).value
    assert receipt["state"] == "unconfirmed" and receipt["error_code"] == "save_interrupted"
    assert not plugin._agent_settings.configured


@pytest.mark.asyncio
async def test_rejected_fields_are_not_applied_and_receipts_are_bounded():
    plugin, _ = make_plugin({})
    for method in (plugin.begin_settings_save, plugin.get_settings_save):
        assert method.__neko_event_meta__.metadata["agent_auto"] is False
    invalid = await plugin.begin_settings_save(operation_id="f" * 32, kind="agent",
        ui_nonce=plugin._settings_action_nonce, unexpected="not a supported field")
    assert invalid.is_err() and not plugin.config.updates
    invalid_nonce = await plugin.begin_settings_save(operation_id="e" * 32, kind="agent", ui_nonce="wrong")
    assert invalid_nonce.error.code == "ui_only"
    for index in range(22):
        receipt = await plugin.begin_settings_save(operation_id=f"{index:032x}", kind="agent",
            ui_nonce=plugin._settings_action_nonce, base_url="https://example.com/v1", model="test")
        assert receipt.is_ok()
        await plugin._settings_saves.task
    assert len(plugin._settings_saves.snapshot()) == 20
    assert (await plugin.get_settings_save(operation_id="0" * 32)).is_err()
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_invalid_model_settings_have_a_definite_failed_receipt_without_a_write():
    plugin, _ = make_plugin({})
    await plugin.begin_settings_save(operation_id="a" * 32, kind="agent",
        ui_nonce=plugin._settings_action_nonce, base_url="", model="example")
    await plugin._settings_saves.task
    receipt = (await plugin.get_settings_save(operation_id="a" * 32)).value
    assert receipt["state"] == "failed" and receipt["persisted"] is False and receipt["applied"] is False
    assert receipt["error_code"] == "invalid_agent_settings"
    assert not plugin.config.updates
    await plugin.shutdown()
