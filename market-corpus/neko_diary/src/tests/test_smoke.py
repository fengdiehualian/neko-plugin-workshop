"""Smoke tests for neko_diary plugin.

最小化测试原则：只验证导入、类存在性和必需方法。
在 market_verify 环境中，插件挂载在 plugin.plugins.neko_diary 下。
"""
import pytest

try:
    from plugin.plugins.neko_diary import NekoDiaryPluginEntry
    from plugin.plugins.neko_diary.plugin import NekoDiaryPlugin
    _HAS_PLUGIN = True
except (ImportError, ModuleNotFoundError):
    _HAS_PLUGIN = False
    NekoDiaryPluginEntry = None  # type: ignore
    NekoDiaryPlugin = None  # type: ignore


@pytest.mark.skipif(not _HAS_PLUGIN, reason="plugin.sdk.plugin not available")
def test_plugin_import():
    """Test that the plugin entry class can be imported."""
    assert NekoDiaryPluginEntry is not None


@pytest.mark.skipif(not _HAS_PLUGIN, reason="plugin.sdk.plugin not available")
def test_plugin_class_exists():
    """Test that the plugin entry class exists and is a class."""
    assert isinstance(NekoDiaryPluginEntry, type)


@pytest.mark.skipif(not _HAS_PLUGIN, reason="plugin.sdk.plugin not available")
def test_plugin_has_required_methods():
    """Test that the plugin has required lifecycle methods."""
    assert hasattr(NekoDiaryPluginEntry, "startup")
    assert hasattr(NekoDiaryPluginEntry, "shutdown")


@pytest.mark.skipif(not _HAS_PLUGIN, reason="plugin.sdk.plugin not available")
def test_core_plugin_class_import():
    """Test that the core diary plugin class can be imported."""
    assert isinstance(NekoDiaryPlugin, type)


def test_smoke():
    """Always-pass smoke test to ensure pytest can run."""
    assert True
