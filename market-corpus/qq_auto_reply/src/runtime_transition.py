"""运行时启停的互斥闸门 —— ``connect()`` 与 ``disconnect()`` 不许交叠。

**为什么要这个**（2026-09-27 00:18:53 的现场）::

    runtime_ops_service.py:73   await self.plugin.qq_client.connect()
    qq_open_plat.py:307         ws_url = await self._get_gateway_url()
    qq_open_plat.py:928         resp = await self._http.get(...)
    AttributeError: 'NoneType' object has no attribute 'get'

宿主 ``qq_open_plat.connect`` 的顺序是「303 行建 ``_http`` → 304 行发 token 网络请求 →
307 行才用它拿网关」。自启路径正卡在 304 那个请求上时，同一进程里另一个入口（配置保存
改了连接模式）并发进来调 ``start_auto_reply``：它看到连接对象的 ``mode`` 与配置不符，
走"模式不匹配 → 断开旧连接重建"分支，``disconnect()``（宿主 366-381 行）把 ``_http``
关掉并置空 —— 前一个 ``connect()`` 从 await 醒来后对 ``None`` 取 ``.get``。宿主的
``connect()`` 在每个 await 之后都不复检 ``_http`` / ``_closing``，所以这条竞态只以
``AttributeError`` 的面目出现，而不是一句干净的"连接已取消"。

闸门挂在**插件对象**上，三个入口共用同一把：``start_auto_reply`` / ``stop_auto_reply``
/ ``stop_runtime`` / ``_restart_auto_reply_runtime``。

**同一个任务重入不算重入**：收尾重建是「停 → 丢对象 → 启」，它持闸期间必然再调
``stop_runtime`` 与 ``start_auto_reply``，普通 ``asyncio.Lock`` 会当场自锁死。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


class RuntimeTransitionGuard:
    """可重入（按任务归属判定）的启停互斥。"""

    def __init__(self, logger: Any = None):
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0
        self._logger = logger
        #: 观测：有多少次启停真的排了队。>0 说明撞上并发，日志里有对应一行。
        self.waits = 0

    @property
    def held(self) -> bool:
        return self._lock.locked()

    @property
    def owner(self) -> asyncio.Task | None:
        """当前持闸任务（无则 None）。"""
        return self._owner

    @property
    def depth(self) -> int:
        """持闸任务的进入层数（0 = 无人持有）。"""
        return self._depth

    def _note(self, message: str) -> None:
        logger = self._logger
        if logger is None:
            return
        try:
            logger.info(message)
        except Exception:
            # 观测失败不该挡住启停：这是纯诊断通道。
            pass

    @asynccontextmanager
    async def hold(self, what: str = "") -> AsyncIterator[None]:
        """持闸执行一段启停。``what`` 只进日志，用来分辨是谁在等谁。"""
        task = asyncio.current_task()
        if self._depth and self._owner is task:
            # 同一任务重入：收尾重建持闸期间还要停一次、启一次。
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        if self._lock.locked():
            self.waits += 1
            self._note(f"[运行时] 上一个启停还没结束，等待中（{what or '未命名'}）")
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        try:
            yield
        finally:
            self._depth = 0
            self._owner = None
            self._lock.release()


def runtime_transition_guard(owner: Any) -> RuntimeTransitionGuard:
    """取（首次调用时惰性挂上）``owner`` 的启停闸门。

    惰性且挂在传入对象上，是为了两条约束同时成立：

    1. 插件与各服务必须共用**同一把**锁 —— 服务一律传 ``self.plugin``；
    2. 只拿 ``SimpleNamespace`` 当插件桩的测试不必预先补这个字段。

    注意别把同一个桩跨两次 ``asyncio.run`` 复用：``asyncio.Lock`` 一旦被某个事件
    循环取用就绑死在它上面，换循环会直接报错。
    """
    guard = getattr(owner, "_runtime_transition_guard", None)
    if isinstance(guard, RuntimeTransitionGuard):
        return guard
    guard = RuntimeTransitionGuard(getattr(owner, "logger", None))
    try:
        owner._runtime_transition_guard = guard
    except Exception:
        # 桩对象不可写时退化成"每次一把新锁"（等于关掉互斥）——不抛，
        # 免得好端端的启停因为一个诊断设施挂掉。
        pass
    return guard
