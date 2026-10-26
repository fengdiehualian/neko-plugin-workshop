# 和猫娘一起开飞机（简单飞机2副驾驶）插件 —— 副驾驶仪表盘的接口契约
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 本文件是 sp2_copilot 插件的一部分，以 GNU General Public License v3.0 许可发布。
# 完整条款见仓库根目录的 LICENSE 文件。

"""副驾驶仪表盘（Hosted UI）的契约测试。

面板只能通过 ``props.api.call`` 调**插件入口**，所以三件事必须被守住：

1. 面板会调的每个 ``@ui.action`` id，都同时是一个 ``@plugin_entry``
   （少了 ``@plugin_entry``，host 的 ``trigger`` 根本找不到它，按钮会 403）；
2. ``@ui.context(id="dashboard")`` 的 id 与 plugin.toml 里 panel 的 context 一致；
3. provider 不做网络请求、不抛异常 —— host 在每次面板动作之前都会重新求值它。

另外钉住两个容易悄悄改坏的点：**离线时快照的字段一个都不能少**
（少一个面板就会出现 undefined 空卡片），以及设置保存的地址校验与范围钳制。
"""

from __future__ import annotations

import asyncio
import logging
import tomllib
from pathlib import Path
from typing import Any, Dict, List

import plugin.plugins.sp2_copilot as plugin_module
from plugin.plugins.sp2_copilot.ui_api import PANEL_CONTEXT_ID, Sp2UiMixin
from plugin.sdk.plugin import ui as ui_mod
from plugin.sdk.shared.constants import EVENT_META_ATTR

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 面板（ui/panel.tsx）会调用的动作 id。改动 ui_api.py / panel.tsx 时同步这里。
EXPECTED_UI_ACTIONS = {
    "sp2_ui_ping",
    "sp2_ui_snapshot",
    "sp2_ui_save_settings",
    "sp2_status",
    "sp2_briefing",
}

# 离线快照必须带齐的字段 —— 面板的每个卡片都从这里取值。
REQUIRED_SNAPSHOT_KEYS = {
    "connected",
    "error",
    "latency_ms",
    "bridge_url",
    "state",
    "aircraft",
    "navigation",
    "environment",
    "phase",
    "phase_label",
    "status_summary",
    "warning_summary",
    "checklist_summary",
    "navigation_summary",
    "warnings",
    "checklist",
    "errors",
    "fetched_at",
}

REQUIRED_CONTEXT_KEYS = {
    "connected",
    "last_error",
    "bridge_url",
    "read_only",
    "config",
    "levels",
    "phases",
}


def _load_manifest() -> Dict[str, Any]:
    with (PLUGIN_ROOT / "plugin.toml").open("rb") as handle:
        return tomllib.load(handle)


def _collect_meta(attr: str) -> Dict[str, Any]:
    """扫插件类，收集带某个元信息属性的方法名 -> meta。"""
    found: Dict[str, Any] = {}
    cls = plugin_module.Sp2CopilotPlugin
    for name in dir(cls):
        member = getattr(cls, name, None)
        if member is None:
            continue
        meta = getattr(member, attr, None)
        if meta:
            found[name] = meta
    return found


def _new_plugin(**overrides: Any) -> Any:
    """造一个**没有跑 __init__** 的插件实例。

    只给面板代码真正会读到的属性赋值，这样可以在不开游戏、不连宿主的情况下
    调用 provider 和离线分支。``_host()`` 靠 ``type(self).__module__`` 反查模块，
    所以必须是真的插件类，不能拿一个替身类顶上。
    """
    obj = plugin_module.Sp2CopilotPlugin.__new__(plugin_module.Sp2CopilotPlugin)
    obj.logger = logging.getLogger("sp2_copilot-test")
    obj.config = None
    obj.ctx = None
    obj._bridge_url = plugin_module.DEFAULT_BRIDGE_URL
    obj._timeout = 12.0
    obj._poll_interval = 3.0
    obj._thresholds = plugin_module._default_thresholds()
    obj._client = None
    obj._client_loop = None
    obj._ui_last_connected = None
    obj._ui_last_error = ""
    for key, value in overrides.items():
        setattr(obj, key, value)
    return obj


