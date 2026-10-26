"""pytest 配置：将插件目录与 N.E.K.O 源码根加入 sys.path，使测试可直接导入模块。"""
import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

# 从插件目录向上找 N.E.K.O 源码根（含 plugin 包），提供 plugin.plugins.steam_manager
# 宿主入口。本地与 CI（workflow 将插件 mount 进 N.E.K.O 树）均适用。
_cand = _PLUGIN_DIR
while True:
    parent = os.path.dirname(_cand)
    if parent == _cand:
        break
    if os.path.isdir(os.path.join(_cand, "plugin")) and os.path.isfile(
            os.path.join(_cand, "plugin", "__init__.py")):
        if _cand not in sys.path:
            sys.path.insert(0, _cand)
        break
    _cand = parent
