"""把插件根目录加入 sys.path，使 tests 可以直接 import core / fetchers。

插件主体 __init__.py 依赖 N.E.K.O SDK（plugin.sdk.plugin），
SDK 缺席时会退化为空实现垫片，因此冒烟测试在独立环境也能运行。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