def test_panel_manifest_declares_context_and_permissions() -> None:
    manifest = _load_manifest()
    ui_section = manifest["plugin"]["ui"]
    assert ui_section["enabled"] is True

    panels = ui_section["panel"]
    assert len(panels) == 1
    panel = panels[0]
    assert panel["id"] == "dashboard"
    assert panel["entry"] == "ui/panel.tsx"
    assert panel["context"] == PANEL_CONTEXT_ID

    # 少一个权限，面板上的按钮就会变成 403。
    permissions = set(panel["permissions"])
    assert {"state:read", "config:read", "action:call"} <= permissions


def test_guide_reuses_panel_context() -> None:
    """指南复用面板的 context，可以少一条 "UI context not found" 警告。"""
    manifest = _load_manifest()
    guides = manifest["plugin"]["ui"]["guide"]
    assert guides[0]["context"] == PANEL_CONTEXT_ID


def test_context_provider_id_matches_manifest() -> None:
    contexts = _collect_meta(ui_mod.UI_CONTEXT_META_ATTR)
    assert contexts, "至少要有一个 @ui.context"
    ids = {meta["id"] for meta in contexts.values()}
    assert PANEL_CONTEXT_ID in ids


def test_every_ui_action_is_also_a_plugin_entry() -> None:
    actions = _collect_meta(ui_mod.UI_ACTION_META_ATTR)
    entries = _collect_meta(EVENT_META_ATTR)

    action_ids = {meta["id"] for meta in actions.values()}
    # EventMeta 是 dataclass，不是 dict。
    entry_ids = {getattr(meta, "id", None) for meta in entries.values()}

    assert EXPECTED_UI_ACTIONS <= action_ids, (
        f"面板会调但后端没暴露：{sorted(EXPECTED_UI_ACTIONS - action_ids)}"
    )
    missing_entries = action_ids - entry_ids
    assert not missing_entries, f"这些 @ui.action 缺 @plugin_entry：{sorted(missing_entries)}"


def test_context_provider_never_throws() -> None:
    """host 在每次面板动作前都会求值 provider，它绝不能抛。"""
    obj = _new_plugin()
    result = asyncio.run(obj.get_dashboard_ui_context())
    assert isinstance(result, dict)
    assert REQUIRED_CONTEXT_KEYS <= set(result.keys())
    assert result["read_only"] is True
    assert result["bridge_url"] == plugin_module.DEFAULT_BRIDGE_URL


def test_context_provider_survives_broken_state() -> None:
    """配置读到一半炸了也要返回最小可用结构。"""
    obj = _new_plugin()
    obj._thresholds = None  # 故意给个坏值
    result = asyncio.run(obj.get_dashboard_ui_context())
    assert isinstance(result, dict)
    assert "levels" in result and "phases" in result


def test_effective_config_exposes_thresholds() -> None:
    obj = _new_plugin()
    config = obj._ui_effective_config()
    for key in (
        "bridge_url",
        "timeout_seconds",
        "poll_interval_seconds",
        "terrain_warning_agl_m",
        "sink_rate_warning_mps",
        "bank_warning_deg",
        "low_fuel_warning_percent",
        "aoa_warning_deg",
        "overspeed_warning_ratio",
    ):
        assert key in config


def test_offline_snapshot_payload_is_complete() -> None:
    """游戏没开时面板也要能画出完整的空卡片，不能出现 undefined。"""
    obj = _new_plugin()
    payload = obj._ui_offline_payload("连不上游戏。")
    assert REQUIRED_SNAPSHOT_KEYS <= set(payload.keys())
    assert payload["connected"] is False
    assert payload["warnings"] == []
    assert payload["checklist"]["items"] == []


