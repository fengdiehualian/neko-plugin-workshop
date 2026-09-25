"""Root conftest -- mock N.E.K.O SDK and prevent root __init__.py collection."""

import sys
from unittest.mock import MagicMock


class _FakeNekoPluginBase:
    """轻量基类替身：支持子类实例化与文件日志开启。"""

    def __init__(self, ctx=None, **_kwargs) -> None:
        self.ctx = ctx

    def enable_file_logging(self, log_level: str = "INFO"):
        return MagicMock()


# Prevent pytest from collecting root Python files as test modules
collect_ignore = [
    "__init__.py",
    "client.py",
    "models.py",
    "operation_log.py",
    "parser.py",
    "plugin.py",
]

# Mock N.E.K.O SDK before any plugin import
if "plugin" not in sys.modules:
    mock_plugin_module = MagicMock()
    mock_plugin_module.NekoPluginBase = _FakeNekoPluginBase
    mock_plugin_module.neko_plugin = lambda x: x
    mock_plugin_module.plugin_entry = lambda **kwargs: lambda x: x
    mock_plugin_module.lifecycle = lambda **kwargs: lambda x: x
    mock_plugin_module.llm_tool = lambda **kwargs: lambda x: x
    mock_plugin_module.Ok = lambda x: x
    mock_plugin_module.Err = lambda x: x
    mock_plugin_module.SdkError = Exception

    sys.modules["plugin"] = MagicMock()
    sys.modules["plugin.sdk"] = MagicMock()
    sys.modules["plugin.sdk.plugin"] = mock_plugin_module
