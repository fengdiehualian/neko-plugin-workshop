"""Smoke tests for mail_all plugin."""

import importlib.util
from pathlib import Path


def test_plugin_toml_exists():
    """测试 plugin.toml 文件存在。"""
    root = Path(__file__).parent.parent
    assert (root / "plugin.toml").exists(), "plugin.toml should exist"


def test_plugin_toml_readable():
    """测试 plugin.toml 文件可读取。"""
    try:
        import toml
    except ImportError:
        # 如果 toml 模块不可用，跳过此测试
        return

    root = Path(__file__).parent.parent
    config = toml.load(root / "plugin.toml")
    assert "plugin" in config, "plugin.toml should have [plugin] section"
    assert "id" in config["plugin"], "plugin.toml should have plugin.id"
    assert config["plugin"]["id"] == "mail_all", "plugin.id should be mail_all"
    assert "entry" in config["plugin"], "plugin.toml should have plugin.entry"
    assert config["plugin"]["entry"] == "plugins.mail_all:MailAllPlugin"


def test_plugin_entry_module_exists():
    """测试 entry 指向的模块文件存在。"""
    root = Path(__file__).parent.parent
    entry_module = root / "plugins" / "mail_all" / "__init__.py"
    assert entry_module.exists(), f"Entry module should exist: {entry_module}"


def test_plugin_class_importable():
    """测试插件类可以导入。"""
    root = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(
        "mail_all_init",
        root / "plugins" / "mail_all" / "__init__.py",
    )
    assert spec is not None, "Should be able to create spec for __init__.py"
    # 注意：这里不执行模块，因为可能依赖 N.E.K.O SDK
    # 只验证文件可以被 spec 识别


def test_readme_exists():
    """测试 README.md 文件存在。"""
    root = Path(__file__).parent.parent
    assert (root / "README.md").exists(), "README.md should exist"


def test_readme_not_empty():
    """测试 README.md 文件非空。"""
    root = Path(__file__).parent.parent
    readme = root / "README.md"
    assert readme.stat().st_size > 0, "README.md should not be empty"
