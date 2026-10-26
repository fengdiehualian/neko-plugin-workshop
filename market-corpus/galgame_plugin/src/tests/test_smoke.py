import tomllib
from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "galgame_plugin"' in text
    assert 'entry = "plugin.plugins.galgame_plugin.plugin_core:GalgamePlugin"' in text


def test_packaged_config_matches_legacy_defaults() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "plugin.toml").open("rb") as handle:
        manifest = tomllib.load(handle)
    with (root / "config.example.toml").open("rb") as handle:
        packaged_defaults = tomllib.load(handle)

    assert "plugin" not in packaged_defaults
    assert packaged_defaults
    for section, value in packaged_defaults.items():
        assert manifest[section] == value
