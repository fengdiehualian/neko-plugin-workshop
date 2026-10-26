# pyright: reportMissingImports=false
"""Tier willingness and cadence survive private image selection guidance."""

from __future__ import annotations

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin, user_message_record
from sticker_manager.core.awareness import build_awareness_text
from sticker_manager.core.catalog import Sticker
from sticker_manager.core.configuration import StickerManagerSettings
from sticker_manager.core.eagerness import (
    DECISION_CHECKPOINT,
    EAGER_EXTRA_NOTE,
    EAGER_FREQUENCY_NOTE,
    INTERNAL_SELECTION_NOTE,
    NEXT_STEP_NOTE,
    REPLY_FIRST_NOTE,
    TOOL_NOTE,
    TRIGGER_CRITERIA,
    WILL,
    decision_checkpoint,
    injection_guidance,
    injection_pointer,
    tool_criteria,
)
from sticker_manager.core.tool_surface import build_list_tool_description, build_send_tool_description
from sticker_manager.services.awareness import Awareness

from .test_natural_baseline import _baseline, _natural

_SHARED_POINTER = (
    f"{REPLY_FIRST_NOTE}"
    "读快捷候选逐图说明，贴切就用 sticker_send(sticker_id=候选ID)；需更多再用 sticker_list，"
    "text 留空即可，图片独立发送；没有合适的就纯文字。"
)


def _stickers(count=1):
    return [Sticker(id=f"s{i}", file=f"{i}.png", desc="smile") for i in range(count)]


@pytest.mark.parametrize("tier", ["natural"])
def test_natural_willingness_is_preserved_while_speech_moves_first(tier):
    expected = _baseline()
    assert WILL[tier] == injection_guidance(tier) == _natural(expected["will"])
    assert DECISION_CHECKPOINT[tier] == decision_checkpoint(tier)
    assert decision_checkpoint(tier).startswith(REPLY_FIRST_NOTE)
    assert TRIGGER_CRITERIA[tier] == tool_criteria(tier) == _natural(expected["criteria"])
    assert TOOL_NOTE[tier] == _natural(expected["tool_note"])


@pytest.mark.parametrize("tier", ["reserved", "natural", "eager"])
def test_shared_pointer_adds_privacy_and_receipt_finishes_current_selection(tier):
    assert injection_pointer(tier) == _SHARED_POINTER + INTERNAL_SELECTION_NOTE
    assert "本轮选图已完成" in NEXT_STEP_NOTE
    assert "若已说完就结束" in NEXT_STEP_NOTE
    assert "不复述原话" in NEXT_STEP_NOTE
    assert "后续想配图" not in NEXT_STEP_NOTE


@pytest.mark.parametrize("junk", ["wild", None, ["eager"]])
def test_unknown_tiers_keep_natural_text_on_actual_surfaces(junk):
    expected = _baseline()
    assert injection_guidance(junk) == _natural(expected["will"])
    assert decision_checkpoint(junk) == decision_checkpoint("natural")
    assert tool_criteria(junk) == _natural(expected["criteria"])
    catalog = "・smile（1 张） — happy reaction"
    assert build_send_tool_description(catalog, junk) == build_send_tool_description(catalog, "natural")
    assert build_awareness_text(_stickers(), eagerness=junk) == build_awareness_text(
        _stickers(), eagerness="natural",
    )


def test_eager_tool_surface_prioritizes_fitting_image_for_ordinary_chat():
    description = build_send_tool_description("・smile（1 张） — happy reaction", "eager")
    assert description.startswith(REPLY_FIRST_NOTE)
    assert "按本轮表达的态度判断哪张贴切，直接选 ID 配图" in description
    for scene in ("轻松接话", "开心庆祝", "撒娇贴贴", "关心安慰", "玩笑吐槽", "犯困", "道晚安"):
        assert scene in description
    assert "需要更多候选时先调用 sticker_list" in description
    assert "不用等用户点名要图" in description
    assert "也别因文字已经说清楚就省掉" in description
    assert EAGER_EXTRA_NOTE in description
    assert "按自己回复的态度读逐图说明" in description
    assert "分清安慰对方和说自己" in description
    assert "用户要求纯文字、无贴切图片或严肃任务就纯文字" in description
    assert "日常问答、询问喜好和关心近况也算聊天" in description
    assert "单回复最多一张" in description
    assert "正常文字回复照常说，text 留空即可，图片独立发送" in description
    assert "先调用 sticker_list(query=正向意图短词)" in description
    assert "sticker_list(group=分类名)" in description
    assert "sticker_send(sticker_id=候选ID)" in description


