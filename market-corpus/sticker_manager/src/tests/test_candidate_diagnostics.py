"""Explain misses using counts while preserving selection and chat privacy."""

from __future__ import annotations

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.catalog import Sticker, search_intent_candidates
from sticker_manager.core.configuration import StickerManagerSettings


@pytest.mark.parametrize("query", ["求摸摸舒服", "自己被摸头开心", "给对方泡茶开心", "安慰对方开心"])
def test_known_compact_action_queries_match_their_separated_equivalent(query):
    pool = [
        Sticker("pet", "a.png", "", tags=["被摸头", "舒服"]),
        Sticker("brew", "b.png", "", tags=["给对方泡茶", "开心"]),
        Sticker("comfort", "c.png", "", tags=["安慰对方", "开心"]),
        Sticker("smile", "d.png", "", tags=["开心"]),
    ]
    action, mood = query[:-2], query[-2:]
    assert search_intent_candidates(pool, query) == search_intent_candidates(pool, f"{action} {mood}")
    assert {s.id for s in search_intent_candidates(pool, query)} <= {"pet", "brew", "comfort"}


@pytest.mark.parametrize("query", ["不是开心", "还没收到开心", "高兴但难过", "抱着杯子开心", "摸头不开心"])
def test_unknown_phrases_and_negation_are_not_split_into_positive_moods(query):
    pool = [Sticker("smile", "a.png", "", tags=["开心", "难过", "摸头"])]
    assert not search_intent_candidates(pool, query)


@pytest.mark.parametrize(("query", "key"), [
    ("未知完整长句", "no_word_match"),
    ("给对方泡茶 开心", "action_filtered"),
    ("已完成 开心", "stage_filtered"),
    ("等待收到 开心", "receipt_filtered"),
    ("开心", "scene_filtered"),
])
def test_filter_counters_explain_distinct_miss_causes_without_raw_text(query, key):
    by_key = {
        "no_word_match": Sticker("a", "a.png", "", tags=["开心"]),
        "action_filtered": Sticker("a", "a.png", "", tags=["自己喝茶", "开心"]),
        "stage_filtered": Sticker("a", "a.png", "", tags=["期待", "开心"]),
        "receipt_filtered": Sticker("a", "a.png", "", tags=["抱着礼盒", "开心"]),
        "scene_filtered": Sticker("a", "a.png", "", tags=["奖杯", "开心"]),
    }
    counters = {}
    assert search_intent_candidates([by_key[key]], query, diagnostics=counters) == []
    assert counters[key] == 1 and counters["matched"] == 0
    assert all(isinstance(value, int) for value in counters.values())
    assert query not in str(counters)


@pytest.mark.parametrize("method", ["tool_sticker_list", "tool_sticker_send"])
def test_recent_filter_is_logged_without_query_text_or_sending(tmp_path, run_async, method, caplog):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(enabled=True)
    plugin._library.load()
    sticker, error = plugin._library.add(data=PNG_BYTES, desc="开心", tags=["开心"])
    assert not error
    plugin._library.append_usage({"id": sticker.id, "ok": True, "lanlan": "K", "at": 1}, keep=200)
    query = "自己开心"
    caplog.set_level("INFO")
    out = run_async(getattr(plugin, method)(query=query, _ctx={"lanlan_name": "K"}))
    assert not out.get("candidate_ids") and not out.get("sent")
    text = caplog.text
    assert "matched=1" in text and "recent_filtered=1" in text and "selectable=0" in text
    assert query not in text and not host.push.calls
    assert "若已说完就结束" in out["hint"]


@pytest.mark.parametrize("query", ["期待开箱", "期待开箱开心", "开心期待开箱", "等待收到拆礼物开心"])
def test_pending_event_phrases_are_preserved_before_compact_action_splitting(query):
    done = Sticker("done", "a.png", "", caption="自己打开礼盒，收到礼物后开心。", tags=["开心"])
    assert not search_intent_candidates([done], query)


@pytest.mark.parametrize("method", ["tool_sticker_list", "tool_sticker_send"])
def test_unavailable_group_is_identified_without_substituting_images(tmp_path, run_async, method, caplog):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(enabled=True)
    plugin._library.load()
    assert not plugin._library.add(data=PNG_BYTES, desc="开心", tags=["开心"], group="present")[1]
    caplog.set_level("INFO")
    out = run_async(getattr(plugin, method)(group="missing", _ctx={"lanlan_name": "K"}))
    assert not out.get("candidate_ids") and not out.get("sent") and not host.push.calls
    assert "scope_unavailable=1 active=1 disabled=0 recent_filtered=0" in caplog.text


def test_diagnostics_do_not_change_ranking_and_count_each_picture_once():
    pool = [
        Sticker("a", "a.png", "", tags=["开心"]),
        Sticker("b", "b.png", "", tags=["难过"]),
        Sticker("c", "c.png", "", tags=["开心", "奖杯"]),
        Sticker("d", "d.png", "", tags=["开心"], disabled=True),
    ]
    diagnostics = {}
    actual = search_intent_candidates(pool, "开心", diagnostics=diagnostics)
    assert actual == search_intent_candidates(pool, "开心") == [pool[0]]
    counters = ("disabled", "action_filtered", "stage_filtered", "receipt_filtered",
                "no_word_match", "scene_filtered", "matched")
    assert sum(diagnostics[key] for key in counters) == diagnostics["pool"] == 4
    search_intent_candidates(pool, "q" * 400, diagnostics=diagnostics)
    assert diagnostics["query_chars"] == 400 and diagnostics["query_clipped"] == 1
