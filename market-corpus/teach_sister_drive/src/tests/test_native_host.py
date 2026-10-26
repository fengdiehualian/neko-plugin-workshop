import builtins
from unittest.mock import Mock

import pytest
from plugin.plugins.teach_sister_drive import TeachSisterDrivePlugin, game_installation


def test_old_host_without_selected_storage_api_is_rejected(monkeypatch):
    plugin = TeachSisterDrivePlugin()
    real_import = builtins.__import__

    def reject_old_host(name, *args, **kwargs):
        if name == "plugin.sdk.shared.core.base_runtime":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_old_host)
    with pytest.raises(game_installation.InstallationError, match="当前 Steam 版不受支持"):
        plugin._installation_locations()


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [True, False])
async def test_detection_checks_source_host_without_creating_a_launcher(tmp_path, monkeypatch, available):
    plugin = TeachSisterDrivePlugin()
    monkeypatch.setattr(plugin, "_installation_locations", lambda: (tmp_path, tmp_path))
    monkeypatch.setattr(game_installation, "installation_status", lambda *args: {"games": [], "message": "No game"})
    monkeypatch.setattr(plugin, "_source_host_available", lambda: available)
    spawn = Mock(side_effect=AssertionError("Detection must not start another host"))
    monkeypatch.setattr("subprocess.Popen", spawn)
    result = await plugin.detect_game()
    assert result.value["takeover_available"] is available
    assert result.value["takeover_reason"] == ("source_plugin_launcher" if available else "source_launcher_required")
    assert not hasattr(plugin, "prepare_steam")
    assert not list(tmp_path.iterdir())
    spawn.assert_not_called()
