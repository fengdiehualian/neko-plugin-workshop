"""连接对象换掉之后，enricher 必须跟着换绑 —— 否则引用正文静默丢出 prompt。

**现场**（2026-09-27 00:41:06 - 00:58:42，插件 ``_error.log`` 共 6 条）::

    enrichment.py:551           data = await self._client.get_msg(rid)
    TypeError: QQOpenPlatformConnection.get_msg() takes 1 positional argument but 2 were given

当时 NapCat 早就连上了（同一条消息的 ``call_action response: get_msg status=ok`` 是活着的
连接器发的），可 enricher 手里还是 00:18:49 建的那个开放平台对象：``_ensure_qq_client_
initialized`` 只在 ``self.enricher is None`` 时建一次，而收尾重建（切换连接模式 / 一键
部署 / 补写配置）会把 ``qq_client`` 整个丢掉重造。后果是引用链、合并转发、语音、文件
增强全部打向一条已断开的连接，异常被 ``_fetch_reply_content`` 的 ``except Exception``
吞成一行日志 —— **用户看到的是"引用她的话"没进 prompt**。
"""

from __future__ import annotations

import asyncio
import inspect
from types import MethodType, SimpleNamespace

from plugin.plugins.qq_auto_reply import QQAutoReplyPlugin
from plugin.plugins.qq_auto_reply.enrichment import QQMessageEnricher


class _OpenPlatformLikeClient:
    """开放平台形状：``get_msg`` 不收参数（这就是那行 TypeError 的来源）。"""

    mode = "open_platform"
    needs_attention = False

    def __init__(self, tag: str):
        self.tag = tag
        self.calls: list[str] = []

    def set_inbound_sink(self, _sink) -> None:
        pass

    async def get_msg(self):  # noqa: D401 - 故意不收参数
        self.calls.append("get_msg")
        return {}


class _OneBotLikeClient(_OpenPlatformLikeClient):
    mode = "napcat"

    async def get_msg(self, message_id):  # noqa: D401 - OneBot 形状
        self.calls.append(f"get_msg:{message_id}")
        return {"message_id": message_id}


async def _noop(*_a, **_k):
    return None


def _plugin(created: list):
    def factory():
        client = _OneBotLikeClient(f"c{len(created)}")
        created.append(client)
        return client

    plugin = SimpleNamespace(
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None,
                               error=lambda *a, **k: None, debug=lambda *a, **k: None,
                               exception=lambda *a, **k: None),
        _emit_log=lambda level, msg: None,
        qq_client=None,
        enricher=None,
        _broadcast_qq_inbound=_noop,
        _describe_reply_image=None,
        _transcribe_voice=None,
    )
    plugin._make_qq_connection = factory
    plugin._ensure_qq_client_initialized = MethodType(
        QQAutoReplyPlugin._ensure_qq_client_initialized, plugin)
    return plugin


def test_enricher_rebinds_when_client_object_is_replaced():
    async def run():
        created: list = []
        plugin = _plugin(created)

        plugin._ensure_qq_client_initialized()
        first = plugin.qq_client
        assert plugin.enricher._client is first

        # 收尾重建：丢对象 → 新对象（不换绑的话 enricher 就停在 first 上）
        plugin.qq_client = None
        plugin._ensure_qq_client_initialized()
        second = plugin.qq_client

        assert second is not first
        assert plugin.enricher._client is second, "enricher 还指着被丢弃的连接对象"
        assert created == [first, second]

    asyncio.run(run())


def test_enricher_object_is_not_recreated():
    """重绑而不是重建：VLM/STT 回调与 emit_log 是构造时注入的，重建会丢。"""

    async def run():
        plugin = _plugin([])
        plugin._ensure_qq_client_initialized()
        enricher = plugin.enricher
        seen: list[str] = []
        enricher._emit_log = lambda level, msg: seen.append(msg)

        plugin.qq_client = None
        plugin._ensure_qq_client_initialized()

        assert plugin.enricher is enricher
        assert enricher._emit_log is not None and seen == [], "重绑本身不必发日志"

    asyncio.run(run())


def test_rebind_is_idempotent_for_the_same_client():
    async def run():
        plugin = _plugin([])
        plugin._ensure_qq_client_initialized()
        client = plugin.qq_client

        monkey = SimpleNamespace(calls=0)
        real_rebind = plugin.enricher.rebind

        def counting_rebind(target):
            monkey.calls += 1
            real_rebind(target)

        plugin.enricher.rebind = counting_rebind
        plugin._ensure_qq_client_initialized()   # 对象没换 → 不该重绑

        assert monkey.calls == 0
        assert plugin.enricher._client is client

    asyncio.run(run())


def test_quote_enrichment_hits_the_live_client_after_rebuild():
    """端到端：换绑之后引用链真的打到新连接上（旧调用会撞 TypeError）。"""

    async def run():
        plugin = _plugin([])
        plugin._ensure_qq_client_initialized()
        stale = _OpenPlatformLikeClient("stale")
        plugin.enricher.rebind(stale)            # 复刻"指针停在旧对象上"

        plugin.qq_client = None
        plugin._ensure_qq_client_initialized()
        live = plugin.qq_client

        message = {"raw_message": "[CQ:reply,id=42] 在吗"}
        await plugin.enricher._fetch_reply_content(message, ["42"])

        assert live.calls == ["get_msg:42"], "引用链没走到活着的连接对象上"
        assert stale.calls == []

    asyncio.run(run())


def test_rebind_api_exists_and_is_used_at_the_single_construction_site():
    assert hasattr(QQMessageEnricher, "rebind")
    source = inspect.getsource(QQAutoReplyPlugin._ensure_qq_client_initialized)
    assert "rebind(" in source, "换绑分支被删了 —— 引用正文又会静默丢出 prompt"
