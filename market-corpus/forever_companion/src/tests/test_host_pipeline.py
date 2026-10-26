"""1.3.2 开箱即用：宿主 LLM 管线通道（services/host_llm.py + [fragments/review].mode）。

覆盖三件事，每件都是本次改造的立项理由：

1. **宿主管线可用时不再读盘拼端点**——免费路由的端点/模型名/key 只在宿主 profile
   默认值里、不在 core_config.json 的存盘字段里，读盘那条路（tone_slot）天生看不境；
   现在把 prompt 交回宿主的 `aget_model_api_config` + `create_chat_llm_async`，
   与宿主内置插件同一条路，零配置即可用；
2. **回落不破**——宿主模块不可 import（独立仓库跑测试、宿主改版挪走内部模块）时
   一律退回直连，默认通道不可能比 1.3.1 更差；本文件里"没有宿主模块"的用例是
   这条回落的活体证明（独立环境本来就什么都没有）；
3. **mode 是用户留的操作空间**，不是历史包袱——`custom` 仍走读盘直连（接本地模型/
   独立服务商），面板状态灯与 `transport` 如实报"这次实际走了哪条路"。

宿主模块用 sys.modules 桩注入（monkeypatch.setitem 自动回收），插件运行时走的是
宿主真实模块：两侧共用同一条 import 语句，桩不会与真模块并存。
"""

from __future__ import annotations

import asyncio
import sys
import types


def run(coro):
    return asyncio.run(coro)


def host_llm(tm):
    """宿主管线服务模块（与 tm.services.tone_slot 同一取法：经已加载的插件包属性）。"""
    return tm.services.host_llm


FREE_ROUTE_CFG = {
    # Steam 免费版的典型形态：assist 走宿主内部代理，槽位自己没有存盘 URL/ModelId
    "coreApi": "free",
    "assistApi": "free",
    "summaryModelProvider": "follow_assist",
    "summaryModelId": "",
}


# ---------------------------------------------------------------------------
# 宿主模块桩
# ---------------------------------------------------------------------------


class _FakeClient:
    """宿主 create_chat_llm_async 返回的客户端替身（ainvoke/aclose 同签名）。"""

    def __init__(self, content="ok", exc=None):
        self.prompts = []
        self.closed = 0
        self._content = content
        self._exc = exc

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self._exc is not None:
            raise self._exc
        return types.SimpleNamespace(content=self._content)

    async def aclose(self):
        self.closed += 1


class _FakeConfigManager:
    def __init__(self, api_cfg, calls, exc=None):
        self._api_cfg = api_cfg
        self._calls = calls
        self._exc = exc

    async def aensure_region_resolved(self, timeout: float = 1.5) -> bool:
        self._calls.append("region")
        return True

    async def aget_model_api_config(self, model_type: str, *, core_config=None) -> dict:
        self._calls.append(f"config:{model_type}")
        if self._exc is not None:
            raise self._exc
        return self._api_cfg


