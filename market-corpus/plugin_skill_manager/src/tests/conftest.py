"""把 engine 以独立包 plugin_skill_manager_engine 载入（纯标准库，无 SDK 依赖）。"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import warnings as _warnings
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def load_engine() -> None:
    name = "plugin_skill_manager_engine"
    if name in sys.modules:
        return
    engine_dir = PLUGIN_DIR / "engine"
    spec = importlib.util.spec_from_file_location(
        name, engine_dir / "__init__.py", submodule_search_locations=[str(engine_dir)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


load_engine()


# ---------------------------------------------------------------------------
# 警告即错误（用户规则）：除下列两条宿主/环境所有的豁免外，一切警告按错误处理。
# 豁免 1：plugin.settings 的模块级弃用告警——宿主 SDK 导入链自带（stacklevel=2
#   归属在导入方，过滤器消息/模块锚定均不可靠），在首次导入前用
#   catch_warnings 吞掉，模块缓存后不会再发。
# 豁免 2：PytestUnraisableExceptionWarning——Windows Proactor 事件循环 GC 时序，
#   宿主自带 lifekit 套件在本机同样触发（环境所有，非插件缺陷）。
# ---------------------------------------------------------------------------

with _warnings.catch_warnings():
    _warnings.filterwarnings("ignore", message=r"plugin\.settings\.PLUGIN_CONFIG_ROOT is deprecated")
    try:
        # 只为副作用导入（首次注册触发弃用警告）；用 import_module 而非
        # import 语句，避免把「按副作用导入」写成未使用的绑定。
        importlib.import_module("plugin.settings")
    except Exception:
        pass


def pytest_configure(config):
    config.addinivalue_line("filterwarnings", "error")
    config.addinivalue_line("filterwarnings", "ignore::pytest.PytestUnraisableExceptionWarning")
