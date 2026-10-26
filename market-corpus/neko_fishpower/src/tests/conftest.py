"""测试路径注入：让 core/adapters 可直接导入（importlib 模式下 rootdir 不在 sys.path）。"""

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
