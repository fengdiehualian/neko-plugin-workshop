"""v0.1.4 工具注册心跳门。

覆盖四类不变量（与 our_life v0.5.0 同名门族对齐）：
1. **间隔自节流**：首拍即查（``_last_run_at=0``）、间隔内 waiting、满间隔再查；
   "没工具可查"（no_tools）**不推进时钟**——工具收集齐后下一拍就该查。
2. **不可达不盲挂**：fetch 返回 None → unreachable、零补挂；且时钟已推进，
   main_server 长期没起时不会退化成每拍追打。
3. **点名补挂**：只重发缺席的那几个；``tools_by_role`` 嵌套形状与平铺形状都要认；
   认不出的形状按"全场无工具"处理（宁可幂等重发，不可漏挂）。
4. **永不炸 tick / 永不连坐**：单名 notify 抛错不影响其余名字；SDK protected 面
   缺席时整体降级为 0；fetch 抛异常 → watch_failed 且不带出方法。

外加一条装配门：timer 入口 ``on_watch`` 在桩宿主下离线可跑、返回 Ok。
"""

from __future__ import annotations

from typing import Any

from conftest import build_plugin
from sticker_manager.services.tool_watch import (
    TOOL_WATCH_INTERVAL_SEC,
    ToolWatch,
    missing_tool_names,
)


class FakePlugin:
    """最小插件替身：公开面 ``list_llm_tools`` + protected 面 ``_llm_tools``/notify。"""

    def __init__(self, names: list[str], *, with_notify: bool = True):
        self.tools = [{"name": name} for name in names]
        self._llm_tools = {name: {"meta": name} for name in names}
        self.reemitted: list[Any] = []
        self.raise_for: set[str] = set()
        if with_notify:
            self._notify_llm_tool_registered = self._notify

    def list_llm_tools(self) -> list[dict[str, Any]]:
        return list(self.tools)

    def _notify(self, meta: Any) -> None:
        name = meta["meta"]
        if name in self.raise_for:
            raise RuntimeError(f"boom for {name}")
        # 记的是**原对象**（重发语义要求 meta 逐字传回基类），测试用 name 读回。
        self.reemitted.append(meta)


def _names(emitted: list[Any]) -> list[str]:
    return sorted(item["meta"] for item in emitted)


def _watch(plugin: Any, payload: Any, *, interval: float = TOOL_WATCH_INTERVAL_SEC) -> tuple[ToolWatch, list[str]]:
    """造一个离线巡检器：fetch 直接回给定 payload，并记录被请求的 URL。

    注意用 ``{"tools_by_role": {}}`` 表达"可达但全场无工具"——空 dict 会被
    形状门当成"认不出"，两者行为故意相同（都重发）但测试里要说清是哪一种。
    """
    urls: list[str] = []

    async def fetch(url: str):
        urls.append(url)
        return payload

    return ToolWatch(plugin, fetch=fetch, interval_sec=interval), urls


# ---------------------------------------------------------------------------
# 纯函数：形状与差集
# ---------------------------------------------------------------------------


def test_missing_uses_nested_tools_by_role_shape() -> None:
    payload = {"ok": True, "tools_by_role": {"小春": [{"name": "sticker_list"}], "别的": [{"name": "x"}]}}
    assert missing_tool_names(payload, ["sticker_list", "sticker_send"]) == ["sticker_send"]


def test_role_specific_absence_is_not_masked_by_another_role() -> None:
    payload = {
        "ok": True,
        "tools_by_role": {
            "YUI": [{"name": "sticker_list"}],
            "ATLS": [{"name": "sticker_list"}, {"name": "sticker_send"}],
        },
    }
    assert missing_tool_names(payload, ["sticker_list", "sticker_send"], role="YUI") == ["sticker_send"]


def test_missing_accepts_flat_fallback_shape() -> None:
    payload = {"role_a": [{"name": "sticker_list"}, {"name": "b"}]}
    assert missing_tool_names(payload, ["sticker_list", "sticker_send"]) == ["sticker_send"]


def test_unrecognized_shape_means_everything_missing() -> None:
    # 认不出 = "一个工具都不在"：调用方幂等重发，比漏挂安全。
    assert missing_tool_names({"weird": 1}, ["b", "a"]) == ["a", "b"]
    assert missing_tool_names(None, ["a"]) == ["a"]


def test_empty_declared_returns_empty() -> None:
    assert missing_tool_names({}, []) == []
    assert missing_tool_names({}, ["", "  "]) == []


def test_interval_constant_is_pinned() -> None:
    # 10 秒覆盖 API 重载后到下一轮主聊天之间的短缺席窗口。
    assert TOOL_WATCH_INTERVAL_SEC == 10.0


# ---------------------------------------------------------------------------
# 间隔与自节流
# ---------------------------------------------------------------------------


