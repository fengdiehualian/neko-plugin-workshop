"""一键部署的**步骤顺序**。

反向模式下插件是**监听**方：先把监听竖起来，NapCat 起来后拨进来即可。
顺序反过来，NapCat 会先往一个还没人听的端口拨，白失败几轮（正向模式更明显，
直接报连接错误）。

源码级断言：跑整条 `deploy()` 要铺下载/解包/启动/等码一串替身，而这里要钉的
只是**两步的相对位置**，源码足够精确。
"""
from __future__ import annotations

import inspect
import re

from plugin.plugins.qq_auto_reply.deploy_service import QQDeployService


def _deploy_src() -> str:
    return inspect.getsource(QQDeployService.deploy)


def test_auto_reply_starts_before_napcat():
    src = _deploy_src()
    runtime_at = src.index("await self.plugin._restart_auto_reply_runtime")
    napcat_at = src.index("await svc.ensure_napcat_started")
    assert runtime_at < napcat_at, (
        "自动回复必须在启动 NapCat **之前**拉起 —— 反向模式下先竖起监听，"
        "NapCat 起来就能拨进来")


def test_write_config_still_precedes_both():
    """写配置仍在最前：NapCat 会热读，但先写好能省一次往返。"""
    src = _deploy_src()
    assert src.index("await self._persist_connection_settings") < src.index(
        "await self.plugin._restart_auto_reply_runtime")


def test_auto_start_is_gated_by_the_switch():
    """`auto_start=False` 时不该动运行时 —— 只写配置、只拉 NapCat。"""
    src = _deploy_src()
    assert re.search(r"if\s+auto_start:", src), "收尾没有被开关门控"


def test_entry_passes_the_switch_through_instead_of_restarting_again():
    """入口层只把开关传下去。它若自己再启一次，会白停一次刚建好的连接。"""
    from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin

    src = inspect.getsource(QQAutoReplyPlugin._deploy_one_click)
    assert "auto_start=bool(auto_start)" in src
    assert "_restart_auto_reply_runtime" not in src, "入口层不该再启一次"
