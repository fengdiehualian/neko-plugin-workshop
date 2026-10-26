# pyright: reportMissingImports=false
"""v0.15.0：积极度写进 `sticker_send` 的**工具描述**（常驻面）。

来由是实机日志：6 次注入只换来 2 次发图，且两次都紧跟在注入后一分钟内，
三道闸一次都没拦——问题不是被拦，是提醒沉底了。工具列表每轮都在场，
所以档位必须同时写到那里。

钉四条：三档描述互不相同且保留静态正文；eager 不再自带"干脆不发"的退路；
换档走 unregister→register、同描述不折腾 IPC；**重注册失败要把旧描述装回去**
（宁可她读到旧档位的句子，也不能因为换档没了工具）。
"""

from __future__ import annotations

from conftest import FakeConfig, FakeHostContext, build_plugin
from sticker_manager.core.configuration import EAGERNESS_LEVELS, StickerManagerSettings
from sticker_manager.core.eagerness import send_tool_description
from sticker_manager.core.tool_surface import build_send_tool_description


def _plugin(tmp_path, *, send: dict | None = None):
    host = FakeHostContext(
        data_root=tmp_path,
        config=FakeConfig(data={"sticker_manager": {"enabled": True, "send": send or {}}}),
    )
    plugin, host = build_plugin(host)
    plugin._settings = StickerManagerSettings.from_config(host.config.data)
    return plugin, host


class TestToolDescriptionTier:
    def test_note_differs_by_tier_and_keeps_the_static_body(self):
        base = "发一张表情包到聊天里。"
        texts = {tier: send_tool_description(base, tier) for tier in EAGERNESS_LEVELS}
        assert len(set(texts.values())) == 3
        for text in texts.values():
            assert text.startswith(base)
        assert "不用等主人开口" in texts["eager"]

    def test_eager_leaves_no_self_granted_exit(self):
        # v0.14.0 的 eager 结尾写着"或干脆不发"，等于自带退路——实机之后拆掉的。
        for tier in EAGERNESS_LEVELS:
            assert "干脆不发" not in send_tool_description("x", tier)

    def test_unlisted_tier_degrades_to_default_note(self):
        assert send_tool_description("base", "wild") == send_tool_description("base", "natural")


class _Registry:
    """假 SDK 注册面：记录 unregister/register 序列，可注入失败。"""

    def __init__(self, tools: dict, *, fail_registers: int = 0):
        self.tools = dict(tools)
        self.calls: list[tuple[str, str]] = []
        self._fail_left = fail_registers

    def list_llm_tools(self):
        return [dict(meta) for meta in self.tools.values()]

    def unregister_llm_tool(self, name: str):
        self.calls.append(("unregister", name))
        return self.tools.pop(name, None) is not None

    def register_llm_tool(self, **kwargs):
        self.calls.append(("register", kwargs["description"]))
        if self._fail_left:
            self._fail_left -= 1
            raise RuntimeError("ipc down")
        self.tools[kwargs["name"]] = {
            "name": kwargs["name"],
            "description": kwargs["description"],
            "parameters": kwargs["parameters"],
            "timeout_seconds": kwargs["timeout"],
            "role": kwargs["role"],
        }
        return True


def _attach(plugin, registry: _Registry) -> None:
    for name in ("list_llm_tools", "unregister_llm_tool", "register_llm_tool"):
        setattr(plugin, name, getattr(registry, name))


def _meta(description: str) -> dict:
    return {
        "name": "sticker_send",
        "description": description,
        "parameters": {"type": "object"},
        "timeout_seconds": 20.0,
        "role": None,
    }


class TestApplyTierToTool:
    def test_tier_swap_rewrites_the_description(self, tmp_path):
        plugin, _host = _plugin(tmp_path, send={"eagerness": "eager"})
        registry = _Registry({"sticker_send": _meta("旧描述")})
        _attach(plugin, registry)
        assert plugin._apply_send_tool_surface() is True
        applied = registry.tools["sticker_send"]["description"]
        assert applied != "旧描述" and "爱发档" in applied
        assert ("unregister", "sticker_send") in registry.calls

    def test_same_description_skips_the_ipc_round_trip(self, tmp_path):
        plugin, _host = _plugin(tmp_path, send={"eagerness": "eager"})
        same = build_send_tool_description(plugin._send_tool_catalog(), "eager")
        registry = _Registry({"sticker_send": _meta(same)})
        _attach(plugin, registry)
        assert plugin._apply_send_tool_surface() is True
        assert registry.calls == []

    def test_failure_rolls_the_old_description_back(self, tmp_path):
        plugin, _host = _plugin(tmp_path, send={"eagerness": "eager"})
        registry = _Registry({"sticker_send": _meta("旧描述")}, fail_registers=1)
        _attach(plugin, registry)
        assert plugin._apply_send_tool_surface() is False
        assert ("register", "旧描述") in registry.calls
        assert registry.tools["sticker_send"]["description"] == "旧描述"

    def test_double_failure_leaves_a_log_not_a_crash(self, tmp_path):
        # 连回滚都失败：不抬异常（重挂不该把启动或入口调用炸掉），工具确实没了。
        plugin, _host = _plugin(tmp_path, send={"eagerness": "eager"})
        registry = _Registry({"sticker_send": _meta("旧描述")}, fail_registers=2)
        _attach(plugin, registry)
        assert plugin._apply_send_tool_surface() is False
        assert "sticker_send" not in registry.tools

    def test_missing_sdk_face_is_a_no_op(self, tmp_path):
        plugin, _host = _plugin(tmp_path)
        assert plugin._apply_send_tool_surface() is False  # 桩基类没有注册面 → 只记日志

    def test_config_change_and_panel_switch_both_reapply(self, tmp_path, run_async):
        plugin, host = _plugin(tmp_path, send={"eagerness": "reserved"})
        applied: list[str] = []
        plugin._apply_send_tool_surface = lambda: applied.append(plugin._settings.send.eagerness) or True
        run_async(plugin.on_config_change())
        run_async(plugin.set_eagerness_entry(eagerness="eager"))
        assert applied == ["reserved", "eager"], "改配置与面板切档都要重挂描述"
        assert host.config.writes == [("sticker_manager.send.eagerness", "eager")]
