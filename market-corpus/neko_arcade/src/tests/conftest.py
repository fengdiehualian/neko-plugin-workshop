"""pytest 引导: 本地独立跑时建立 plugins.neko_arcade 包链。

CI(verify)里插件已被 mount 到 N.E.K.O 树, ``plugin.plugins.neko_arcade``
可直接导入, 本文件不做事; 本地(dev 不在 N.E.K.O 树)手动建链, 使
测试里的 ``from plugin.plugins.neko_arcade...`` 完整路径导入可用。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 已安装为 plugin.plugins.neko_arcade(CI 场景)时无需建链
try:
    _installed = importlib.util.find_spec("plugin.plugins.neko_arcade") is not None
except (ImportError, ModuleNotFoundError):
    _installed = False

if not _installed:
    # 本地独立跑: 手动建 plugins 包链, 模拟 N.E.K.O 树内的 plugin.plugins.neko_arcade
    _plugin_root = types.ModuleType("plugin")
    _plugin_root.__path__ = []
    sys.modules["plugin"] = _plugin_root

    _plugins_pkg = types.ModuleType("plugin.plugins")
    _plugins_pkg.__path__ = []
    sys.modules["plugin.plugins"] = _plugins_pkg

    _arcade_pkg = types.ModuleType("plugin.plugins.neko_arcade")
    _arcade_pkg.__path__ = [str(ROOT)]
    sys.modules["plugin.plugins.neko_arcade"] = _arcade_pkg

    _games_pkg = types.ModuleType("plugin.plugins.neko_arcade.games")
    _games_pkg.__path__ = [str(ROOT / "games")]
    sys.modules["plugin.plugins.neko_arcade.games"] = _games_pkg

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
