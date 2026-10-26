from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "neko_survivors"' in text
    assert 'entry = "plugin.plugins.neko_survivors:SurvivorsPlugin"' in text


def test_game_runtime_bundled() -> None:
    root = Path(__file__).resolve().parents[1]
    game = root / "static" / "game"
    index = game / "index.html"
    main = game / "src" / "main.js"
    assert index.is_file(), "static/game/index.html 缺失"
    assert main.is_file(), "static/game/src/main.js 缺失"
    html = index.read_text(encoding="utf-8")
    assert "src/main.js" in html
    assert (game / "assets").is_dir()


def test_no_spoiler_in_listing() -> None:
    """上架文案不得剧透隐藏 Boss 的名字（用户要求）。"""
    # 字面量拼接，避免测试文件自身包含这个名字
    name = "\u8299\u5170"
    english = "Flan" + "dre"
    root = Path(__file__).resolve().parents[1]
    for rel in ("plugin.toml", "README.md", "i18n/zh-CN.json", "i18n/en.json",
                "static/index.html"):
        text = (root / rel).read_text(encoding="utf-8")
        assert name not in text, f"{rel} 泄露了隐藏 Boss 的名字"
        assert english not in text, f"{rel} 泄露了隐藏 Boss 的英文名"
