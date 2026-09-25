import tomllib
from pathlib import Path


def test_plugin_manifest_targets_nested_source_package() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8"))

    assert manifest["plugin"]["id"] == "neko_wows"
    assert manifest["plugin"]["entry"] == (
        "plugin.plugins.neko_wows.neko_wows:NekoWowsPlugin"
    )
    assert (root / "neko_wows" / "__init__.py").is_file()
