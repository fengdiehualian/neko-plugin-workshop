"""让 plugin_database 的测试可在仓库里直接运行。

两件事：
1. 把 ``vendor/`` 加入 ``sys.path``（生产路径是插件 startup 的 ``ensure_vendor_path``，
   测试沿用同一约定，避免向共享测试 venv 安装驱动依赖）。
2. 补全 ``plugin.plugins`` 命名空间，使测试里的
   ``from plugin.plugins.plugin_database.adapters import ...`` 能落到本仓目录。
   宿主会在启动插件时建这个命名空间；测试环境没有。

   宿主 SDK 本身（``plugin.sdk``）需要 ``PYTHONPATH`` 指向 N.E.K.O 仓库根：

       set PYTHONPATH=C:\\Users\\54301\\N.E.K.O
       python -m pytest tests -q
"""

from __future__ import annotations

import importlib
import sys
import types
import warnings as _warnings
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]      # .../plugins/plugin_database
_PLUGINS_DIR = _PLUGIN_ROOT.parent                       # .../plugins

VENDOR = _PLUGIN_ROOT / "vendor"
if VENDOR.is_dir() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))


def _ensure_plugins_namespace() -> None:
    """把 ``plugin.plugins`` 指向仓库的 ``plugins/`` 目录。

    ``plugin.plugins.plugin_database`` 于是解析到本目录的 ``__init__.py``，
    而它依赖的 ``plugin.sdk`` 由宿主 SDK 提供（需 PYTHONPATH 指向 N.E.K.O 根）。
    """
    try:
        # 用 import_module 而不是 import 语句：这个绑定随后要 setattr，
        # 而 ``import plugin`` 形式在「先绑定后赋值」的写法下会被判为未使用。
        plugin = importlib.import_module("plugin")
    except Exception:
        return  # 没有宿主 SDK：让测试以清晰的 ModuleNotFoundError 失败

    ns = sys.modules.get("plugin.plugins")
    if ns is None:
        ns = types.ModuleType("plugin.plugins")
        ns.__path__ = []  # type: ignore[attr-defined]
        sys.modules["plugin.plugins"] = ns
        setattr(plugin, "plugins", ns)

    if not hasattr(ns, "__path__"):
        ns.__path__ = []  # type: ignore[attr-defined]
    path = list(ns.__path__)  # type: ignore[attr-defined]
    if str(_PLUGINS_DIR) not in path:
        path.insert(0, str(_PLUGINS_DIR))
    ns.__path__ = path  # type: ignore[attr-defined]


_ensure_plugins_namespace()


# ---------------------------------------------------------------------------
# 警告即错误（用户规则）：除下列两条宿主/环境固有的豁免外，一切警告按错误处理。
# 豁免 1：plugin.settings 的模块级弃用警告——宿主 SDK 导入链自带（stacklevel=2
#   归属在导入方，过滤器消息/模块锚定均不可靠），在首次导入前用
#   catch_warnings 吞掉，模块缓存后不会再次发出。
# 豁免 2：PytestUnraisableExceptionWarning——Windows Proactor 事件循环 GC 时序，
#   宿主自带 lifekit 套件在本机同样触发（环境固有，非插件缺陷）。
# ---------------------------------------------------------------------------

with _warnings.catch_warnings():
    _warnings.filterwarnings("ignore", message=r"plugin\.settings\.PLUGIN_CONFIG_ROOT is deprecated")
    try:
        # 只为副作用导入（触发弃用警告的首次注册）；用 import_module
        # 而非 import 语句，避免把「按副作用导入」写成未使用的绑定。
        importlib.import_module("plugin.settings")
    except Exception:
        pass


def pytest_configure(config):
    config.addinivalue_line("filterwarnings", "error")
    config.addinivalue_line("filterwarnings", "ignore::pytest.PytestUnraisableExceptionWarning")