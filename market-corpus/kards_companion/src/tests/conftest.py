"""测试导入垫片。

发布目录名含点号不是合法包名，且包顶层 __init__.py import 宿主专属的
plugin.sdk。pytest 收集包内测试时会执行该 __init__，因此：
1. 先往 sys.modules 注入 plugin.sdk.plugin 桩（装饰器均为恒等）；
2. 再以 kards_companion 名义挂载 namespace 包（宿主安装后的真实包名），
   让测试可以 `from kards_companion.state.model import ...`。
"""

import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class _StubBase:
    def __init__(self, ctx) -> None:
        self.ctx = ctx


def _identity_factory(*_args, **_kwargs):
    def wrap(func):
        return func
    return wrap


def _install_sdk_stub() -> None:
    if "plugin.sdk.plugin" in sys.modules:
        return
    plugin_pkg = types.ModuleType("plugin")
    # The real N.E.K.O checkout runs these tests from plugin/plugins/<id>, so
    # pytest imports the package as plugin.plugins.kards_companion.  Mark the
    # stub as a package and pre-register the child level; otherwise the stub
    # replaces the host's ``plugin`` package and the dotted import fails with
    # "'plugin' is not a package".
    plugin_pkg.__path__ = []
    plugins_pkg = types.ModuleType("plugin.plugins")
    plugins_pkg.__path__ = []
    plugin_pkg.plugins = plugins_pkg
    sdk_mod = types.ModuleType("plugin.sdk")
    api = types.ModuleType("plugin.sdk.plugin")

    class _Ui:
        context = staticmethod(_identity_factory)
        action = staticmethod(_identity_factory)

    api.NekoPluginBase = _StubBase
    api.neko_plugin = lambda cls: cls
    api.plugin_entry = _identity_factory
    api.lifecycle = _identity_factory
    api.llm_tool = _identity_factory
    api.message = _identity_factory
    api.ui = _Ui()
    api.Ok = lambda payload=None: {"ok": True, "data": payload}
    api.Err = lambda error: {"ok": False, "error": str(error)}
    api.SdkError = type("SdkError", (Exception,), {})
    # 不提供 PluginSettings/SettingsField：走主类里的 ImportError 回退分支
    sdk_mod.plugin = api
    plugin_pkg.sdk = sdk_mod
    sys.modules["plugin"] = plugin_pkg
    sys.modules["plugin.plugins"] = plugins_pkg
    sys.modules["plugin.sdk"] = sdk_mod
    sys.modules["plugin.sdk.plugin"] = api


_install_sdk_stub()

if "kards_companion" not in sys.modules:
    package = types.ModuleType("kards_companion")
    package.__path__ = [str(PLUGIN_ROOT)]
    sys.modules["kards_companion"] = package

sys.modules.setdefault("plugin.plugins.kards_companion",
                       sys.modules["kards_companion"])

# pytest 9 把带 __init__.py 的祖先目录当 Package 节点，setup 时按目录点号
# 拆分出的 dotted name 导入根 __init__.py；预注册让那次导入直接命中缓存。
for alias in ("n.e.k.o_plugin_kards_companion", "__init__", PLUGIN_ROOT.name):
    sys.modules.setdefault(alias, sys.modules["kards_companion"])
