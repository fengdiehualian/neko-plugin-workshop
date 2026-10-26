# pyright: reportMissingImports=false
"""v0.14.1 目标解析链（`services/lanlan.py`）的门。

起因是实机：面板切完档位点「现在注一条」报 `awareness_no_target`。根因两条——
宿主给面板动作的 `_ctx` 里只有 run_id，而旧实现只读 conversations 存储
（普通话轮根本不写它）且只认 `lanlan_name`（memory 桶里的键叫 `lanlan`）。
"""

from __future__ import annotations

import pytest
from sticker_manager.services.lanlan import LanlanResolver, lanlan_of, latest_lanlan, records_of


class _List:
    """假 SDK bus 列表：只暴露 dump_records()，形状与 SdkBusList 对偶。"""

    def __init__(self, records):
        self._records = records

    def dump_records(self):
        return list(self._records)


class _Namespace:
    """两个桶的公共部分：**签名严格照 SDK**——宽容的 `**kwargs` 桩曾把真 bug 遮了三轮
    （v0.14.1 起 `_from_bus` 给两个桶都塞了 `{max_count, limit, bucket_id}`，
    真机上两边各抛 TypeError，整级静默失效，全靠 HTTP 兜住）。
    """

    def __init__(self, records=None, *, error=None):
        self._records = records
        self._error = error
        self.calls = 0
        self.last_kwargs: dict | None = None

    def _run(self, kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        return _List(self._records or [])


class _ConvNamespace(_Namespace):
    """`SdkConversationsBus.get(*, conversation_id, max_count, since_ts, timeout)`。"""

    def get(
        self,
        *,
        conversation_id: str | None = None,
        max_count: int = 50,
        since_ts: float | None = None,
        timeout: float = 5.0,
    ):
        return self._run(
            {
                "conversation_id": conversation_id,
                "max_count": max_count,
                "since_ts": since_ts,
                "timeout": timeout,
            }
        )


class _MemNamespace(_Namespace):
    """`SdkMemoryBus.get(*, bucket_id, limit, timeout)`——没有 max_count，bucket_id 必填。"""

    def get(self, *, bucket_id: str, limit: int = 20, timeout: float = 5.0):
        return self._run({"bucket_id": bucket_id, "limit": limit, "timeout": timeout})


class _Bus:
    def __init__(self, conversations=None, memory=None):
        self.conversations = conversations
        self.memory = memory


class _Ctx:
    def __init__(self, lanlan=""):
        self._current_lanlan = lanlan


class _Plugin:
    def __init__(self, bus=None, ctx=None):
        self.bus = bus
        self.ctx = ctx


def _conv(ts: float, name: str):
    return {"timestamp": ts, "lanlan_name": name}


@pytest.fixture
def enable_http():
    """打开宿主 HTTP 这一级（全局夹具默认关着）；用例都必须自己打桩 `_fetch_blocking`。"""
    import sticker_manager.services.lanlan as mod

    previous = mod.HTTP_ENABLED
    mod.HTTP_ENABLED = True
    try:
        yield mod
    finally:
        mod.HTTP_ENABLED = previous


class TestChain:
    def test_hint_short_circuits_bus_and_http(self, run_async):
        conv = _ConvNamespace([_conv(1.0, "M")])
        resolver = LanlanResolver(_Plugin(bus=_Bus(conversations=conv)))
        resolver._fetch_blocking = lambda: (_ for _ in ()).throw(AssertionError("不该问 HTTP"))
        assert run_async(resolver.resolve("K")) == "K"
        assert conv.calls == 0

    def test_conversation_record_target(self, run_async):
        plugin = _Plugin(bus=_Bus(conversations=_ConvNamespace([_conv(1.0, "M"), _conv(9.0, "K")])))
        assert run_async(LanlanResolver(plugin).resolve()) == "K"

    def test_memory_bucket_uses_the_lanlan_key(self, run_async):
        # memory 桶的角色键叫 `lanlan`（不是 lanlan_name）——旧实现因此永远读不到它。
        plugin = _Plugin(bus=_Bus(memory=_MemNamespace([{"_ts": 3.0, "lanlan": "小春"}])))
        assert run_async(LanlanResolver(plugin).resolve()) == "小春"

    def test_target_drifts_instead_of_being_pinned(self, run_async):
        conv = _ConvNamespace([_conv(1.0, "M")])
        resolver = LanlanResolver(_Plugin(bus=_Bus(conversations=conv)))
        assert run_async(resolver.resolve()) == "M"
        conv._records = [_conv(99.0, "K")]
        assert run_async(resolver.resolve()) == "K", "总线结果不许被缓存钉住"

    def test_http_is_the_fallback_and_is_cached_both_ways(self, run_async, enable_http):
        resolver = LanlanResolver(_Plugin(bus=_Bus()))
        calls = []

        def fake_fetch():
            calls.append(1)
            return "Nao"

        resolver._fetch_blocking = fake_fetch
        assert run_async(resolver.resolve()) == "Nao"
        assert run_async(resolver.resolve()) == "Nao"
        assert len(calls) == 1, "15s TTL 内不该再问宿主"

    def test_unreachable_host_is_cached_as_failure(self, run_async, enable_http):
        resolver = LanlanResolver(_Plugin(bus=_Bus(), ctx=_Ctx("粘滞卡")))

        def boom():
            raise OSError("connection refused")

        resolver._fetch_blocking = boom
        assert run_async(resolver.resolve()) == "粘滞卡", "HTTP 不通要退到 ctx 粘滞值"
        assert run_async(resolver.resolve()) == "粘滞卡"

    def test_both_buckets_get_only_their_own_kwargs(self, run_async):
        # 真机踩过：两个桶都塞 {max_count, limit, bucket_id} → 各抛 TypeError → 整级静默。
        conv = _ConvNamespace([_conv(9.0, "K")])
        mem = _MemNamespace([{"_ts": 3.0, "lanlan": "小春"}])
        plugin = _Plugin(bus=_Bus(conversations=conv, memory=mem))
        assert run_async(LanlanResolver(plugin).resolve()) == "K"
        assert set(conv.last_kwargs) == {"conversation_id", "max_count", "since_ts", "timeout"}
        assert conv.calls == 1 and mem.calls == 0  # 上一级给了名字就不往下读

    def test_memory_bucket_is_read_with_its_own_signature(self, run_async):
        mem = _MemNamespace([{"_ts": 3.0, "lanlan": "小春"}])
        plugin = _Plugin(bus=_Bus(conversations=_ConvNamespace([]), memory=mem))
        assert run_async(LanlanResolver(plugin).resolve()) == "小春"
        assert set(mem.last_kwargs) == {"bucket_id", "limit", "timeout"}
        assert mem.last_kwargs["bucket_id"] == "default"  # 必填：不给就是 TypeError

    def test_wrapped_sdk_records_are_unwrapped_before_reading(self, run_async):
        # dump_records() 给对象序列时，不 unwrap 就全数丢掉——症状与传错 kwargs 一样静默。
        class _Rec:
            def __init__(self, payload):
                self.payload = payload

        mem = _MemNamespace([])
        mem._records = [_Rec({"_ts": 5.0, "lanlan": "包装卡"})]
        plugin = _Plugin(bus=_Bus(conversations=_ConvNamespace([]), memory=mem))
        assert run_async(LanlanResolver(plugin).resolve()) == "包装卡"

    def test_bus_read_failure_degrades_not_raises(self, run_async):
        plugin = _Plugin(bus=_Bus(conversations=_ConvNamespace(error=RuntimeError("bus down"))))
        resolver = LanlanResolver(plugin)
        resolver._fetch_blocking = lambda: ""
        assert run_async(resolver.resolve()) == ""


class TestPureHelpers:
    def test_lanlan_of_reads_both_key_names_and_metadata(self):
        assert lanlan_of({"lanlan_name": "A"}) == "A"
        assert lanlan_of({"lanlan": "B"}) == "B"
        assert lanlan_of({"metadata": {"lanlan": "C"}}) == "C"
        assert lanlan_of({"lanlan_name": "  "}) == ""
        assert lanlan_of({}) == ""

    def test_latest_lanlan_picks_newest_and_survives_junk(self):
        records = [{"timestamp": 5.0, "lanlan": "旧"}, {"timestamp": 10 ** 400, "lanlan": "坏戳"}, {"nope": 1}]
        assert latest_lanlan(records) == "旧"
        assert latest_lanlan([]) == ""

    def test_records_of_accepts_dumpable_and_plain_shapes(self):
        assert records_of(_List([{"lanlan": "X"}])) == [{"lanlan": "X"}]
        assert records_of([{"lanlan": "Y"}]) == [{"lanlan": "Y"}]
        assert records_of(None) == []
        assert records_of("nonsense") == []

    def test_fallback_http_is_bounded_by_the_panel_budget(self):
        import sticker_manager.services.lanlan as mod

        # 面板 context 的总预算是 5s：兜底 HTTP 若比它长，一次宿主不可达就能拖死刷新。
        assert 0 < mod._HTTP_TIMEOUT_SEC < 5.0
