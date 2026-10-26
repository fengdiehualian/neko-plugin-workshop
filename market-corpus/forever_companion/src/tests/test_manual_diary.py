"""Manual writing must create real entries and preserve material on failure."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from types import SimpleNamespace

import pytest
from forever_companion.core.journal import build_journal_prompt, journal_materials
from forever_companion.core.state import _JOURNAL_MAX_PAGES, _MANUAL_DIARY_ATTEMPT_SEC, _channel_route
from forever_companion.mixins import senses

REPLY = json.dumps({"title": "A quiet evening", "text": "【这段时间】一起聊了今天的小事。\n【我在想】这段相处很安静。"})


def run(coro):
    return asyncio.run(coro)


def prepare(plugin_factory_full, monkeypatch, *, lanlan="灵", replies=None):
    plugin = plugin_factory_full(current_lanlan=lanlan)
    shard = run(plugin._ensure_shard(lanlan))
    shard.review_stats = {"turns": 20}
    calls = []
    responses = iter(replies or [REPLY])
    monkeypatch.setattr(plugin, "_resolve_tone_slot", lambda *_: {"model": "m", "base_url": "http://test", "api_key": "k"})

    async def recent(*_, **__):
        return "今天分享了一件小事", "我听着呢，慢慢说。"

    async def chat(resolved, prompt, **kwargs):
        calls.append((prompt, kwargs))
        return next(responses)

    monkeypatch.setattr(plugin, "_poll_recent_turns", recent)
    monkeypatch.setattr(plugin, "_channel_chat", chat)
    return plugin, shard, calls


def test_personal_click_always_queues_real_writing_without_push_or_random_choice(plugin_factory_full, monkeypatch):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    assert run(plugin.invite_journal()).value["accepted"] is True
    assert not calls and not plugin._pushed
    assert run(plugin.invite_journal()).value["reason"] == "already_writing"
    assert run(plugin.dashboard())["journal_write"]["writing"] is True
    run(plugin._drain_pending_journal_writes())
    assert len(calls) == 1 and "第一人称" in calls[0][0]
    assert "随机决定" in calls[0][0]
    assert len(shard.journal) == 1 and shard.journal[0]["title"] == "A quiet evening"
    assert plugin.store.data["journal@灵"] == shard.journal
    assert shard.journal_write_result["written"] is True
    assert shard.review_stats["turns"] == 20 and not plugin._pushed
    assert run(plugin.dashboard())["journal_write"]["writing"] is False


@pytest.mark.parametrize("entry", ["journal", "review"])
@pytest.mark.parametrize("bad", [None, "", '{"title":"missing body"}'])
def test_manual_retry_writes_exactly_one_entry(plugin_factory_full, monkeypatch, entry, bad):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch, replies=[bad, REPLY])
    action = plugin.invite_journal if entry == "journal" else plugin.write_review_now
    drain = plugin._drain_pending_journal_writes if entry == "journal" else plugin._drain_pending_review_writes
    assert run(action()).value["accepted"] is True
    run(drain())
    assert len(calls) == 2
    assert len(shard.journal if entry == "journal" else shard.review) == 1
    result = shard.journal_write_result if entry == "journal" else shard.review_write_result
    assert result["written"] is True
    assert 0 < calls[1][1]["timeout_sec"] <= calls[0][1]["timeout_sec"] <= _MANUAL_DIARY_ATTEMPT_SEC


@pytest.mark.parametrize("entry", ["journal", "review"])
@pytest.mark.parametrize(("reply", "reason"), [(None, "request_failed"), ('{"title":"only"}', "invalid_reply")])
def test_failed_writing_preserves_all_material_and_returns_specific_reason(plugin_factory_full, monkeypatch, entry, reply, reason):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch, replies=[reply, reply])
    before = copy.deepcopy((shard.journal, shard.review, shard.review_stats, shard.diary))
    assert run((plugin.invite_journal if entry == "journal" else plugin.write_review_now)()).value["accepted"] is True
    run((plugin._drain_pending_journal_writes if entry == "journal" else plugin._drain_pending_review_writes)())
    assert len(calls) == 2
    assert (shard.journal, shard.review, shard.review_stats, shard.diary) == before
    result = shard.journal_write_result if entry == "journal" else shard.review_write_result
    assert result["reason"] == reason and result["written"] is False


def test_personal_generates_new_page_and_keeps_other_roles_and_previous_titles(plugin_factory_full, monkeypatch):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    shard.journal = [{"page_no": 7, "title": "Previous", "entries": [{"ts": "2026-10-01", "text": "old"}]}]
    other = run(plugin._ensure_shard("another"))
    other.journal = [{"page_no": 1, "entries": [{"text": "private"}]}]
    before = copy.deepcopy(other.journal)
    run(plugin.invite_journal())
    plugin.ctx._current_lanlan = "another"
    run(plugin._drain_pending_journal_writes())
    assert shard.journal[-1]["page_no"] == 8
    assert shard.journal[0]["title"] == "Previous"
    assert other.journal == before and len(calls) == 1


def test_personal_archive_and_restart_keep_generated_entry(plugin_factory_full, monkeypatch):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)
    shard.journal = [{"page_no": i + 1, "entries": [{"ts": "2026-09-01", "text": str(i)}]} for i in range(_JOURNAL_MAX_PAGES)]
    run(plugin.invite_journal())
    run(plugin._drain_pending_journal_writes())
    assert shard.journal_archive[0]["page_no"] == 1
    restarted = plugin_factory_full(current_lanlan="灵", store_initial=plugin.store.data)
    restored = run(restarted.get_journal()).value["pages"]
    assert restored[-1]["title"] == "A quiet evening"
    assert run(restarted.get_journal(scope="archive")).value["pages"][0]["page_no"] == 1


def test_personal_store_failure_rolls_back_page_and_archive(plugin_factory_full, monkeypatch, tm):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)
    before = copy.deepcopy((shard.journal, shard.journal_archive, shard.review_stats))
    original = plugin.store.set

    async def fail(key, value):
        return tm.Err(tm.SdkError("disk_full")) if key == "journal@灵" else await original(key, value)

    monkeypatch.setattr(plugin.store, "set", fail)
    run(plugin.invite_journal())
    run(plugin._drain_pending_journal_writes())
    assert (shard.journal, shard.journal_archive, shard.review_stats) == before
    assert shard.journal_write_result["reason"] == "persist_failed"


def test_personal_switch_rechecked_after_acceptance(plugin_factory_full, monkeypatch):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    assert run(plugin.invite_journal()).value["accepted"]
    plugin._journal_cfg["enabled"] = False
    run(plugin._drain_pending_journal_writes())
    assert not calls and not shard.journal
    assert shard.journal_write_result["reason"] == "disabled"


@pytest.mark.parametrize("entry", ["journal", "review"])
def test_budget_exhaustion_rejected_at_click_before_queue(plugin_factory_full, monkeypatch, entry):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    plugin._review_cfg["slot"] = "agent"
    plugin._agent_tier_cfg["daily_budget"] = 5
    shard.cycle.update(agent_budget_date=str(plugin._today_str()), agent_budget_used=5)
    result = run((plugin.invite_journal if entry == "journal" else plugin.write_review_now)()).value
    assert result["accepted"] is False
    assert result.get("dormant_reason") == "budget" or result["reason"] == "budget_exhausted"
    assert not calls and not shard.pending_review_write and shard.pending_journal_write is None


def test_retry_cannot_spend_beyond_remaining_budget(plugin_factory_full, monkeypatch):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch, replies=[None])
    plugin._review_cfg["slot"] = "agent"
    plugin._agent_tier_cfg["daily_budget"] = 5
    shard.cycle.update(agent_budget_date=str(plugin._today_str()), agent_budget_used=4)
    run(plugin.invite_journal())
    run(plugin._drain_pending_journal_writes())
    assert len(calls) == 1 and shard.cycle["agent_budget_used"] == 5
    assert shard.journal_write_result["reason"] == "budget_exhausted"


def test_fused_personal_channel_rejected_before_queue(plugin_factory_full, monkeypatch, tm):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    route = _channel_route("agent", {"transport": "direct"})
    plugin._channel_break["灵|review"] = {"route": route, "fails": tm._CHANNEL_BREAK_FAILURES}
    assert run(plugin.invite_journal()).value["reason"] == "channel_rejected"
    assert not calls and shard.pending_journal_write is None


def test_personal_writing_works_with_review_disabled(plugin_factory_full, monkeypatch):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)
    plugin._review_cfg["enabled"] = False
    assert run(plugin.invite_journal()).value["accepted"]
    run(plugin._drain_pending_journal_writes())
    assert shard.journal_write_result["written"] is True


def test_personal_uses_new_notes_when_recent_conversation_unavailable(plugin_factory_full, monkeypatch):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)

    async def unavailable(*_, **__):
        return None

    monkeypatch.setattr(plugin, "_poll_recent_turns", unavailable)
    shard.diary = [{"ts": "2026-10-13", "entry": "记下今天分享的事情", "mood": "happy"}]
    assert run(plugin.invite_journal()).value["accepted"]
    run(plugin._drain_pending_journal_writes())
    assert len(shard.journal) == 1


def test_personal_uses_only_target_character_persona(plugin_factory_full, monkeypatch):
    plugin, _, calls = prepare(plugin_factory_full, monkeypatch)

    async def characters(method, path, **kwargs):
        assert path == "/api/characters" and kwargs["headers"]["Accept-Language"] == "zh-CN"
        return {"猫娘": {"灵": {"system_prompt": "轻快的说话方式"}, "other": {"system_prompt": "DO_NOT_INCLUDE"}}}

    monkeypatch.setattr(plugin, "_proactive_http", characters)
    run(plugin.invite_journal())
    run(plugin._drain_pending_journal_writes())
    assert "轻快的说话方式" in calls[0][0] and "DO_NOT_INCLUDE" not in calls[0][0]


def test_duplicate_requests_after_await_cannot_queue_twice(plugin_factory_full, monkeypatch):
    plugin, _, _ = prepare(plugin_factory_full, monkeypatch)

    async def scenario():
        results = await asyncio.gather(plugin.invite_journal(), plugin.invite_journal())
        assert sorted(result.value["accepted"] for result in results) == [False, True]

    run(scenario())


def test_personal_exception_has_visible_result_and_does_not_log_contents(plugin_factory_full, monkeypatch):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)
    lines = []
    plugin.logger.warning = lambda fmt, *args: lines.append(fmt.format(*args))

    async def explode(*_, **__):
        raise RuntimeError("SECRET_CONVERSATION")

    monkeypatch.setattr(plugin, "_channel_chat", explode)
    run(plugin.invite_journal())
    run(plugin._drain_pending_journal_writes())
    assert shard.journal_write_result["reason"] == "compose_raised"
    assert "SECRET_CONVERSATION" not in "\n".join(lines)
    assert not plugin._journal_writing


def test_journal_material_and_prompt_are_bounded_without_mutating_sources():
    pages = [{"entries": [{"ts": "2026-10-10", "text": "previous"}]}]
    diary = [
        {"ts": "2026-10-09", "entry": "old"},
        {"ts": "2026-10-12", "source": "auto", "quote": "new" * 200},
        {"ts": "2026-10-12", "entry": "latest"},
    ]
    before = copy.deepcopy((pages, diary))
    material = journal_materials(pages, diary, [("hello", "reply"), ("", "unpaired")])
    assert len(material["items"]) == 2 and len(material["items"][0]["text"]) == 300
    assert material["sample_turns"] == [("hello", "reply")]
    assert (pages, diary) == before
    prompt = build_journal_prompt(material, "灵")
    assert "不再询问" in prompt
    assert "不要重复附加记录日期" in prompt and "不要只用日期作标题" in prompt


def test_pending_manual_journal_consumed_by_own_timer_without_new_user_message(plugin_factory_full, monkeypatch):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)
    run(plugin.invite_journal())
    shard.last_journal_invite_ts = time.time()
    run(plugin.diary_write_tick())
    assert shard.journal_write_result["written"] is True
    assert len(shard.journal) == 1


@pytest.mark.parametrize("entry", ["journal", "review"])
@pytest.mark.parametrize(("state", "reason"), [("budget", "budget_exhausted"), ("breaker", "channel_rejected")])
def test_channel_changes_after_acceptance_have_specific_failure(plugin_factory_full, monkeypatch, tm, entry, state, reason):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    plugin._review_cfg["slot"] = "agent"
    action = plugin.invite_journal if entry == "journal" else plugin.write_review_now
    assert run(action()).value["accepted"]
    if state == "budget":
        plugin._agent_tier_cfg["daily_budget"] = 5
        shard.cycle.update(agent_budget_date=str(plugin._today_str()), agent_budget_used=5)
    else:
        plugin._channel_break["灵|review"] = {
            "route": _channel_route("agent", {"transport": "direct"}), "fails": tm._CHANNEL_BREAK_FAILURES,
        }
    run((plugin._drain_pending_journal_writes if entry == "journal" else plugin._drain_pending_review_writes)())
    result = shard.journal_write_result if entry == "journal" else shard.review_write_result
    assert result["reason"] == reason and not result["written"]
    assert not calls and shard.review_stats["turns"] == 20


@pytest.mark.parametrize("entry", ["journal", "review"])
def test_store_not_ready_rejects_writing_without_consuming_material(plugin_factory_full, monkeypatch, entry):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    monkeypatch.setattr(plugin.store, "enabled", False, raising=False)
    result = run((plugin.invite_journal if entry == "journal" else plugin.write_review_now)()).value
    assert not result["accepted"] and result["reason"] == "persist_failed"
    assert not calls and shard.review_stats["turns"] == 20


@pytest.mark.parametrize("entry", ["journal", "review"])
def test_switch_changed_during_model_call_does_not_save_entry(plugin_factory_full, monkeypatch, entry):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)

    async def disable(*_, **__):
        (plugin._journal_cfg if entry == "journal" else plugin._review_cfg)["enabled"] = False
        return REPLY

    monkeypatch.setattr(plugin, "_channel_chat", disable)
    assert run((plugin.invite_journal if entry == "journal" else plugin.write_review_now)()).value["accepted"]
    run((plugin._drain_pending_journal_writes if entry == "journal" else plugin._drain_pending_review_writes)())
    result = shard.journal_write_result if entry == "journal" else shard.review_write_result
    assert result["reason"] == "disabled" and not shard.journal and not shard.review
    assert shard.review_stats["turns"] == 20


def test_pending_and_inflight_personal_writing_block_autonomous_tool_and_invitation(plugin_factory_full, monkeypatch):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)
    assert run(plugin.invite_journal()).value["accepted"]
    result = run(plugin.tool_write_journal(events="自主写作", lanlan="灵"))
    assert result.__class__.__name__ == "Err" and not shard.journal
    assert run(plugin._maybe_journal_invite("灵", shard, force=True)) == (False, "")
    shard.pending_journal_write = None
    plugin._journal_writing.add("灵")
    assert run(plugin.tool_write_journal(events="自主写作", lanlan="灵")).__class__.__name__ == "Err"
    assert run(plugin._maybe_journal_invite("灵", shard)) == (False, "")
    plugin._journal_writing.clear()
    assert run(plugin.tool_write_journal(events="自主写作", lanlan="灵")).value["saved"]
    assert not plugin._journal_writing


def test_same_second_write_resolves_pending_invitation(plugin_factory_full, monkeypatch):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)
    shard.last_journal_invite_ts = time.time()
    assert plugin._journal_invite_pending(shard)
    run(plugin.invite_journal())
    run(plugin._drain_pending_journal_writes())
    assert not plugin._journal_invite_pending(shard)


@pytest.mark.parametrize("entry", ["journal", "review"])
def test_model_timeouts_retry_then_report_failure_without_losing_material(plugin_factory_full, monkeypatch, entry):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)

    async def timed_out(*_, **__):
        calls.append("timeout")
        raise TimeoutError()

    monkeypatch.setattr(plugin, "_channel_chat", timed_out)
    run((plugin.invite_journal if entry == "journal" else plugin.write_review_now)())
    run((plugin._drain_pending_journal_writes if entry == "journal" else plugin._drain_pending_review_writes)())
    result = shard.journal_write_result if entry == "journal" else shard.review_write_result
    assert len(calls) == 2 and result["reason"] == "request_timeout"
    assert not shard.journal and not shard.review and shard.review_stats["turns"] == 20
    assert not plugin._channel_break


@pytest.mark.parametrize("entry", ["journal", "review"])
def test_attempt_timeout_leaves_time_for_a_real_retry(plugin_factory_full, monkeypatch, entry):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    monkeypatch.setattr(senses, "_MANUAL_DIARY_ATTEMPT_SEC", 0.03)
    monkeypatch.setattr(senses, "_MANUAL_DIARY_TOTAL_SEC", 0.3)

    async def slow_first(*_, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            await asyncio.Event().wait()
        return REPLY

    monkeypatch.setattr(plugin, "_channel_chat", slow_first)
    assert run((plugin.invite_journal if entry == "journal" else plugin.write_review_now)()).value["accepted"]
    run(plugin.diary_write_tick())
    result = shard.journal_write_result if entry == "journal" else shard.review_write_result
    assert len(calls) == 2 and result["written"]
    assert all(0 < item["timeout_sec"] <= 0.03 for item in calls)
    assert not plugin._channel_break


def test_total_timeout_bounds_both_attempts_and_reports_elapsed_time(plugin_factory_full, monkeypatch):
    plugin, shard, calls = prepare(plugin_factory_full, monkeypatch)
    monkeypatch.setattr(senses, "_MANUAL_DIARY_ATTEMPT_SEC", 0.1)
    monkeypatch.setattr(senses, "_MANUAL_DIARY_TOTAL_SEC", 0.02)
    clock = SimpleNamespace(now=0.0)
    # Control only the writer's clock, leaving asyncio's event-loop clock intact.
    monkeypatch.setattr(senses, "time", SimpleNamespace(time=time.time, monotonic=lambda: clock.now))
    logs = []
    plugin.logger.warning = lambda fmt, *args: logs.append(fmt.format(*args))

    async def stalled(*_, **kwargs):
        calls.append(kwargs)
        clock.now += kwargs["timeout_sec"]
        raise TimeoutError()

    monkeypatch.setattr(plugin, "_channel_chat", stalled)
    run(plugin.invite_journal())
    run(plugin.diary_write_tick())
    assert len(calls) == 1 and calls[0]["timeout_sec"] <= 0.02
    assert shard.journal_write_result["reason"] == "request_timeout"
    assert any("elapsed_ms=20 " in item and "route=direct|agent" in item for item in logs)
    assert not plugin._journal_writing and not plugin._channel_break


def test_main_tick_remains_available_while_manual_diary_waits(plugin_factory_full, monkeypatch):
    plugin, shard, _ = prepare(plugin_factory_full, monkeypatch)
    shard.review_stats["turns"] = 100
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def waiting(*_, **kwargs):
        calls.append(kwargs)
        started.set()
        await finish.wait()
        return REPLY

    monkeypatch.setattr(plugin, "_channel_chat", waiting)

    async def scenario():
        assert (await plugin.invite_journal()).value["accepted"]
        tick = await asyncio.wait_for(plugin.tick(), 1)
        assert not tick.value["review_written"] and not calls
        writer = asyncio.create_task(plugin.diary_write_tick())
        try:
            await asyncio.wait_for(started.wait(), 1)
            tick = await asyncio.wait_for(plugin.tick(), 1)
            assert not tick.value["review_written"]
            assert shard.journal_write_result is None and len(calls) == 1
        finally:
            finish.set()
            await writer
        assert shard.journal_write_result["written"]
        assert not shard.review and shard.review_stats["turns"] == 100

    run(scenario())
