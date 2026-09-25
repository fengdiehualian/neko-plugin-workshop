from pathlib import Path


def test_plugin_manifest_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = (root / "plugin.toml").read_text(encoding="utf-8")

    assert 'id = "bilibili_integration"' in manifest
    assert (
        'entry = "plugin.plugins.bilibili_integration:BiliDMPlugin"' in manifest
    )
