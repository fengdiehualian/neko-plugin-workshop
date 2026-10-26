"""运行时启停必须互斥：``disconnect()`` 不许插进 ``connect()`` 中间。

**这条测试钉的现场**（2026-09-27 00:18:53，插件日志 + ``_error.log``）::

    runtime_ops_service.py:73   await self.plugin.qq_client.connect()
    qq_open_plat.py:307         ws_url = await self._get_gateway_url()
    qq_open_plat.py:928         resp = await self._http.get(...)
    AttributeError: 'NoneType' object has no attribute 'get'

自启路径正卡在 ``connect()`` 内部的 token 网络请求上（宿主 303 行建 ``_http``、304 行
发请求、307 行才用它），另一个入口并发调 ``start_auto_reply`` 时看到连接对象的
``mode`` 与刚改好的配置不符，走"模式不匹配 → 断开旧连接重建"，``disconnect()``
（宿主 366-381 行）把 ``_http`` 关掉并置空 —— 前一个 ``connect()`` 从 await 醒来就
对 ``None`` 取 ``.get``。宿主 ``connect()`` 每个 await 之后都不复检，所以竞态只能以
``AttributeError`` 的面目出现。

下面用假连接对象复刻这个时序：``connect()`` 卡在闸门上时并发 ``stop_runtime``，
断言 ``disconnect`` **必须晚于** ``connect`` 返回。
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
from types import MethodType, SimpleNamespace

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
from plugin.plugins.qq_auto_reply import runtime_ops_service as runtime_ops_module
from plugin.plugins.qq_auto_reply.runtime_ops_service import QQRuntimeOpsService
from plugin.plugins.qq_auto_reply.runtime_transition import (
    RuntimeTransitionGuard,
    runtime_transition_guard,
)
from plugin.sdk.plugin import Ok

# ── 假件 ────────────────────────────────────────────────────────────

class _FakeClient:
    """按宿主 ``qq_open_plat`` 的形状做的连接对象：``disconnect`` 会把 ``http`` 置空。"""

    def __init__(self, mode: str = "open_platform", *, gate: asyncio.Event | None = None,
                 events: list[str] | None = None):
        self.mode = mode
        self.needs_attention = False
        self.http: object | None = object()      # connect() 303 行建出来的那个客户端
        self._gate = gate
        self.events = events if events is not None else []
        self.connected = False

    def set_inbound_sink(self, _sink) -> None:  # 插件会挂广播钩子
        pass

    async def connect(self) -> None:
        self.events.append("connect:enter")
        if self._gate is not None:
            await self._gate.wait()              # 模拟 token / 网关请求
        if self.http is None:
            # 宿主 928 行那句，字面复刻
            raise AttributeError("'NoneType' object has no attribute 'get'")
        self.connected = True
        self.events.append("connect:done")

    async def disconnect(self) -> None:
        self.events.append("disconnect")
        self.http = None
        self.connected = False


async def _noop(*_a, **_k):
    return None


def _plugin(client, *, mode: str = "open_platform"):
    """装够 ``start_auto_reply`` / ``stop_runtime`` 走完所需的最小字段。"""
    plugin = SimpleNamespace(
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                               error=lambda *a, **k: None, debug=lambda *a, **k: None,
                               exception=lambda *a, **k: None),
        _emit_log=lambda level, msg: None,
        _qq_settings={"qq_connection_mode": mode},
        qq_client=client,
        _running=False,
        _message_task=None,
        _session_housekeeping_task=None,
        _handler_tasks=set(),
        _session_locks={},
        _user_sessions={},
        _group_memory_sync_tasks=[],
        _prompt_change_discard_tasks=[],
        attention_service=None,
        attention_gate_service=None,
        reply_buffer_service=None,
        napcat_service=SimpleNamespace(clear_startup_error=lambda: None,
                                       get_startup_error=lambda: ""),
        settings_service=SimpleNamespace(ensure_identity_scope_declared=lambda *a, **k: None),
        i18n=SimpleNamespace(t=lambda key, default="", **kw: default or key),
        _process_messages=_noop,
        _session_housekeeping_loop=_noop,
        _broadcast_qq_inbound=_noop,
        _describe_reply_image=None,
        _transcribe_voice=None,
        enricher=None,
    )
    plugin._make_qq_connection = lambda: _FakeClient(mode=mode)
    plugin._ensure_qq_client_initialized = MethodType(
        QQAutoReplyPlugin._ensure_qq_client_initialized, plugin)
    plugin._restart_auto_reply_runtime = MethodType(
        QQAutoReplyPlugin._restart_auto_reply_runtime, plugin)
    plugin._stop_auto_reply_runtime = MethodType(
        QQAutoReplyPlugin._stop_auto_reply_runtime, plugin)
    plugin.runtime_ops_service = QQRuntimeOpsService(plugin)
    return plugin


# ── 闸门本身 ────────────────────────────────────────────────────────

def test_guard_is_shared_for_the_same_owner():
    plugin = _plugin(_FakeClient())

    assert runtime_transition_guard(plugin) is runtime_transition_guard(plugin)


def test_nested_hold_from_same_task_is_reentrant():
    """收尾重建是「停 → 丢对象 → 启」，持闸期间必然再停一次、启一次。"""

    async def run():
        guard = RuntimeTransitionGuard()
        async with guard.hold("外层"):
            assert guard.held and guard.depth == 1
            async with guard.hold("内层"):
                assert guard.depth == 2
            assert guard.held and guard.depth == 1
        assert not guard.held and guard.depth == 0
        assert guard.waits == 0, "同任务重入不算排队"

    asyncio.run(run())


def test_second_task_waits_and_the_wait_is_logged():
    async def run():
        noted: list[str] = []
        guard = RuntimeTransitionGuard(SimpleNamespace(info=noted.append))
        entered: list[str] = []
        release = asyncio.Event()

        async def holder():
            async with guard.hold("启动自动回复"):
                entered.append("holder")
                await release.wait()

        async def waiter():
            async with guard.hold("停止运行时"):
                entered.append("waiter")

        first = asyncio.create_task(holder())
        await asyncio.sleep(0)
        second = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert entered == ["holder"], "第二个任务必须排队，不能并行改连接对象"
        assert guard.waits == 1
        assert any("等待中" in line and "停止运行时" in line for line in noted)

        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        assert entered == ["holder", "waiter"]
        assert not guard.held

    asyncio.run(run())


# ── 现场复刻：stop 不许插进 connect 中间 ─────────────────────────────

def test_stop_cannot_disconnect_a_connecting_client():
    async def run():
        gate = asyncio.Event()
        client = _FakeClient(gate=gate)
        plugin = _plugin(client)
        service = plugin.runtime_ops_service

        starting = asyncio.create_task(service.start_auto_reply())
        await asyncio.sleep(0)
        assert client.events == ["connect:enter"], "连接已进到 await 里"

        stopping = asyncio.create_task(service.stop_runtime(stop_napcat=False))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "disconnect" not in client.events, (
            "停止插进了 connect 中间 —— 宿主那边就是 _http 被置空后 AttributeError"
        )

        gate.set()
        started, _ = await asyncio.wait_for(asyncio.gather(starting, stopping), timeout=5)

        assert started.is_ok(), started
        assert client.events == ["connect:enter", "connect:done", "disconnect"]
        assert plugin._running is False, "stop 等到 start 落地后才真的停"
        assert client.http is None

    asyncio.run(run())


def test_stop_auto_reply_sees_the_start_it_waited_for():
    """``not_running`` 早退必须在闸门内判定，否则用户点停止会被在飞的启动绕过。"""

    async def run():
        gate = asyncio.Event()
        client = _FakeClient(gate=gate)
        plugin = _plugin(client)
        service = plugin.runtime_ops_service

        starting = asyncio.create_task(service.start_auto_reply())
        await asyncio.sleep(0)
        stopping = asyncio.create_task(service.stop_auto_reply())
        await asyncio.sleep(0)
        gate.set()
        await asyncio.wait_for(starting, timeout=5)
        stopped = await asyncio.wait_for(stopping, timeout=5)

        assert stopped.is_ok() and stopped.value == {"status": "stopped"}, stopped
        assert client.events[-1] == "disconnect"
        assert plugin._running is False

    asyncio.run(run())


def test_restart_holds_the_guard_and_shares_it_with_a_running_start():
    """收尾重建与入口启动共用一把锁：重建期间别的启动必须排队。"""

    async def run():
        plugin = _plugin(_FakeClient())
        guard = runtime_transition_guard(plugin)
        touched: list[str] = []

        async def _stop(*, stop_napcat):
            touched.append("stop")

        async def _start():
            touched.append("start")
            return Ok({"status": "started"})

        plugin._stop_auto_reply_runtime = _stop
        plugin.runtime_ops_service = SimpleNamespace(start_auto_reply=_start)

        async with guard.hold("启动自动回复"):
            restart = asyncio.create_task(plugin._restart_auto_reply_runtime(True))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert touched == [], "重建必须等闸门，不能插进在飞的启停"

        out = await asyncio.wait_for(restart, timeout=5)
        assert touched == ["stop", "start"], "闸门一放就按 停 → 启 的顺序走完"
        assert out == {"ok": True, "status": "started", "error": ""}, out

    asyncio.run(run())


def test_restart_does_not_self_lock():
    """整链真跑一遍：内层 stop/start 重入同一把锁，不能自锁死。"""

    async def run():
        gate = asyncio.Event()
        gate.set()
        created: list[_FakeClient] = []
        discarded = _FakeClient(mode="napcat")

        def factory():
            client = _FakeClient(mode="napcat", gate=gate)
            created.append(client)
            return client

        plugin = _plugin(discarded, mode="napcat")
        plugin._make_qq_connection = factory

        out = await asyncio.wait_for(plugin._restart_auto_reply_runtime(True), timeout=5)

        assert out == {"ok": True, "status": "started", "error": ""}, out
        assert discarded.events == ["disconnect"], "旧对象被停掉"
        assert len(created) == 1, "停 → 丢对象 → 用新对象启"
        assert created[0].events == ["connect:enter", "connect:done"]
        assert runtime_transition_guard(plugin).held is False, "跑完必须放闸"

    asyncio.run(run())


# ── 断口守卫：四个入口都得持闸 ──────────────────────────────────────

def test_every_transition_entry_point_holds_the_guard():
    entries = {
        "start_auto_reply": QQRuntimeOpsService.start_auto_reply,
        "stop_auto_reply": QQRuntimeOpsService.stop_auto_reply,
        "stop_runtime": QQRuntimeOpsService.stop_runtime,
        "_restart_auto_reply_runtime": QQAutoReplyPlugin._restart_auto_reply_runtime,
    }
    for name, func in entries.items():
        source = inspect.getsource(func)
        assert "runtime_transition_guard" in source and ".hold(" in source, (
            f"{name} 丢了启停互斥闸门 —— 这正是 2026-09-27 那条 AttributeError 的入口"
        )


def test_mode_mismatch_rebuild_is_logged():
    """重建连接对象必须留痕：这次排查就卡在"重建静默发生"。"""
    source = pathlib.Path(runtime_ops_module.__file__).read_text(encoding="utf-8")

    assert "连接模式不匹配" in source
