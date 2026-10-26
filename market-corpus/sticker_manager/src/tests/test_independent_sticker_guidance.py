# pyright: reportMissingImports=false
"""Model-visible contracts, not a simulation of whether an LLM calls tools."""

from __future__ import annotations

import inspect
from copy import deepcopy

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.catalog import SEND_TEXT_MAX_CHARS
from sticker_manager.core.configuration import EAGERNESS_LEVELS, StickerManagerSettings
from sticker_manager.core.eagerness import (
    EAGER_EXTRA_NOTE,
    REPLY_FIRST_NOTE,
    TOOL_NOTE,
    WILL,
    effective_inject_interval_n,
    injection_guidance,
    injection_pointer,
)
from sticker_manager.core.tool_surface import build_send_tool_description
from sticker_manager.services.sender import SendResult

from .test_natural_baseline import _baseline, _natural

_GROUP = "smile"
_INDEPENDENT_GUIDANCE = (
    "正常文字回复照常说，text 留空即可，图片独立发送"
)
_TIER_NOTES = {
    "reserved": (
        " 矜持档：有贴切图片的轻松互动优先配一张，不用等主人开口，文字说清也别省掉；"
        "按回复意图读候选再选 ID，无贴切图片、信息问答或正经办事纯文字，单回复最多一张。"
    ),
    "natural": _natural(_baseline()["tool_note"]),
    "eager": _baseline()["tool_note"],
}

def _metadata(handler, schema_key):
    (meta,) = [
        record["kwargs"] for record in handler.__neko_stub_meta__
        if schema_key in record["kwargs"]
    ]
    return meta


def _setup(tmp_path, *, tier="natural"):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"enabled": True, "send": {"eagerness": tier}}}
    )
    plugin._library.load(force=True)
    assert plugin._library.create_group(_GROUP, "happy reaction", "")[1] == ""
    sticker, error = plugin._library.add(data=PNG_BYTES, desc="smile", tags=[], group=_GROUP)
    assert not error
    return plugin, host, sticker


def _invoke(plugin, handler_name, **kwargs):
    if "selected_id" in kwargs:
        kwargs["sticker_id" if handler_name == "tool_sticker_send" else "id"] = kwargs.pop("selected_id")
    return getattr(plugin, handler_name)(group=_GROUP, _ctx={"lanlan_name": "K"}, **kwargs)


def _unwrap(result, handler_name):
    if handler_name == "send_entry":
        return result.value if result.is_ok() else result.error.details
    return result


def test_main_tool_schema_explains_candidates_and_exact_id_selection(tmp_path):
    plugin, _host, _sticker = _setup(tmp_path)
    schema = _metadata(plugin.tool_sticker_send, "parameters")["parameters"]
    properties = schema["properties"]
    assert next(iter(properties)) == "group"
    assert "返回逐图候选" in properties["group"]["description"]
    assert "具体图片 ID 才会发送" in properties["sticker_id"]["description"]
    assert "id" not in properties and schema["required"] == []
    description = build_send_tool_description(plugin._send_tool_catalog(), "eager")
    assert "sticker_send(sticker_id=候选ID)" in description
    assert "先调用 sticker_list" in description
    assert "只查候选，不发送" in description


@pytest.mark.parametrize("handler_name,schema_key", [
    ("tool_sticker_send", "parameters"), ("send_entry", "input_schema"),
])
def test_both_declared_schemas_allow_group_only_and_explain_independent_reply(
    tmp_path, handler_name, schema_key,
):
    plugin, _host, _sticker = _setup(tmp_path)
    handler = getattr(plugin, handler_name)
    schema = _metadata(handler, schema_key)[schema_key]
    assert schema["type"] == "object"
    assert schema["properties"]["group"]["type"] == "string"
    assert schema.get("required", []) == []
    text = schema["properties"]["text"]
    assert text["type"] == "string"
    if schema_key == "parameters":
        assert "普通回复照常说，此项留空只发图" in text["description"]
        assert "先单独发配文，再单独发图片" in text["description"]
    else:
        assert "text 无需填写" in text["description"]
        assert "只有额外配文才填 text" in text["description"]
    assert "配文不要在正文重复" in text["description"]
    if schema_key == "input_schema":
        assert text["maxLength"] == SEND_TEXT_MAX_CHARS == 500
    else:
        assert "≤500 字" in text["description"]
    assert inspect.signature(handler).parameters["text"].default == ""


