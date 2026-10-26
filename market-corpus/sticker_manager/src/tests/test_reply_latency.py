"""Speech pacing and candidate lookup must not wait on avoidable role probes."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.core.eagerness import REPLY_FIRST_NOTE, injection_pointer
from sticker_manager.core.tool_surface import build_list_tool_description, build_send_tool_description

from .test_host_reply_tail import CONTEXT


def _surface(plugin, *, modern=True, reject_context=False):
    metas = {
        "sticker_send": {
            "name": "sticker_send", "description": "send", "parameters": {}, "role": None,
        },
        "sticker_list": {
            "name": "sticker_list", "description": "lookup", "parameters": {"required": []},
            "role": "K", "timeout_seconds": 10.0,
        },
    }
    calls = []
    plugin.list_llm_tools = lambda: list(metas.values())
    plugin.unregister_llm_tool = lambda name: metas.pop(name, None) is not None

    def register(*, name, description, parameters, handler, timeout, role, reply_tail=False):
        calls.append((name, reply_tail))
        if reject_context and name == "sticker_list" and reply_tail:
            return False
        metas[name] = {
            "name": name, "description": description, "parameters": parameters,
            "handler": handler, "timeout_seconds": timeout, "role": role, "reply_tail": reply_tail,
        }
        return True

    def old_register(*, name, description, parameters, handler, timeout, role):
        return register(
            name=name, description=description, parameters=parameters,
            handler=handler, timeout=timeout, role=role,
        )

    plugin.register_llm_tool = register if modern else old_register
    return metas, calls


def test_both_tools_opt_into_original_role_context_once(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    metas, calls = _surface(plugin)
    before = deepcopy(metas["sticker_list"])
    assert plugin._apply_send_tool_surface()
    assert calls == [("sticker_list", True), ("sticker_send", True)]
    lookup = metas["sticker_list"]
    assert lookup["handler"] == plugin.tool_sticker_list
    assert lookup["description"] == build_list_tool_description("natural")
    for name in ("parameters", "role", "timeout_seconds"):
        assert lookup[name] == before[name]
    assert plugin._apply_send_tool_surface()
    assert len(calls) == 2


def test_unsupported_sdk_keeps_lookup_registered_without_new_flag(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    metas, calls = _surface(plugin, modern=False)
    before = deepcopy(metas["sticker_list"])
    assert plugin._apply_send_tool_surface()
    assert calls == [("sticker_list", False), ("sticker_send", False)]
    assert metas["sticker_list"]["description"] == build_list_tool_description("natural")
    for name in ("parameters", "role", "timeout_seconds"):
        assert metas["sticker_list"][name] == before[name]


def test_failed_context_upgrade_restores_lookup_and_keeps_retry_flag(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    metas, calls = _surface(plugin, reject_context=True)
    assert not plugin._refresh_send_tool_surface()
    assert calls == [("sticker_list", True), ("sticker_list", False)]
    assert metas["sticker_list"]["handler"] == plugin.tool_sticker_list
    assert metas["sticker_list"]["description"] == "lookup"
    assert metas["sticker_list"]["reply_tail"] is False
    assert "sticker_send" in metas and plugin._surface_dirty


@pytest.mark.parametrize("modern", [True, False])
def test_switching_tiers_updates_both_tools_once_without_changing_routing(tmp_path, modern):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    metas, calls = _surface(plugin, modern=modern)
    before = deepcopy(metas["sticker_list"])
    for tier in ("natural", "eager", "reserved"):
        plugin._settings = StickerManagerSettings.from_config(
            {"sticker_manager": {"enabled": True, "send": {"eagerness": tier}}}
        )
        assert plugin._refresh_send_tool_surface()
        assert metas["sticker_list"]["description"] == build_list_tool_description(tier)
        assert metas["sticker_send"]["description"] == build_send_tool_description("", tier)
        for name in ("parameters", "role", "timeout_seconds"):
            assert metas["sticker_list"][name] == before[name]
        assert metas["sticker_list"]["reply_tail"] is modern
        assert metas["sticker_send"]["reply_tail"] is modern
        applied = len(calls)
        assert plugin._refresh_send_tool_surface()
        assert len(calls) == applied
    assert len(calls) == 6
    assert not host.push.calls


def test_rejected_tier_update_restores_previous_lookup_description_and_context(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    metas, _ = _surface(plugin)
    assert plugin._refresh_send_tool_surface()
    before = dict(metas["sticker_list"])
    original_register = plugin.register_llm_tool
    plugin._settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"send": {"eagerness": "eager"}}}
    )

    def reject_eager(*, name, description, parameters, handler, timeout, role, reply_tail=False):
        if name == "sticker_list" and description == build_list_tool_description("eager"):
            return False
        return original_register(
            name=name, description=description, parameters=parameters,
            handler=handler, timeout=timeout, role=role, reply_tail=reply_tail,
        )

    plugin.register_llm_tool = reject_eager
    assert not plugin._refresh_send_tool_surface()
    assert metas["sticker_list"] == before
    assert plugin._surface_dirty
    plugin.register_llm_tool = original_register
    assert plugin._refresh_send_tool_surface(force=False)
    assert metas["sticker_list"]["description"] == build_list_tool_description("eager")
    assert not plugin._surface_dirty


def test_original_role_lookup_avoids_bus_and_current_role_probes(tmp_path, run_async):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(enabled=True)
    plugin._library.load()
    first, error = plugin._library.add(data=PNG_BYTES, desc="first", tags=[], group="happy")
    assert not error
    second, error = plugin._library.add(data=PNG_BYTES + b"second", desc="second", tags=[], group="happy")
    assert not error
    plugin._library.append_usage(
        {"id": first.id, "lanlan": "K", "ok": True, "at": 1.0}, keep=200,
    )
    plugin._lanlan._from_bus = AsyncMock(side_effect=AssertionError("unnecessary bus read"))
    plugin._lanlan._from_host = AsyncMock(side_effect=AssertionError("unnecessary role HTTP"))
    result = run_async(plugin.tool_sticker_list(
        group="happy", _ctx={"host_reply": CONTEXT, "lanlan_name": "other-role"},
    ))
    assert result["candidate_ids"] == [second.id]
    plugin._lanlan._from_bus.assert_not_awaited()
    plugin._lanlan._from_host.assert_not_awaited()
    assert not host.push.calls


def test_legacy_lookup_still_resolves_role_with_existing_fallback(tmp_path, run_async):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(enabled=True)
    plugin._library.load()
    plugin._lanlan._from_bus = AsyncMock(return_value="K")
    plugin._lanlan._from_host = AsyncMock(side_effect=AssertionError("unnecessary role HTTP"))
    run_async(plugin.tool_sticker_list())
    plugin._lanlan._from_bus.assert_awaited_once()
    plugin._lanlan._from_host.assert_not_awaited()


def test_speech_precedes_lookup_on_both_tools_and_every_tier(tmp_path):
    plugin, _ = build_plugin(FakeHostContext(data_root=tmp_path))
    lookup = next(
        item["kwargs"] for item in plugin.tool_sticker_list.__neko_stub_meta__
        if "parameters" in item["kwargs"]
    )
    surfaces = [lookup["description"]]
    for tier in ("reserved", "natural", "eager"):
        surfaces.extend((
            build_send_tool_description("happy", tier),
            build_list_tool_description(tier),
            injection_pointer(tier),
        ))
    for surface in surfaces:
        assert surface.startswith(REPLY_FIRST_NOTE)
        assert surface.index("说完正文") < surface.index("再选 ID 配图")
        assert "只发图可直接选图" in surface
        assert "开口前" not in surface and "开口之前" not in surface
        assert "正文只说一次" in surface and "若已说完就结束" in surface
