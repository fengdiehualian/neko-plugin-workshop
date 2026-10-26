import tomllib
from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "neko_sous"' in text
    assert 'entry = "plugin.plugins.neko_sous:NekoSousPlugin"' in text


def test_source_discovery_keeps_ui_actions_without_importing_plugin() -> None:
    from plugin.core.registry import _extract_entries_preview
    from plugin.server.application.plugins.ui_query_service import _entry_ids_from_meta

    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8"))
    preview = _extract_entries_preview("neko_sous", type("UnscannedPluginStub", (), {}), config, config["plugin"])
    assert _entry_ids_from_meta({"entries_preview": preview}) == {"connect", "disconnect", "set_mode", "update_settings"}
