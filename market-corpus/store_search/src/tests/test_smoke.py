from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "store_search"' in text
    assert 'entry = "plugin.plugins.store_search:PluginStoreSearchPlugin"' in text


def test_runtime_defaults_are_in_config_example() -> None:
    root = Path(__file__).resolve().parents[1]
    config_example = root / "config.example.toml"

    assert config_example.is_file()
    text = config_example.read_text(encoding="utf-8")
    assert "[plugin_runtime]" in text
    assert "[store]" in text
