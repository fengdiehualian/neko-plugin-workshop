"""Conversation cues are reminders, not automatic sends or frequency guarantees."""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import (
    PNG_BYTES,
    FakeBus,
    FakeHostContext,
    build_plugin,
    conversation_record,
    user_message_record,
)
from sticker_manager.core.awareness import build_awareness_text, emotional_signal
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import (
    EAGERNESS_LEVELS,
    AwarenessSettings,
    SendSettings,
    StickerManagerSettings,
)
from sticker_manager.core.tool_surface import build_send_tool_description


@pytest.mark.parametrize(
    "text",
    [
        "摸摸",
        "摸摸头",
        "摸头",
        "不摸了",
        "别摸了",
        "贴贴",
        "蹭蹭",
        "亲亲",
        "撒娇",
        "给你摸摸头",
        "可以摸摸头吗",
        "  摸摸  ",
        "\t贴贴\n",
        "摸摸。",
        "蹭蹭～",
        "亲亲？",
        "可以摸摸头吗？",
        "（摸摸头）",
        "今天好开心",
        "谢谢你",
        "无语",
        "哈哈哈哈",
    ],
)
def test_light_conversation_and_existing_emotions_are_signals(text):
    assert emotional_signal(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "摸鱼",
        "摸索",
        "请把摸摸替换成问候",
        "帮我查摸摸鱼游戏",
        "抚摸怎么翻译",
        "你摸摸看这个函数",
        "现在几点",
        "帮我算一下128乘7",
        "python的with怎么用？",
        "把报告第三节的日期改成5月",
        "",
        "   ",
    ],
)
def test_light_conversation_match_does_not_expand_to_business_substrings(text):
    assert emotional_signal(text) is False


def _settings(*, floor=0.0, event_gated=True):
    return StickerManagerSettings(
        enabled=True,
        send=SendSettings(eagerness="eager"),
        awareness=AwarenessSettings(
            enabled=True,
            inject_mode="interval_n",
            inject_interval_n=3,
            min_interval_sec=floor,
            event_gated=event_gated,
        ),
    )


def _plugin(tmp_path, records):
    host = FakeHostContext(
        data_root=tmp_path,
        bus=FakeBus(
            [conversation_record("conversation", 1.0, "K")],
            memory_records=records,
        ),
    )
    plugin, host = build_plugin(host)
    sticker, error = plugin._library.add(
        data=PNG_BYTES, desc="猫猫轻松回应", tags=[], group="轻松互动"
    )
    assert not error and sticker is not None
    return plugin, host, sticker


def test_single_interaction_gets_signal_cue_before_three_turn_threshold(tmp_path, run_async):
    plugin, host, _ = _plugin(tmp_path, [user_message_record(100.0, "摸摸", "K")])
    settings = _settings()

    result = run_async(plugin._awareness.maybe_run(settings=settings, now=100.0))

    assert result["status"] == "injected" and result["trigger"] == "signal"
    assert plugin._awareness.signal_hits == 1
    assert plugin._awareness.trigger_counts == {"signal": 1}
    assert plugin._awareness._turns.turns_since("K") == 0
    assert len(host.push.calls) == 1
    call = host.push.calls[0]
    assert call["visibility"] == []
    assert call["ai_behavior"] == "read"
    assert call["target_lanlan"] == "K"
    assert call["coalesce_key"] == "sticker_manager:awareness:K"
    assert call["description"] == "sticker_manager:awareness"
    assert [part["type"] for part in call["parts"]] == ["text"]
    assert "sticker_send" in call["parts"][0]["text"]
    assert not plugin._library.read_usage()


def test_interaction_cue_still_obeys_sixty_second_floor(tmp_path, run_async):
    plugin, host, _ = _plugin(tmp_path, [user_message_record(100.0, "摸摸", "K")])
    settings = _settings(floor=60.0)
    plugin._awareness._last_injected["K"] = 90.0

    result = run_async(plugin._awareness.maybe_run(settings=settings, now=100.0))

    assert result["status"] == "not_due"
    assert plugin._awareness.signal_hits == 1
    assert plugin._awareness._turns.turns_since("K") == 1
    assert plugin._awareness._last_injected["K"] == 90.0
    assert host.push.calls == []


