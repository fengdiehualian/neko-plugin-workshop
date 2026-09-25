"""Pytest configuration for CI environment.

在 CI 环境中，N.E.K.O SDK 不可用。此 conftest 在 pytest 导入插件代码前
mock 所需的 SDK 模块，使测试可以在独立环境中运行。
"""

import sys
from unittest.mock import MagicMock


class _FakeNekoPluginBase:
    """轻量基类替身：支持子类任意属性访问与文件日志开启。"""

    def __init__(self, ctx=None, **_kwargs) -> None:
        self.ctx = ctx

    def enable_file_logging(self, log_level: str = "INFO"):
        return MagicMock()


def _setup_sdk_mocks():
    """Mock N.E.K.O SDK modules before plugin import."""
    if "plugin" in sys.modules:
        return  # Already mocked or running in N.E.K.O environment

    # Create mock modules
    mock_plugin = MagicMock()
    mock_sdk = MagicMock()
    mock_plugin_module = MagicMock()

    # Mock classes and functions that __init__.py imports
    mock_plugin_module.NekoPluginBase = _FakeNekoPluginBase
    mock_plugin_module.neko_plugin = lambda x: x
    mock_plugin_module.plugin_entry = lambda **kwargs: lambda x: x
    mock_plugin_module.lifecycle = lambda **kwargs: lambda x: x
    mock_plugin_module.llm_tool = lambda **kwargs: lambda x: x
    mock_plugin_module.Ok = lambda x: x
    mock_plugin_module.Err = lambda x: x
    mock_plugin_module.SdkError = Exception

    # Register in sys.modules
    sys.modules["plugin"] = mock_plugin
    sys.modules["plugin.sdk"] = mock_sdk
    sys.modules["plugin.sdk.plugin"] = mock_plugin_module


# Run mocks setup before any imports
_setup_sdk_mocks()
