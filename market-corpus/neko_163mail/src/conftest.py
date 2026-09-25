"""Conftest for neko_163mail plugin tests — Mock N.E.K.O SDK for CI."""
import sys
from unittest.mock import MagicMock

collect_ignore = [
    "__init__.py",
    "upload_to_market.py",
]

# Mock plugin.sdk modules before any plugin imports
MOCK_MODULES = [
    "plugin",
    "plugin.sdk",
    "plugin.sdk.plugin",
    "plugin.sdk.errors",
    "plugin.sdk.types",
]

for mod in MOCK_MODULES:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Create mock decorators and classes
mock_plugin_module = sys.modules["plugin.sdk.plugin"]

# Decorators that accept any kwargs and return the decorated function/class unchanged
def mock_lifecycle(**kwargs):
    """Mock lifecycle decorator that accepts any kwargs."""
    def decorator(func):
        return func
    return decorator

def mock_llm_tool(*args, **kwargs):
    """Mock llm_tool decorator that accepts any args/kwargs."""
    def decorator(func):
        return func
    # Handle both @llm_tool and @llm_tool(...) usage
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]
    return decorator

def mock_plugin_entry(*args, **kwargs):
    """Mock plugin_entry decorator that accepts any args/kwargs."""
    def decorator(func):
        return func
    if len(args) == 1 and callable(args[0]) and not kwargs:
        return args[0]
    return decorator

def mock_neko_plugin(cls):
    """Mock neko_plugin decorator."""
    return cls

mock_plugin_module.lifecycle = mock_lifecycle
mock_plugin_module.llm_tool = mock_llm_tool
mock_plugin_module.plugin_entry = mock_plugin_entry
mock_plugin_module.neko_plugin = mock_neko_plugin

# Mock base class
class MockNekoPluginBase:
    pass

mock_plugin_module.NekoPluginBase = MockNekoPluginBase

# Mock result types
class MockOk:
    def __init__(self, value):
        self.value = value

class MockErr:
    def __init__(self, error):
        self.error = error

mock_plugin_module.Ok = MockOk
mock_plugin_module.Err = MockErr

# Mock SdkError
class MockSdkError(Exception):
    pass

mock_plugin_module.SdkError = MockSdkError