def test_offline_ping_reports_connection_state() -> None:
    obj = _new_plugin()

    class _BrokenClient:
        async def get(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("connection refused")

    obj._get_client = lambda: _BrokenClient()  # type: ignore[method-assign]
    result = asyncio.run(obj.sp2_ui_ping())
    assert result.is_ok()
    payload = result.value
    assert payload["connected"] is False
    assert "连不上" in payload["message"]
    assert obj._ui_last_connected is False


def test_offline_snapshot_short_circuits() -> None:
    """离线时不要再去读四个接口，直接返回空结构。"""
    obj = _new_plugin()

    class _BrokenClient:
        async def get(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("connection refused")

    obj._get_client = lambda: _BrokenClient()  # type: ignore[method-assign]
    result = asyncio.run(obj.sp2_ui_snapshot())
    assert result.is_ok()
    payload = result.value
    assert REQUIRED_SNAPSHOT_KEYS <= set(payload.keys())
    assert payload["connected"] is False


def test_save_settings_rejects_bad_url() -> None:
    obj = _new_plugin()
    result = asyncio.run(obj.sp2_ui_save_settings(bridge_url="127.0.0.1:21580"))
    assert result.is_err()
    assert "http://" in str(result.error) or "https://" in str(result.error)


def test_save_settings_clamps_out_of_range_values() -> None:
    """面板的 NumberInput 可能被敲进任何值，后端必须自己钳制。"""
    obj = _new_plugin()

    class _SilentConfig:
        def __init__(self) -> None:
            self.patched: Dict[str, Any] = {}

        async def update(self, patch: Dict[str, Any], timeout: float = 0.0) -> None:
            self.patched = patch

    config = _SilentConfig()
    obj.config = config
    result = asyncio.run(
        obj.sp2_ui_save_settings(
            bridge_url="http://127.0.0.1:21580",
            timeout_seconds=999.0,
            poll_interval_seconds=0.01,
            bank_warning_deg=180.0,
        )
    )
    assert result.is_ok()
    section = config.patched["sp2_copilot"]
    assert section["timeout_seconds"] == 60.0
    assert section["poll_interval_seconds"] == 1.0
    assert section["bank_warning_deg"] == 90.0
    # 运行时也要立刻生效，不能等框架回调
    assert obj._timeout == 60.0
    assert obj._thresholds["bank_deg"] == 90.0


def test_plugin_is_read_only_by_design() -> None:
    """只读副驾驶：UI 层不暴露任何会改变游戏状态的动作。

    这条测试是刻意加的护栏 —— 以后谁想加「帮我放起落架」的按钮，
    它就会先失败，逼着你去想清楚要不要破坏这条设计原则。
    """
    actions = _collect_meta(ui_mod.UI_ACTION_META_ATTR)
    # 注意别用 "set" 这种过宽的词 —— save_settings 保存的是插件自己的配置，
    # 不是游戏状态，不该被误伤。
    forbidden = {"control", "command", "press", "click", "hold", "release"}
    for name, meta in actions.items():
        lowered = f"{name} {meta['id']}".lower()
        assert not any(word in lowered for word in forbidden), (
            f"{name} 看起来是个写操作；本插件承诺只读，不要在这里加控制动作"
        )
        for part in ("gear", "throttle", "flaps", "autopilot"):
            assert part not in lowered, (
                f"{name} 涉及飞机操纵（{part}）；本插件承诺只读，动手的必须是玩家"
            )


def test_llm_tools_registered() -> None:
    """模型能调的工具必须都在，改名会导致对话里用不了。"""
    from plugin.sdk.plugin.llm_tool import LLM_TOOL_META_ATTR  # noqa: PLC0415

    cls = plugin_module.Sp2CopilotPlugin
    names: List[str] = []
    for name in dir(cls):
        member = getattr(cls, name, None)
        meta = getattr(member, LLM_TOOL_META_ATTR, None)
        if meta:
            names.append(meta.name)
    expected = {
        "read_sp2_docs",
        "get_sp2_status",
        "get_sp2_instruments",
        "get_sp2_aircraft",
        "get_sp2_navigation",
        "get_sp2_warnings",
        "get_sp2_checklist",
        "get_sp2_briefing",
    }
    assert expected <= set(names), f"缺少工具：{sorted(expected - set(names))}"
    # 弱模型在工具超过 ~15 个后选择准确率会明显下降。
    assert len(names) <= 15


def test_mixin_is_used_by_plugin() -> None:
    assert issubclass(plugin_module.Sp2CopilotPlugin, Sp2UiMixin)
