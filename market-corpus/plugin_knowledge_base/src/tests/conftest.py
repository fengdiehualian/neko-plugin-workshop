"""把引擎与 sibling 数据库适配器以独立包形式载入，供纯单元/集成测试使用。

engine 与 adapters 都不 import plugin.sdk（宿主之外可独立运行），
用 importlib 按路径加载，绕过父包的宿主依赖。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import warnings as _warnings
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[1]
SIBLING_DB = PLUGIN_DIR.parent / "plugin_database"

#: sibling plugin_database 不在时，依赖它的集成测试会自行 skip
#: （见 test_plugin.py 的 _NEEDS_SIBLING）；纯引擎测试不受影响。
#: CI 只检出本仓库，因此这里不能抛 ImportError 让整套测试报错。
SIBLING_DB_PRESENT = (SIBLING_DB / "adapters" / "__init__.py").is_file()


def _load_package(name: str, init_path: Path, search_dir: Path) -> None:
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        name, init_path, submodule_search_locations=[str(search_dir)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


# engine：知识库引擎（嵌入器/分块/存储/热度/检索）
_load_package("kb_engine", PLUGIN_DIR / "engine" / "__init__.py", PLUGIN_DIR / "engine")

# plugin_database.adapters + hot_schema：真实 sqlite 适配器（含 vendor 路径）
if SIBLING_DB_PRESENT:
    _vendor = SIBLING_DB / "vendor"
    if _vendor.is_dir() and str(_vendor) not in sys.path:
        sys.path.insert(0, str(_vendor))
    # plugin_database/__init__ 依赖宿主 SDK，不能整包加载；只加载 adapters 与
    # hot_schema 两个子模块（它们的相对导入指向 adapters 包内部）。
    _load_package(
        "plugin_database.adapters",
        SIBLING_DB / "adapters" / "__init__.py",
        SIBLING_DB / "adapters",
    )
    _hot_spec = importlib.util.spec_from_file_location(
        "plugin_database.hot_schema", SIBLING_DB / "hot_schema.py"
    )
    _hot_mod = importlib.util.module_from_spec(_hot_spec)
    sys.modules["plugin_database.hot_schema"] = _hot_mod
    _hot_spec.loader.exec_module(_hot_mod)


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
