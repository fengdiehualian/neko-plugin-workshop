"""把插件纯逻辑子包注册成轻量顶层包，绕开会拉 SDK 的根 __init__。"""

from __future__ import annotations

import pathlib
import sys
import types

_PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent

if "neko_calabiqiu" not in sys.modules:
    _pkg = types.ModuleType("neko_calabiqiu")
    _pkg.__path__ = [str(_PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules["neko_calabiqiu"] = _pkg

if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))
