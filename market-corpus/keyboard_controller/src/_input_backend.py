"""平台输入后端选择器。

win32 与 linux 后端实现同构公开接口；插件主模块通过
``from ._input_backend import backend as win32`` 使用，所有既有调用点零改动。
不支持的平台上 ``backend`` 为 None，工具层由 ``_supported()`` 守卫早退。
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    from . import _win32_input as backend  # noqa: F401
elif sys.platform.startswith("linux"):
    from . import _linux_input as backend  # noqa: F401
else:
    backend = None  # type: ignore[assignment]
