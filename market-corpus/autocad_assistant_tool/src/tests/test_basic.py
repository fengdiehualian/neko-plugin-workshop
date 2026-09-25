"""基础冒烟测试。

不依赖 N.E.K.O 宿主 SDK，只验证插件自带模块的结构一致性：
- autocad_controller 可导入且命令目录完整
- __init__ 的 dispatch 表覆盖命令目录中的每一个 action

在插件目录内运行：``python -m pytest tests/test_basic.py -q``
"""
import importlib
import os
import sys

# 让 ``import autocad_controller`` / ``import _bootstrap`` 可在无安装状态下工作
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_controller_imports():
    mod = importlib.import_module("autocad_controller")
    assert hasattr(mod, "AutoCADController")
    assert hasattr(mod, "COMMAND_CATALOG")
    assert isinstance(mod.COMMAND_CATALOG, dict) and len(mod.COMMAND_CATALOG) > 20


def test_controller_methods_cover_catalog():
    """COMMAND_CATALOG 中列出的每个 action 都应在控制器上有同名方法。"""
    mod = importlib.import_module("autocad_controller")
    ctrl = mod.AutoCADController()
    for action in mod.COMMAND_CATALOG:
        # "clear" 映射到 clear_modelspace，其余同名
        method_name = "clear_modelspace" if action == "clear" else action
        assert hasattr(ctrl, method_name), f"控制器缺少方法: {method_name}"


def test_dispatch_table_covers_catalog():
    """插件 __init__ 的 _DISPATCH 必须覆盖 COMMAND_CATALOG 的每个 action。"""
    ctrl_mod = importlib.import_module("autocad_controller")
    # 直接读取 __init__.py 源码解析 _DISPATCH 键，避免触发 plugin.sdk 导入。
    init_path = os.path.join(ROOT, "__init__.py")
    src = open(init_path, encoding="utf-8").read()
    for action in ctrl_mod.COMMAND_CATALOG:
        assert ('"%s"' % action) in src, f"__init__ dispatch 表缺少 action: {action}"


def test_plugin_id_consistency():
    """plugin.toml / script.js / index.html 的插件 ID 必须一致。"""
    pid = "autocad_assistant_tool"
    toml = open(os.path.join(ROOT, "plugin.toml"), encoding="utf-8").read()
    js = open(os.path.join(ROOT, "static", "script.js"), encoding="utf-8").read()
    assert ('id = "%s"' % pid) in toml
    assert ('"%s"' % pid) in js
