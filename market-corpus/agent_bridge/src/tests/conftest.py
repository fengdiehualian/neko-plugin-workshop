"""让 agent_bridge 的测试无需宿主 SDK 即可运行。

`bridge` 包本身是纯标准库的（这正是它可被单独测试的原因），但 pytest 会把插件根
目录当作测试包的父级导入，从而触发 ``agent_bridge/__init__.py`` → ``plugin.sdk``
（只有宿主环境才有）。这里在收集前把插件根目录插到 ``sys.path`` 并预先把
``agent_bridge`` 注册成一个「只有 __path__ 的命名空间包」，跳过那个 __init__。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# 只暴露 __path__，让 `import agent_bridge.bridge` 可用但不执行插件 __init__。
if "agent_bridge" not in sys.modules:
    pkg = types.ModuleType("agent_bridge")
    pkg.__path__ = [str(PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules["agent_bridge"] = pkg
