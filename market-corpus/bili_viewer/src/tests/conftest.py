"""pytest 配置：从插件目录向上定位 N.E.K.O 源码根并注入 sys.path。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parent.parent  # .../plugins/bili_viewer
_PLUGINS_ROOT = _PLUGIN_DIR.parent  # .../plugins
_NEKO_ROOT = _PLUGINS_ROOT.parent  # N.E.K.O 源码根（含 plugin/ 包）

for _p in (_NEKO_ROOT, _PLUGIN_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


@pytest.fixture(autouse=True)
def _no_background_sync(monkeypatch) -> None:
    """v0.9.32 测试隔离：默认禁用后台实时感知线程。

    BiliViewerPlugin 经 NekoPluginBase 包装后 SdkContext._host_ctx 恒非 None，
    _start_sync_watcher 会真实启动线程；v0.9.31 起线程首轮即执行
    _bridge_reply_tick 扫真实 outbox（存在 done 回执时向 fake_ctx push），
    污染所有断言 pushed 数量的测试。统一设 BILLI_SYNC_DISABLE=1 禁用线程，
    需要验证的 tick（_unread_tick/_bridge_reply_tick）测试均直接调用方法。
    """
    monkeypatch.setenv("BILLI_SYNC_DISABLE", "1")
