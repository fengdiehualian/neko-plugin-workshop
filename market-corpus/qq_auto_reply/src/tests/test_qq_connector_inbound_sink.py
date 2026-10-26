"""The QQ connector's inbound broadcast hook (``set_inbound_sink``).

方案 A：适配器把入站消息广播出去。任何插件 ``conn.set_inbound_sink(sink)`` 后，
连接层每收到一条规范化消息就调 ``sink(message)`` 一次（尽力而为，异常吞掉）——
这是「适配器可被任何插件链接」的接收面。qq_auto_reply 用它把消息推进
``bus.messages`` 供其它插件订阅。
"""
from __future__ import annotations

import asyncio

# 连接器经 connector_seam 解析（宿主优先、回退插件内置副本），因此本文件在两种宿主上
# 都能真跑，不再需要 importorskip 静默跳过。
from plugin.plugins.qq_auto_reply import connector_seam as _qq_conn

OneBotClient = _qq_conn.OneBotClient
QQOpenPlatformConnection = _qq_conn.QQOpenPlatformConnection
create_onebot_connection = _qq_conn.create_onebot_connection


def _make_onebot_client() -> OneBotClient:
    return OneBotClient(onebot_url="ws://0.0.0.0:6199", emit_log=lambda *a, **k: None)


async def test_set_inbound_sink_receives_dispatched_message():
    client = _make_onebot_client()
    received: list = []

    async def sink(msg):
        received.append(msg)

    client.set_inbound_sink(sink)
    await client._dispatch_inbound({"message_type": "group", "content": "hi"})
    # Dispatch is fire-and-forget (background task): drain it before asserting.
    await asyncio.gather(*list(getattr(client, "_inbound_sink_tasks", ())))

    assert received == [{"message_type": "group", "content": "hi"}]


async def test_dispatch_noop_when_no_sink():
    client = _make_onebot_client()
    # no sink set → _dispatch_inbound must not raise and must not cache anything
    await client._dispatch_inbound({"message_type": "group"})
    assert client.inbound_sink is None


async def test_sink_exception_is_swallowed():
    client = _make_onebot_client()

    async def sink(_msg):
        raise RuntimeError("boom")

    client.set_inbound_sink(sink)
    await client._dispatch_inbound({"message_type": "group"})  # must not raise


async def test_set_inbound_sink_none_clears():
    client = _make_onebot_client()
    client.set_inbound_sink(lambda m: m)
    assert client.inbound_sink is not None
    client.set_inbound_sink(None)
    assert client.inbound_sink is None


async def test_open_platform_connector_has_same_hook():
    # QQOpenPlatformConnection also inherits the hook from OneBotConnectionBase.
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    received: list = []

    async def sink(msg):
        received.append(msg)

    conn.set_inbound_sink(sink)
    await conn._dispatch_inbound({"message_type": "private", "content": "yo"})
    # Dispatch is fire-and-forget (background task): drain it before asserting.
    await asyncio.gather(*list(getattr(conn, "_inbound_sink_tasks", ())))
    assert received == [{"message_type": "private", "content": "yo"}]


def test_sink_attached_before_connect_via_factory():
    # The factory builds the concrete connection; a plugin sets the sink on it
    # after creation. Sanity: the returned object exposes set_inbound_sink.
    conn = create_onebot_connection({"qq_connection_mode": "napcat"})
    assert hasattr(conn, "set_inbound_sink")
    assert conn.inbound_sink is None
