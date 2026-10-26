import importlib
from unittest.mock import AsyncMock

import pytest
from plugin.sdk.plugin import Err, Ok


def test_reference_configuration_is_saved_without_starting_or_reading_a_dataset(plugin_instance, plugin_module, run_async, monkeypatch, tmp_path):
    raw = {"runtime": {"mode": "external"}, "server": {"host": "fixture.invalid", "username": "Alice"}}
    patch = {"knowledge": {"minedojo_index_file": str(tmp_path / "separate-wiki.sqlite"), "retrieval": "hybrid"}}
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "dump", AsyncMock(return_value=raw))
    monkeypatch.setattr(plugin_instance.config, "update", update)
    result = run_async(plugin_instance.save_settings(settings=patch))
    assert isinstance(result, Ok)
    update.assert_awaited_once_with(patch)
    settings = plugin_module.Settings.parse({**raw, **patch})
    public = importlib.import_module(plugin_module.__name__ + ".dashboard").public_config(settings)
    assert public["knowledge"] == patch["knowledge"]
    assert settings.identity == plugin_module.Settings.parse(raw).identity
    assert not (tmp_path / "separate-wiki.sqlite").exists()
    assert plugin_instance._worker is None


@pytest.mark.parametrize("knowledge", [{"minedojo_index_file": "relative.sqlite"}, {"retrieval": "semantic"}])
def test_unsupported_reference_configuration_is_rejected_before_write(plugin_instance, run_async, monkeypatch, knowledge):
    update = AsyncMock()
    monkeypatch.setattr(plugin_instance.config, "update", update)
    result = run_async(plugin_instance.save_settings(settings={"knowledge": knowledge}))
    assert isinstance(result, Err)
    update.assert_not_awaited()