def test_eager_frequency_is_a_preference_with_a_text_only_exit():
    surfaces = (
        build_list_tool_description("eager"),
        build_send_tool_description("・smile（1 张） — happy reaction", "eager"),
        build_awareness_text(_stickers(), eagerness="eager"),
    )
    for text in surfaces:
        assert EAGER_FREQUENCY_NOTE in text
        assert "轻松聊天默认配一张贴切图" in text
        assert EAGER_EXTRA_NOTE in text
        assert "用户要求纯文字、无贴切图片或严肃任务就纯文字" in text
        assert text.index("说完正文") < text.index(EAGER_FREQUENCY_NOTE)
        assert "正文只说一次" in text
    for tier in ("reserved", "natural"):
        assert EAGER_FREQUENCY_NOTE not in build_list_tool_description(tier)
        assert EAGER_FREQUENCY_NOTE not in build_send_tool_description("", tier)
    for text in (WILL["eager"], DECISION_CHECKPOINT["eager"], TOOL_NOTE["eager"]):
        assert "贴切" in text and "纯文字" in text and "单回复最多一张" in text
        for banned in ("连续", "连着", "随机", "每三轮", "每3轮", "必须配", "必须发", "每轮都发", "概率", "配额"):
            assert banned not in text


@pytest.mark.parametrize("event", [False, True])
@pytest.mark.parametrize("tier", ["reserved", "natural", "eager"])
def test_baseline_reminder_budget_covers_190_stickers_and_both_triggers(event, tier):
    text = build_awareness_text(_stickers(190), eagerness=tier, event=event)
    assert "190 张表情包" in text
    assert WILL[tier] in text
    assert "分清安慰与自述" in text
    assert _SHARED_POINTER in text
    assert INTERNAL_SELECTION_NOTE in text
    assert len(text) < 340 + (len(EAGER_EXTRA_NOTE) if tier == "eager" else 0)
    assert text.startswith("（表情包点名）") is event


@pytest.mark.parametrize("trigger", ["count", "signal"])
@pytest.mark.parametrize("tier", ["reserved", "natural", "eager"])
def test_actual_awareness_push_gets_baseline_text_with_existing_transport(
    tmp_path, run_async, trigger, tier,
):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    settings = StickerManagerSettings.from_config(
        {"sticker_manager": {"enabled": True, "send": {"eagerness": tier}}}
    )
    plugin._library.load(force=True)
    assert plugin._library.add(data=PNG_BYTES, desc="smile", tags=[])[1] == ""
    awareness = Awareness(plugin, plugin._library)
    assert settings.awareness.inject_mode == "interval_n"
    assert settings.awareness.inject_interval_n == 0
    assert settings.awareness.min_interval_sec == 60.0
    assert settings.awareness.event_gated is True
    interval = 3 if tier == "reserved" else 1
    assert Awareness.interval_n(settings) == interval

    texts = [f"第{i}条" for i in range(1, interval + 1)] if trigger == "count" else ["好开心"]
    for turn_n, content in enumerate(texts, start=1):
        host.bus.memory.records.append(user_message_record(float(turn_n), content, "K"))
        result = run_async(awareness.maybe_run(settings=settings, now=100.0 + turn_n))
        if turn_n < len(texts):
            assert result["status"] == "not_due"
            assert host.push.calls == []
    assert result["status"] == "injected" and result["trigger"] == trigger
    (call,) = host.push.calls
    assert call["visibility"] == []
    assert call["ai_behavior"] == "read"
    assert call["target_lanlan"] == "K"
    assert call["coalesce_key"] == "sticker_manager:awareness:K"
    assert call["description"] == "sticker_manager:awareness"
    assert call["parts"][0]["type"] == "text"
    text = call["parts"][0]["text"]
    assert WILL[tier] in text
    assert "分清安慰与自述" in text and "单回复最多一张" in text
    assert _SHARED_POINTER in text
    assert text.startswith("（表情包点名）") is (trigger == "signal")
    assert awareness.turns_seen == len(texts)
    assert awareness.trigger_counts == {trigger: 1}

    injected_at = awareness.last_inject_at
    host.bus.memory.records.append(user_message_record(10.0, "抱抱", "K"))
    blocked = run_async(awareness.maybe_run(settings=settings, now=injected_at + 59.0))
    assert blocked["status"] == "not_due" and len(host.push.calls) == 1
    host.bus.memory.records.append(user_message_record(11.0, "抱抱", "K"))
    due = run_async(awareness.maybe_run(settings=settings, now=injected_at + 60.0))
    assert due["status"] == "injected" and due["trigger"] == "signal"
    assert len(host.push.calls) == 2