def test_first_run_checks_immediately_then_waits_interval(run_async: Any) -> None:
    plugin = FakePlugin(["sticker_list"])
    watch, urls = _watch(plugin, {"tools_by_role": {"r": [{"name": "sticker_list"}]}})
    first = run_async(watch.maybe_run(now=1000.0))
    assert first["status"] == "healthy" and urls == ["/api/tools"]
    # 间隔内：不再发请求、不推进时钟
    assert run_async(watch.maybe_run(now=1000.0 + TOOL_WATCH_INTERVAL_SEC - 1))["status"] == "waiting"
    assert len(urls) == 1
    # 满间隔：再查一次
    assert run_async(watch.maybe_run(now=1000.0 + TOOL_WATCH_INTERVAL_SEC))["status"] == "healthy"
    assert len(urls) == 2


def test_no_tools_does_not_advance_clock(run_async: Any) -> None:
    plugin = FakePlugin([])
    watch, urls = _watch(plugin, {"tools_by_role": {"r": [{"name": "sticker_list"}]}})
    assert run_async(watch.maybe_run(now=1000.0))["status"] == "no_tools"
    assert urls == []
    # 时钟没被推进：工具收集齐后（间隔内！）下一拍就该查。
    plugin.tools = [{"name": "sticker_list"}]
    plugin._llm_tools["sticker_list"] = {"meta": "sticker_list"}
    assert run_async(watch.maybe_run(now=1030.0))["status"] == "healthy"
    assert urls == ["/api/tools"]


# ---------------------------------------------------------------------------
# 不可达 / 补挂 / 降级
# ---------------------------------------------------------------------------


def test_unreachable_skips_reissue_but_keeps_clock(run_async: Any) -> None:
    plugin = FakePlugin(["sticker_list"])
    watch, urls = _watch(plugin, None)
    assert run_async(watch.maybe_run(now=1000.0))["status"] == "unreachable"
    assert plugin.reemitted == []
    # 时钟已推进：main_server 长期没起时不会每拍追打
    assert run_async(watch.maybe_run(now=1009.0))["status"] == "waiting"
    assert run_async(watch.maybe_run(now=1010.0))["status"] == "unreachable"
    assert len(urls) == 2


def test_repairs_only_missing_by_name(run_async: Any) -> None:
    plugin = FakePlugin(["sticker_list", "sticker_send"])
    payload = {"tools_by_role": {"r": [{"name": "sticker_list"}]}}
    watch, _urls = _watch(plugin, payload)
    result = run_async(watch.maybe_run(now=1000.0))
    assert result["status"] == "recovering"
    assert result["missing"] == ["sticker_send"]  # 排序稳定
    assert result["reissued"] == 1
    assert _names(plugin.reemitted) == ["sticker_send"]
    # 重发的是 SDK 里的 meta 对象本身（宿主按它拼 IPC payload）
    assert all(isinstance(meta, dict) for meta in plugin.reemitted)


def test_single_tool_failure_does_not_stop_the_rest(run_async: Any) -> None:
    plugin = FakePlugin(["a", "b", "c"])
    plugin.raise_for = {"b"}
    watch, _urls = _watch(plugin, {"tools_by_role": {"r": []}})
    result = run_async(watch.maybe_run(now=1000.0))
    assert result["status"] == "recovering"
    assert result["reissued"] == 2
    assert _names(plugin.reemitted) == ["a", "c"]


def test_missing_protected_surface_degrades_to_zero(run_async: Any) -> None:
    # 宿主哪天改名/收口 protected 面：心跳失效，不崩、不误伤其余流程。
    plugin = FakePlugin(["a"], with_notify=False)
    del plugin._llm_tools
    watch, _urls = _watch(plugin, {"tools_by_role": {"r": []}})
    result = run_async(watch.maybe_run(now=1000.0))
    assert result["status"] == "repair_pending"
    assert result["reissued"] == 0


def test_fetch_exception_is_swallowed(run_async: Any) -> None:
    plugin = FakePlugin(["a"])

    async def fetch(_url: str):
        raise OSError("pipe exploded")

    watch = ToolWatch(plugin, fetch=fetch)
    assert run_async(watch.maybe_run(now=1000.0))["status"] == "watch_failed"


def test_source_mismatch_is_treated_as_missing(run_async: Any) -> None:
    plugin = FakePlugin(["sticker_send"])
    plugin.plugin_id = "sticker_manager"
    payload = {
        "ok": True,
        "tools_by_role": {"YUI": [{"name": "sticker_send", "source": "plugin:other"}]},
    }
    watch, _urls = _watch(plugin, payload)
    result = run_async(watch.maybe_run(now=1000.0))
    assert result["status"] == "recovering"
    assert result["missing_by_role"] == {"YUI": ["sticker_send"]}


# ---------------------------------------------------------------------------
# 装配门：timer 入口在桩宿主下离线可跑
# ---------------------------------------------------------------------------


