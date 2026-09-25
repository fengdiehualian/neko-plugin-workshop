"""时光日记·自动碎片的单测（独立仓库可跑，不依赖宿主）。

覆盖：提取 prompt 组装、模型回复解析（容错/钳制/坏数据）、碎片记录截断、
检索过滤（类型/关键词/来源/上限）、吵架轻语判定，以及主链路
_maybe_capture_fragments 的基线/水位/落盘/轻语集成行为。

依赖 tests/conftest.py 注入的宿主 SDK 桩与 ``plugin_factory`` / ``tm`` fixture。
运行方式（插件目录内）：uv run python -m pytest tests -q
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forever_companion.core.fragments import (  # noqa: E402
    build_fragment_prompt,
    fragment_record,
    parse_fragment_response,
    recall_fragments,
    should_nudge_fight,
)


def run(coro):
    return asyncio.run(coro)


# ---------- prompt 组装 ----------


def test_prompt_contains_user_text_and_context() -> None:
    prompt = build_fragment_prompt("我最喜欢你做的饭", "真的吗嘿嘿")
    assert "我最喜欢你做的饭" in prompt
    assert "真的吗嘿嘿" in prompt  # 她的回复作为语境拼入


def test_prompt_without_her_reply_has_no_empty_context() -> None:
    prompt = build_fragment_prompt("今天好累", "")
    # 拼接的语境段不出现（prompt 模板说明文字里那句不算）
    assert "（猫娘当时的回复：" not in prompt


# ---------- 回复解析 ----------


def test_parse_valid_capture_with_surrounding_text() -> None:
    raw = '好的，这是结果：{"capture": true, "kind": "like", "quote": "我最喜欢你做的饭", "note": "他说最喜欢她做的饭", "confidence": 0.9} 以上。'
    parsed = parse_fragment_response(raw)
    assert parsed == {
        "capture": True,
        "kind": "like",
        "quote": "我最喜欢你做的饭",
        "note": "他说最喜欢她做的饭",
        "confidence": 0.9,
    }


def test_parse_capture_false_is_passthrough() -> None:
    parsed = parse_fragment_response('{"capture": false, "confidence": 0.3}')
    assert parsed == {"capture": False, "confidence": 0.3}
    # capture 缺省按 False（宁可漏记不可错记）
    assert parse_fragment_response('{"confidence": 0.5}')["capture"] is False


def test_parse_bad_json_and_bad_kind_return_none() -> None:
    assert parse_fragment_response("完全不是 JSON") is None
    assert parse_fragment_response("") is None
    assert parse_fragment_response('{"capture": true, "kind": "hate", "quote": "x"}') is None
    # 缺 quote 无从对证
    assert parse_fragment_response('{"capture": true, "kind": "like"}') is None


def test_parse_confidence_clamped() -> None:
    parsed = parse_fragment_response(
        '{"capture": true, "kind": "overstep", "quote": "滚开", "confidence": 5}'
    )
    assert parsed["confidence"] == 1.0
    bad = parse_fragment_response(
        '{"capture": true, "kind": "overstep", "quote": "滚开", "confidence": "abc"}'
    )
    assert bad["confidence"] == 0.0


# ---------- 记录构建 ----------


def test_fragment_record_truncates() -> None:
    record = fragment_record(
        "2026-08-29T10:00:00+00:00",
        "menstrual",
        {"kind": "overstep", "quote": "滚" * 100, "note": "骂" * 100, "confidence": 0.8},
    )
    assert record["source"] == "auto"
    assert record["phase"] == "menstrual"
    # 截断上限 60（state.py _FRAGMENT_QUOTE_MAX_CHARS/_FRAGMENT_NOTE_MAX_CHARS）
    assert len(record["quote"]) == 60
    assert len(record["note"]) == 60
    assert record["confidence"] == 0.8


# ---------- 检索 ----------


def _seed_diary() -> list[dict]:
    return [
        {"ts": "1", "source": "self", "mood": "委屈", "entry": "他凶我"},
        {"ts": "2", "source": "auto", "kind": "like", "quote": "最喜欢你做的饭", "note": "喜好"},
        {"ts": "3", "source": "auto", "kind": "overstep", "quote": "滚开 别烦我", "note": "骂人"},
        {"ts": "4", "source": "auto", "kind": "like", "quote": "喜欢安静", "note": "喜好"},
    ]


def test_recall_only_auto_source_newest_first() -> None:
    hits = recall_fragments(_seed_diary())
    assert [h["ts"] for h in hits] == ["4", "3", "2"]


def test_recall_kind_and_query_filters() -> None:
    hits = recall_fragments(_seed_diary(), kind="like")
    assert [h["ts"] for h in hits] == ["4", "2"]
    hits = recall_fragments(_seed_diary(), query="滚开")
    assert [h["ts"] for h in hits] == ["3"]
    # 关键词大小写不敏感
    assert [h["ts"] for h in recall_fragments(_seed_diary(), query="LIKE")] == []


def test_recall_limit_clamped() -> None:
    diary = [
        {"ts": str(i), "source": "auto", "kind": "important", "quote": f"q{i}", "note": ""}
        for i in range(40)
    ]
    assert len(recall_fragments(diary, limit=5)) == 5
    # limit 钳制到 30
    assert len(recall_fragments(diary, limit=999)) == 30


# ---------- 吵架轻语判定 ----------


def test_nudge_only_for_heavy_actions_and_nudge_kinds() -> None:
    pause = frozenset({"ebb_tide", "storm_surge"})
    record = {"kind": "overstep"}
    assert should_nudge_fight(record, "storm_surge", pause) is True
    # 正面动作不轻语
    assert should_nudge_fight(record, "warm_current", pause) is False
    # 喜好类碎片不轻语
    assert should_nudge_fight({"kind": "like"}, "storm_surge", pause) is False
    # 无动作生效不轻语
    assert should_nudge_fight(record, "", pause) is False


# ---------- 主链路集成 ----------


def _setup_capture(p, reply_json: str, marker: str = "2:new") -> None:
    """给插件实例挂上碎片捕获的最小桩：recent 轮次、槽位解析、直连回复。"""
    shard = p._current_shard()

    async def fake_poll(shard_, *, lanlan="", peek=False, advance=True):
        shard_.last_recent_marker = marker
        return ("我最喜欢你做的饭", "真的吗嘿嘿")

    p._poll_recent_turns = fake_poll
    p._resolve_tone_slot = lambda cfg, slot, _seen=frozenset(): {
        "model": "m", "api_key": "k", "base_url": "http://x"
    }
    p._post_chat_completion = lambda *a, **k: reply_json
    return shard


def test_capture_baseline_first_run_does_not_analyze(plugin_factory) -> None:
    p = plugin_factory()
    shard = _setup_capture(p, '{"capture": true, "kind": "like", "quote": "喜欢", "confidence": 0.9}')
    # 首趟：基线未建立，只记水位不落碎片
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert shard.last_fragment_marker == "2:new"
    assert shard.diary == []


def test_capture_appends_fragment_and_persists(plugin_factory, tm) -> None:
    p = plugin_factory()
    shard = _setup_capture(p, '{"capture": true, "kind": "like", "quote": "最喜欢你做的饭", "confidence": 0.9}')
    shard.last_fragment_marker = "1:old"  # 基线已建立，本轮 marker 变化 → 分析
    assert run(p._maybe_capture_fragments("default", shard)) is True
    assert len(shard.diary) == 1
    record = shard.diary[0]
    assert record["source"] == "auto"
    assert record["kind"] == "like"
    assert record["quote"] == "最喜欢你做的饭"
    # 落盘到当前角色的 diary key
    assert p.store.data[tm._diary_key("default")][-1]["source"] == "auto"


def test_capture_below_confidence_drops(plugin_factory) -> None:
    p = plugin_factory()
    shard = _setup_capture(p, '{"capture": true, "kind": "like", "quote": "x", "confidence": 0.2}')
    shard.last_fragment_marker = "1:old"
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert shard.diary == []


def test_capture_nudge_during_fight(plugin_factory) -> None:
    p = plugin_factory()
    shard = _setup_capture(
        p,
        '{"capture": true, "kind": "overstep", "quote": "滚开 别烦我", "note": "凶我", "confidence": 0.9}',
    )
    shard.last_fragment_marker = "1:old"
    # 情绪风暴生效中（非限时校验：expires_at=0 视为不校验到期）
    shard.mood.action = "storm_surge"
    shard.mood.started_at = time.time()
    shard.mood.expires_at = time.time() + 600
    assert run(p._maybe_capture_fragments("default", shard)) is True
    pushes = [item for item in p._pushed if "fragment_nudge" in str(item.get("coalesce_key"))]
    assert len(pushes) == 1
    assert "滚开 别烦我" in pushes[0]["parts"][0]["text"]


def test_capture_no_nudge_when_calm(plugin_factory) -> None:
    p = plugin_factory()
    shard = _setup_capture(
        p,
        '{"capture": true, "kind": "overstep", "quote": "滚开", "confidence": 0.9}',
    )
    shard.last_fragment_marker = "1:old"
    assert run(p._maybe_capture_fragments("default", shard)) is True
    assert shard.diary  # 碎片照记
    assert p._pushed == []  # 但平静期不轻语


def test_capture_dormant_when_slot_unresolved(plugin_factory) -> None:
    p = plugin_factory()
    shard = p._current_shard()

    async def fake_poll(shard_, *, lanlan="", peek=False, advance=True):
        shard_.last_recent_marker = "2:new"
        return ("话", "回")

    p._poll_recent_turns = fake_poll
    p._resolve_tone_slot = lambda cfg, slot, _seen=frozenset(): None  # 槽位未配模型
    shard.last_fragment_marker = "1:old"
    assert run(p._maybe_capture_fragments("default", shard)) is False
    assert shard.diary == []
    # 水位已推进：同一轮不会被反复重试
    assert shard.last_fragment_marker == "2:new"


# ---------- 工具层 ----------


def test_tool_recall_fragments_returns_hits(plugin_factory) -> None:
    p = plugin_factory()
    shard = p._current_shard()
    shard.diary = _seed_diary()
    result = run(p.tool_recall_fragments(kind="overstep"))
    assert result.value["found"] == 1
    assert result.value["items"][0]["quote"] == "滚开 别烦我"


def test_tool_write_journal_returns_tail_without_mirror(plugin_factory) -> None:
    p = plugin_factory()
    first = run(p.tool_write_journal(events="第一篇日记，这几天很开心。"))
    assert first.value["saved"] is True
    assert first.value["page"] == 1
    assert first.value["recent_context"] == ""
    second = run(p.tool_write_journal(thoughts="接着写今天的事。"))
    assert second.value["page"] == 1
    assert "第一篇日记" in second.value["recent_context"]  # 续写衔接（带日期前缀）
    # 个人日记不镜像 read 推送（只给用户看）
    assert p._pushed == []


def test_tool_write_journal_structured_fields_assemble(plugin_factory) -> None:
    p = plugin_factory()
    result = run(p.tool_write_journal(
        events="他带我去了海边。", thoughts="我有点紧张。", feelings="喜欢多一点。", extra="下次想去看日出。",
    ))
    text = p._current_shard().journal[0]["entries"][0]["text"]
    assert result.value["saved"] is True
    assert "【这段时间】他带我去了海边。" in text
    assert "【我在想】我有点紧张。" in text
    assert "【对他的感觉】喜欢多一点。" in text
    assert "【想说的】下次想去看日出。" in text


def test_tool_write_journal_rejects_all_empty(plugin_factory) -> None:
    p = plugin_factory()
    result = run(p.tool_write_journal(new_page=True))
    assert hasattr(result, "error")


def test_tool_mood_disabled_rejects(plugin_factory) -> None:
    p = plugin_factory(mood_extra={"enabled": False})
    result = run(p.tool_recall_fragments())
    assert hasattr(result, "error")
    result2 = run(p.tool_write_journal(events="x"))
    assert hasattr(result2, "error")
