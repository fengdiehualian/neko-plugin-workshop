"""Smoke tests for tarot plugin.

最小化测试：只验证导入、类存在和必要方法。
在 market_verify 环境中，插件被挂载到 plugin.plugins.tarot 下。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

try:
    from plugin.plugins.tarot import TarotReaderPlugin
    _HAS_PLUGIN = True
except (ImportError, ModuleNotFoundError):
    _HAS_PLUGIN = False
    TarotReaderPlugin = None  # type: ignore


@pytest.mark.skipif(not _HAS_PLUGIN, reason="plugin.sdk.plugin not available")
def test_plugin_import():
    """Test that the plugin entry class can be imported."""
    assert TarotReaderPlugin is not None


@pytest.mark.skipif(not _HAS_PLUGIN, reason="plugin.sdk.plugin not available")
def test_plugin_class_exists():
    """Test that the plugin entry class exists and is a class."""
    assert isinstance(TarotReaderPlugin, type)


def _load_module(name):
    """直接按文件路径加载数据模块，避免依赖包结构与 SDK。"""
    root = Path(__file__).parent.parent
    spec = importlib.util.spec_from_file_location(name, root / f"{name}.py")
    assert spec is not None, f"{name}.py should be loadable"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass 等特性依赖 sys.modules 注册
    spec.loader.exec_module(mod)
    return mod


def test_yijing_data():
    """梅花易数起卦算法可独立运行。"""
    yj = _load_module("yijing_data")
    r = yj.divine(3, 7)
    assert r.hexagram_name
    assert r.changed_name
    assert r.mutual_name
    assert 1 <= r.moving_line <= 6


def test_name_data():
    """姓名五格/姻缘评分可独立运行。"""
    nd = _load_module("name_data")
    score = nd.fate_score("林晚星", "沈知远")
    assert 0 <= score <= 100


def test_smoke():
    """Always-pass smoke test to ensure pytest can run."""
    assert True
