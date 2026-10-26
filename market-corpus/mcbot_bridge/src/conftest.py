"""让插件在独立仓库里可被 pytest 直接导入（不再需要 staging 拷贝）。

背景：仓库根 `__init__.py` 是插件入口，模块级 import 了宿主专属的
`plugin.sdk.plugin`，并用相对 import 拉 `prompts` / `service`。目录名
`n.e.k.o_plugin_mcbot_bridge` 不是合法标识符，pytest 无法把它当包导入；更糟的是
仓库根在 `sys.path` 上，pytest 会把根目录当包溯源、去 `import __init__`，此时模块
以顶层身份执行，相对 import 直接报 `attempted relative import with no known parent
package` —— 表现是 **tests/ 下每个用例都在 setup 阶段报错**，连什么都不 import 的
`test_smoke.py` 也一样。

本文件只在独立仓库生效——宿主树里 `plugin.sdk.plugin` 存在，`STANDALONE` 为假，
桩件整块跳过，因此对宿主布局零影响。独立仓库里做三件事：

  1. 注入最小桩件，让模块级 import 通过
  2. 以 `mcbot_bridge_plugin` 为包名加载根 `__init__.py`（`submodule_search_locations`
     指向仓库根，使 `from .service import ...` 等相对 import 解析到真实子模块）
  3. 把已加载的模块登记为 `sys.modules["__init__"]`，让 pytest 的溯源 import 命中它，
     而不是把入口文件当顶层模块重跑一遍

桩件只覆盖插件在**导入期**用到的名字。`Ok` / `Err` 的 `is_ok()` / `value` / `error`
与真实 SDK 语义一致。
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent


def _missing(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return True
    return False


STANDALONE = _missing("plugin.sdk.plugin")


if STANDALONE:
    # ── plugin.sdk.plugin ───────────────────────────────────────────────
    class SdkError(Exception):
        """桩件；真实实现在宿主 SDK。"""

    class Ok:
        __slots__ = ("value",)

        def __init__(self, value=None):
            self.value = value

        def is_ok(self) -> bool:
            return True

    class Err:
        __slots__ = ("error",)

        def __init__(self, error=None):
            self.error = error

        def is_ok(self) -> bool:
            return False

    def _identity_decorator(*_args, **_kwargs):
        """装饰器桩件：只保留函数，注册发生在运行时，导入期不需要。"""

        def _wrap(fn):
            return fn

        return _wrap

    def neko_plugin(cls):
        return cls

    class _Ui:
        context = staticmethod(_identity_decorator)
        action = staticmethod(_identity_decorator)

    class NekoPluginBase:
        """只覆盖插件自身在导入后立刻会碰到的两个钩子。

        测试不实例化 `McbotBridgePlugin`；如果将来有测试要实例化，这里还要补
        `config` / `ctx` 等接口。
        """

        def __init__(self, ctx=None):
            self.ctx = ctx

        def enable_file_logging(self, log_level="INFO"):
            return logging.getLogger("mcbot_bridge")

        def push_message(self, **_kwargs):
            return None

    _plugin_sdk_plugin = types.ModuleType("plugin.sdk.plugin")
    for _name, _obj in {
        "Err": Err,
        "NekoPluginBase": NekoPluginBase,
        "Ok": Ok,
        "SdkError": SdkError,
        "lifecycle": _identity_decorator,
        "llm_tool": _identity_decorator,
        "neko_plugin": neko_plugin,
        "plugin_entry": _identity_decorator,
        "ui": _Ui,
    }.items():
        setattr(_plugin_sdk_plugin, _name, _obj)
    sys.modules["plugin"] = types.ModuleType("plugin")
    sys.modules["plugin.sdk"] = types.ModuleType("plugin.sdk")
    sys.modules["plugin.sdk.plugin"] = _plugin_sdk_plugin


def _load_plugin_root():
    """以 `mcbot_bridge_plugin` 为包名加载根 `__init__.py`。

    `submodule_search_locations=[ROOT]` 让模块成为以仓库根为根目录的包，于是
    `from .service import ...` / `from . import prompts` 解析到真实子模块。
    """
    spec = importlib.util.spec_from_file_location(
        "mcbot_bridge_plugin",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["mcbot_bridge_plugin"] = module
    spec.loader.exec_module(module)
    return module


try:
    plugin_root = _load_plugin_root()
except Exception:  # pragma: no cover - 宿主布局或加载失败时相关测试自行 skip
    plugin_root = None
else:
    # pytest 会尝试 `import __init__`（仓库根在 pythonpath 上）。提前登记，让它命中
    # 已加载的模块，而不是重新执行一遍宿主 import。
    sys.modules.setdefault("__init__", plugin_root)


@pytest.fixture
def plugin_module():
    """插件根模块（`McbotBridgePlugin` 所在模块）。不可用时跳过。"""
    if plugin_root is None:
        pytest.skip("插件根模块不可用（宿主布局或加载失败）")
    return plugin_root
