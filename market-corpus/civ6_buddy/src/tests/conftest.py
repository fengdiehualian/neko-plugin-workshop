"""pytest 配置：从插件目录向上定位 N.E.K.O 源码根并注入 sys.path。"""

from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent  # .../plugins/civ6_buddy
_PLUGINS_ROOT = _PLUGIN_DIR.parent  # .../plugins
_NEKO_ROOT = _PLUGINS_ROOT.parent  # N.E.K.O 源码根（含 plugin/ 包）

for _p in (_NEKO_ROOT, _PLUGIN_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