def test_disabled_event_gate_keeps_single_interaction_below_threshold(tmp_path, run_async):
    plugin, host, _ = _plugin(tmp_path, [user_message_record(100.0, "摸摸", "K")])

    result = run_async(
        plugin._awareness.maybe_run(settings=_settings(event_gated=False), now=100.0)
    )

    assert result["status"] == "not_due"
    assert plugin._awareness.signal_hits == 0
    assert plugin._awareness._turns.turns_since("K") == 1
    assert host.push.calls == []


def test_old_interaction_in_batch_does_not_cue_latest_business_message(tmp_path, run_async):
    plugin, host, _ = _plugin(
        tmp_path,
        [
            user_message_record(100.0, "摸摸", "K"),
            user_message_record(101.0, "现在几点", "K"),
        ],
    )

    result = run_async(plugin._awareness.maybe_run(settings=_settings(), now=102.0))

    assert result["status"] == "not_due"
    assert plugin._awareness.signal_hits == 1
    assert plugin._awareness.turns_seen == 2
    assert plugin._awareness._turns.turns_since("K") == 2
    assert host.push.calls == []


@pytest.mark.parametrize("tier", EAGERNESS_LEVELS)
@pytest.mark.parametrize("event", [False, True])
def test_cues_offer_candidate_selection_without_premature_gate_warnings(tier, event):
    cue = build_awareness_text(
        [Sticker(id="one", file="one.png", desc="轻松回应", tags=[], group="轻松互动")],
        eagerness=tier,
        event=event,
    )
    assert "sticker_list" in cue and "sticker_send(sticker_id=候选ID)" in cue
    assert "text" in cue and any(word in cue for word in ("无需", "可选", "留空"))
    assert "文字" in cue
    for gate_instruction in ("冷却", "概率", "最近不重复", "节奏闸", "换一张就好", "别重试"):
        assert gate_instruction not in cue


@pytest.mark.parametrize("tier", EAGERNESS_LEVELS)
def test_tool_description_keeps_group_path_and_text_only_exit(tier):
    description = build_send_tool_description("・轻松互动（1 张）—轻松接话", tier)
    assert "sticker_send" in description and "group" in description
    assert "轻松互动" in description
    assert "文字" in description
    assert "你必须配图" not in description and "一定要发" not in description


@pytest.mark.parametrize("source", ["tool", "agent"])
def test_selected_images_retain_source_and_transport(tmp_path, run_async, source):
    plugin, host, sticker = _plugin(tmp_path, [])
    plugin._settings = _settings()

    if source == "tool":
        result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
        assert result["ok"] is True and result["sent"] == sticker.id
    else:
        result = run_async(plugin.send_entry(id=sticker.id, group="轻松互动", _ctx={"lanlan_name": "K"}))
        assert result.is_ok() and result.value["id"] == sticker.id

    assert len(host.push.calls) == 1
    assert [part["type"] for part in host.push.calls[0]["parts"]] == ["image"]
    assert host.push.calls[0]["visibility"] == ["chat"]
    assert host.push.calls[0]["ai_behavior"] == ("blind" if source == "tool" else "read")
    assert plugin._library.read_usage()[-1]["source"] == source
    assert plugin._runstats.sticker_send_calls == int(source == "tool")
    assert plugin._runstats.agent_send_calls == int(source == "agent")


@pytest.mark.parametrize("source", ["tool", "agent"])
def test_more_sensitive_reminders_do_not_bypass_probability_gate(tmp_path, run_async, source):
    plugin, host, sticker = _plugin(tmp_path, [])
    settings = _settings()
    plugin._settings = replace(settings, send=replace(settings.send, probability=0.0))

    if source == "tool":
        result = run_async(plugin.tool_sticker_send(sticker_id=sticker.id))
        assert result["ok"] is False and result["reason"] == "probability_declined"
    else:
        result = run_async(plugin.send_entry(id=sticker.id, group="轻松互动"))
        assert not result.is_ok() and str(result.error) == "probability_declined"

    assert host.push.calls == []
