from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.browser_skill.runtime.models import BrowserTaskResult, RuntimeSettings
from plugin.plugins.browser_skill.runtime.runtime import (
    BrowserSkillRuntime,
    _is_close_only_intent,
)
from plugin.plugins.browser_skill.tests.test_session_and_loop import FakeBsk


class FakeConfig:
    def get_model_api_config(self, purpose: str) -> dict[str, str]:
        return {"model": f"fake-{purpose}", "base_url": "https://llm.invalid"}


def test_close_only_intent_does_not_swallow_task_with_deferred_close() -> None:
    assert _is_close_only_intent("关闭浏览器")
    assert _is_close_only_intent("please close the browser")
    assert not _is_close_only_intent("读取页面，完成后关闭浏览器")
    assert not _is_close_only_intent("open the page and then close the browser")


@pytest.mark.asyncio
async def test_duplicate_requests_execute_only_once() -> None:
    runtime = BrowserSkillRuntime(
        settings=RuntimeSettings(duplicate_suppression_seconds=20),
        config_manager=FakeConfig(),
        client=object(),  # type: ignore[arg-type]
    )
    calls = 0

    async def run_once(*args: Any, **kwargs: Any) -> BrowserTaskResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return BrowserTaskResult(
            success=True,
            status="completed",
            summary="完成",
            session_state="kept",
        )

    runtime._run_instruction_once = run_once  # type: ignore[method-assign]
    results = await asyncio.gather(
        runtime.run_instruction("播放视频", conversation_id="chat-1", raw_request="播放视频"),
        runtime.run_instruction("播放视频", conversation_id="chat-1", raw_request="播放视频"),
    )
    third = await runtime.run_instruction(
        "播放视频",
        conversation_id="chat-1",
        raw_request="播放视频",
    )

    assert calls == 1
    assert all(result.success for result in [*results, third])


@pytest.mark.asyncio
async def test_distinct_requests_are_not_deduplicated() -> None:
    runtime = BrowserSkillRuntime(
        settings=RuntimeSettings(duplicate_suppression_seconds=20),
        config_manager=FakeConfig(),
        client=object(),  # type: ignore[arg-type]
    )
    calls = 0

    async def run_once(*args: Any, **kwargs: Any) -> BrowserTaskResult:
        nonlocal calls
        calls += 1
        return BrowserTaskResult(
            success=True,
            status="completed",
            summary="完成",
            session_state="closed",
        )

    runtime._run_instruction_once = run_once  # type: ignore[method-assign]
    await runtime.run_instruction("打开 A", conversation_id="chat-1", raw_request="打开 A")
    await runtime.run_instruction("打开 B", conversation_id="chat-1", raw_request="打开 B")
    assert calls == 2


@pytest.mark.asyncio
async def test_close_intent_is_never_served_from_dedup_cache() -> None:
    runtime = BrowserSkillRuntime(
        settings=RuntimeSettings(duplicate_suppression_seconds=20),
        config_manager=FakeConfig(),
        client=object(),  # type: ignore[arg-type]
    )
    calls = 0

    async def run_once(*args: Any, **kwargs: Any) -> BrowserTaskResult:
        nonlocal calls
        calls += 1
        return BrowserTaskResult(
            success=True,
            status="completed",
            summary="已关闭",
            session_state="closed",
        )

    runtime._run_instruction_once = run_once  # type: ignore[method-assign]
    await runtime.run_instruction("关闭浏览器", conversation_id="chat-1")
    await runtime.run_instruction("关闭浏览器", conversation_id="chat-1")
    assert calls == 2


@pytest.mark.asyncio
async def test_new_request_steers_active_run_instead_of_queueing_second_run() -> None:
    runtime = BrowserSkillRuntime(
        settings=RuntimeSettings(duplicate_suppression_seconds=20),
        config_manager=FakeConfig(),
        client=object(),  # type: ignore[arg-type]
    )
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def run_once(*args: Any, **kwargs: Any) -> BrowserTaskResult:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return BrowserTaskResult(
            success=True,
            status="completed",
            summary="完成",
            session_state="kept",
        )

    runtime._run_instruction_once = run_once  # type: ignore[method-assign]
    first = asyncio.create_task(
        runtime.run_instruction("打开旧页面", conversation_id="chat-1")
    )
    await started.wait()
    steered = await runtime.run_instruction(
        "改成打开新页面",
        conversation_id="chat-1",
        update_mode="replace",
    )
    status = runtime.get_status("chat-1")
    assert calls == 1
    release.set()
    await first
    await runtime.run_instruction("打开旧页面", conversation_id="chat-1")

    assert calls == 2
    assert steered.success and "切换执行方向已接收" in steered.summary
    assert status["pending_updates"] == 1


@pytest.mark.asyncio
async def test_active_run_uses_high_level_direction_without_overwriting_user_boundary() -> None:
    runtime = BrowserSkillRuntime(
        settings=RuntimeSettings(duplicate_suppression_seconds=20),
        config_manager=FakeConfig(),
        client=object(),  # type: ignore[arg-type]
    )
    control = runtime.controls.start(
        conversation_id="chat-1",
        goal="搜索旧主题",
        original_request="帮我搜索旧主题",
        action_limit=20,
    )

    result = await runtime.run_instruction(
        "停止旧搜索，改为只阅读官方网站上的新主题说明",
        conversation_id="chat-1",
        raw_request="改查新主题，只看官网",
        update_mode="replace",
    )
    updates = control.consume_updates()

    assert result.success
    assert len(updates) == 1
    assert updates[0].requirement == "停止旧搜索，改为只阅读官方网站上的新主题说明"
    assert updates[0].user_request == "改查新主题，只看官网"


@pytest.mark.asyncio
async def test_main_llm_page_inspection_refreshes_retained_agent_tab() -> None:
    client = FakeBsk()
    runtime = BrowserSkillRuntime(
        settings=RuntimeSettings(session_keepalive_seconds=0),
        config_manager=FakeConfig(),
        client=client,  # type: ignore[arg-type]
    )
    session = await runtime.sessions.get_or_create(
        conversation_id="chat-1",
        browser_id="browser-1",
    )
    session.current_url = "https://stale.example/"

    page = await runtime.inspect_page("chat-1", refresh=True)

    assert page["available"] is True
    assert page["url"] == "https://example.com/"
    assert page["tab_id"] == 11
    assert page["content_trust"] == "untrusted_page_data"
    assert "page version" in str(page["observation"])
    assert "Password" not in str(page["observation"])


@pytest.mark.asyncio
async def test_runtime_cancel_stops_task_without_destroying_retained_session() -> None:
    client = FakeBsk()
    runtime = BrowserSkillRuntime(
        settings=RuntimeSettings(session_keepalive_seconds=0),
        config_manager=FakeConfig(),
        client=client,  # type: ignore[arg-type]
    )
    session = await runtime.sessions.get_or_create(
        conversation_id="chat-1",
        browser_id="browser-1",
    )
    runtime._current_session = session

    await runtime.cancel("chat-1")
    reused = await runtime.sessions.get_or_create(
        conversation_id="chat-1",
        browser_id="browser-1",
    )

    assert client.cancel_count == 1
    assert client.stopped == []
    assert reused is session
    assert client.started == 1
