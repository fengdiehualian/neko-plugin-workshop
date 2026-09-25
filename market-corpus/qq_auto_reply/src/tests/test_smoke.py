from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "qq_auto_reply"' in text
    assert 'entry = "plugin.plugins.qq_auto_reply:QQAutoReplyPlugin"' in text
