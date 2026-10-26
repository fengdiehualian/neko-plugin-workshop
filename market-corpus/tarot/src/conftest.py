"""Pytest conftest for tarot smoke tests.

在独立仓库环境中，plugin.sdk.plugin 可能不在 sys.path 上。
本文件提供模块级 mock，确保 test_smoke.py 能够正常收集与运行。
"""
import importlib
import sys
import types

# 阻止 pytest 收集根目录插件源码文件作为测试模块（相对导入在包外会失败）
collect_ignore = [
    "__init__.py",
    "dream_dict.py",
    "name_data.py",
    "stroke_data.py",
    "tarot_minor_data.py",
    "yijing_data.py",
]


def _ensure_mock(name: str):
    """若模块不存在，创建一个 mock 模块并注册到 sys.modules。"""
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _install_sdk_mocks():
    """为 plugin.sdk.plugin 提供最小可用的 mock；真实 SDK 可用时不覆盖。"""
    try:
        importlib.import_module("plugin.sdk.plugin")
        return  # 真实 SDK 可用，不做 mock
    except Exception:
        pass

    sdk = _ensure_mock("plugin")
    if not hasattr(sdk, "sdk"):
        sdk.sdk = _ensure_mock("plugin.sdk")
    plugin_pkg = _ensure_mock("plugin.sdk.plugin")

    class _NekoPluginBase:
        def __init__(self, ctx=None):
            self._ctx = ctx

    def _neko_plugin(cls):
        return cls

    def _plugin_entry(cls):
        return cls

    def _lifecycle(*args, **kwargs):
        def deco(fn):
            return fn
        return deco

    def _llm_tool(*args, **kwargs):
        def deco(fn):
            return fn
        return deco

    class _Ok:
        def __init__(self, value=None):
            self.value = value

    class _Err:
        def __init__(self, error=None):
            self.error = error

    class _SdkError(Exception):
        pass

    plugin_pkg.NekoPluginBase = _NekoPluginBase
    plugin_pkg.neko_plugin = _neko_plugin
    plugin_pkg.plugin_entry = _plugin_entry
    plugin_pkg.lifecycle = _lifecycle
    plugin_pkg.llm_tool = _llm_tool
    plugin_pkg.Ok = _Ok
    plugin_pkg.Err = _Err
    plugin_pkg.SdkError = _SdkError


_install_sdk_mocks()
