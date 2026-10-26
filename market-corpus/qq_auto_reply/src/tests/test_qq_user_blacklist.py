# -*- coding: utf-8 -*-
"""用户黑名单：被拉黑的人说什么都不进管线。

**为什么要有这条**（2026-09-27 使用者要求）：「黑名单的用户在群里发言的时候需要过滤掉
不发给猫娘」。级别放在 ``PermissionManager`` 里（``blacklist``，与 admin/trusted/normal
同一份名单 —— 控制台那张「信任用户」表就是管理入口），拦截点在
``message_dispatcher.handle_message`` **最前面**，三个位置要求缺一不可：

* 在**戳一戳分支之前**：那个分支会直接 ``send_group_poke`` 然后 return ——
  放后面就拦不住"回戳"；
* 在 **backlog 记录之前**：否则黑名单用户的话仍会进 backlog，被「回溯补回」
  在焦点切换时喂给猫娘；
* 在 **enrichment（VLM/STT/引用链）之前**：省一遍开销，也不给内容被别处引用的机会。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.message_dispatcher import QQMessageDispatcher
from plugin.plugins.qq_auto_reply.permission import PermissionManager

# ── 级别本身 ────────────────────────────────────────────────────────

def test_blacklist_is_a_valid_user_level():
    manager = PermissionManager([{"qq": "111", "level": "blacklist"}])

    assert manager.get_permission_level("111") == "blacklist"
    assert manager.is_blacklisted("111") is True


def test_blacklist_has_no_privilege():
    manager = PermissionManager([
        {"qq": "111", "level": "blacklist"},
        {"qq": "222", "level": "trusted"},
    ])

    assert manager.is_trusted("111") is False, "黑名单不能算信任用户"
    assert manager.is_admin("111") is False
    assert manager.is_trusted("222") is True


def test_level_survives_a_round_trip_through_list_users():
    manager = PermissionManager([{"qq": "111", "level": "blacklist", "nickname": "广告号"}])
    listed = manager.list_users()

    assert listed == [{"qq": "111", "level": "blacklist", "nickname": "广告号"}]
    assert PermissionManager(listed).is_blacklisted("111") is True


# ── 派发层拦截 ──────────────────────────────────────────────────────

class _Recorder:
    def __init__(self, *, level: str):
        self.calls: list = []
        self.backlog: list = []
        self.pokes: list = []
        self.group_handoffs: list = []
        self.emitted: list = []
        self.enricher = None
        self.backlog_service = SimpleNamespace(record_message=self._record_backlog)
        self.permission_mgr = SimpleNamespace(get_permission_level=lambda qq: level)
        self.qq_client = SimpleNamespace(
            needs_attention=True,
            self_id="999",
            send_group_poke=self._poke,
            is_group_muted=lambda gid: False,
        )
        self._qq_settings = {"backlog_labels": []}
        self.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
        self._emit_log = lambda level_, msg: self.emitted.append((level_, msg))

    #: 深管线里最先用到的那几个插件能力（控制组要能一路走到派发点）
    _sanitize_message_text = staticmethod(lambda text, **kw: text)
    _build_session_key = staticmethod(lambda **kw: "session")
    _user_sessions: dict = {}

    async def _record_backlog(self, message):
        self.backlog.append(message)

    async def _poke(self, *args, **kwargs):
        self.pokes.append((args, kwargs))

    async def _maybe_notify_backlog_summary(self, *args, **kwargs):
        return None


def _dispatcher(level: str) -> tuple[QQMessageDispatcher, _Recorder]:
    plugin = _Recorder(level=level)
    dispatcher = QQMessageDispatcher(plugin)

    async def _handoff(*args, **kwargs):
        plugin.group_handoffs.append((args, kwargs))

    # 只替换"再往下就是整条群聊管线"的那一步：控制组要证明的是
    # **非黑名单用户真的走到了这一步**，而不是把整条管线都搭出来。
    dispatcher.handle_group_message = _handoff
    return dispatcher, plugin


def test_group_message_from_a_blacklisted_user_is_dropped_before_anything():
    dispatcher, plugin = _dispatcher("blacklist")

    asyncio.run(dispatcher.handle_message({
        "message_type": "group", "group_id": "G1", "user_id": "111",
        "content": "在吗", "raw_message": "在吗", "message_id": "m1",
    }))

    assert plugin.backlog == [], "黑名单用户的消息不该进 backlog（会被回溯补回喂给猫娘）"
    assert any("用户黑名单过滤" in msg for _lvl, msg in plugin.emitted), plugin.emitted


def test_poke_from_a_blacklisted_user_does_not_get_poked_back():
    """戳一戳分支在更下面，且会直接 send_group_poke —— 拦截必须在它之前。"""
    dispatcher, plugin = _dispatcher("blacklist")

    asyncio.run(dispatcher.handle_message({
        "message_type": "notice", "notice_type": "poke", "group_id": "G1",
        "user_id": "111", "target_id": "999",
    }))

    assert plugin.pokes == [], "黑名单用户戳猫娘，猫娘不该回戳"
    assert plugin.backlog == []


def test_private_message_from_a_blacklisted_user_is_dropped():
    dispatcher, plugin = _dispatcher("blacklist")

    asyncio.run(dispatcher.handle_message({
        "message_type": "private", "user_id": "111", "content": "在吗", "message_id": "m2",
    }))

    assert plugin.backlog == []


def test_normal_user_still_flows_through():
    """对照组：同一个入口，非黑名单用户必须照常往下走（不然就是整条管线被关了）。"""
    dispatcher, plugin = _dispatcher("trusted")

    asyncio.run(dispatcher.handle_message({
        "message_type": "group", "group_id": "G1", "user_id": "222",
        "content": "在吗", "raw_message": "在吗", "message_id": "m3",
    }))

    assert plugin.backlog, "非黑名单用户的消息被一起拦了 —— 拦多了"
    assert plugin.group_handoffs, "非黑名单用户没走到群聊派发点 —— 拦多了"
    assert not any("用户黑名单过滤" in msg for _lvl, msg in plugin.emitted)


def test_no_permission_manager_means_nobody_is_blacklisted():
    """轻量宿主 / 单测桩没有权限管理器时不能误伤。"""
    dispatcher, plugin = _dispatcher("trusted")
    plugin.permission_mgr = None

    assert dispatcher._is_blacklisted_user("111") is False