@pytest.mark.parametrize("tier", EAGERNESS_LEVELS)
def test_dynamic_registration_keeps_real_schema_and_positive_group_only_guidance(
    tmp_path, monkeypatch, tier,
):
    plugin, _host, _sticker = _setup(tmp_path, tier=tier)
    declared = _metadata(plugin.tool_sticker_send, "parameters")
    tools = {
        "sticker_send": {
            "name": "sticker_send",
            "description": declared["description"],
            "parameters": deepcopy(declared["parameters"]),
            "timeout_seconds": declared["timeout"],
            "role": None,
        }
    }
    registered = []

    def register(**kwargs):
        registered.append(kwargs)
        tools[kwargs["name"]] = {
            "name": kwargs["name"],
            "description": kwargs["description"],
            "parameters": kwargs["parameters"],
            "timeout_seconds": kwargs["timeout"],
            "role": kwargs["role"],
        }
        return True

    monkeypatch.setattr(plugin, "list_llm_tools", lambda: list(tools.values()), raising=False)
    monkeypatch.setattr(
        plugin, "unregister_llm_tool", lambda name: tools.pop(name, None) is not None,
        raising=False,
    )
    monkeypatch.setattr(plugin, "register_llm_tool", register, raising=False)
    assert plugin._apply_send_tool_surface() is True
    (applied,) = registered
    assert applied["handler"] == plugin.tool_sticker_send
    assert applied["parameters"] == declared["parameters"]
    assert applied["parameters"]["required"] == []
    description = applied["description"]
    assert description == build_send_tool_description(plugin._send_tool_catalog(), tier)
    assert _INDEPENDENT_GUIDANCE in description
    assert "配文与图片分条发送" in description
    assert "只有额外配文才填 text" in description
    assert "配文不要在正文重复" in description
    assert _GROUP in description and description.endswith(_TIER_NOTES[tier])
    if tier == "eager":
        assert description.startswith(REPLY_FIRST_NOTE)
        assert "直接选 ID 配图" in description
        assert "不用等用户点名要图，也别因文字已经说清楚就省掉" in description
        assert EAGER_EXTRA_NOTE in description
        assert "分清安慰对方和说自己" in description
        assert "用户要求纯文字、无贴切图片或严肃任务就纯文字，单回复最多一张" in description


@pytest.mark.parametrize("tier", EAGERNESS_LEVELS)
def test_injection_pointer_teaches_candidate_then_id_and_independent_image(tier):
    pointer = injection_pointer(tier)
    assert pointer.startswith(REPLY_FIRST_NOTE)
    assert "图片独立发送" in pointer
    assert "sticker_list" in pointer and "sticker_send(sticker_id=候选ID)" in pointer
    assert "text 留空即可" in pointer
    assert "没有合适的就纯文字" in pointer


@pytest.mark.parametrize("tier,interval", [
    ("reserved", 3), ("natural", 1), ("eager", 1),
])
def test_clarification_does_not_change_tier_permissions_cadence_or_send_gates(tier, interval):
    assert TOOL_NOTE[tier] == _TIER_NOTES[tier]
    assert injection_guidance(tier) == WILL[tier]
    for banned in ("别重试", "概率", "节奏闸", "最近不重复"):
        assert banned not in injection_guidance(tier)
    assert effective_inject_interval_n(tier, 0) == interval
    send = StickerManagerSettings.from_config(
        {"sticker_manager": {"send": {"eagerness": tier}}}
    ).send
    assert send.eagerness == tier
    assert (
        send.cooldown_sec, send.recent_dedup_count, send.probability,
        send.probability_reuse_sec, send.reply_tail_display_buffer_sec,
    ) == (20.0, 5, 1.0, 60.0, 2.0)


@pytest.mark.parametrize("handler_name", ["tool_sticker_send", "send_entry"])
@pytest.mark.parametrize("length,expected_pushes", [(0, 1), (500, 2), (501, 0)])
def test_selected_id_send_and_optional_text_retain_runtime_limits(
    tmp_path, run_async, handler_name, length, expected_pushes,
):
    plugin, host, sticker = _setup(tmp_path)
    kwargs = {} if length == 0 else {"text": "x" * length}
    result = _unwrap(run_async(_invoke(plugin, handler_name, selected_id=sticker.id, **kwargs)), handler_name)
    assert len(host.push.calls) == expected_pushes
    if length > 500:
        assert result["ok"] is False and result["reason"] == "text_too_long"
        assert plugin._runstats.sent == 0
        return
    assert result["ok"] is True and result["sent"] == sticker.id
    if length:
        assert host.push.calls[0]["parts"] == [{"type": "text", "text": "x" * length}]
    assert host.push.calls[-1]["parts"] == [
        {"type": "image", "data": PNG_BYTES, "mime": "image/png"},
    ]
    assert plugin._runstats.sent == 1


@pytest.mark.parametrize("handler_name", ["tool_sticker_send", "send_entry"])
def test_queued_selected_image_receipt_is_not_an_actual_send(
    tmp_path, monkeypatch, run_async, handler_name,
):
    plugin, host, sticker = _setup(tmp_path)

    async def queue(selected, **kwargs):
        assert selected.id == sticker.id and kwargs["text"] == ""
        assert kwargs["source"] == ("tool" if handler_name == "tool_sticker_send" else "agent")
        return SendResult(ok=True, sticker_id=selected.id, desc=selected.desc, queued=True)

    monkeypatch.setattr(plugin._sender, "send", queue)
    result = _unwrap(run_async(_invoke(plugin, handler_name, selected_id=sticker.id)), handler_name)
    assert result["ok"] is True and result["queued"] == sticker.id and result["sent"] == ""
    assert "已排队" in result["note"] and "回复与显示缓冲结束后" in result["note"]
    assert "表情包已提交到聊天" not in result["note"] and "配文已提交" not in result["note"]
    assert not host.push.calls and not plugin._library.read_usage()
    assert plugin._runstats.tool_calls == 1 and plugin._runstats.sent == 0
