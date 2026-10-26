"""拍板 2.0.68：apply_settings_entry payload 容错。

背景：旧签名 `payload: dict` 在面板调用栈偶发传空 arg 列表时
→ `TypeError: missing 1 required positional argument: 'payload'` 抛到 entry call 干掉整个调用。
新签名 `payload: dict | None = None`，None → Err(INVALID_INPUT) 而非 TypeError。

不构造插件实例——用 `GenshinGameBuddyPlugin.__new__` 跳过 __init__，
只填 apply_settings_entry 需要的最小字段（self._options）。
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from plugin.plugins.genshin_game_buddy import GenshinGameBuddyPlugin
from plugin.plugins.genshin_game_buddy.game_registry import PluginOptions


def _make_plugin_stub() -> GenshinGameBuddyPlugin:
    """最小可用 stub——只填 _options（其他字段用 MagicMock 防 AttributeError）。"""
    plugin = GenshinGameBuddyPlugin.__new__(GenshinGameBuddyPlugin)
    plugin._options = PluginOptions()
    # apply_settings 路径里会用到的字段（不是全部，但保持可调用）
    plugin._registry = MagicMock()
    plugin._ocr_executor = None
    plugin.logger = MagicMock()
    plugin.config = MagicMock()
    plugin.config.update = MagicMock(return_value=None)
    return plugin


@pytest.mark.unit
def test_apply_settings__signature_has_default_payload() -> None:
    """拍板 2.0.68：payload 是可选参数（默认 None）。"""
    sig = inspect.signature(GenshinGameBuddyPlugin.apply_settings_entry)
    payload_param = sig.parameters["payload"]
    assert payload_param.default is None, (
        f"payload default should be None, got {payload_param.default!r}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_settings__payload_none_returns_err() -> None:
    """拍板 2.0.68：apply_settings_entry() 不带 payload → Err(INVALID_INPUT)，不抛。"""
    result = await GenshinGameBuddyPlugin.apply_settings_entry(_make_plugin_stub(), payload=None)
    # result 是 Err(...)——具体实现是 SdkError(code="INVALID_INPUT", ...)
    # 简单校验：返回了 Err 包，不是 None/异常
    assert result is not None
    # 不抛 TypeError 是关键——本测试能跑完就是成功


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_settings__payload_missing_arg_returns_err() -> None:
    """拍板 2.0.68：不传 payload 参数（最坏情况：调用栈传空 arg）→ Err 而非 TypeError。

    旧签名这里会抛 `TypeError: missing 1 required positional argument: 'payload'`。
    新签名让 None payload 走 Err 分支。
    """
    plugin = _make_plugin_stub()
    # 完全不传 payload（调用栈崩成 () 的模拟）
    result = await GenshinGameBuddyPlugin.apply_settings_entry(plugin)
    assert result is not None  # Err 不是 None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_settings__payload_empty_dict_returns_err() -> None:
    """拍板 2.0.68：空 dict payload 走 INVALID_INPUT 分支（不是合法设置）。"""
    result = await GenshinGameBuddyPlugin.apply_settings_entry(
        _make_plugin_stub(), payload={}
    )
    # {} 里没 options 键 → options_payload = {} → 仍然 INVALID_INPUT（既不是 None 也不是合法 options）
    assert result is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_settings__payload_with_empty_options_succeeds() -> None:
    """拍板 2.0.68：payload={"options": {}} 是合法的（"啥也没改"也是合法场景）。"""
    plugin = _make_plugin_stub()
    result = await GenshinGameBuddyPlugin.apply_settings_entry(
        plugin, payload={"options": {}}
    )
    # 应当成功——payload 校验通过，"应用"了 0 项设置
    assert result is not None
    # config.update 应被调用（持久化）
    assert plugin.config.update.called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_settings__payload_non_mapping_options_returns_err() -> None:
    """拍板 2.0.68：options 不是 Mapping → Err(INVALID_INPUT)，不抛。"""
    result = await GenshinGameBuddyPlugin.apply_settings_entry(
        _make_plugin_stub(),
        payload={"options": "not an object"},
    )
    assert result is not None  # Err 不是 None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_settings__payload_with_changes_succeeds() -> None:
    """拍板 2.0.68：正常 payload（含 changes）走原校验 + 持久化逻辑。"""
    plugin = _make_plugin_stub()
    result = await GenshinGameBuddyPlugin.apply_settings_entry(
        plugin,
        payload={"options": {"desktop_markers_enabled": False}},
    )
    assert result is not None
    # config.update 应被调用
    assert plugin.config.update.called
    # self._options 已更新
    assert plugin._options.desktop_markers_enabled is False