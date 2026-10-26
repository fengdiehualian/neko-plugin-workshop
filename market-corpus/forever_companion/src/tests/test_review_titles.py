"""我的日记标题：一次成文、旧回复兼容、坏回复保留素材和归档重启。"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest
from forever_companion.core.review import (
    build_review_prompt,
    parse_review_document,
    parse_review_response,
    review_record,
)
from forever_companion.core.state import _REVIEW_ENTRY_MAX_CHARS, _REVIEW_MAX_ENTRIES


@pytest.mark.parametrize("wrapper", ["plain", "fenced", "explained"])
def test_structured_review_keeps_title_and_complete_body(wrapper):
    body = '好的，今天是一段平静的相处。\n\n他提到「{那件小事}」，她认真听完。'
    raw = json.dumps({"title": "  晚风\n 和你\t ", "text": body}, ensure_ascii=False)
    if wrapper == "fenced":
        raw = f"```json\n{raw}\n```"
    elif wrapper == "explained":
        raw = f"以下是日记：\n{raw}\n完成。"
    assert parse_review_document(raw) == {"title": "晚风 和你", "text": body}
    assert parse_review_response(raw) == body


@pytest.mark.parametrize("raw", [
    None, "", '{"title":"只有标题"}', '{"title":"题","text":""}',
    '{"title":"题","text":" \n "}', '{"title":"题","text":123}', '{"text":[]}',
    '{"text":{}}', '{"title":"题","text":"正文"', "以下是日记：\n" + '{"title":"坏回复"',
    '[{"title":"题","text":"正文"}]', '```json\n[{"title":"题","text":"正文"}]\n```',
    '```json\n{"title":"题","text":"正文"}', '{"unknown":"正文"}',
])
def test_incomplete_or_title_only_review_cannot_consume_material(raw):
    assert parse_review_document(raw) == {}
    assert parse_review_response(raw) == ""


@pytest.mark.parametrize("title", [None, 123, {}, [], " \n\t "])
def test_missing_or_invalid_title_keeps_usable_body(title):
    assert parse_review_document(json.dumps({"title": title, "text": "正文"})) == {"title": "", "text": "正文"}


def test_legacy_plain_body_still_works_without_inventing_a_title():
    assert parse_review_document("「他最近很温柔。」") == {"title": "", "text": "他最近很温柔。"}
    assert parse_review_document("好的，以下是这篇评价：\n正文") == {"title": "", "text": "正文"}
    assert parse_review_document('他分享了一个对象 {"name":"小事"}，她认真听着。')["text"].startswith("他分享")


def test_review_title_and_body_limits_do_not_change_the_stats_snapshot():
    stats = {"turns": 60, "tone": {"happy": 8}, "fragments": [{"kind": "like", "quote": "他分享的原话"}]}
    before = deepcopy(stats)
    document = parse_review_document(json.dumps({"title": "题" * 80, "text": "文" * (_REVIEW_ENTRY_MAX_CHARS + 100)}))
    record = review_record("2026-10-13", stats, document["text"], title=document["title"])
    assert record["title"] == "题" * 40
    assert record["text"] == "文" * _REVIEW_ENTRY_MAX_CHARS
    assert record["quotes"][0]["quote"] == "他分享的原话"
    assert stats == before
    assert "title" not in review_record("old", {}, "旧正文")


def test_prompt_requests_title_and_body_together_without_changing_observer_role():
    prompt = build_review_prompt({"turns": 60})
    assert "JSON" in prompt and "title" in prompt and "text" in prompt and "40 字" in prompt
    assert "第三人称观察者" in prompt and "不粉饰" in prompt and "共 60 轮对话" in prompt
    assert "\\n 转义" in prompt
    assert "不要重复附加记录日期" in prompt and "不要只用日期作标题" in prompt


def test_single_compose_saves_title_and_archive_survives_restart(plugin_factory_full, monkeypatch):
    plugin = plugin_factory_full(current_lanlan="灵")
    shard = asyncio.run(plugin._ensure_shard("灵"))
    shard.review = [
        review_record(f"2026-{8 + index // 28:02d}-{index % 28 + 1:02d}", {}, f"旧正文{index}", title=f"旧标题{index}")
        for index in range(_REVIEW_MAX_ENTRIES)
    ]
    shard.review_stats = {"turns": 60}
    prompts = []
    monkeypatch.setattr(plugin, "_resolve_tone_slot", lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"})

    def compose(*args, **kwargs):
        prompts.append(args[3])
        return json.dumps({"title": "平静相处的一周", "text": "他分享了日常，她也认真回应。\n\n这段相处很安静。"})

    monkeypatch.setattr(plugin, "_post_chat_completion", compose)
    assert asyncio.run(plugin._maybe_write_review("灵", shard)) == (True, "written")
    assert len(prompts) == 1 and "title" in prompts[0] and "text" in prompts[0]
    assert shard.review[-1]["title"] == "平静相处的一周"
    assert shard.review_archive[0]["title"] == "旧标题0"
    assert plugin.store.data["review@灵"]["entries"][-1]["title"] == "平静相处的一周"
    assert plugin.store.data["review@灵"]["stats"]["turns"] == 0
    assert plugin._pushed == []

    restarted = plugin_factory_full(current_lanlan="灵", store_initial=plugin.store.data)
    current = asyncio.run(restarted.get_review()).value["entries"]
    assert current[0]["title"] == "平静相处的一周"
    assert current[0]["text"].endswith("这段相处很安静。")
    for reader in (restarted.get_review(scope="archive"), restarted.get_review_archive()):
        archived = asyncio.run(reader).value["entries"]
        assert archived[0]["title"] == "旧标题0" and archived[0]["text"] == "旧正文0"
    dashboard = asyncio.run(restarted.dashboard())
    assert dashboard["review_brief"]["last_ts"] == current[0]["ts"]
    assert dashboard["review_brief"]["entries"] == _REVIEW_MAX_ENTRIES


@pytest.mark.parametrize("reply", ['{"title":"只有题目"}', '{"title":"被截断","text":"正文"'])
def test_failed_structured_compose_preserves_diaries_and_material(plugin_factory, monkeypatch, reply):
    plugin = plugin_factory()
    shard = plugin._get_shard("default")
    shard.review = [review_record("old", {}, "旧正文", title="旧标题")]
    shard.review_stats = {"turns": 60}
    before = deepcopy((shard.review, shard.review_stats, shard.review_archive))
    monkeypatch.setattr(plugin, "_resolve_tone_slot", lambda cfg, slot: {"base_url": "http://x", "api_key": "k", "model": "m"})
    monkeypatch.setattr(plugin, "_post_chat_completion", lambda *args, **kwargs: reply)
    assert asyncio.run(plugin._maybe_write_review("default", shard)) == (False, "compose_failed")
    assert (shard.review, shard.review_stats, shard.review_archive) == before
    assert "review@default" not in plugin.store.data
