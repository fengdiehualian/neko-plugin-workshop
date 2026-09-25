"""pytest 引导：本地独立跑时建立 plugin.plugins.neko_pawpilot 包链 + SDK stub。"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    _installed = importlib.util.find_spec("plugin.plugins.neko_pawpilot") is not None
except (ImportError, ModuleNotFoundError):
    _installed = False

if not _installed:
    _plugin_root = types.ModuleType("plugin")
    _plugin_root.__path__ = []
    sys.modules["plugin"] = _plugin_root

    # SDK stub：本地测试不加载宿主 SDK，仅提供同名装饰器/类型
    _sdk = types.ModuleType("plugin.sdk")
    _sdk.__path__ = []
    sys.modules["plugin.sdk"] = _sdk

    _sdk_plugin = types.ModuleType("plugin.sdk.plugin")


    class Ok:
        def __init__(self, value=None):
            self.value = value

        def unwrap(self):
            return self.value


    class Err:
        def __init__(self, error):
            self.error = error

        def unwrap(self):
            raise self.error


    class SdkError(Exception):
        pass


    class NekoPluginBase:
        def __init__(self, ctx=None):
            self.ctx = ctx
            self.config_dir = Path(".")

        def register_static_ui(self, *a, **k):
            pass

        def enable_file_logging(self, **k):
            return self


    def neko_plugin(cls):
        return cls


    def lifecycle(**kwargs):
        def deco(fn):
            fn._lifecycle = kwargs
            return fn
        return deco


    def message(**kwargs):
        def deco(fn):
            fn._message = kwargs
            return fn
        return deco


    def plugin_entry(**kwargs):
        def deco(fn):
            fn._plugin_entry = kwargs
            return fn
        return deco


    class _UI:
        @staticmethod
        def context(**kwargs):
            def deco(fn):
                fn._ui_context = kwargs
                return fn
            return deco

        @staticmethod
        def action(**kwargs):
            def deco(fn):
                fn._ui_action = kwargs
                return fn
            return deco


    ui = _UI()
    _sdk_plugin.Ok = Ok
    _sdk_plugin.Err = Err
    _sdk_plugin.SdkError = SdkError
    _sdk_plugin.NekoPluginBase = NekoPluginBase
    _sdk_plugin.neko_plugin = neko_plugin
    _sdk_plugin.lifecycle = lifecycle
    _sdk_plugin.message = message
    _sdk_plugin.plugin_entry = plugin_entry
    _sdk_plugin.ui = ui
    sys.modules["plugin.sdk.plugin"] = _sdk_plugin

    _plugins_pkg = types.ModuleType("plugin.plugins")
    _plugins_pkg.__path__ = []
    sys.modules["plugin.plugins"] = _plugins_pkg

    _pawpilot_pkg = types.ModuleType("plugin.plugins.neko_pawpilot")
    _pawpilot_pkg.__path__ = [str(ROOT)]
    sys.modules["plugin.plugins.neko_pawpilot"] = _pawpilot_pkg

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
