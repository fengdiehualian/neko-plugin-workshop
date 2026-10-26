"""init 生成的冒烟测试 + 入口类可导入性（A-08 的最早一道门）。

init 版只断言 plugin.toml 里存在两行字符串——那挡不住 entry 拼写错误，
所以这里补一条真正 import 到类的用例。
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    with (PLUGIN_DIR / "plugin.toml").open("rb") as stream:
        return tomllib.load(stream)


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "genshin_game_buddy"' in text
    assert 'entry = "plugin.plugins.genshin_game_buddy:GenshinGameBuddyPlugin"' in text


def test_entry_class_importable() -> None:
    """entry 必须真的能 import 到那个类（提前暴露拼写错误）。"""
    from plugin.sdk.plugin import NekoPluginBase

    entry = _manifest()["plugin"]["entry"]
    module_path, _, class_name = entry.partition(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    assert issubclass(cls, NekoPluginBase)


def test_business_modules_exist() -> None:
    """业务分层必须都在位，且都是纯逻辑模块（可脱离宿主单测）。"""
    for name in ("game_registry", "term_store", "session", "context_pack"):
        path = PLUGIN_DIR / f"{name}.py"
        assert path.is_file(), path