def test_on_watch_entry_returns_ok_offline(run_async: Any) -> None:
    # 桩基类没有 list_llm_tools 公开面 → declared 为空 → no_tools（不发网络、
    # 不推进时钟、返回 Ok）。真实宿主里工具收集齐后同一入口走完整巡检。
    # v0.16.0：on_watch 只剩工具心跳一件事——存在感注入搬到了 10s 的 on_turns 拍上，
    # 蹭 60s 的粒度会把连发的几条用户轮并成一轮看见。
    plugin, _host = build_plugin()
    result = run_async(plugin.on_watch())
    assert result.is_ok()
    assert result.value["tool_watch"]["status"] == "no_tools"
    assert "awareness" not in result.value


def test_on_turns_entry_returns_ok_offline(run_async: Any) -> None:
    # 存在感注入的新拍：默认配置 [sticker_manager].enabled=false（fail-closed）
    # → 注入器不动作，但入口照样回 Ok（timer 无 watchdog，炸出去就是整链静默失声）。
    plugin, _host = build_plugin()
    result = run_async(plugin.on_turns())
    assert result.is_ok()
    assert result.value["awareness"]["status"] == "disabled"


# ---------------------------------------------------------------------------
# 缺席窗口记账（v0.20.1）：把"她根本没有这个工具"的时长变成可推断的读数
# ---------------------------------------------------------------------------


def _scripted_watch(plugin: Any, payloads: list[Any]) -> ToolWatch:
    """每次巡检回一份预设 payload（列表用尽后重复最后一份）。"""
    state = {"i": 0}

    async def fetch(url: str):
        index = min(state["i"], len(payloads) - 1)
        state["i"] += 1
        return payloads[index]

    return ToolWatch(plugin, fetch=fetch)


def test_gap_seconds_accumulate_only_from_a_healthy_baseline(run_async: Any) -> None:
    plugin = FakePlugin(["sticker_list", "sticker_send"])
    full = {"tools_by_role": {"r": [{"name": "sticker_list"}, {"name": "sticker_send"}]}}
    gone = {"tools_by_role": {"r": []}}
    watch = _scripted_watch(plugin, [full, gone])
    assert run_async(watch.maybe_run(now=1000.0))["status"] == "healthy"
    repaired = run_async(watch.maybe_run(now=1000.0 + TOOL_WATCH_INTERVAL_SEC))
    assert repaired["status"] == "recovering" and repaired["gaps"] == 1
    # 上界语义：真实缺席起点在两次巡检之间，不可知；报的是"距上次确认在场"的秒数。
    assert repaired["gap_seconds_max"] == TOOL_WATCH_INTERVAL_SEC
    assert watch.gap_seconds_max == TOOL_WATCH_INTERVAL_SEC


def test_first_sweep_missing_leaves_the_duration_unknown(run_async: Any) -> None:
    # 开机第一拍就发现缺席：不能说"缺席了 0 秒"，也不能编一个时长——只记次数。
    plugin = FakePlugin(["sticker_send"])
    watch = _scripted_watch(plugin, [{"tools_by_role": {"r": []}}])
    repaired = run_async(watch.maybe_run(now=5000.0))
    assert repaired["status"] == "recovering" and repaired["gaps"] == 1
    assert repaired["gap_seconds_max"] == 0.0


def test_repair_alone_is_not_proof_of_presence(run_async: Any) -> None:
    # 补挂≠确认在场：基线被清空，第二次缺席在没有新的 healthy 之前不再累加秒数。
    plugin = FakePlugin(["sticker_send"])
    gone, full = {"tools_by_role": {"r": []}}, {"tools_by_role": {"r": [{"name": "sticker_send"}]}}
    watch = _scripted_watch(plugin, [full, gone, gone, full, gone])
    run_async(watch.maybe_run(now=1000.0))  # healthy → 建立基线
    first = run_async(watch.maybe_run(now=1300.0))
    second = run_async(watch.maybe_run(now=1600.0))  # 补挂后没再确认过：秒数不再涨
    assert first["gap_seconds_max"] == 300.0 and second["gap_seconds_max"] == 300.0
    assert second["gaps"] == 1
    run_async(watch.maybe_run(now=1900.0))  # healthy → 重新建立基线
    third = run_async(watch.maybe_run(now=2200.0))
    assert third["gaps"] == 2 and third["gap_seconds_max"] == 600.0


def test_unreachable_is_neither_healthy_nor_a_gap(run_async: Any) -> None:
    # 对面没答应 = 状态未知：算进 unreachable，绝不写成缺席秒数（那是仪器说谎）。
    plugin = FakePlugin(["sticker_send"])
    watch = _scripted_watch(plugin, [None])
    result = run_async(watch.maybe_run(now=1000.0))
    assert result["status"] == "unreachable"
    assert watch.unreachable == 1 and watch.gaps == 0 and watch.gap_seconds_max == 0.0
