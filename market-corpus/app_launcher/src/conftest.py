"""Root conftest - mock N.E.K.O SDK to allow __init__.py to be imported."""
import sys
from unittest.mock import MagicMock

if "plugin" not in sys.modules:
    mock_plugin_module = MagicMock()
    mock_plugin_module.NekoPluginBase = MagicMock
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
    sys.modules["plugin.sdk.shared"] = MagicMock()
    sys.modules["plugin.sdk.shared.i18n"] = MagicMock()
