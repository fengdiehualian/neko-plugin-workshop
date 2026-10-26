# -*- coding: utf-8 -*-
"""P7.4 的守卫：**入口打通**（0.8.3 真机「没法开局 + 聊天指令无效」事故的回归防线）。

0.8.3 真机上暴露了三个互相独立的入口问题：

1. **面板没法开局**。空闲态只有一行冰冷的文字，没有任何可点的入口 ——
   玩家要么去聊天框打字，要么就卡在「当前没有进行中的对局」。
2. **聊天指令打不动**。插件一个 ``@llm_tool`` 都没有（只有 ``@plugin_entry``），
   而这两者是**不同的东西**：``@llm_tool`` 才会注册到 main_server 的
   ``/api/tools`` registry，猫娘手里有它才可能调用。于是玩家说「加入一局」，
   猫娘只会当成闲聊回一句「打深渊还是清委托？」。
3. **面板是陈旧的**。宿主**从不轮询** Hosted 面板的上下文 —— 只在「插件启停」
   和「面板自己发起动作之后」刷新。于是这一局结束后，面板还停在局中界面，
   玩家点「一键梭哈」只会拿到 ``not_joined``，而宿主把**任何**动作失败都当成
   「面板运行时错误」上报 —— 屏幕顶部弹一个黄色的「插件界面控件错误」覆盖层。

这一阶段的硬要求：

* 空闲态必须有**真的能点**的开局按钮（经典 / 无尽）；
* 空闲态**不许**出现局中的操作按钮；
* 插件必须注册 ``@llm_tool``（聊天指令才可能生效），且必须**自愈**
  （``main_server`` 重启后注册会丢，官方文档明确说 helper 不做自动重注册）；
* 面板必须**自己轮询**上下文（宿主不轮询）；
* 引擎层与模式层一个字节都不许动。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from conftest import engine_mode_code_changes

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _PLUGIN_ROOT / "ui" / "panel.tsx"
_I18N = _PLUGIN_ROOT / "i18n" / "zh-CN.json"
_PLUGIN_TOML = _PLUGIN_ROOT / "plugin.toml"


# ---------------------------------------------------------------- 工具


def _panel_source() -> str:
    return _PANEL.read_text(encoding="utf-8")


def _panel_code() -> str:
    """面板源码去掉注释与 CSS 模板字符串后的正文。"""
    source = _panel_source()
    kept: list[str] = []
    in_block = False
    for line in source.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            if "*/" not in stripped:
                in_block = True
            continue
        if stripped.startswith("//"):
            continue
        kept.append(line)
    code = "\n".join(kept)
    marker = "const RRM_CSS = `"
    if marker in code:
        head, _, rest = code.partition(marker)
        _, _, tail = rest.partition("`")
        code = head + "\n/* <RRM_CSS> */\n" + tail
    return code


def _idle_branch() -> str:
    """空闲态分支的源码（``if (state.stage !== "playing") {`` 那一整块）。"""
    code = _panel_code()
    marker = 'if (state.stage !== "playing") {'
    assert marker in code, "面板里找不到空闲态分支"
    rest = code.split(marker, 1)[1]
    # 分支体里的缩进都 ≥4，所以第一个顶格 ``  }`` 就是分支结束。
    end = rest.find("\n  }\n")
    assert end != -1, "空闲态分支没有正常闭合"
    return rest[:end]


def _i18n() -> dict[str, str]:
    return json.loads(_I18N.read_text(encoding="utf-8"))


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def _plugin_source() -> str:
    return (_PLUGIN_ROOT / "__init__.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 面板：开局入口


def test_idle_state_has_clickable_join_buttons() -> None:
    """空闲态必须有**真的能点**的开局按钮（0.8.3 的核心投诉）。

    P14.0：按钮从两个变成**三档卡**（街机 / 命运挑战 / 无尽），
    命运挑战再点开是二级选择（入门 / 进阶 / 地狱）。
    按钮不再用 ``ActionButton``，而是走 P11.1 那把全局请求锁的 ``callAction`` ——
    不变式不变：**每一档都能真的开局**。
    """
    branch = _idle_branch()
    assert 'actionById("join")' in branch, "空闲态必须有 join 按钮"
    # 五档一个都不许少（街机 + 命运挑战三档 + 无尽）。
    for mode in (
        "classic_arcade",
        "classic_beginner",
        "classic_advanced",
        "classic_hell",
        "infinite",
    ):
        assert f'"{mode}"' in branch, f"必须有 {mode} 这一档"
    # 按钮必须真的去调 action（不能只是取了个 action 就完事）。
    assert "joinMode(" in branch, "档位卡必须走 joinMode"
    assert "callAction(joinActionId" in branch, "开局必须真的调 join action"


def test_idle_state_hides_the_trade_action_area() -> None:
    """空闲态**不许**出现局中的操作按钮。

    玩家点了拿到的只会是 ``not_joined``，而宿主把动作失败当面板运行时错误
    上报 —— 屏幕顶部弹黄色「插件界面控件错误」，体验极差。
    """
    branch = _idle_branch()
    for banned in (
        'actionById("buy")',
        'actionById("sell")',
        'actionById("daily_claim")',
        'actionById("daily_loan")',
        'actionById("daily_reject")',
        'actionById("skip_day")',
        'actionById("analyze")',
        'actionById("force_liquid")',
    ):
        assert banned not in branch, f"空闲态不许出现局中操作按钮：{banned}"


def test_join_buttons_are_guarded_by_the_action_existing() -> None:
    """join 按钮必须有存在性守卫。

    ``joinAction`` 为空还去渲染按钮 ⇒ ``api.call(undefined, ...)`` ⇒ 宿主当成
    面板运行时错误上报。老版本插件没把 join 挂成 UI action 时不能渲染。

    P14.0：档位卡不再用 ``ActionButton``，所以守卫从"判空后渲染"改成
    **"判空后连调用都不许发生"** —— 更严。
    """
    branch = _idle_branch()
    assert 'actionById("join")' in branch, "必须取 join action"
    assert "joinActionId" in branch, "join 必须判空"
    assert "if (!joinActionId) return" in branch, "join 必须在调用前判空"
    assert "joinAction ?" in branch, "档位卡必须判空后才渲染"


def test_panel_polls_its_own_context() -> None:
    """面板必须**自己轮询**上下文（宿主从不轮询）。"""
    code = _panel_code()
    assert "PANEL_POLL_INTERVAL_MS" in code, "面板必须有轮询节奏常量"
    assert "setInterval" in code, "面板必须自己定时拉上下文"
    assert "props.api.refresh()" in code, "轮询必须走 props.api.refresh()"


def test_failed_actions_resync_the_context() -> None:
    """动作失败 → 立刻把上下文拉回来（面板不能停在旧状态）。"""
    code = _panel_code()
    assert "const resync = () =>" in code
    assert "onActionError" in code, "动作失败必须有统一的错误处理"
    # 走 ``ActionButton`` 的动作：按钮上必须挂 ``onError``。
    for action in ("daily_claim", "daily_loan", "skip_day"):
        block = code.split(f'actionById("{action}")', 1)
        assert len(block) == 2, f"面板里找不到 {action} 按钮"
        tail = block[1][:400]
        assert "onActionError" in tail, f"{action} 失败后必须 resync"
    # P12：买入 / 卖出换成了 ``Button`` + ``callAction``（为了进 P11.1 的
    # 全局请求锁），所以它们的失败重同步由 ``callAction`` 统一负责 ——
    # **失败分支里必须也有一次 refresh**。
    for action in ("buy", "sell"):
        assert f'callAction("{action}"' in code, f"面板里找不到 {action} 的调用"
    call_body = code.split("const callAction = async", 1)[1].split("const callMeow", 1)[0]
    assert "catch" in call_body, "callAction 必须有失败分支"
    assert "api.refresh()" in call_body, "callAction 失败后也必须把上下文拉回来"


# ---------------------------------------------------------------- 后端：LLM 工具


def test_join_is_exposed_as_a_ui_action() -> None:
    """``join`` 必须同时挂 ``@ui.action``（面板才拿得到它）。"""
    source = _plugin_source()
    assert 'id="join"' in source
    # ui.action 与 plugin_entry 必须都带 id="join"。
    assert re.search(r'@ui\.action\(\s*\n\s*id="join"', source), "join 必须挂 @ui.action"
    assert re.search(r'@plugin_entry\(\s*\n\s*id="join"', source), "join 必须保留 @plugin_entry"


def test_plugin_registers_llm_tools() -> None:
    """插件必须注册 ``@llm_tool``（聊天指令才可能生效）。"""
    source = _plugin_source()
    assert "llm_tool" in source, "插件必须导入 llm_tool"
    names = re.findall(r'@llm_tool\(\s*\n\s*name="([^"]+)"', source)
    assert names, "至少要有一个 @llm_tool"
    assert "mock_stock_meow_join" in names, "必须有开局工具"
    assert "mock_stock_meow_status" in names, "必须有状态查询工具"
    # 工具名要满足 main_server 的约束：``[A-Za-z0-9_.\-]{1,64}``。
    for name in names:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", name), f"工具名不合法：{name}"


def test_llm_tool_descriptions_mention_the_trigger_phrases() -> None:
    """工具描述必须写清**什么时候该调用**（模型靠它决定要不要调）。"""
    source = _plugin_source()
    block = source.split("@llm_tool(", 1)[-1]
    assert "加入一局" in block, "开局工具的描述里必须出现触发词「加入一局」"


def test_llm_tools_self_heal_after_main_server_restart() -> None:
    """必须**自愈**：``main_server`` 重启后注册会丢，官方文档说 helper 不管这事。"""
    source = _plugin_source()
    assert "_LLM_TOOL_REREGISTER_SECONDS" in source, "必须有重注册周期常量"
    assert "async def _reregister_llm_tools_if_due" in source, "必须有重注册方法"
    # 必须挂在心跳上（否则插件活着的时候永远不重发）。
    heartbeat = source.split("async def market_tick(", 1)[-1]
    heartbeat = heartbeat.split("\n    async def ", 1)[0]
    assert "_reregister_llm_tools_if_due()" in heartbeat, "重注册必须挂在心跳上"


def test_llm_join_really_starts_a_game(plugin) -> None:
    """LLM 工具真的能开局（聊天框里说「加入一局」就走这条路）。"""
    result = _run(plugin.llm_join(mode="classic_arcade"))
    assert result["is_error"] is False
    assert result["output"]["ok"] is True
    assert result["output"]["mode"] == "classic_arcade"
    # 真的建了一局。
    payload = _run(plugin.market_context())
    assert payload.get("stage") == "playing"


def test_llm_join_reports_a_friendly_error_when_already_joined(plugin) -> None:
    """已经在局中 → 返回**工具级错误**（``is_error``），而不是抛异常。"""
    _run(plugin.llm_join(mode="classic_arcade"))
    result = _run(plugin.llm_join(mode="classic_arcade"))
    assert result["is_error"] is True
    assert result["output"]["ok"] is False
    assert result["error"]


def test_llm_status_reports_idle_then_playing(plugin) -> None:
    """状态工具：空闲时说空闲，局中说局中。"""
    idle = _run(plugin.llm_status())
    assert idle["is_error"] is False
    assert idle["output"]["in_game"] is False

    _run(plugin.llm_join(mode="infinite"))
    playing = _run(plugin.llm_status())
    assert playing["is_error"] is False
    assert playing["output"]["in_game"] is True
    assert playing["output"]["day"] == 1
    assert playing["output"]["alive"] is True


def test_llm_tools_accept_extra_arguments(plugin) -> None:
    """LLM 偶尔会多吐键，宿主还会塞 ``_ctx`` —— 多出来的键必须能吞掉。"""
    result = _run(plugin.llm_status(_ctx={"host_reply": {}}, reason="玩家问的"))
    assert result["is_error"] is False


# ---------------------------------------------------------------- 文案 / 版本


def test_new_i18n_keys_exist() -> None:
    table = _i18n()
    for key in (
        "panel.idle_title",
        "panel.idle_hint",
        "panel.join_classic",
        "panel.join_infinite",
        "panel.idle_modes_hint",
        "llm.join.done",
        "llm.status.idle",
        "llm.status.playing",
    ):
        assert key in table, f"i18n 缺少 {key}"


def test_idle_hint_no_longer_points_at_the_chat_box() -> None:
    """空闲提示不许再让玩家去聊天框打字（面板现在自己能开局）。"""
    table = _i18n()
    assert "聊天" not in table["panel.idle_hint"], "空闲提示不该再指向聊天框"


def test_panel_i18n_keys_all_exist() -> None:
    """面板里每一个**静态** ``t("...")`` 键在 i18n 里都有条目。"""
    table = _i18n()
    used = set(re.findall(r"""\bt\(\s*["']([^"']+)["']""", _panel_code()))
    missing = sorted(key for key in used if key not in table)
    assert not missing, f"i18n 缺少面板用到的键：{missing}"


def test_version_is_bumped() -> None:
    raw = _PLUGIN_TOML.read_text(encoding="utf-8")
    assert 'version = "0.10.5"' in raw


def test_engine_and_mode_layers_are_still_untouched() -> None:
    """P7.4 **不许**碰引擎层与模式层（数值与公式零改动）。"""
    changed = engine_mode_code_changes()
    assert not changed, f"引擎层/模式层的代码不许改动：{changed}"
