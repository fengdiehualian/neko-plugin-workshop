"""插件工具桥：把别的插件按分级挂到 QQ 会话上（只在开放平台）。

这个功能有**四道闸**，每一道都对应一个真实的坏结果，所以每一道都要单独钉住：

1. **通道闸**：只在开放平台挂。NapCat 那边"群里什么样的人都有"，多给一层工具面就多
   一层风险；
2. **启动闸**：只带**已经启动**的插件 —— 没启动的挂上去，模型点到只会拿到一个错误，
   而且它还会占掉提示词预算；
3. **非 QQ 闸**：排除自己与 `qq*` 家族。让她"通过调用别的插件"再回到 QQ 发送链路是
   自指；
4. **权限闸**：`all` 档给名册里的人，`admin` 档只给管理员；**认不出来的人什么都不给** ——
   那正是陌生人 @ 一下就能指挥插件的情形。

外加一条不是闸但要命的：handler 只认**本轮挂上去的 entry 清单**，
模型不能自己编一个 entry id 来透传。

另外，候选表的取数**只走后台刷新**（`refresh_candidates`）：真机上在 entry handler 里
同步查宿主会超时（`Plugin query timed out after 15.0s`），调用方一律只读缓存。
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from types import SimpleNamespace

import pytest
from plugin.plugins.qq_auto_reply.plugin_tool_service import (
    CANDIDATES_TTL_SECONDS,
    ENTRY_HINT_MAX_CHARS,
    MAX_MOUNTED_PLUGINS,
    RESULT_MAX_CHARS,
    TIER_ADMIN,
    TIER_ALL,
    QQPluginToolService,
)

# ── 夹具 ────────────────────────────────────────────────────────────

def _plugin(
    *, tiers=None, plugins_result=None, mode="open_platform",
    query_delay: float = 0.0, stub_fetch: bool = True,
):
    """最小插件桩：只带服务真正用到的那几样。

    候选表来自**宿主插件服务的 HTTP** `/plugins`（不是 SDK 的 IPC `query_plugins` ——
    真机上那条路根本没到过宿主）。默认把取数替换成桩；测 HTTP 那一层时传
    ``stub_fetch=False``。
    """
    calls: list[tuple] = []
    queries: list[dict] = []

    async def _fetch():
        queries.append({"fetch": True})
        if query_delay:
            await asyncio.sleep(query_delay)
        return plugins_result if plugins_result is not None else {"plugins": []}

    class _Plugins:
        @staticmethod
        async def call_entry(entry_ref, params=None, *, timeout=10.0):
            calls.append((entry_ref, params, timeout))
            return SimpleNamespace(value={"ok": True, "echo": params}, is_err=lambda: False)

    client = SimpleNamespace()
    if mode == "open_platform":
        client.CHANNEL = "open"
        client.mode = "open_platform"
        client.needs_attention = False
    else:
        client.CHANNEL = "onebot"
        client.mode = "napcat"
        client.needs_attention = True

    emitted: list[tuple[str, str]] = []
    logged: list[str] = []
    plugin = SimpleNamespace(
        plugins=_Plugins(),
        qq_client=client,
        _qq_settings={"qq_open_plugin_tools": dict(tiers or {})},
        logger=SimpleNamespace(
            info=lambda msg, *a, **k: logged.append(str(msg)),
            warning=lambda msg, *a, **k: logged.append(str(msg)),
        ),
        _emit_log=lambda level, msg: emitted.append((level, msg)),
        calls=calls,
        emitted=emitted,
        logged=logged,
        queries=queries,
    )
    service = QQPluginToolService(plugin)
    if stub_fetch:
        service._fetch_registry = _fetch
    plugin.plugin_tool_service = service
    return plugin


def _candidate(plugin_id, entries=("do_it",), name=None, description="", status="running"):
    """宿主 `/plugins` 的一条记录（形状照真实 payload：entries 是对象数组）。"""
    return {
        "id": plugin_id,
        "name": name or plugin_id.title(),
        "description": description,
        "status": status,
        "entries": [
            {"id": entry, "name": f"{entry} 功能", "description": f"{entry} 的说明"}
            for entry in entries
        ],
    }


def _plugins_payload(*rows):
    return {"plugins": list(rows)}


async def _refresh(service) -> list[dict]:
    """走"后台刷新"那条路取候选（生产里由常驻循环调用）。

    调用方（entry handler / 生成路径）只读缓存，所以测试也测刷新那条路。
    """
    return await service.refresh_candidates()


# ── 0. 取数：缓存与后台刷新 ─────────────────────────────────────────

def test_a_fresh_cache_is_reused_without_asking_the_host_again():
    plugin = _plugin(plugins_result=_plugins_payload(_candidate("web_search", ("search",))))
    service = plugin.plugin_tool_service

    first = asyncio.run(_refresh(service))
    second = asyncio.run(service.list_candidates())

    assert [row["plugin_id"] for row in first] == ["web_search"]
    assert second == first
    assert len(plugin.queries) == 1, "缓存还新鲜却又问了一次宿主"


def test_a_stale_cache_never_blocks_the_reader():
    """缓存过期时读路径**不阻塞**：踢一次后台刷新，先返回手里那份。

    判据用"查询故意睡 10 秒，读路径仍须秒回" —— 比数调用次数可靠：后台刷新跑不跑
    取决于事件循环调度，而"读路径不 await 宿主"是必须成立的（真机上同步查就是超时）。
    """
    plugin = _plugin(plugins_result=_plugins_payload(_candidate("web_search")))
    service = plugin.plugin_tool_service
    asyncio.run(_refresh(service))

    # 换成慢查询并让缓存过期
    stale = _plugin(plugins_result=_plugins_payload(_candidate("web_search")), query_delay=10.0)
    stale_service = stale.plugin_tool_service
    stale_service._candidates_cache = (time.monotonic() - CANDIDATES_TTL_SECONDS - 1,
                                       service.cached_candidates())

    started = time.monotonic()
    rows = asyncio.run(stale_service.list_candidates())
    elapsed = time.monotonic() - started

    assert [row["plugin_id"] for row in rows] == ["web_search"], "没拿到手里那份缓存"
    assert elapsed < 1.0, f"读路径等了 {elapsed:.2f}s —— 它去 await 宿主了"


# ── 0b. 界面那条路：要**此刻的真相**，不是 60 秒内的快照 ──────────────
#
# 真机复现（2026-09-26 17:3x，`.dsh-artifacts/repro-stale-candidate.py`）：
#   启动前：宿主=False 桥候选=False
#   POST /plugin/mcp_adapter/start → success
#   启动后（立刻问桥，0.34s）：宿主=True  桥候选=False   ← 使用者的原话
# 根因：界面走 `wait_for_a_fresh_cache`，它按**年龄**判"新鲜"（< 60s），于是最多 60 秒
# 前的快照会被原样当成最新的。

def test_the_ui_path_asks_the_host_again_instead_of_serving_a_stale_snapshot():
    plugin = _plugin(plugins_result=_plugins_payload(_candidate("mcp_adapter", status="stopped")))
    service = plugin.plugin_tool_service
    asyncio.run(_refresh(service))            # 缓存里是"停着"的快照（且按年龄算新鲜）

    # 使用者在别处把它启动了：宿主那边已经是 running
    stale = service.cached_candidates()

    async def _now_running():
        return _plugins_payload(_candidate("mcp_adapter", status="running"))

    service._fetch_registry = _now_running

    fresh = asyncio.run(service.refresh_for_ui())

    assert stale[0]["running"] is False, "夹具没造出「旧快照」这个前提"
    row = next(r for r in fresh if r["plugin_id"] == "mcp_adapter")
    assert row["running"] is True, "界面拿到了 60 秒内的旧快照（卡片就会一直写「未启动」）"
    assert service.cached_candidates()[0]["running"] is True, "顺手也该把缓存换成新的"


def test_the_ui_path_falls_back_to_the_cache_when_the_host_is_slow():
    """界面不该被宿主拖住：问超时就给手里那份（并踢一次后台刷新）。"""
    plugin = _plugin(plugins_result=_plugins_payload(_candidate("web_search")), query_delay=10.0)
    service = plugin.plugin_tool_service
    service._candidates_cache = (time.monotonic(), [{"plugin_id": "web_search", "entries": [], "running": True}])

    started = time.monotonic()
    rows = asyncio.run(service.refresh_for_ui(timeout=0.3))
    elapsed = time.monotonic() - started

    assert [row["plugin_id"] for row in rows] == ["web_search"], "超时后没退回缓存"
    assert elapsed < 3.0, f"界面被宿主拖了 {elapsed:.2f}s"


def test_the_query_asks_for_entry_ids():
    """工具定义要靠 entry id 列表 —— 取数那条路必须真的被走到。"""
    plugin = _plugin(plugins_result=_plugins_payload(_candidate("web_search")))
    asyncio.run(_refresh(plugin.plugin_tool_service))
    assert plugin.queries, "刷新没有去取宿主目录"


def test_the_registry_is_fetched_over_the_plugin_server_http(monkeypatch):
    """取数走 HTTP `/plugins`（SDK 的 IPC `query_plugins` 在真机上根本没到过宿主）。

    判据：URL 必须是插件服务的 `/plugins`，而且真的 await 了那个客户端。
    """
    import utils.internal_http_client as internal

    seen: list[str] = []

    class _Response:
        @staticmethod
        def json():
            return {"plugins": []}

    class _Client:
        @staticmethod
        async def get(url, timeout=None):
            seen.append(str(url))
            return _Response()

    monkeypatch.setattr(internal, "get_internal_http_client", lambda: _Client())
    plugin = _plugin(stub_fetch=False)

    assert asyncio.run(plugin.plugin_tool_service._query_host_registry()) == []
    assert len(seen) == 1
    assert seen[0].endswith("/plugins"), seen[0]


@pytest.mark.parametrize("boom", [RuntimeError("host down"), TimeoutError("slow")])
def test_a_registry_failure_is_swallowed(monkeypatch, boom):
    """这条链路是可选增强：查不到就什么都没有，绝不能把聊天带下去。"""
    import utils.internal_http_client as internal

    class _Client:
        @staticmethod
        async def get(url, timeout=None):
            raise boom

    monkeypatch.setattr(internal, "get_internal_http_client", lambda: _Client())
    plugin = _plugin(stub_fetch=False)

    assert asyncio.run(_refresh(plugin.plugin_tool_service)) == []
    assert asyncio.run(plugin.plugin_tool_service._query_host_registry()) == []


def test_a_missing_internal_http_client_yields_no_candidates(monkeypatch):
    """轻量调用方/测试里没有那个客户端时，也只是没有候选。"""
    import utils.internal_http_client as internal

    def _boom():
        raise RuntimeError("no client")

    monkeypatch.setattr(internal, "get_internal_http_client", _boom)
    plugin = _plugin(stub_fetch=False)
    assert asyncio.run(_refresh(plugin.plugin_tool_service)) == []


def test_the_refresh_loop_is_idempotent():
    plugin = _plugin()
    service = plugin.plugin_tool_service

    async def _run():
        service.ensure_refresh_loop()
        first = service._refresh_task
        service.ensure_refresh_loop()
        assert service._refresh_task is first, "重复调用建了第二个刷新循环"
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


# ── 1. 分级读取：配置坏了不该炸会话 ─────────────────────────────────

def test_tiers_are_sanitised_on_read():
    plugin = _plugin(tiers={
        "web_search": "all",
        "mijia": "admin",
        "bad_tier": "everyone",
        "  ": "all",
        "": "admin",
        "upper": "ADMIN",
    })
    assert QQPluginToolService(plugin).tiers() == {
        "web_search": "all", "mijia": "admin", "upper": "admin",
    }


def test_tiers_tolerate_garbage():
    for bad in (None, [], "web_search", 42):
        plugin = _plugin()
        plugin._qq_settings["qq_open_plugin_tools"] = bad
        assert QQPluginToolService(plugin).tiers() == {}


# ── 2. 权限闸 ───────────────────────────────────────────────────────

def test_admin_sees_both_tiers():
    service = QQPluginToolService(_plugin())
    assert service.allowed_tiers_for("admin") == {TIER_ALL, TIER_ADMIN}


@pytest.mark.parametrize("level", ["trusted", "normal", "TRUSTED"])
def test_known_non_admin_users_only_get_the_all_tier(level):
    assert QQPluginToolService(_plugin()).allowed_tiers_for(level) == {TIER_ALL}


@pytest.mark.parametrize("level", ["none", "", None, "unknown"])
def test_unrecognised_speakers_get_nothing(level):
    """**这条最重要**：认不出来的人一个插件工具都不该拿到。"""
    assert QQPluginToolService(_plugin()).allowed_tiers_for(level) == set()


def test_select_mounted_respects_the_tier_and_skips_non_candidates():
    plugin = _plugin(tiers={"web_search": "all", "mijia": "admin", "jukebox_controller": "all"})
    service = plugin.plugin_tool_service
    candidates = [
        {"plugin_id": "web_search", "entries": ["search"], "running": True},
        {"plugin_id": "mijia", "entries": ["turn_on"], "running": True},
        # jukebox_controller 配了但**没在跑** → 不在候选里
    ]
    mounted = service.select_mounted(candidates, allowed_tiers={TIER_ALL})
    assert [row["plugin_id"] for row, _tier in mounted] == ["web_search"]

    mounted_admin = service.select_mounted(candidates, allowed_tiers={TIER_ALL, TIER_ADMIN})
    # 顺序按 plugin_id 排：挂载顺序不该取决于调用方给的候选顺序（同一次调用要可复现）
    assert [row["plugin_id"] for row, _tier in mounted_admin] == ["mijia", "web_search"]


def test_select_mounted_caps_the_count():
    tiers = {f"p{i:02d}": TIER_ALL for i in range(MAX_MOUNTED_PLUGINS + 4)}
    plugin = _plugin(tiers=tiers)
    candidates = [{"plugin_id": pid, "entries": ["e"], "running": True} for pid in sorted(tiers)]
    mounted = plugin.plugin_tool_service.select_mounted(candidates, allowed_tiers={TIER_ALL})
    assert len(mounted) == MAX_MOUNTED_PLUGINS


# ── 3. 候选：只带已启动的非 QQ 插件 ─────────────────────────────────

def test_candidates_list_every_non_qq_plugin_and_mark_who_is_running():
    """候选表**不筛启动状态**（使用者定的：加插件时不检查有没有启动，全量出卡片），
    只在每条上标 ``running`` —— 挂载那道闸看它。QQ 家族仍然排除。"""
    plugin = _plugin(plugins_result=_plugins_payload(
        _candidate("web_search", ("search",), name="搜索"),
        _candidate("qq_auto_reply", ("send",)),          # 自己
        _candidate("qq_official_bridge", ("x",)),        # qq 家族
        _candidate("sleepy_plugin", ("x",), status="stopped"),
        _candidate("no_entries", ()),
        "not-a-dict",
    ))
    rows = asyncio.run(_refresh(plugin.plugin_tool_service))
    by_id = {row["plugin_id"]: row for row in rows}

    assert set(by_id) == {"web_search", "sleepy_plugin", "no_entries"}, "候选不该只剩在跑的那些"
    assert by_id["web_search"]["running"] is True
    assert by_id["sleepy_plugin"]["running"] is False, "停着的要标出来（界面据此打「未启动」）"
    assert by_id["web_search"]["entries"] == ["search"]
    assert by_id["web_search"]["name"] == "搜索"


def test_only_running_plugins_are_mounted():
    """提示词那一侧只带**在跑**的：停着的配了也不挂。"""
    plugin = _plugin(
        tiers={"sleepy_plugin": "all", "web_search": "all"},
        plugins_result=_plugins_payload(
            _candidate("sleepy_plugin", ("x",), status="stopped"),
            _candidate("web_search", ("search",)),
        ),
    )
    rows = asyncio.run(_refresh(plugin.plugin_tool_service))
    mounted = plugin.plugin_tool_service.select_mounted(rows, allowed_tiers={TIER_ALL})

    assert [row["plugin_id"] for row, _tier in mounted] == ["web_search"]


def test_a_running_plugin_without_entries_is_not_mounted():
    """在跑但没有任何可调 entry：挂上去只会让模型空调用。"""
    plugin = _plugin(
        tiers={"no_entries": "all"},
        plugins_result=_plugins_payload(_candidate("no_entries", ())),
    )
    rows = asyncio.run(_refresh(plugin.plugin_tool_service))
    assert plugin.plugin_tool_service.select_mounted(rows, allowed_tiers={TIER_ALL}) == []


def test_candidates_dedupe_and_sort_entries():
    plugin = _plugin(plugins_result=_plugins_payload(_candidate("web_search", ("b", "a", "b"))))
    rows = asyncio.run(_refresh(plugin.plugin_tool_service))
    assert rows[0]["entries"] == ["a", "b"]


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"plugins": None}, {"plugins": "x"}, {"plugins": [None, 1]}],
)
def test_malformed_registry_payload_yields_no_candidates(payload):
    plugin = _plugin(plugins_result=payload)
    assert asyncio.run(_refresh(plugin.plugin_tool_service)) == []


def test_a_plugin_without_the_fetch_layer_yields_no_candidates():
    """没有 HTTP 取数层（轻量调用方）时也只是没有候选，不抛。"""
    plugin = _plugin(stub_fetch=False)

    async def _boom():
        return None

    plugin.plugin_tool_service._fetch_registry = _boom
    assert asyncio.run(plugin.plugin_tool_service._query_host_registry()) == []


# ── 4. 工具定义 ─────────────────────────────────────────────────────

def test_tool_definition_shape():
    plugin = _plugin()
    definition = QQPluginToolService.build_tool_definition(
        plugin.plugin_tool_service,
        {"plugin_id": "web_search", "name": "搜索", "entries": ["search", "news"]},
        TIER_ADMIN,
    )
    assert definition.name == "plugin_web_search"
    props = definition.parameters["properties"]
    assert props["entry_id"]["enum"] == ["search", "news"]
    assert definition.parameters["required"] == ["entry_id"]
    # 档位要说给模型听：她据此判断该不该替这个人做这件事
    assert "只有管理员" in definition.description
    assert "搜索" in definition.description


def test_the_tool_description_names_the_required_params():
    """`params` 是自由对象 —— 描述里点名"必填 time、message"比让模型猜准得多。

    用真实的 memo_reminder 形状：`add_reminder` 必填 `time`/`message`，
    `list_reminders` 没有必填。
    """
    plugin = _plugin()
    candidate = {
        "plugin_id": "memo_reminder",
        "name": "备忘提醒",
        "entries": ["add_reminder", "list_reminders"],
        "entry_hints": {
            "add_reminder": "排期一个备忘提醒（必填参数：time、message）",
            "list_reminders": "列出所有待触发的提醒",
        },
    }
    definition = QQPluginToolService.build_tool_definition(
        plugin.plugin_tool_service, candidate, TIER_ALL,
    )

    assert "add_reminder：排期一个备忘提醒（必填参数：time、message）" in definition.description
    assert "list_reminders：列出所有待触发的提醒" in definition.description


def test_the_hint_carries_required_and_optional_params_and_is_capped():
    """`_entry_hints` 要从 input_schema 里抠出必填/可选参数名，并且整行封顶。

    可选参数名不是装饰：`analyze_text` 的 `use_neko_model` 在缺 api_key 时能救场 ——
    只列必填的话模型看不到它，就会照着缺 key 的默认路走然后当场失败（真机踩过）。
    """
    long_text = "说明" * 200
    item = {
        "entries": [
            {
                "id": "add_reminder",
                "description": "排期一个提醒",
                "input_schema": {
                    "type": "object",
                    "required": ["time", "message"],
                    "properties": {"time": {}, "message": {}, "repeat": {}, "max_count": {}},
                },
            },
            {"id": "list_reminders", "description": "", "input_schema": {"type": "object"}},
            {"id": "long_one", "description": long_text, "input_schema": {"required": ["x"]}},
        ],
    }
    hints = QQPluginToolService._entry_hints(item)

    assert "必填 time、message" in hints["add_reminder"]
    assert "可选 repeat、max_count" in hints["add_reminder"]
    assert "list_reminders" not in hints, "没有说明也没有参数时不该硬造一行"
    assert hints["long_one"].endswith("…"), "超长没被截断"
    assert len(hints["long_one"]) <= ENTRY_HINT_MAX_CHARS + 1, len(hints["long_one"])


def test_the_tool_description_forbids_claiming_work_that_never_happened():
    """真机 16:53：她说「好，我这就再帮你提交一次」，而那一轮**没有任何工具调用**。

    工具结果里那句"别承诺"管不住这种情形 —— 得在**工具定义**里就写死：
    只有真的调用成功之后才许说"已经提交/我去办"。
    """
    tool = QQPluginToolService.build_tool_definition(
        _plugin().plugin_tool_service,
        {"plugin_id": "web_search", "name": "搜索", "entries": ["search"], "entry_hints": {}},
        TIER_ALL,
    )

    assert "真的调用本工具" in tool.description
    assert "不许这么说" in tool.description


def test_optional_params_are_names_only_and_capped():
    """可选参数只列名字，且数量有上限（工具描述每轮都发，不能无限膨胀）。"""
    entry = {
        "input_schema": {
            "required": ["a"],
            "properties": {**{f"p{i}": {} for i in range(10)}, "a": {}},
        },
    }
    optional = QQPluginToolService._optional_params(entry)
    assert len(optional) == 4, optional
    assert "a" not in optional, "必填参数不该出现在可选里"


# ── 4b. 异步任务：当场把"下一步"告诉模型 ─────────────────────────────

def test_an_async_result_gets_a_followup_note_naming_the_poller():
    """真机教训：`analyze_text` 只回 `{task_id, status}`，模型于是承诺"我第一时间告诉你"，
    而这条承诺没法兑现（一轮只走一次工具轮）。所以要当场写明：本轮没结果、别承诺、
    对方再问时用哪个 entry 带哪个字段去查。"""
    plugin = _plugin()
    service = plugin.plugin_tool_service
    note = service._async_followup_note(
        {"task_id": "c5d013ade5dd", "status": "queued"},
        plugin_id="writer_power_analysis",
        poller="get_analysis_status",
    )

    assert "异步任务" in note
    assert "writer_power_analysis:get_analysis_status" in note
    assert "task_id=c5d013ade5dd" in note
    assert "不要承诺" in note


def test_a_sync_result_gets_no_note():
    service = _plugin().plugin_tool_service
    for payload in ({"ok": True}, "纯文本", None, {"task_id": ""}, [1, 2]):
        assert service._async_followup_note(payload, plugin_id="p", poller="q") == ""


def test_the_poller_entry_is_picked_from_the_plugin_entries():
    service = _plugin().plugin_tool_service
    assert service._find_poller_entry({"entries": ["analyze_text", "get_analysis_status"]}) == (
        "get_analysis_status"
    )
    # 搜索那两个都不是"查状态"的 entry —— 认不出来就返回空（宁可不说，也别瞎指一个）
    assert service._find_poller_entry({"entries": ["search", "search_summary"]}) == ""
    assert service._find_poller_entry({"entries": []}) == ""


def test_tool_names_stay_inside_the_host_name_rule():
    """宿主对工具名有正则约束，别造出非法名。"""
    import re

    pattern = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
    for plugin_id in ("web_search", "mijia", "a" * 40):
        assert pattern.match(QQPluginToolService.tool_name(plugin_id)), plugin_id


# ── 5. 分发器：只能调本轮挂上的 entry ───────────────────────────────

def _handler(plugin, mounted):
    return plugin.plugin_tool_service.build_handler(mounted, session_key="private:x")


def test_handler_calls_the_plugin_entry():
    plugin = _plugin()
    candidates = [{"plugin_id": "web_search", "entries": ["search"], "running": True}]
    handler = _handler(plugin, [(candidates[0], TIER_ALL)])

    result = asyncio.run(handler(SimpleNamespace(
        call_id="c1", name="plugin_web_search",
        arguments={"entry_id": "search", "params": {"q": "cat"}},
    )))

    assert plugin.calls == [("web_search:search", {"q": "cat"}, 20.0)]
    assert result.call_id == "c1" and result.name == "plugin_web_search"
    assert "echo" in result.output


def test_handler_refuses_an_entry_outside_the_mounted_list():
    """模型不能自己编一个 entry id 来透传 —— 这是这条链路唯一的安全边界。"""
    plugin = _plugin()
    candidates = [{"plugin_id": "web_search", "entries": ["search"], "running": True}]
    handler = _handler(plugin, [(candidates[0], TIER_ALL)])

    result = asyncio.run(handler(SimpleNamespace(
        call_id="c2", name="plugin_web_search",
        arguments={"entry_id": "delete_everything"},
    )))

    assert plugin.calls == []
    assert "不在可用列表里" in result.output


def test_handler_refuses_a_tool_that_was_not_mounted():
    plugin = _plugin()
    handler = _handler(plugin, [])
    result = asyncio.run(handler(SimpleNamespace(
        call_id="c3", name="plugin_mijia", arguments={"entry_id": "turn_on"},
    )))
    assert plugin.calls == []
    assert "没有挂载" in result.output


def test_handler_tolerates_missing_arguments():
    plugin = _plugin()
    candidates = [{"plugin_id": "web_search", "entries": ["search"], "running": True}]
    handler = _handler(plugin, [(candidates[0], TIER_ALL)])

    result = asyncio.run(handler(SimpleNamespace(
        call_id="c4", name="plugin_web_search", arguments=None,
    )))

    assert plugin.calls == []
    assert "entry_id" in result.output


# ── 6. 结果渲染：不把长文整段塞回上下文 ─────────────────────────────

def test_long_results_are_truncated_with_a_trace():
    text = QQPluginToolService.render_result("字" * (RESULT_MAX_CHARS + 30), ok=True)
    assert text.startswith("字" * RESULT_MAX_CHARS)
    assert "已截断 30 字" in text


def test_empty_results_say_so():
    assert "没有返回内容" in QQPluginToolService.render_result("", ok=True)
    assert "调用失败" in QQPluginToolService.render_result("", ok=False)


def test_an_error_result_is_rendered_as_failure():
    plugin = _plugin()

    class _Plugins:
        @staticmethod
        async def call_entry(entry_ref, params=None, *, timeout=10.0):
            return SimpleNamespace(error="permission denied", is_err=lambda: True)

    plugin.plugins = _Plugins()
    output = asyncio.run(plugin.plugin_tool_service.call_plugin_entry("web_search", "search", {}))
    assert output.startswith("调用失败")
    assert "permission denied" in output


def test_a_raising_call_entry_becomes_a_readable_message():
    plugin = _plugin()

    class _Plugins:
        @staticmethod
        async def call_entry(entry_ref, params=None, *, timeout=10.0):
            raise RuntimeError("ipc down")

    plugin.plugins = _Plugins()
    output = asyncio.run(plugin.plugin_tool_service.call_plugin_entry("web_search", "search", {}))
    assert "调用 web_search:search 失败" in output
    assert "RuntimeError" in output


def test_a_plugin_without_call_entry_says_so():
    plugin = _plugin()
    plugin.plugins = SimpleNamespace()
    output = asyncio.run(plugin.plugin_tool_service.call_plugin_entry("web_search", "search", {}))
    assert "跨插件调用接口" in output


# ── 6b. 密钥脱敏 + 失败措辞：她会把这段念出来（真机先例） ──────────────
#
# 真机（16:51）`writer_power_analysis:list_models` 因为 key 失效报错，错误原文长这样：
#   Authentication Fails, Your api key: ****149a is invalid (request_id: 01179f4a-…)
# 这段文本会进她的上下文，而她可能正站在群里。所以：**键名像密钥的字段**与
# **文本里的密钥形状**都要掩掉，并且明确告诉她失败原文不要复述。

def test_secret_shaped_fields_are_masked():
    """**按键名**掩：值是密钥但长得不像密钥（没有 `sk-` 前缀）也要掩。

    特意用一个"没有形状"的值，好把这条判据与"文本形状"那条分开钉住。
    """
    out = QQPluginToolService.render_result(
        {"api_key": "plainkey123456", "access_token": "deadbeefcafe", "model": "x", "token_count": 5},
        ok=True,
    )

    assert "plainkey123456" not in out, "api_key 的值还露着"
    assert "deadbeefcafe" not in out, "access_token 的值还露着"
    assert "（已隐藏）" in out
    assert "token_count" in out and "5" in out, "正常字段被误伤了（只该掩键名像密钥的）"


def test_secret_shapes_inside_text_are_masked():
    """别人插件的错误文本里写着 key 的形状 —— 也要掩，不能只认键名。"""
    real = QQPluginToolService.render_result(
        "Authentication Fails, Your api key: ****149a is invalid "
        "(request_id: 01179f4a-6819-415d-85ca-4999c5c48825)",
        ok=False,
    )
    assert "149a" not in real, "脱敏后的 key 尾号还留着"
    assert "Authentication Fails" in real, "别把有用的原因也一起掩掉"

    assert "abcdefgh12345678" not in QQPluginToolService.render_result(
        "Authorization: Bearer abcdefgh12345678", ok=True,
    )
    assert "sk-live-abcdefghijkl" not in QQPluginToolService.render_result(
        "key is sk-live-abcdefghijkl", ok=True,
    )


def test_a_failed_call_tells_her_not_to_quote_the_error():
    """失败原文里有账号/端点/内部 id：告诉她用大白话说一句，别复述。"""
    assert "不要把它念给对方" in QQPluginToolService.render_result("boom", ok=False)
    assert "不要把它念给对方" not in QQPluginToolService.render_result("fine", ok=True)


# ── 6c. 关键日志双写：否则事后完全查不到 ─────────────────────────────
#
# 真机 16:53 的教训：`plugin._emit_log` 只进内存 ring，而 ring 在插件重载时清空 ——
# "这一轮到底挂没挂工具、她到底调没调那个 entry"只能靠别的插件的日志反推。
# 所以桥的判定性日志必须**同时**落到文件日志。

def test_the_call_result_goes_into_the_file_log_too():
    plugin = _plugin()

    class _Plugins:
        @staticmethod
        async def call_entry(entry_ref, params=None, *, timeout=10.0):
            return SimpleNamespace(value={"ok": True}, is_err=lambda: False)

    plugin.plugins = _Plugins()
    asyncio.run(plugin.plugin_tool_service.call_plugin_entry("web_search", "search", {}))

    assert any("web_search:search -> ok" in line for line in plugin.logged), (
        "调用结果没进文件日志（重载后就查不到了）"
    )


def test_the_mount_decision_goes_into_the_file_log_too():
    plugin = _configured_plugin(tiers={"web_search": "all"})
    service = _generation_service(plugin)
    session = _Session()

    asyncio.run(service._arm_turn_tools(
        context=_context(), user_session=session, consent_before={},
    ))

    assert any("本轮挂载 1 个插件工具" in line for line in plugin.logged), (
        "挂载决定没进文件日志"
    )


def test_result_rendering_never_leaks_a_raw_object():
    """渲染必须是文本：把非字符串对象直接塞回去会让工具结果不可读。"""
    text = QQPluginToolService.render_result({"a": 1, "b": [1, 2]}, ok=True)
    assert json.loads(text) == {"a": 1, "b": [1, 2]}


# ── 7. 挂载路径（reply_generation_service）────────────────────────────

class _Session:
    def __init__(self):
        self.tools = None
        self.handler = None
        self.round_start = None
        self.cleared = 0

    def set_tools(self, tools):
        self.tools = tools
        self.cleared += 1 if tools is None else 0

    def set_tool_call_handler(self, handler):
        self.handler = handler

    def set_tool_round_start_callback(self, cb):
        self.round_start = cb


def _generation_service(plugin):
    from plugin.plugins.qq_auto_reply.reply_generation_service import QQReplyGenerationService

    service = QQReplyGenerationService(plugin)
    # recall 那条路单独测过；这里把它的"该不该挂"照着真实判据钉一下：
    # 真实实现是 `use_memory_context` 为假就返回 False（不挂 recall）。
    service._arm_recall_tool = lambda **kw: bool(kw["context"].use_memory_context)
    plugin.memory_tool_service = SimpleNamespace(
        build_recall_tool_definition=lambda: SimpleNamespace(name="recall_memory"),
    )
    return service


def _context(*, permission_level="admin", use_memory_context=True):
    return SimpleNamespace(
        permission_level=permission_level,
        use_memory_context=use_memory_context,
        session_key="private:u1",
    )


def _configured_plugin(**kw):
    """配好一个可用插件，并把候选表刷进缓存（生产里由后台循环做）。"""
    plugin = _plugin(
        plugins_result=_plugins_payload(_candidate("web_search", ("search",), name="搜索")),
        **kw,
    )
    asyncio.run(_refresh(plugin.plugin_tool_service))
    return plugin


def test_without_configuration_the_old_path_runs_verbatim():
    """功能默认关闭：没配任何插件时，行为与之前一致（只挂 recall）。"""
    plugin = _configured_plugin(tiers={})
    service = _generation_service(plugin)
    session = _Session()

    armed, bridge = asyncio.run(service._arm_turn_tools(
        context=_context(), user_session=session, consent_before={},
    ))

    assert (armed, bridge) == (True, False)
    assert session.tools is None, "空配置不该走合并挂载那条路"


def test_configured_on_the_open_platform_arms_recall_plus_bridge():
    plugin = _configured_plugin(tiers={"web_search": "all"})
    service = _generation_service(plugin)
    session = _Session()

    armed, bridge = asyncio.run(service._arm_turn_tools(
        context=_context(), user_session=session, consent_before={},
    ))

    assert (armed, bridge) == (True, True)
    assert [tool.name for tool in session.tools] == ["recall_memory", "plugin_web_search"]
    assert any("[PluginTool]" in msg for _lvl, msg in plugin.emitted)


def test_the_bridge_also_arms_on_the_onebot_channel():
    """OneBot（反向 `napcat`）与开放平台一样挂插件工具。

    **这是使用者 2026-10-02 改的决定**：原先是"只在开放平台挂"，理由是 NapCat 那边
    "群里什么样的人都有，多给一层工具面就多一层风险"。放开之后风险仍由**分档**控制 ——
    这里配的是 `all` 档，等于"名册里的人都能用"是显式配出来的；默认配置是空的。
    """
    plugin = _configured_plugin(tiers={"web_search": "all"}, mode="napcat")
    service = _generation_service(plugin)
    session = _Session()

    armed, bridge = asyncio.run(service._arm_turn_tools(
        context=_context(), user_session=session, consent_before={},
    ))

    assert (armed, bridge) == (True, True)
    assert [tool.name for tool in session.tools] == ["recall_memory", "plugin_web_search"]


def test_no_channel_object_means_no_bridge():
    """闸不是恒真：没有连接对象时一个插件工具都不挂（也是未连接时的真实状态）。"""
    plugin = _configured_plugin(tiers={"web_search": "all"})
    plugin.qq_client = None
    service = _generation_service(plugin)
    session = _Session()

    armed, bridge = asyncio.run(service._arm_turn_tools(
        context=_context(), user_session=session, consent_before={},
    ))

    assert bridge is False
    assert session.tools is None


def test_an_unknown_transport_is_not_a_plugin_tool_channel():
    """判据是传输族，不是"有连接就开"：认不出来的传输族一律不挂。"""
    from plugin.plugins.qq_auto_reply.plugin_tool_service import channel_allows_plugin_tools

    assert channel_allows_plugin_tools(None) is False
    for channel, mode in (("open", ""), ("onebot", "napcat"), ("", "napcat_forward"), ("", "open_platform")):
        assert channel_allows_plugin_tools(SimpleNamespace(CHANNEL=channel, mode=mode)) is True
    assert channel_allows_plugin_tools(SimpleNamespace(CHANNEL="discord", mode="gateway")) is False
    assert channel_allows_plugin_tools(SimpleNamespace()) is False


def test_an_unrecognised_speaker_gets_no_tools_at_all():
    plugin = _configured_plugin(tiers={"web_search": "all"})
    service = _generation_service(plugin)
    session = _Session()

    armed, bridge = asyncio.run(service._arm_turn_tools(
        context=_context(permission_level="none", use_memory_context=False),
        user_session=session, consent_before={},
    ))

    assert (armed, bridge) == (False, False)
    assert session.tools is None


def test_admin_only_plugins_are_not_mounted_for_a_normal_user():
    plugin = _plugin(
        tiers={"mijia": "admin", "web_search": "all"},
        plugins_result=_plugins_payload(_candidate("mijia"), _candidate("web_search")),
    )
    asyncio.run(_refresh(plugin.plugin_tool_service))
    service = _generation_service(plugin)
    session = _Session()

    asyncio.run(service._arm_turn_tools(
        context=_context(permission_level="normal"), user_session=session, consent_before={},
    ))

    assert [tool.name for tool in session.tools] == ["recall_memory", "plugin_web_search"]


def test_a_stopped_plugin_is_reported_but_not_mounted():
    """配了但没在跑：挂不上去，**而且要留痕** —— 否则使用者以为配好了就能用。"""
    plugin = _plugin(
        tiers={"jukebox_controller": "all"},
        plugins_result=_plugins_payload(_candidate("jukebox_controller", status="stopped")),
    )
    asyncio.run(_refresh(plugin.plugin_tool_service))
    service = _generation_service(plugin)
    session = _Session()

    armed, bridge = asyncio.run(service._arm_turn_tools(
        context=_context(), user_session=session, consent_before={},
    ))

    assert bridge is False
    assert session.tools is None
    assert any(
        "jukebox_controller" in msg and "未挂载" in msg for _lvl, msg in plugin.emitted
    ), f"没说明为什么没挂上: {plugin.emitted}"


def test_the_dispatcher_routes_recall_and_bridge_separately():
    plugin = _configured_plugin(tiers={"web_search": "all"})
    service = _generation_service(plugin)
    session = _Session()
    recall_calls: list = []

    async def _recall(tool_call):
        recall_calls.append(tool_call)
        return SimpleNamespace(call_id="r", name="recall_memory", output="回忆")

    service._build_recall_tool_handler = lambda **kw: _recall

    asyncio.run(service._arm_turn_tools(context=_context(), user_session=session, consent_before={}))

    async def _run():
        first = await session.handler(SimpleNamespace(
            call_id="c1", name="recall_memory", arguments={},
        ))
        second = await session.handler(SimpleNamespace(
            call_id="c2", name="plugin_web_search", arguments={"entry_id": "search"},
        ))
        assert first.call_id == "r", "recall 那条没被分发到原来的 handler"
        assert second.call_id == "c2"

    asyncio.run(_run())
    assert len(recall_calls) == 1
    assert plugin.calls == [("web_search:search", {}, 20.0)]


def test_arming_failure_clears_the_slots():
    plugin = _configured_plugin(tiers={"web_search": "all"})
    service = _generation_service(plugin)

    class _Boom(_Session):
        def set_tools(self, tools):
            raise RuntimeError("session gone")

    session = _Boom()
    armed, bridge = asyncio.run(service._arm_turn_tools(
        context=_context(), user_session=session, consent_before={},
    ))
    assert (armed, bridge) == (False, False)


def test_the_candidate_ids_are_never_qq_plugins():
    """自指检查：候选里永远不该出现自己，也不该出现 qq* 家族。"""
    plugin = _plugin(plugins_result=_plugins_payload(
        _candidate("qq_auto_reply"), _candidate("qq_anything"), _candidate("web_search"),
    ))
    rows = asyncio.run(_refresh(plugin.plugin_tool_service))
    assert all(not row["plugin_id"].startswith("qq") for row in rows)


# ── 8. 界面契约（拖拽分档位）────────────────────────────────────────
#
# 2026-10-02：这一页从"只有开放平台面板有"变成**两个面板共用**一份实现
# （`static/plugin-tools.js`）—— 通道闸对所有 QQ 通道都开之后，NapCat 那边也得配档位。
# 所以下面分两层钉：
#   · **界面本体**（markup + 行为）在共用模块里，只此一份；
#   · **两个面板**各自只留空容器 + 侧边栏项 + 脚本标签 + switchPage 接线。

_STATIC = pathlib.Path(__file__).resolve().parents[1] / "static"
_PANELS = ("open_platform.html", "napcat.html")
_MODULE = _STATIC / "plugin-tools.js"


def _module_text() -> str:
    """共用实现（markup + 行为）的唯一来源。"""
    return _MODULE.read_text(encoding="utf-8")


def _panel_text(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def test_both_panels_have_the_page_and_neither_keeps_its_own_copy():
    """独立一页（像表情包那样）：配置页里塞不下 —— 可添加的插件一多，列表底部的卡片
    根本拖不到右侧分区去。两个面板都要有这一页，且**都不许自己抄一份 markup**。"""
    module = _module_text()
    assert 'id="pt-zone-available"' in module, "共用模块里没有拖拽区"
    for name in _PANELS:
        text = _panel_text(name)
        assert 'data-page="plugintools"' in text, f"{name} 侧边栏没有「插件」这一项"
        assert 'id="page-plugintools"' in text, f"{name} 没有对应的页面容器"
        assert "plugin-tools.js" in text, f"{name} 没有引入共用模块"
        assert "pt-zone-available" not in text, f"{name} 自己抄了一份 markup（该删）"
        assert "loadPluginTools()" in text and "ptScheduleRefresh()" in text, (
            f"{name} 切到这一页不会加载数据 / 不会起自动刷新"
        )


def test_available_cards_carry_tier_buttons():
    """一多起来纯拖拽没法用 → 每张卡片自己带档位按钮（可靠路径），拖拽只是加速器。

    原来那个「添加」按钮是坏的（点了传空串，等于撤销一个还没加的插件，什么也不发生），
    使用者要求去掉 —— 现在两个按钮直接就是档位，语义与结果一致。
    """
    text = _module_text()
    assert "ptSetTier('${pid}','${want}')" in text, "卡片上的按钮没有把档位写进去"
    assert "btn('all'" in text and "btn('admin'" in text, "卡片上没有两个档位按钮"
    assert "ui.openplat.plugintools.btn_add" not in text, "那个没用的「添加」按钮还在"


def test_the_available_list_is_scrollable_and_filterable():
    """长列表要能滚能筛，且右侧档位区 sticky（否则滚动时落点跑出视野）。"""
    text = _module_text()
    assert 'id="pt-search"' in text, "没有搜索框"
    assert "max-height:46vh" in text and "overflow-y:auto" in text, "可添加列表没有自己的滚动区"
    assert "position:sticky" in text, "档位区没有 sticky"


def test_the_page_has_the_three_drop_zones():
    """可用区 + 两个档位区，各自带 data-tier（空串 = 撤销/未添加）。"""
    text = _module_text()
    for zone, tier in (("pt-zone-available", ""), ("pt-zone-all", "all"), ("pt-zone-admin", "admin")):
        assert f'id="{zone}"' in text, f"缺拖拽区 {zone}"
        assert f'id="{zone}"' in text and f'data-tier="{tier}"' in text, (
            f"{zone} 的 data-tier 不是 {tier!r} —— 落点判据会错"
        )


def test_cards_are_draggable_and_the_drop_saves_the_tier():
    """卡片要 draggable，落点要真的调 ptSetTier（拖完即保存）。"""
    text = _module_text()
    assert 'draggable="true"' in text, "卡片不可拖"
    assert "dragstart" in text and "text/plain" in text, "拖拽没有携带 plugin_id"
    assert "ptSetTier(pid" in text, "落点没有把 pid 交给保存逻辑"
    assert "qq_open_plugin_tools" in text, "保存没有带上那个设置键"


def test_the_page_reports_which_channel_the_tools_are_live_on():
    """通道闸对所有 QQ 通道都开，界面仍要把**当前通道**写出来 —— 否则换通道之后
    没人知道配置还在生效（2026-10-02 放开 OneBot 时顺手把那条"只在开放平台生效"的
    死文案换成了按通道说的两句话）。"""
    text = _module_text()
    assert "enabled_on_this_channel" in text, "没有按通道提示生效范围"
    assert "channel_ok" in text, "生效中那句没了"
    assert "channel_unsupported" in text, "挂不上那句没了"


def test_cards_are_green_while_running_and_grey_while_stopped():
    """使用者 17:3x 定：**在跑 = 绿卡、没在跑 = 灰卡**，一眼看出来哪些挂了工具。"""
    text = _module_text()

    assert "var(--success-tint-strong)" in text and "var(--success-border)" in text, "没有绿卡那套色"
    assert "border-left:3px solid var(--success)" in text, "绿卡没有明显的绿色左边条"
    assert "background:var(--overlay);border:1px solid var(--border)" in text, "没有灰卡那套色"
    assert "ui.openplat.plugintools.running" in text, "运行中没打标记"
    assert "data-running=" in text, "卡片没把状态落在 DOM 上（不好验也不好样式化）"


def test_the_page_refreshes_itself_so_the_cards_do_not_go_stale():
    """使用者 17:3x 的原话：「插件已经启动了，但是卡片还是显示未启动」。

    以前这页只在进页时读一次；状态是在别处（插件管理）改的，于是卡片会一直停在旧值。
    现在进页起定时器、离开就停 —— 定时器住在共用模块，接线在两个面板各自的 switchPage。
    """
    module = _module_text()

    assert "function ptScheduleRefresh" in module and "function ptStopRefresh" in module, "没有自动刷新那对函数"
    assert "setInterval(" in module, "没有定时器"
    assert "document.hidden" in module, "后台标签页里还在轮询"
    assert "currentPage === 'plugintools'" in module, "定时器不认当前页（别的页也会被一起轮询）"
    for name in _PANELS:
        text = _panel_text(name)
        assert "ptScheduleRefresh()}" in text, f"{name} 切到插件页没起定时器"
        assert "if(page!=='plugintools')ptStopRefresh()" in text, f"{name} 离开插件页没停定时器"


def test_the_plugin_tools_query_asks_for_a_real_refresh():
    """那条 bug 其实在**接线**上：界面查询当时选了"读缓存"那条路。

    所以这里直接钉接线：`plugin_tools` 的取数必须是 `refresh_for_ui()`（真去问一次），
    而不是 `list_candidates(refresh=True)`（按年龄算新鲜）。
    """
    source = (pathlib.Path(__file__).resolve().parents[1] / "__init__.py").read_text(encoding="utf-8")

    assert "await service.refresh_for_ui()" in source, "界面查询又退回读缓存了"
    assert "list_candidates(refresh=True)" not in source, "界面查询还在用按年龄判新鲜那条路"


def test_every_plugin_tools_label_exists_in_both_bundles():

    base = pathlib.Path(__file__).resolve().parents[1] / "i18n"
    zh = json.loads((base / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((base / "en.json").read_text(encoding="utf-8"))
    keys = [key for key in zh if key.startswith("ui.openplat.plugintools.")]
    assert keys, "一个插件工具面板的文案键都没有"
    assert set(keys) == {key for key in en if key.startswith("ui.openplat.plugintools.")}, (
        "两个语种的键集合不一致"
    )
    for required in ("title", "tier_all", "tier_admin", "tier_all_short", "tier_admin_short",
                     "search_placeholder", "btn_remove", "saved"):
        assert f"ui.openplat.plugintools.{required}" in zh, f"缺键 {required}"
