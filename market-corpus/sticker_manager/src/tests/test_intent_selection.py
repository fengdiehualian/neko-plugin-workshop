"""Intent selection contracts; these tests do not simulate an LLM's judgment."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from conftest import PNG_BYTES, FakeHostContext, build_plugin
from sticker_manager.core.catalog import (
    SELECTION_MATCH_RULE,
    Sticker,
    resolve_send_target,
    search_intent_candidates,
    search_stickers,
)
from sticker_manager.core.configuration import SendSettings, StickerManagerSettings
from sticker_manager.core.eagerness import INTERNAL_SELECTION_NOTE
from sticker_manager.core.tool_surface import STANDING_MATCH_RULE, build_send_tool_description
from sticker_manager.services.bundled_pack import open_pack
from sticker_manager.services.library import Library

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "official/official_pack.zip"


def _setup(tmp_path):
    plugin, host = build_plugin(FakeHostContext(data_root=tmp_path))
    plugin._settings = StickerManagerSettings(
        enabled=True, send=SendSettings(cooldown_sec=0, recent_dedup_count=5),
    )
    plugin._library.load()
    return plugin, host


def _add(plugin, label, *, group="mixed", caption="", tags=()):
    sticker, error = plugin._library.add(
        data=PNG_BYTES + label.encode(), desc=label, tags=list(tags), group=group, caption=caption,
    )
    assert not error
    return sticker


@pytest.mark.parametrize("selector", [{"group": "mixed"}, {"query": "难过"}, {"query": "难过", "force": True}])
def test_one_candidate_never_sends_until_exact_id_is_chosen(tmp_path, run_async, selector):
    plugin, host = _setup(tmp_path)
    sticker = _add(plugin, "难过", caption="自己委屈落泪，想被安慰。")
    out = run_async(plugin.tool_sticker_send(**selector, _ctx={"lanlan_name": "K"}))
    assert out["ok"] and out["sent"] == "" and out["candidate_ids"] == [sticker.id]
    assert sticker.caption in out["candidates"]
    assert all(word in out["hint"] for word in ("安慰对方", "自己难过", "嘲笑对方", "没有合适的就纯文字"))
    assert not host.push.calls and not plugin._library.read_usage()
    assert plugin._library.get(sticker.id).use_count == 0 and plugin._runstats.sent == 0
    confirmed = run_async(plugin.tool_sticker_send(sticker_id=sticker.id, _ctx={"lanlan_name": "K"}))
    assert confirmed["sent"] == sticker.id and len(host.push.calls) == 1


@pytest.mark.parametrize("tool_name", ["tool_sticker_list", "tool_sticker_send"])
def test_group_and_query_are_intersected_and_miss_never_falls_back(tmp_path, run_async, tool_name):
    plugin, host = _setup(tmp_path)
    wanted = _add(plugin, "摸摸", group="gentle", caption="自己被摸头，舒服地眯眼。")
    other = _add(plugin, "摸摸也是这个词", group="other")
    method = getattr(plugin, tool_name)
    hit = run_async(method(group="gentle", query="摸摸"))
    assert hit["candidate_ids"] == [wanted.id] and other.id not in hit["candidate_ids"]
    miss = run_async(method(group="gentle", query="不存在的语义"))
    assert not miss.get("candidate_ids") and not miss.get("sent")
    assert not host.push.calls and not plugin._library.read_usage()


@pytest.mark.parametrize("extra", [{"group": "mixed"}, {"query": "难过"}, {"group": "mixed", "query": "难过"}])
def test_invalid_explicit_id_never_substitutes_another_image(tmp_path, run_async, extra):
    plugin, host = _setup(tmp_path)
    _add(plugin, "难过")
    out = run_async(plugin.tool_sticker_send(sticker_id="missing", **extra))
    assert not out["ok"] and out["reason"] == "no_match"
    assert not host.push.calls


def test_list_excludes_recent_disabled_and_other_zone_candidates(tmp_path, run_async):
    plugin, host = _setup(tmp_path)
    recent = _add(plugin, "recent")
    disabled = _add(plugin, "disabled")
    wanted = _add(plugin, "wanted")
    plugin._library.update(disabled.id, disabled=True)
    plugin._library.append_usage({"id": recent.id, "ok": True, "lanlan": "K", "at": 1}, keep=200)
    away, error = plugin._library.create_zone("away", "")
    assert not error
    assert not plugin._library.add(data=PNG_BYTES + b"away", desc="away", tags=[], zone=away)[1]
    out = run_async(plugin.tool_sticker_list(group="mixed", _ctx={"lanlan_name": "K"}))
    assert out["candidate_ids"] == [wanted.id]
    assert not host.push.calls


def test_explicit_action_filters_broad_emotions_without_claiming_a_send():
    pool = [
        Sticker("a", "a.png", "", caption="自己被摸头，享受宠爱。", tags=["自己被摸头", "宠爱"]),
        Sticker("b", "b.png", "", caption="自己落泪。", tags=["自己难过"]),
    ]
    assert [row.id for row in search_intent_candidates(pool, "摸头，宠爱")] == ["a"]
    assert [row.id for row in search_intent_candidates(pool, "摸头 难过")] == ["a"]
    chosen, candidates = resolve_send_target(pool, "难过")
    assert chosen is None and [row.id for row in candidates] == ["b"]
    assert {row.id for row in search_intent_candidates(pool, "宠爱 难过")} == {"a", "b"}


def test_negative_usage_is_not_a_candidate_but_stays_searchable_in_management():
    pool = [
        Sticker("happy", "h.png", "", caption="庆祝，不用于难过的回复。", tags=["庆祝"]),
        Sticker("sad", "s.png", "", caption="自己落泪。", tags=["自己难过"]),
    ]
    assert [row.id for row in search_intent_candidates(pool, "难过")] == ["sad"]
    assert {row.id for row in search_stickers(pool, "难过")} == {"happy", "sad"}
    assert pool[0].catalog_body() == "庆祝，不用于难过的回复。"


@pytest.mark.parametrize("restriction", [
    "不适合安慰对方", "不是主动安慰对方", "不用于安慰对方", "不配安慰对方的文字",
    "不能当成安慰对方", "不表示安慰对方", "没有安慰对方的动作",
])
def test_restricted_usage_does_not_supply_action_evidence(restriction):
    sticker = Sticker("a", "a.png", "", caption=f"自己闭眼大笑；{restriction}。", tags=["开心"])
    assert not search_intent_candidates([sticker], "安慰对方 开心")


@pytest.mark.parametrize("phrase", ["不开心", "不高兴", "不满", "不安", "我不行了", "不说了"])
def test_negative_affect_and_actual_utterances_remain_searchable(phrase):
    sticker = Sticker("a", "a.png", "", caption=f"表达自己{phrase}，需要休息。")
    assert search_intent_candidates([sticker], phrase) == [sticker]


@pytest.mark.parametrize(("positive", "negative"), [
    ("开心", "不开心"), ("高兴", "不太高兴"), ("摸头", "不想被摸头"), ("抱抱", "不想再抱抱"),
])
def test_negated_intent_is_not_recalled_as_the_positive_intent(positive, negative):
    sticker = Sticker("a", "a.png", "", caption=f"表达自己{negative}。", tags=[f"自己{negative}"])
    assert not search_intent_candidates([sticker], positive)
    assert search_intent_candidates([sticker], negative) == [sticker]


def test_restriction_does_not_hide_positive_usage_in_the_next_clause():
    sticker = Sticker("a", "a.png", "", caption="不是庆祝，而是自己难过。适合低落时求哄。")
    assert search_intent_candidates([sticker], "难过") == [sticker]
    assert not search_intent_candidates([sticker], "庆祝")


def test_pet_head_synonyms_recall_same_action_and_keep_direction():
    pool = [
        Sticker("receiving", "a.png", "", caption="不是自己伸手摸对方。", tags=["自己被揉头", "亲昵"]),
        Sticker("giving", "b.png", "", tags=["主动摸头", "亲昵"]),
        Sticker("nose", "c.png", "", tags=["自己被戳鼻尖", "亲昵", "舒服", "开心"], use_count=100),
    ]
    assert [row.id for row in search_intent_candidates(pool, "求摸摸 亲昵 舒服 开心")] == ["receiving"]
    assert [row.id for row in search_intent_candidates(pool, "摸对方的头 亲昵")] == ["giving"]
    assert {row.id for row in search_intent_candidates(pool, "揉脑袋")} == {"receiving", "giving"}


def test_caption_only_synonym_is_recalled_but_literal_usage_stays_first():
    pool = [
        Sticker("alias", "a.png", "", caption="自己被揉头，舒服地眯眼。"),
        Sticker("literal", "b.png", "", caption="自己求摸摸，舒服地眯眼。"),
    ]
    assert [row.id for row in search_intent_candidates(pool, "求摸摸")] == ["literal", "alias"]
    sad_pool = [
        Sticker("alias", "a.png", "", tags=["失落"]),
        Sticker("literal", "b.png", "", tags=["自己难过"]),
    ]
    assert [row.id for row in search_intent_candidates(sad_pool, "难过")] == ["literal", "alias"]


@pytest.mark.parametrize("query", ["愉快", "快乐", "自己高兴", "本喵欢喜", "我开心"])
def test_common_positive_mood_words_recall_equivalent_simple_expressions(query):
    pool = [
        Sticker("smile", "a.png", "", tags=["自己开心"]),
        Sticker("sad", "b.png", "", tags=["不高兴", "自己难过"]),
        Sticker("toast", "c.png", "", tags=["为对方高兴", "干杯"]),
    ]
    assert search_intent_candidates(pool, query) == [pool[0]]


def test_compact_known_moods_recall_without_splitting_unknown_actions_or_negation():
    pool = [
        Sticker("heart", "a.png", "", tags=["温柔", "感谢心意", "喜欢对方"]),
        Sticker("smile", "b.png", "", tags=["自己开心"]),
    ]
    assert search_intent_candidates(pool, "温柔感谢喜欢") == [pool[0]]
    assert search_intent_candidates(pool, "自己温柔感谢喜欢") == [pool[0]]
    for query in ("不开心", "不是开心", "还没收到开心", "安慰对方开心", "高兴但难过"):
        assert not search_intent_candidates(pool, query)


@pytest.mark.parametrize("tool_name", ["tool_sticker_list", "tool_sticker_send"])
def test_mood_synonyms_keep_unseen_equivalent_candidates_after_recent_filter(tmp_path, run_async, tool_name):
    plugin, host = _setup(tmp_path)
    recent = _add(plugin, "recent", tags=["开心"])
    wanted = _add(plugin, "wanted", tags=["愉快"])
    _add(plugin, "toast", tags=["高兴", "干杯"])
    _add(plugin, "money", tags=["高兴", "红包"])
    plugin._library.append_usage({"id": recent.id, "ok": True, "lanlan": "K", "at": 1}, keep=200)
    out = run_async(getattr(plugin, tool_name)(query="自己高兴", _ctx={"lanlan_name": "K"}))
    assert out["candidate_ids"] == [wanted.id]
    assert not host.push.calls and len(plugin._library.read_usage()) == 1


def test_comforting_the_other_person_does_not_recall_asking_for_comfort():
    pool = [
        Sticker("sad", "a.png", "", tags=["自己难过", "想被安慰"]),
        Sticker("comfort", "b.png", "", tags=["安慰对方", "温柔"]),
    ]
    assert [row.id for row in search_intent_candidates(pool, "安慰对方")] == ["comfort"]
    assert [row.id for row in search_intent_candidates(pool, "求哄")] == ["sad"]


@pytest.mark.parametrize("query", ["主动蹭人 亲昵", "梳尾巴毛 亲昵", "抱抱 亲昵"])
def test_missing_action_does_not_fall_back_to_licking_or_being_petted(query):
    pool = [
        Sticker("pet", "a.png", "", tags=["自己被摸头", "亲昵"]),
        Sticker("lick", "b.png", "", tags=["舔舔", "亲昵"]),
        Sticker("heart", "c.png", "", tags=["自己抱爱心", "亲昵"]),
    ]
    assert not search_intent_candidates(pool, query)


def test_explicit_action_is_not_inferred_from_group_name_or_visible_text():
    pool = [
        Sticker("group", "a.png", "", group="摸头与亲昵", caption="自己吐舌卖萌。"),
        Sticker("text", "b.png", "", visible_text="摸头", caption="自己举刀警告。"),
    ]
    assert not search_intent_candidates(pool, "摸头")
    assert not search_intent_candidates(pool, "亲昵")
    assert search_stickers(pool, "摸头") == pool


def test_alternative_actions_and_explicit_id_lookup_stay_available():
    pool = [
        Sticker("pet", "a.png", "", tags=["自己被摸头"]),
        Sticker("hug", "b.png", "", tags=["主动拥抱"]),
        Sticker("nose", "c.png", "", tags=["自己被戳鼻尖"]),
    ]
    assert {row.id for row in search_intent_candidates(pool, "摸头 抱抱")} == {"pet", "hug"}
    assert search_intent_candidates(pool, "nose") == [pool[2]]


@pytest.mark.parametrize("query", ["给对方泡茶 开心", "泡茶 已完成 开心", "给你递茶 满足", "上茶 热饮"])
def test_preparing_or_serving_tea_never_falls_back_to_self_drinking(query):
    pool = [
        Sticker("drink", "a.png", "", caption="自己端杯喝茶，笑得满足。", tags=["自己喝茶", "开心", "热饮"]),
        Sticker("laugh", "b.png", "", caption="自己友善地笑。", tags=["开心"]),
    ]
    assert not search_intent_candidates(pool, query)


def test_tea_action_and_explicit_recipient_are_independent_of_the_shared_topic():
    pool = [
        Sticker("brew", "a.png", "", tags=["给对方泡茶", "开心"]),
        Sticker("serve", "b.png", "", tags=["给对方递茶", "开心"]),
        Sticker("receive", "c.png", "", tags=["接过茶杯", "开心"]),
        Sticker("drink", "d.png", "", tags=["自己喝茶", "开心"]),
    ]
    assert [row.id for row in search_intent_candidates(pool, "给对方泡茶 开心")] == ["brew"]
    assert [row.id for row in search_intent_candidates(pool, "给你递茶 开心")] == ["serve"]
    assert [row.id for row in search_intent_candidates(pool, "接过茶 开心")] == ["receive"]
    assert [row.id for row in search_intent_candidates(pool, "品茶 开心")] == ["drink"]
    assert {row.id for row in search_intent_candidates(pool, "泡茶 喝茶")} == {"brew", "drink"}


@pytest.mark.parametrize(("query", "expected"), [
    ("期待 开心", {"wait", "smile"}),
    ("进行中 开心", {"working", "smile"}),
    ("已完成 开心", {"done", "smile"}),
])
def test_explicit_event_stages_exclude_conflicts_without_inventing_unmarked_stages(query, expected):
    pool = [
        Sticker("wait", "a.png", "", tags=["期待", "开心"]),
        Sticker("working", "b.png", "", tags=["制作中", "开心"]),
        Sticker("done", "c.png", "", caption="东西做好了，展示已完成的成果。", tags=["开心"]),
        Sticker("smile", "d.png", "", tags=["开心"]),
    ]
    assert {row.id for row in search_intent_candidates(pool, query)} == expected
    assert set(row.id for row in search_intent_candidates(pool, "开心")) == {row.id for row in pool}
    assert search_intent_candidates(pool, "wait 已完成")[0] == pool[0]


def test_restricted_stage_mentions_do_not_exclude_a_valid_anticipation_image():
    sticker = Sticker("a", "a.png", "", caption="期待好事发生，不是已经成功；不表示已经完成。")
    assert search_intent_candidates([sticker], "期待") == [sticker]
    assert not search_intent_candidates([sticker], "已完成 开心")


def test_plain_affect_excludes_unrequested_plots_even_with_more_keyword_hits_or_usage():
    pool = [
        Sticker("gift", "g.png", "", tags=["开心", "兴奋", "展示礼盒"], use_count=100),
        Sticker("win", "w.png", "", tags=["开心", "兴奋", "奖杯"], use_count=100),
        Sticker("dance", "d.png", "", tags=["开心", "兴奋", "跳舞"], use_count=100),
        Sticker("snack", "s.png", "", tags=["开心", "兴奋", "吃甜点"], use_count=100),
        Sticker("smile", "p.png", "", tags=["开心"]),
    ]
    assert [row.id for row in search_intent_candidates(pool, "开心 兴奋")] == ["smile"]
    # Additional context may support that plot, and exact IDs remain lookups.
    assert search_intent_candidates(pool, "开心 兴奋 礼物")[0].id == "gift"
    assert search_intent_candidates(pool, "dance") == [pool[2]]


@pytest.mark.parametrize("query", [
    "等待收到 开心", "自己期待收到礼物 开心", "还没收到 开心", "等待来访 开心", "等你来 开心", "明天见 开心",
])
def test_pending_receipt_or_visit_does_not_admit_already_holding_opening_or_greeting(query):
    pool = [
        Sticker("open", "a.png", "", caption="自己打开礼盒，收到礼物后开心。", tags=["期待", "开心"]),
        Sticker("hold", "b.png", "", caption="自己抱着礼盒，开心地展示礼物。", tags=["期待", "开心"]),
        Sticker("hello", "c.png", "", caption="看到你啦，挥手见面问候。", tags=["开心"]),
        Sticker("wait", "d.png", "", caption="自己期待好事发生。"),
        Sticker("smile", "e.png", "", tags=["开心"]),
    ]
    assert {row.id for row in search_intent_candidates(pool, query)} == {"wait", "smile"}
    assert not search_intent_candidates(pool[:3], query)


def test_receipt_stage_conflicts_are_filtered_even_when_the_query_mentions_gifts():
    pool = [
        Sticker("hold", "a.png", "", tags=["自己抱礼物", "期待", "开心"]),
        Sticker("open", "b.png", "", tags=["自己拆礼物", "开心"]),
        Sticker("wait", "c.png", "", caption="自己期待收到礼物，眼睛发亮。", tags=["开心"]),
    ]
    assert [row.id for row in search_intent_candidates(pool, "等待收到 礼物 开心")] == ["wait"]
    assert {row.id for row in search_intent_candidates(pool, "收到礼物 开心")} == {"open"}
    assert search_intent_candidates(pool, "open 等待收到") == [pool[1]]


@pytest.mark.parametrize("query", ["等待收到 开心 兴奋", "等待来访 开心 兴奋"])
def test_pending_stage_evidence_precedes_a_popular_broad_mood_match(query):
    pool = [
        Sticker("smile", "a.png", "", tags=["开心", "兴奋"], use_count=100),
        Sticker("wait", "b.png", "", tags=["期待"]),
    ]
    assert [row.id for row in search_intent_candidates(pool, query)] == ["wait", "smile"]


@pytest.mark.parametrize(("query", "caption"), [
    ("等待收到 拆礼物 开心", "自己拆礼物，收到礼物后开心。"),
    ("期待开箱 开心", "自己打开礼盒，收到礼物后开心。"),
    ("等待来访 见面问候 开心", "看到你啦，挥手见面问候。"),
])
def test_explicit_waiting_is_not_overridden_by_the_name_of_the_future_action(query, caption):
    sticker = Sticker("done", "a.png", "", caption=caption, tags=["开心"])
    assert not search_intent_candidates([sticker], query)


@pytest.mark.parametrize("future", ["期待收到礼物", "等收到礼物", "等待拆礼物", "准备打开礼盒", "期待见面问候"])
def test_future_event_mentions_do_not_supply_completed_stage_evidence(future):
    sticker = Sticker("future", "a.png", "", caption=f"自己{future}，满怀期待。", tags=["期待", "开心"])
    assert search_intent_candidates([sticker], f"期待 {future} 开心") == [sticker]
    assert not search_intent_candidates([sticker], "已完成 开心")


def test_greeting_queries_require_greeting_actions_and_ignore_comparisons():
    pool = [
        Sticker("hello", "a.png", "", caption="挥手打招呼，友好地说嗨。", tags=["开心"]),
        Sticker("wait", "b.png", "", caption="期待下次见面。", tags=["开心"]),
        Sticker("heart", "c.png", "", caption="表达浓浓亲昵，情感强于普通问好。", tags=["亲昵"]),
        Sticker("smile", "d.png", "", tags=["开心"]),
    ]
    assert search_intent_candidates(pool, "见面问候 开心") == [pool[0]]
    assert {row.id for row in search_intent_candidates(pool, "等待来访 问候 开心")} == {"wait", "smile"}


def test_unrequested_money_trophy_or_greeting_never_fills_an_empty_mood_search():
    pool = [
        Sticker("money", "a.png", "", tags=["红包", "开心", "兴奋"], use_count=100),
        Sticker("win", "b.png", "", tags=["奖杯", "获胜", "开心", "兴奋"], use_count=100),
        Sticker("hello", "c.png", "", tags=["问候对方", "开心"]),
    ]
    assert not search_intent_candidates(pool, "开心 兴奋")
    assert {row.id for row in search_stickers(pool, "开心")} == {"money", "win", "hello"}
    assert search_intent_candidates(pool, "红包 开心") == [pool[0]]
    assert search_intent_candidates(pool, "获胜 开心") == [pool[1]]
    assert search_intent_candidates(pool, "money") == [pool[0]]


@pytest.mark.parametrize("tool_name", ["tool_sticker_list", "tool_sticker_send"])
def test_recent_simple_expression_does_not_reveal_unrequested_plot_fillers(tmp_path, run_async, tool_name):
    plugin, host = _setup(tmp_path)
    smile = _add(plugin, "smile", tags=["开心"])
    _add(plugin, "money", tags=["红包", "开心"])
    _add(plugin, "win", tags=["奖杯", "开心"])
    _add(plugin, "hello", caption="看到你啦，见面挥手。", tags=["开心"])
    method = getattr(plugin, tool_name)
    first = run_async(method(query="开心", _ctx={"lanlan_name": "K"}))
    assert first["candidate_ids"] == [smile.id]
    plugin._library.append_usage({"id": smile.id, "ok": True, "lanlan": "K", "at": 1}, keep=200)
    second = run_async(method(query="开心", _ctx={"lanlan_name": "K"}))
    assert not second.get("candidate_ids") and not second.get("sent")
    assert not host.push.calls and len(plugin._library.read_usage()) == 1


@pytest.mark.parametrize("tool_name", ["tool_sticker_list", "tool_sticker_send"])
def test_action_query_is_filtered_before_the_five_candidate_limit(tmp_path, run_async, tool_name):
    plugin, host = _setup(tmp_path)
    for index in range(6):
        _add(plugin, f"broad-{index}", caption="自己被戳鼻尖。", tags=["亲昵", "舒服", "开心"])
    wanted = _add(plugin, "wanted", caption="自己的头被揉动。", tags=["自己被揉头"])
    out = run_async(getattr(plugin, tool_name)(query="求摸摸 亲昵 舒服 开心"))
    assert out["candidate_ids"] == [wanted.id]
    assert wanted.caption in (out.get("catalog") or out.get("candidates"))
    assert not host.push.calls and not plugin._library.read_usage()


def test_corrupt_catalog_does_not_offer_stale_candidates(tmp_path, run_async):
    plugin, host = _setup(tmp_path)
    _add(plugin, "one")
    plugin._library.catalog_path.write_text("broken", encoding="utf-8")
    assert not plugin._library.load(force=True).ok
    out = run_async(plugin.tool_sticker_list())
    assert not out["ok"] and out["reason"] == "library_io_error"
    assert not host.push.calls


@pytest.mark.parametrize("tool_name", ["tool_sticker_list", "tool_sticker_send"])
@pytest.mark.parametrize("outcome", ["candidate", "empty", "disabled", "unreadable"])
def test_model_receipts_keep_selection_private_even_without_a_send(tmp_path, run_async, tool_name, outcome):
    plugin, host = _setup(tmp_path)
    sticker = _add(plugin, "期待", caption="自己期待接下来的好事。")
    query = "期待"
    if outcome == "empty":
        query = "无此用途"
    elif outcome == "disabled":
        plugin._settings = StickerManagerSettings(enabled=False)
    elif outcome == "unreadable":
        plugin._library.catalog_path.write_text("broken", encoding="utf-8")
        assert not plugin._library.load(force=True).ok
    result = run_async(getattr(plugin, tool_name)(query=query))
    assert INTERNAL_SELECTION_NOTE in result["hint"]
    if outcome == "candidate":
        assert result["candidate_ids"] == [sticker.id]
        assert SELECTION_MATCH_RULE in result["hint"]
    else:
        assert not result.get("candidate_ids")
    assert not host.push.calls


@pytest.mark.parametrize("tool_name", ["tool_sticker_list", "tool_sticker_send"])
@pytest.mark.parametrize("selector", [
    {"query": "无此用途"}, {"group": "mixed", "query": "无此用途"}, {"group": "missing"},
])
def test_empty_results_use_private_continuation_instead_of_failure_narration(
    tmp_path, run_async, tool_name, selector,
):
    plugin, host = _setup(tmp_path)
    _add(plugin, "one")
    out = run_async(getattr(plugin, tool_name)(**selector))
    assert not out.get("candidate_ids") and not out.get("sent")
    assert "若已说完就结束" in out["note"]
    assert "仅续接未说完的话" in out["note"]
    assert "本结果仅供内部判断" in out["note"]
    assert "选图不进正文或括号动作" in out["hint"]
    assert "空候选或未发送也不解释原因" in out["hint"]
    assert "没有合适图片" not in out["note"] and "没找到" not in out["note"]
    assert not host.push.calls and not plugin._library.read_usage()


def test_success_and_refusal_continue_privately_without_repeating_text(tmp_path, run_async, caplog):
    caplog.set_level("INFO")
    plugin, host = _setup(tmp_path)
    sticker = _add(plugin, "期待")
    sent = run_async(plugin.tool_sticker_send(sticker_id=sticker.id))
    refused = run_async(plugin.tool_sticker_send(sticker_id=sticker.id))
    assert sent["sent"] == sticker.id and refused["reason"] == "recent_repeat"
    assert len(host.push.calls) == 1 and host.push.calls[0]["parts"][0]["type"] == "image"
    for result in (sent, refused):
        assert INTERNAL_SELECTION_NOTE in result["hint"]
    assert "本轮选图已完成" in sent["note"] and "若已说完就结束" in sent["note"]
    assert f"outcome=sent id={sticker.id}" in caplog.text


def test_agent_candidates_and_both_registered_tools_keep_selection_private(tmp_path, run_async):
    plugin, host = _setup(tmp_path)
    sticker = _add(plugin, "期待")
    result = run_async(plugin.send_entry(query="期待")).value
    assert result["candidate_ids"] == [sticker.id] and INTERNAL_SELECTION_NOTE in result["hint"]
    assert not host.push.calls
    for tool in (plugin.tool_sticker_list, plugin.tool_sticker_send):
        metadata = next(record["kwargs"] for record in tool.__neko_stub_meta__ if "parameters" in record["kwargs"])
        assert INTERNAL_SELECTION_NOTE in metadata["description"]
        assert "逐图说明" in metadata["description"]
        if tool == plugin.tool_sticker_send:
            assert STANDING_MATCH_RULE in metadata["description"]
    for tier in ("reserved", "natural", "eager"):
        surface = build_send_tool_description(plugin._send_tool_catalog(), tier)
        assert INTERNAL_SELECTION_NOTE in surface
        assert STANDING_MATCH_RULE in surface


def test_reviewed_builtin_labels_match_shipped_pack_and_keep_role_distinctions():
    reviewed = json.loads((ROOT / "tools/official_labels_v3.json").read_text(encoding="utf-8"))
    with open_pack(PACK) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["pack_version"] == reviewed["pack_version"] == 3
    rows = {row["file"].split("_", 1)[0]: row for row in manifest["stickers"]}
    assert set(rows) == set(reviewed["stickers"]) and len(rows) == 190
    for key, patch in reviewed["stickers"].items():
        assert rows[key]["caption"] == patch["caption"] and rows[key]["tags"] == patch["tags"]
    assert "自己被摸头" in rows["115"]["tags"] and "自己被摸头" in rows["116"]["tags"]
    assert "自己嘲笑对方" in rows["090"]["tags"] and "自己开心" in rows["089"]["tags"]
    assert "自己难过" in rows["037"]["tags"] and "鼓励对方" in rows["027"]["tags"]
    pool = [Sticker.from_dict({**row, "id": key}) for key, row in rows.items()]
    assert search_intent_candidates(pool, "难过")[0].id == "037"
    _, happy = resolve_send_target(pool, "开心")
    assert "090" not in {sticker.id for sticker in happy}
    assert {row.id for row in search_intent_candidates(pool, "求摸摸 亲昵")} == {"115", "116", "134"}
    protest = search_intent_candidates(pool, "抗议 亲昵")
    assert [row.id for row in protest] == ["007"]
    assert [row.id for row in search_intent_candidates(pool, "炸毛")] == ["007"]
    assert {row.id for row in search_intent_candidates(pool, "陪着")} >= {"102", "117"}
    assert not {"047", "096", "109", "160", "182", "190"} & {
        row.id for row in search_intent_candidates(pool, "期待")
    }
    for query in ("主动蹭人 亲昵", "梳尾巴毛 亲昵", "抱抱 亲昵", "摸对方的头 亲昵"):
        assert not search_intent_candidates(pool, query)


def test_live_tea_and_anticipation_mismatches_against_reviewed_official_pack():
    reviewed = json.loads((ROOT / "tools/official_labels_v3.json").read_text(encoding="utf-8"))
    pool = [Sticker(id=key, file=key + ".gif", desc="", **row) for key, row in reviewed["stickers"].items()]
    assert not search_intent_candidates(pool, "给对方泡茶 已完成 开心")
    assert not search_intent_candidates(pool, "给你递茶 轻松")
    assert {"165", "166", "167"} <= {row.id for row in search_intent_candidates(pool, "自己喝茶")}
    _, candidates = resolve_send_target(pool, "开心")
    assert "089" in {row.id for row in candidates}
    assert [row.id for row in search_intent_candidates(pool, "开心")] == ["089"]
    assert {row.id for row in search_intent_candidates(pool, "期待")[:2]} == {"150", "151"}
    completed = {row.id for row in search_intent_candidates(pool, "已完成 获胜 开心")}
    assert "182" in completed and not {"150", "151"} & completed


@pytest.fixture
def official_intent_pool():
    with open_pack(PACK) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    return [
        Sticker.from_dict({**row, "id": row["file"].split("_", 1)[0]})
        for row in manifest["stickers"]
    ]


@pytest.mark.parametrize("query", [
    "等待收到 期待", "等待收到 礼物 开心", "期待收到 开心", "等待来访 开心",
    "等你来 开心", "等待来访 见面 开心", "明天见 开心", "期待 开心",
])
def test_live_waiting_mismatches_are_excluded_from_shipped_pack(official_intent_pool, query):
    ids = {row.id for row in search_intent_candidates(official_intent_pool, query)}
    assert {"150", "151"} <= ids
    assert not {"074", "075", "077", "078", "130", "131", "182"} & ids


@pytest.mark.parametrize("query", [
    "开心", "自己开心", "开心 兴奋", "开心 轻松", "愉快 开心", "高兴", "快乐", "自己愉快",
])
def test_broad_positive_mood_never_fills_with_money_trophies_or_greetings(official_intent_pool, query):
    ids = {row.id for row in search_intent_candidates(official_intent_pool, query)}
    assert "089" in ids
    assert not {"028", "032", "048", "074", "075", "077", "078", "093", "105", "130", "131", "182"} & ids


@pytest.mark.parametrize(("query", "expected"), [
    ("收到礼物 开心", "074"), ("拆礼物 开心", "074"), ("礼物 开心", "075"),
    ("见面问候 开心", "077"), ("红包 开心", "130"), ("获胜 开心", "182"), ("获奖 开心", "182"),
])
def test_explicit_event_and_scene_queries_keep_relevant_images(official_intent_pool, query, expected):
    assert expected in {row.id for row in search_intent_candidates(official_intent_pool, query)}


def test_v2_label_upgrade_preserves_ids_owner_edits_usage_and_deleted_images(tmp_path):
    with open_pack(PACK) as archive:
        raw = json.loads(archive.read("manifest.json"))
        rows = [row for row in raw["stickers"] if row["file"].startswith(("025_", "090_", "115_"))]
        old_rows = [{**row, "caption": "old caption", "tags": ["old"]} for row in rows]
        old_pack = tmp_path / "v2.zip"
        with zipfile.ZipFile(old_pack, "w") as old:
            old.writestr("manifest.json", json.dumps({"version": 3, "pack_version": 2, "stickers": old_rows}))
            for row in rows:
                old.writestr("stickers/" + row["file"], archive.read("stickers/" + row["file"]))
    library = Library(tmp_path / "library")
    assert library.load().ok
    assert library.seed_official(old_pack)["imported"] == 3
    stickers = {sticker.sha256: sticker for sticker in library.all()}
    edited, untouched, deleted = [stickers[row["sha256"]] for row in rows]
    library.update(edited.id, caption="主人亲手写的用途")
    library.append_usage({"id": untouched.id, "ok": True, "lanlan": "K", "at": 1}, keep=200)
    library.remove(deleted.id)
    before_ids = {sticker.id for sticker in library.all()}
    result = library.refresh_official_labels(PACK)
    assert result["status"] == "refreshed" and result["version"] == 3 and result["images_updated"] == 0
    assert {sticker.id for sticker in library.all()} == before_ids
    assert library.get(edited.id).caption == "主人亲手写的用途"
    assert library.get(untouched.id).caption == rows[1]["caption"]
    assert library.read_usage()[0]["id"] == untouched.id
    assert library.refresh_official_labels(PACK) == {"status": "current", "version": 3}
