"""Pytest conftest for neko_diary smoke tests.

在 CI 环境中，plugin.sdk.plugin 可能不在 sys.path 上。
此文件提供模块级别的 mock，确保 test_smoke.py 能正常导入。
"""
import sys
import types


def _ensure_mock(name: str):
    """如果模块不存在，创建一个 mock 模块并注册到 sys.modules。"""
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _install_sdk_mocks():
    """为 plugin.sdk.plugin 提供最小化的 mock，让导入链不报错。"""
    # 只在 plugin.sdk.plugin 确实不可导入时才 mock
    try:
        import importlib
        importlib.import_module("plugin.sdk.plugin")
        return  # 真实环境可用，无需 mock
    except Exception:
        pass

    # 创建 mock
    sdk = _ensure_mock("plugin")
    if not hasattr(sdk, "sdk"):
        sdk.sdk = _ensure_mock("plugin.sdk")
    plugin_pkg = _ensure_mock("plugin.sdk.plugin")

    # 提供装饰器和基类的 mock
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