def _install_host(monkeypatch, *, api_cfg=None, client=None, calls=None, config_exc=None):
    """把 utils.config_manager / utils.llm_client / utils.token_tracker 桩进 sys.modules。

    与 tests/conftest.py 桩宿主 SDK 同一手法：运行时 import 语句原样生效，
    只是解析到假模块；monkeypatch 撤销后 sys.modules 回到本文件外的世界。
    """
    calls = calls if calls is not None else []
    utils_pkg = types.ModuleType("utils")
    cm_mod = types.ModuleType("utils.config_manager")
    llm_mod = types.ModuleType("utils.llm_client")
    tt_mod = types.ModuleType("utils.token_tracker")

    cm = _FakeConfigManager(api_cfg or {}, calls, exc=config_exc)
    cm_mod.get_config_manager = lambda: cm
    created: list[dict] = []

    async def _create_chat_llm_async(model, base_url, api_key, **kwargs):
        created.append({"model": model, "base_url": base_url, "api_key": api_key, **kwargs})
        if client is None:
            raise RuntimeError("no client configured")
        return client

    llm_mod.create_chat_llm_async = _create_chat_llm_async
    tracked: list[str] = []
    tt_mod.set_call_type = lambda call_type: tracked.append(call_type)

    utils_pkg.config_manager = cm_mod
    utils_pkg.llm_client = llm_mod
    utils_pkg.token_tracker = tt_mod

    for name, mod in (
        ("utils", utils_pkg),
        ("utils.config_manager", cm_mod),
        ("utils.llm_client", llm_mod),
        ("utils.token_tracker", tt_mod),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return calls, created, tracked


# ---------------------------------------------------------------------------
# resolve_host_slot：解析归宿主
# ---------------------------------------------------------------------------


def test_resolve_host_slot_reads_host_config(monkeypatch, tm) -> None:
    calls, _created, _tracked = _install_host(
        monkeypatch,
        api_cfg={
            "model": "free-model",
            "base_url": "https://www.lanlan.app/text/v1",
            "api_key": "free-access",
            "provider_type": "openai",
        },
        calls=[],
    )
    resolved = run(host_llm(tm).resolve_host_slot("summary", logger=None))
    assert resolved == {
        "model": "free-model",
        "api_key": "free-access",
        "base_url": "https://www.lanlan.app/text/v1",
        "provider_type": "openai",
        "transport": tm._CHANNEL_TRANSPORT_HOST,
    }
    # 区域改写先等于把免费路由 URL 交给宿主判定（冷启动子进程不能抢在探测前读端点）
    assert calls == ["region", "config:summary"]


def test_resolve_host_slot_rejects_incomplete_and_unknown(monkeypatch, tm) -> None:
    _install_host(monkeypatch, api_cfg={"model": "m", "base_url": "", "api_key": "k"})
    assert run(host_llm(tm).resolve_host_slot("summary")) is None  # 缺 base_url
    # 非文本槽位不开：tts/realtime/image 是宿主音频栈的进程级单一身份约束槽
    assert run(host_llm(tm).resolve_host_slot("tts")) is None
    assert run(host_llm(tm).resolve_host_slot("realtime")) is None


def test_resolve_host_slot_degrades_without_host_modules(monkeypatch, tm) -> None:
    """宿主内部模块不可用（独立仓库安装、宿主改版把模块挪走）→ None，由调用方回落直连。

    "缺席"必须显式造出来，不能指望恰好跑在哪个环境：官方市场 CI 把本仓挂进宿主
    包树跑 `check -r`，那里 `utils.config_manager` 不但可导入，还会读出开发者本机
    的 core_config——把它当环境前提会让这条测试换一个环境就换个含义。
    """
    monkeypatch.setitem(sys.modules, "utils.config_manager", None)  # import 即 ImportError
    assert run(host_llm(tm).resolve_host_slot("summary")) is None


def test_resolve_host_slot_swallows_config_errors(monkeypatch, tm) -> None:
    logs = []

    class _Logger:
        def warning(self, msg, *args):
            logs.append(str(msg))

    _install_host(monkeypatch, api_cfg={}, config_exc=RuntimeError("boom https://u:pw@x/v1"))
    assert run(host_llm(tm).resolve_host_slot("summary", logger=_Logger())) is None
    # 脱敏契约：异常原文（可能含带凭据的 URL）不进日志，只留类型名
    assert logs and "boom" not in logs[0]
    assert "RuntimeError" in logs[0]


# ---------------------------------------------------------------------------
# chat_via_host：调用归宿主，失败静默
# ---------------------------------------------------------------------------


def test_chat_via_host_invokes_and_closes(monkeypatch, tm) -> None:
    client = _FakeClient(content='{"capture": true}')
    _calls, created, tracked = _install_host(monkeypatch, api_cfg={}, client=client)
    resolved = {
        "model": "free-model",
        "api_key": "free-access",
        "base_url": "https://www.lanlan.app/text/v1",
        "provider_type": "openai",
        "transport": tm._CHANNEL_TRANSPORT_HOST,
    }
    out = run(
        host_llm(tm).chat_via_host(
            resolved, "prompt", timeout_sec=15.0, max_completion_tokens=400,
            call_type="plugin_fragment_capture",
        )
    )
    assert out == '{"capture": true}'
    assert client.closed == 1  # 客户端必须关，否则每次调用漏一个连接
    assert created[0]["provider_type"] == "openai"  # 协议判定跟着槽位走
    assert created[0]["timeout"] == 15.0 and created[0]["max_completion_tokens"] == 400
    assert client.prompts[0] == [{"role": "user", "content": "prompt"}]  # 自定义 prompt 原样进
    # 归集声明要照内置插件的写法发出（宿主子进程未装记账补丁，目前无消费者：host_llm 注释）
    assert tracked == ["plugin_fragment_capture"]


def test_chat_via_host_failure_is_silent_and_redacted(monkeypatch, tm) -> None:
    logs: list[str] = []

    class _Logger:
        def warning(self, msg, *args):
            logs.append(str(msg))

    client = _FakeClient(exc=RuntimeError("401 https://u:pw@lanlan.tech/text/v1"))
    _install_host(monkeypatch, api_cfg={}, client=client)
    out = run(
        host_llm(tm).chat_via_host(
            {"model": "m", "base_url": "http://x", "api_key": "k"}, "p",
            timeout_sec=1.0, max_completion_tokens=10, logger=_Logger(),
        )
    )
    assert out is None
    assert client.closed == 1  # 抛了异常也要关
    assert logs and "lanlan" not in logs[0] and "u:pw" not in logs[0]


def test_chat_via_host_empty_reply_degrades(monkeypatch, tm) -> None:
    _install_host(monkeypatch, api_cfg={}, client=_FakeClient(content="   "))
    assert run(
        host_llm(tm).chat_via_host(
            {"model": "m", "base_url": "http://x", "api_key": ""}, "p",
            timeout_sec=1.0, max_completion_tokens=10,
        )
    ) is None


def test_host_timeout_can_be_reported_without_leaking_response(monkeypatch, tm):
    import pytest

    api_timeout = type("APITimeoutError", (Exception,), {})
    client = _FakeClient(exc=api_timeout("SECRET_REQUEST"))
    _install_host(monkeypatch, api_cfg={}, client=client)
    logs = []
    logger = types.SimpleNamespace(warning=lambda message: logs.append(message))
    with pytest.raises(TimeoutError):
        run(host_llm(tm).chat_via_host(
            {"model": "m", "base_url": "http://x", "api_key": ""},
            "PRIVATE_MATERIAL", timeout_sec=1, max_completion_tokens=10,
            logger=logger, raise_timeout=True,
        ))
    assert client.closed == 1
    assert "APITimeoutError" in "\n".join(logs)
    assert "SECRET_REQUEST" not in "\n".join(logs) and "PRIVATE_MATERIAL" not in "\n".join(logs)


def test_direct_timeout_can_be_reported_to_manual_writer(monkeypatch, tm):
    import urllib.request

    import pytest

    def timed_out(*_, **__):
        raise TimeoutError("SECRET_ENDPOINT")

    monkeypatch.setattr(urllib.request, "urlopen", timed_out)
    with pytest.raises(TimeoutError) as error:
        tm._post_chat_completion("http://x", "", "m", "PRIVATE", raise_timeout=True)
    assert not str(error.value)


def test_wrapped_direct_timeout_is_distinct_from_connection_failure(monkeypatch, tm):
    import urllib.error
    import urllib.request

    import pytest

    def timed_out(*_, **__):
        raise urllib.error.URLError(TimeoutError("SECRET_ENDPOINT"))

    monkeypatch.setattr(urllib.request, "urlopen", timed_out)
    assert tm._post_chat_completion("http://x", "", "m", "PRIVATE") is None
    with pytest.raises(TimeoutError) as error:
        tm._post_chat_completion("http://x", "", "m", "PRIVATE", raise_timeout=True)
    assert not str(error.value)
    assert not tm.services.tone_slot._is_request_timeout(urllib.error.URLError("connection refused"))


def test_host_cancellation_closes_client_and_propagates(monkeypatch, tm):
    class WaitingClient(_FakeClient):
        async def ainvoke(self, prompt):
            await asyncio.Event().wait()

    client = WaitingClient()
    _install_host(monkeypatch, api_cfg={}, client=client)

    async def scenario():
        task = asyncio.create_task(host_llm(tm).chat_via_host(
            {"model": "m", "base_url": "http://x", "api_key": ""},
            "PRIVATE", timeout_sec=1, max_completion_tokens=10, raise_timeout=True,
        ))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("host transport swallowed cancellation")

    run(scenario())
    assert client.closed == 1


# ---------------------------------------------------------------------------
# mode 归一化与通道分派
# ---------------------------------------------------------------------------


def test_channel_mode_normalizes_everything_but_custom_to_host(tm) -> None:
    assert tm._channel_mode({}) == "host"
    assert tm._channel_mode({"mode": ""}) == "host"
    assert tm._channel_mode({"mode": "HOST"}) == "host"  # 大小写不误入 custom
    assert tm._channel_mode({"mode": "宿主管线"}) == "host"  # 写错值悄悄退回默认，不退回旧行为
    assert tm._channel_mode({"mode": " custom "}) == "custom"


def test_host_channel_hit_never_reads_disk(plugin_factory, tm) -> None:
    resolved = {
        "model": "free-model", "api_key": "free-access",
        "base_url": "https://www.lanlan.app/text/v1",
        "transport": tm._CHANNEL_TRANSPORT_HOST,
    }
    p = plugin_factory()

    async def fake_host_slot(slot):
        return resolved

    def boom():
        raise AssertionError("宿主管线给得出端点就不该再读盘拼一遍")

    p._resolve_host_slot = fake_host_slot
    p._aload_core_config = boom
    out, slot, core_cfg = run(p._resolve_channel_endpoint({"slot": "summary"}, default_slot="summary"))
    assert out is resolved and slot == "summary" and core_cfg == {}


def test_host_channel_falls_back_to_direct(plugin_factory, tm) -> None:
    """宿主模块缺席/槽位无模型 → 回落直连，且把 core_cfg 带回给休眠诊断用。"""
    p = plugin_factory()

    async def no_host(slot):
        return None

    direct = {"model": "m", "api_key": "k", "base_url": "http://local:11434/v1"}
    p._resolve_host_slot = no_host
    p._load_core_config = lambda: dict(FREE_ROUTE_CFG)
    p._resolve_tone_slot = lambda cfg, slot, _seen=frozenset(): direct
    out, slot, core_cfg = run(
        p._resolve_channel_endpoint({"slot": "summary", "mode": "host"}, default_slot="summary")
    )
    assert out is direct and core_cfg == FREE_ROUTE_CFG
    # custom 模式根本不问宿主（问都不问，才是"我就是要自己直连"）
    asked = []

    async def spy_host(slot):
        asked.append(slot)
        return direct

    p._resolve_host_slot = spy_host
    run(p._resolve_channel_endpoint({"slot": "summary", "mode": "custom"}, default_slot="summary"))
    assert asked == []


def test_channel_chat_dispatches_by_transport(plugin_factory, tm) -> None:
    p = plugin_factory()
    host_calls, direct_calls = [], []

    async def fake_host(resolved, prompt, **kw):
        host_calls.append((resolved, prompt, kw))
        return "HOST"

    def fake_direct(*args, **kwargs):
        direct_calls.append((args, kwargs))
        return "DIRECT"

    p._chat_via_host = fake_host
    p._post_chat_completion = fake_direct
    host_resolved = {"model": "m", "api_key": "k", "base_url": "http://x",
                     "transport": tm._CHANNEL_TRANSPORT_HOST}
    got = run(p._channel_chat(host_resolved, "p", timeout_sec=11.0,
                              max_completion_tokens=22, call_type="ct"))
    assert got == "HOST" and direct_calls == []
    assert host_calls[0][2] == {"timeout_sec": 11.0, "max_completion_tokens": 22, "call_type": "ct"}
    # 1.3.1 形状的 resolved 没有 transport 键 → 按直连处理（tests 打桩锚点不破）
    got = run(p._channel_chat({"model": "m", "api_key": "k", "base_url": "http://x"}, "p",
                              timeout_sec=13.0, max_completion_tokens=22, call_type="ct"))
    assert got == "DIRECT" and host_calls and len(direct_calls) == 1
    assert direct_calls[0][0] == ("http://x", "k", "m", "p")
    # 超时值经通道层透到 urllib 形参（不再写死 15 秒）
    assert direct_calls[0][1] == {"timeout_sec": 13.0}


def test_direct_completion_passes_timeout_to_urllib(monkeypatch, tm) -> None:
    seen: dict = {}

    class _Resp:
        def read(self):
            return b'{"choices": [{"message": {"content": "hi"}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    out = tm._post_chat_completion("http://x/v1", "", "m", "p", timeout_sec=25.0)
    assert out == "hi" and seen["timeout"] == 25.0
    # 默认值仍是 1.3.1 的 15 秒（未显式给超时的调用面行为不变）
    seen.clear()
    tm._post_chat_completion("http://x/v1", "", "m", "p")
    assert seen["timeout"] == 15.0


# ---------------------------------------------------------------------------
# 端到端：免费路由下碎片与状态灯（本次改造的主张）
# ---------------------------------------------------------------------------


def _capture_setup(p, reply_json: str, *, host_resolved) -> None:
    shard = p._current_shard()

    async def fake_poll(shard_, *, lanlan="", peek=False, advance=True):
        shard_.last_recent_marker = "2:new"
        return ("我最喜欢你做的饭", "真的吗嘿嘿")

    async def fake_host_slot(slot):
        return host_resolved

    async def fake_host_chat(resolved, prompt, **kw):
        return reply_json

    p._poll_recent_turns = fake_poll
    p._resolve_host_slot = fake_host_slot
    p._chat_via_host = fake_host_chat
    p._load_core_config = lambda: dict(FREE_ROUTE_CFG)
    # 这是"宿主管线"那条路的专测，所以走法显式设回 host（conftest 把业务用例的默认
    # 钉成 custom）：不显式写就会在 fixture 改动后悄悄变成"custom + 一套用不上的桩"
    p._fragments_cfg["mode"] = "host"
    shard.last_fragment_marker = "1:old"
    return shard


HOST_RESOLVED = {
    "model": "free-model", "api_key": "free-access",
    "base_url": "https://www.lanlan.app/text/v1",
    "provider_type": "openai", "transport": "host",
}


def test_fragment_capture_works_on_free_route_with_host_pipeline(plugin_factory, tm) -> None:
    """立项主张：宿主在用免费路由、core_config 里什么都没有时，碎片提取照样记进日记。"""
    p = plugin_factory()
    shard = _capture_setup(
        p, '{"capture": true, "kind": "like", "quote": "最喜欢你做的饭", "confidence": 0.9}',
        host_resolved=HOST_RESOLVED,
    )
    assert run(p._maybe_capture_fragments("default", shard)) is True
    assert shard.diary[0]["quote"] == "最喜欢你做的饭"
    assert p.store.data[tm._diary_key("default")][-1]["source"] == "auto"


def test_fragment_capture_still_dormant_on_custom_free_route(plugin_factory) -> None:
    """操作空间反面：用户坚持 custom 时，免费路由下依旧拼不出端点——休眠并给出真指引。"""
    p = plugin_factory()
    shard = _capture_setup(p, '{"capture": true}', host_resolved=HOST_RESOLVED)
    p._fragments_cfg["mode"] = "custom"
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert shard.diary == []


def test_fragment_capture_falls_back_to_direct_when_host_unavailable(plugin_factory) -> None:
    """宿主管线缺席（本用例就是独立环境的样子）→ 回落直连，1.3.1 的行为一字不丢。"""
    p = plugin_factory()
    shard = _capture_setup(p, '{"capture": true}', host_resolved=None)
    p._resolve_tone_slot = lambda cfg, slot, _seen=frozenset(): {
        "model": "m", "api_key": "k", "base_url": "http://local:11434/v1"
    }
    posted = []
    p._post_chat_completion = lambda *a, **kw: posted.append(a) or '{"capture": true, "kind": "important", "quote": "q", "confidence": 0.9}'
    assert run(p._maybe_capture_fragments("default", shard)) is True
    assert posted and posted[0][0] == "http://local:11434/v1"


def test_dashboard_channel_light_is_green_on_free_route(plugin_factory, tm) -> None:
    """状态灯与运行时同源：宿主管线给得出端点就是绿灯 + transport=host，哪怕 core_config 全空。"""
    p = plugin_factory()

    async def fake_host_slot(slot):
        return HOST_RESOLVED

    p._resolve_host_slot = fake_host_slot
    p._load_core_config = lambda: dict(FREE_ROUTE_CFG)
    # 专测宿主管线的灯：两条通道的走法都显式设成 host（conftest 把业务用例钉成 custom）
    p._fragments_cfg["mode"] = "host"
    p._review_cfg["mode"] = "host"
    payload = run(p.dashboard())
    cs = payload["channel_status"]
    assert cs["fragments"] == {
        "enabled": True, "dormant_reason": "", "transport": tm._CHANNEL_TRANSPORT_HOST,
    }
    assert cs["review"]["transport"] == tm._CHANNEL_TRANSPORT_HOST
    # 语气默认经宿主情感端点，transport 一并如实标出（没有 mode 旋钮也有走法可报）
    assert cs["tone"]["transport"] == tm._CHANNEL_TRANSPORT_HOST


def test_dashboard_reports_fallback_and_custom_mode(plugin_factory, tm) -> None:
    """灯要区分"宿主管线没生效"与"用户就要直连"：前者是回落，后者按直连诊断报因。"""
    p = plugin_factory()

    async def no_host(slot):
        return None

    p._resolve_host_slot = no_host
    p._load_core_config = lambda: dict(FREE_ROUTE_CFG)
    cs = run(p.dashboard())["channel_status"]
    # 默认 host 却回落直连：free_route 诊断照报（面板据此出"已回落直连"灯，不骗用户说正常）
    assert cs["fragments"]["dormant_reason"] == "free_route"
    assert cs["fragments"]["transport"] == tm._CHANNEL_TRANSPORT_DIRECT

    p._fragments_cfg["mode"] = "custom"
    cs = run(p.dashboard())["channel_status"]
    assert cs["fragments"]["dormant_reason"] == "free_route"
    assert cs["fragments"]["transport"] == tm._CHANNEL_TRANSPORT_DIRECT
    # 语气选了非默认槽位 = 直连（本次没碰它的行为，只补 transport 口径）
    p._emotion_sense_cfg["slot"] = "conversation"
    cs = run(p.dashboard())["channel_status"]
    assert cs["tone"]["transport"] == tm._CHANNEL_TRANSPORT_DIRECT


def test_update_settings_roundtrips_mode(plugin_factory, tm) -> None:
    p = plugin_factory()
    res = run(p.update_settings(fragments_mode="custom", review_mode="host"))
    assert not isinstance(res, tm.Err)
    assert p._fragments_cfg["mode"] == "custom"
    assert p._review_cfg["mode"] == "host"
    snap = p._settings_snapshot(p._current_shard())
    assert snap["fragments_mode"] == "custom" and snap["review_mode"] == "host"
    bad = run(p.update_settings(fragments_mode="宿主管线"))
    assert isinstance(bad, tm.Err) and "invalid_fragments_mode" in str(bad.error)
    assert p._fragments_cfg["mode"] == "custom"  # 被拒的写入不落盘


def test_resolved_provider_url_reads_flat_key(tm) -> None:
    """宿主 resolvedProviderUrls 是扁平 "scope:provider" 键；嵌套读法恒空（1.3.2 修的 bug）。"""
    tone_slot = tm.services.tone_slot
    flat = {"resolvedProviderUrls": {"assist:qwen": "https://assist.example.com/v1"}}
    nested_shape = {"resolvedProviderUrls": {"assist": {"qwen": "https://x"}}}
    assert tone_slot._resolved_provider_url(flat, "assist", "qwen") == "https://assist.example.com/v1"
    assert tone_slot._resolved_provider_url(nested_shape, "assist", "qwen") == ""
    assert tone_slot._resolved_provider_url({"resolvedProviderUrls": "坏了"}, "assist", "qwen") == ""
    # 这条链修好的是 custom 模式：付费服务商下 follow_assist 终于解析得出端点
    paid = {
        "coreApi": "qwen", "assistApi": "qwen", "assistApiKeyQwen": "sk-x",
        "summaryModelProvider": "follow_assist", "summaryModelId": "qwen-sum",
        "resolvedProviderUrls": {"assist:qwen": "https://assist.example.com/v1"},
    }
    resolved = tm._resolve_tone_slot(paid, "summary")
    assert resolved == {
        "model": "qwen-sum", "api_key": "sk-x", "base_url": "https://assist.example.com/v1",
    }

def test_host_slot_resolution_is_cached_per_slot(plugin_factory, tm, monkeypatch) -> None:
    """一个槽位一次读盘：面板 5s 轮询问两通道 + tick 再问，不能每次都重开 core_config.json。

    桩打在**服务模块**上（不是主类委托）：委托经模块对象现取解析函数，
    所以两层都能被桩到——这条同时是"晚绑定不破"的回归门。
    """
    calls: list[str] = []

    async def fake_resolve(slot, logger=None):
        calls.append(slot)
        return {"model": "m", "api_key": "k", "base_url": "http://x",
                "transport": tm._CHANNEL_TRANSPORT_HOST}

    monkeypatch.setattr(tm.services.host_llm, "resolve_host_slot", fake_resolve)
    p = plugin_factory()
    first = run(p._resolve_host_slot("summary"))
    second = run(p._resolve_host_slot("summary"))
    assert first is second and calls == ["summary"]
    assert run(p._resolve_host_slot("conversation")) is not None
    assert calls == ["summary", "conversation"]  # 按槽位分开缓存，不共用一个格子
    # 过期即重取（TTL 与 core_config 缓存同一把尺子）
    ts = p._host_slot_cache["summary"][1] - tm._CORE_CONFIG_CACHE_TTL - 1
    p._host_slot_cache["summary"] = (first, ts)
    run(p._resolve_host_slot("summary"))
    assert calls == ["summary", "conversation", "summary"]


# ---------------------------------------------------------------------------
# 通道熔断（1.3.3 修订轮）
#
# 立项理由是实机数字：免费路由的文字档从插件调用 15 次全被服务端拒（400 "STOP
# ABUSE"），碎片那条约每 62 秒撞一次，而面板一路绿灯。所以这一层的契约有两条：
# **不再发请求**（第一条）与**灯要说真话**（第二条）。两条都配了反向对照——
# 把桩换成"肯定成功"的回复后如果调用真发了出去，说明熔断根本没生效，测试必红。
# ---------------------------------------------------------------------------


def _fuse_setup(p, *, reply):
    """碎片通道的宿主管线桩：每次调用返回 reply（None = 请求层面失败）。"""
    shard = p._current_shard()
    calls: list[str] = []
    attempts = [0]

    async def fake_poll(shard_, *, lanlan="", peek=False, advance=True):
        # 水位按"尝试次数"推进而不是按"实际发出次数"：被闸挡下的那些拍也算见过新内容，
        # 否则反向对照（抬高预算后再试一次）会因为"marker 没变"被当成没有新素材挡掉
        attempts[0] += 1
        shard_.last_recent_marker = f"{attempts[0] + 1}:m"
        return ("我最喜欢你做的饭", "真的吗嘿嘿")

    async def fake_host_slot(slot):
        return HOST_RESOLVED

    async def fake_chat(resolved, prompt, **kw):
        calls.append(prompt)
        return reply() if callable(reply) else reply

    p._poll_recent_turns = fake_poll
    p._resolve_host_slot = fake_host_slot
    p._chat_via_host = fake_chat
    p._load_core_config = lambda: dict(FREE_ROUTE_CFG)
    p._fragments_cfg["mode"] = "host"
    # 熔断专测走 summary 档：出厂默认现在是 agent，那条路上还压着日预算与 300 秒地板
    # （由本文件下半截的 test_agent_tier_* 专测），留着会让"连着两次"这一步被地板挡掉
    p._fragments_cfg["slot"] = "summary"
    # 熔断要看的是"连着几次"，所以拆掉 60 秒最小间隔这道闸（否则第二次根本进不来）
    p._fragments_cfg["min_interval_sec"] = 0
    shard.last_fragment_marker = "1:old"
    return shard, calls


def test_fragment_channel_fuses_after_two_rejections(plugin_factory) -> None:
    p = plugin_factory()
    shard, calls = _fuse_setup(p, reply=None)
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert len(calls) == 2

    async def would_succeed(resolved, prompt, **kw):
        calls.append(prompt)
        return '{"capture": true, "kind": "like", "quote": "q", "confidence": 0.9}'

    p._chat_via_host = would_succeed
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert len(calls) == 2  # 反向对照：熔断开着，一次都不许多发
    assert shard.diary == []


def test_fuse_releases_when_the_route_changes(plugin_factory) -> None:
    """改槽位/改通道 = 换了一条路：旧账必须作废，否则用户改对了也永远翻不了身。"""
    p = plugin_factory()
    shard, calls = _fuse_setup(p, reply=None)
    run(p._maybe_capture_fragments("default", shard))
    run(p._maybe_capture_fragments("default", shard))
    assert len(calls) == 2
    p._fragments_cfg["slot"] = "agent"
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert len(calls) == 3


def test_success_clears_the_failure_streak(plugin_factory) -> None:
    """熔断数的是"连续"被拒：中间成一次就该归零，不能被历史账误伤。

    成败交替四拍：拒→成→拒→成。若把累计失败当判据，第四拍就不会再发请求，
    那一次成功白捡的碎片也永远记不上。
    """
    good = '{"capture": true, "kind": "like", "quote": "最喜欢你做的饭", "confidence": 0.9}'
    p = plugin_factory()
    replies = iter([None, good, None, good])
    shard, calls = _fuse_setup(p, reply=lambda: next(replies))
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert run(p._maybe_capture_fragments("default", shard)) is True
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert run(p._maybe_capture_fragments("default", shard)) is True
    assert len(calls) == 4
    assert [item["quote"] for item in shard.diary] == ["最喜欢你做的饭", "最喜欢你做的饭"]


def test_dashboard_light_reports_rejected(plugin_factory, tm) -> None:
    """灯与熔断同源：解析得到端点只证明"知道往哪发"，被连着拒就不该再报正常。"""
    p = plugin_factory()
    shard, _calls = _fuse_setup(p, reply=None)
    run(p._maybe_capture_fragments("default", shard))
    run(p._maybe_capture_fragments("default", shard))
    p._review_cfg["mode"] = "host"
    cs = run(p.dashboard())["channel_status"]
    assert cs["fragments"]["dormant_reason"] == tm._CHANNEL_BREAK_REASON
    assert cs["fragments"]["transport"] == tm._CHANNEL_TRANSPORT_HOST
    # 没熔断的那条不受牵连——熔断是按"角色 + 通道"分别记的
    assert cs["review"]["dormant_reason"] == ""


def test_review_compose_stops_calling_once_fused(plugin_factory, tm) -> None:
    """成文同理，且回执沿用 slot_unresolved：面板据此问灯，灯答 rejected。"""
    p = plugin_factory()
    shard = p._current_shard()
    calls: list[str] = []

    async def gate(shard_, *, force=False):
        return True, "", HOST_RESOLVED

    async def collect(shard_, lanlan):
        return []

    async def chat(resolved, prompt, **kw):
        calls.append(prompt)
        return None

    p._review_write_gate = gate
    p._collect_review_sample_turns = collect
    p._chat_via_host = chat
    p._review_cfg["mode"] = "host"
    assert run(p._maybe_write_review("default", shard, force=True)) == (False, "request_failed")
    assert run(p._maybe_write_review("default", shard, force=True))[0] is False
    assert len(calls) == 2
    assert run(p._maybe_write_review("default", shard, force=True)) == (False, "channel_rejected")
    assert len(calls) == 2


def test_shipped_channel_defaults_are_agent_and_host(plugin_factory, tm) -> None:
    """出厂形态钉死：碎片与成文**两条都** = agent 槽 + host 通道，且必须配着日预算。

    为什么"两条都 agent"可以接受而早先不行：免费路由只放行 agent 档，留在 summary 的那条
    对新用户等于永久休眠（实机 15 次 400 全在切档之前）；而 agent 档带服务端日配额、与宿主
    agent 功能共用一份池子——所以这个默认成立的前提是 `[agent_tier].daily_budget` 与 300 秒
    地板在场。把预算删掉或配成 0 就等于放开了限，这条门同时盯住这两件事。
    """
    import tomllib
    from pathlib import Path

    assert (tm._FRAGMENT_DEFAULT_SLOT, tm._REVIEW_DEFAULT_SLOT) == ("agent", "agent")
    assert (tm._FRAGMENT_DEFAULT_MODE, tm._REVIEW_DEFAULT_MODE) == ("host", "host")
    toml_path = Path(tm.__file__).with_name("plugin.toml")
    shipped = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert (shipped["fragments"]["slot"], shipped["fragments"]["mode"]) == ("agent", "host")
    assert (shipped["review"]["slot"], shipped["review"]["mode"]) == ("agent", "host")
    # 预算在场且落在推荐带内（出厂 20）；地板也得真在
    assert tm._AGENT_TIER_MIN_BUDGET <= shipped["agent_tier"]["daily_budget"] <= tm._AGENT_TIER_MAX_BUDGET
    assert tm._AGENT_TIER_MIN_INTERVAL_SEC >= 60.0


# ---------------------------------------------------------------------------
# agent 档每日预算与间隔地板（1.3.3 修订轮）
#
# 立项理由：免费路由上只有 agent 档放行，而那一档带服务端日配额（宿主自己定 500 次/天，
# quota 注释明写"并非只在本地实施"），且宿主自己的 agent 功能花的是同一个池子。碎片
# 约每 62 秒一拍，一旦切到 agent 档就是拿宿主正经功能在跑。
# ---------------------------------------------------------------------------

GOOD_FRAGMENT_REPLY = '{"capture": true, "kind": "like", "quote": "最喜欢你做的饭", "confidence": 0.9}'


def _agent_setup(p, *, budget: int = 20, reply=GOOD_FRAGMENT_REPLY, slot: str = "agent"):
    """让碎片通道实际走 agent 档（其余桩沿用 _fuse_setup）。"""
    shard, calls = _fuse_setup(p, reply=reply)
    p._fragments_cfg["slot"] = slot
    p._agent_tier_cfg["daily_budget"] = budget
    return shard, calls


def _free_the_floor(shard) -> None:
    """把间隔地板的水位挪到过去——只测预算时不想被地板挡。"""
    shard.cycle["agent_budget_last_ts"] = 0.0


def _prime_budget(p, shard, used: int) -> None:
    """把当日计数摆到指定格。预算下限是 5（配 1、2 会被取值口拒掉），所以"快用完/
    已用完"靠预置计数造，不用生产环境不可能出现的值。"""
    shard.cycle["agent_budget_date"] = str(p._today_str())
    shard.cycle["agent_budget_used"] = used
    shard.cycle["agent_budget_last_ts"] = 0.0


def test_agent_tier_budget_stops_sending_when_spent(plugin_factory) -> None:
    p = plugin_factory()
    shard, calls = _agent_setup(p, budget=5)
    _prime_budget(p, shard, 3)
    assert run(p._maybe_capture_fragments("default", shard)) is True  # 第 4 次
    _free_the_floor(shard)
    assert run(p._maybe_capture_fragments("default", shard)) is True  # 第 5 次＝用满
    assert len(calls) == 2 and len(shard.diary) == 2
    _free_the_floor(shard)
    # 预算用完：桩给的是"肯定成功"的回复，照发就说明预算闸形同虚设
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert len(calls) == 2
    # 反向对照：用户把预算抬高一格，立刻恢复发送（不是熔断那种要改路才复活）
    p._agent_tier_cfg["daily_budget"] = 6
    _free_the_floor(shard)
    assert run(p._maybe_capture_fragments("default", shard)) is True
    assert len(calls) == 3


def test_agent_tier_floor_spaces_calls_but_keeps_material(plugin_factory) -> None:
    """地板只限速、不丢素材：被挡下的那一拍不推进水位，稍后回来还能记上。"""
    p = plugin_factory()
    shard, calls = _agent_setup(p, budget=20)
    assert run(p._maybe_capture_fragments("default", shard)) is True
    marker = shard.last_fragment_marker
    shard.last_fragment_analysis_ts = 0.0  # 绕开碎片自己的间隔闸，专测 agent 地板
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert len(calls) == 1
    assert shard.last_fragment_marker == marker
    _free_the_floor(shard)
    assert run(p._maybe_capture_fragments("default", shard)) is True
    assert len(calls) == 2


def test_non_agent_slots_are_not_budgeted(plugin_factory) -> None:
    """预算只管 agent 档：用户自己配的付费服务商不该按免费档的稀缺性限死。"""
    p = plugin_factory()
    shard, calls = _agent_setup(p, budget=1, slot="summary")
    for _ in range(3):
        assert run(p._maybe_capture_fragments("default", shard)) is True
    assert len(calls) == 3
    assert "agent_budget_used" not in shard.cycle


def test_review_ignores_the_floor_but_not_the_budget(plugin_factory) -> None:
    """成文自带双门槛与在飞锁，不再被地板限一次；当日总账照样管它。"""
    p = plugin_factory()
    shard = p._current_shard()
    calls: list[str] = []

    async def gate(shard_, *, force=False):
        return True, "", HOST_RESOLVED

    async def collect(shard_, lanlan):
        return []

    async def chat(resolved, prompt, **kw):
        calls.append(prompt)
        return "这一期你们聊得很好。"

    p._review_write_gate = gate
    p._collect_review_sample_turns = collect
    p._chat_via_host = chat
    p._review_cfg["mode"] = "host"
    p._review_cfg["slot"] = "agent"
    p._agent_tier_cfg["daily_budget"] = 5
    _prime_budget(p, shard, 2)
    # 连着两拍都发得出去 = 地板没管成文（第二拍距第一拍不到 300 秒）
    assert run(p._maybe_write_review("default", shard, force=True))[0] is True
    assert run(p._maybe_write_review("default", shard, force=True))[0] is True
    assert len(calls) == 2
    # 再发一拍就用满 5 次；下一拍预算闸落下：成文一样安静，且回执让面板去问灯
    assert run(p._maybe_write_review("default", shard, force=True))[0] is True
    assert len(calls) == 3
    assert run(p._maybe_write_review("default", shard, force=True)) == (False, "budget_exhausted")
    assert len(calls) == 3


def test_budget_counters_persist_and_panel_says_so(plugin_factory, tm) -> None:
    """计数落 cycle 盘（重启仍认），面板读数与灯都说真话。"""
    p = plugin_factory()
    shard, _calls = _agent_setup(p, budget=5)
    _prime_budget(p, shard, 4)
    assert run(p._maybe_capture_fragments("default", shard)) is True  # 用满 5
    stored = p.store.data["cycle@default"]
    assert stored["agent_budget_used"] == 5
    assert stored["agent_budget_date"] == str(p._today_str())
    payload = run(p.dashboard())
    assert payload["channel_status"]["fragments"]["dormant_reason"] == tm._CHANNEL_BUDGET_REASON
    assert payload["agent_tier_usage"] == {"used": 5, "budget": 5}
    # 预算记的是"这个角色在 agent 档上的总账"，不按通道分账——成文默认也走 agent 槽，
    # 所以它一起安静下来。这正是与宿主 agent 共池的语义：池子空了谁都不能再舀
    assert payload["channel_status"]["review"]["dormant_reason"] == tm._CHANNEL_BUDGET_REASON


def test_budget_math_rolls_over_by_day(tm) -> None:
    cycle: dict = {}
    assert tm.tier_budget_left(cycle, "2026-09-22", 3) == 3
    tm.tier_budget_spend(cycle, "2026-09-22")
    tm.tier_budget_spend(cycle, "2026-09-22")
    assert tm.tier_budget_used(cycle, "2026-09-22") == 2
    assert tm.tier_budget_left(cycle, "2026-09-22", 3) == 1
    assert tm.tier_budget_used(cycle, "2026-09-23") == 0  # 次日归零，不需要谁去清
    assert tm.tier_budget_left(cycle, "2026-09-23", 3) == 3


def test_update_settings_validates_agent_budget(plugin_factory, tm) -> None:
    """越界是拒收而不是钳制：一次笔误不该换掉一整天的公共额度。"""
    p = plugin_factory()
    ok = run(p.update_settings(agent_daily_budget=150))
    assert isinstance(ok, tm.Ok)
    assert p._settings_snapshot()["agent_daily_budget"] == 150
    snap = run(p.dashboard())["settings"]
    assert snap["agent_daily_budget"] == 150
    for bad in (0, 4, 151, 9999):
        res = run(p.update_settings(agent_daily_budget=bad))
        assert isinstance(res, tm.Err) and "invalid_agent_budget" in str(res.error), bad
    res = run(p.update_settings(agent_daily_budget="很多"))
    assert isinstance(res, tm.Err) and "invalid_agent_budget" in str(res.error), res
    # 拒收之后原值不变
    assert run(p.dashboard())["settings"]["agent_daily_budget"] == 150
