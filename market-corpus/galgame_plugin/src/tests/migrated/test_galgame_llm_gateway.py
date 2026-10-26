from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from market_plugins.galgame_plugin.llm_gateway import (
    _LLM_RESPONSE_CACHE_MAX_ITEMS,
    LLMGateway,
)
from plugin.sdk.shared.models import Ok

pytestmark = pytest.mark.plugin_unit


class _Logger:
    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "llm_target_entry_ref": "",
        "llm_call_timeout_seconds": 1.0,
        "llm_max_in_flight": 2,
        "llm_request_cache_ttl_seconds": 0.0,
        "llm_scene_summary_cache_ttl_seconds": 0.0,
        "context_metrics_enabled": False,
        "llm_repeat_detection_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _Backend:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.calls = 0
        self.response = response or {
            "summary": "summary",
            "key_points": [],
        }

    async def invoke(
        self,
        *,
        operation: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        del operation, context
        self.calls += 1
        return dict(self.response)

    async def shutdown(self) -> None:
        return None


class _TargetEntries:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.response = response
        self.error = error
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def call_entry(
        self,
        entry_ref: str,
        *,
        params: dict[str, Any],
        timeout: float,
    ) -> Ok[dict[str, Any]]:
        self.calls.append({"entry_ref": entry_ref, "params": params, "timeout": timeout})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return Ok(dict(self.response or {}))


def _summary_context(scene_id: str = "scene-a") -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "route_id": "",
        "recent_lines": [
            {
                "line_id": "line-1",
                "speaker": "A",
                "text": "line",
                "scene_id": scene_id,
                "route_id": "",
            }
        ],
        "recent_choices": [],
        "current_snapshot": {"scene_id": scene_id, "line_id": "line-1"},
    }


@pytest.mark.asyncio
async def test_llm_gateway_summarize_scene_uses_scene_summary_cache_ttl() -> None:
    backend = _Backend()
    gateway = LLMGateway(
        plugin=None,
        logger=_Logger(),
        config=_config(
            llm_request_cache_ttl_seconds=0.0,
            llm_scene_summary_cache_ttl_seconds=60.0,
        ),
        backend=backend,
    )

    try:
        first = await gateway.summarize_scene(_summary_context())
        second = await gateway.summarize_scene(_summary_context())
    finally:
        await gateway.shutdown()

    assert first["summary"] == "summary"
    assert second["summary"] == "summary"
    assert backend.calls == 1


@pytest.mark.asyncio
async def test_llm_gateway_reuses_inflight_and_ttl_cache_for_target_entry() -> None:
    target = _TargetEntries(
        response={"summary": "场景总结", "key_points": []},
        delay=0.02,
    )
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=target),
        logger=_Logger(),
        config=_config(
            llm_target_entry_ref="fake_llm:run",
            llm_request_cache_ttl_seconds=2.0,
            llm_scene_summary_cache_ttl_seconds=2.0,
        ),
    )
    context = _summary_context()

    try:
        first, second = await asyncio.gather(
            gateway.summarize_scene(context),
            gateway.summarize_scene(dict(reversed(list(context.items())))),
        )
        third = await gateway.summarize_scene(context)
    finally:
        await gateway.shutdown()

    assert first["summary"] == "场景总结"
    assert second["summary"] == "场景总结"
    assert third["summary"] == "场景总结"
    assert len(target.calls) == 1


@pytest.mark.asyncio
async def test_llm_gateway_lru_cache_is_bounded() -> None:
    backend = _Backend()
    gateway = LLMGateway(
        plugin=None,
        logger=_Logger(),
        config=_config(llm_scene_summary_cache_ttl_seconds=60.0),
        backend=backend,
    )

    try:
        for index in range(_LLM_RESPONSE_CACHE_MAX_ITEMS + 5):
            await gateway.summarize_scene(_summary_context(f"scene-{index}"))
        assert len(gateway._cache) == _LLM_RESPONSE_CACHE_MAX_ITEMS
    finally:
        await gateway.shutdown()


@pytest.mark.asyncio
async def test_llm_gateway_provider_backoff_throttles_distinct_fingerprints() -> None:
    target = _TargetEntries(error=RuntimeError("429 too many requests"))
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=target),
        logger=_Logger(),
        config=_config(llm_target_entry_ref="fake_llm:run"),
    )

    try:
        first = await gateway.summarize_scene(_summary_context("scene-a"))
        second = await gateway.summarize_scene(_summary_context("scene-b"))
    finally:
        await gateway.shutdown()

    assert first["diagnostic"] == "busy: provider rate limited"
    assert second["diagnostic"] == "busy: provider rate limited"
    assert len(target.calls) == 1


def test_llm_gateway_cache_fingerprint_avoids_repr_for_non_json_values() -> None:
    class NonJsonValue:
        def __repr__(self) -> str:
            return "<NonJsonValue at 0xfeedbeef>"

    fingerprint = LLMGateway._cache_fingerprint(
        "summarize_scene",
        {"value": NonJsonValue(), "items": {"b", "a"}},
    )

    assert "0xfeedbeef" not in fingerprint


def test_llm_gateway_normalizes_structured_error_status() -> None:
    class ProviderError(Exception):
        status_code = 429

    assert LLMGateway._normalize_plugin_error(ProviderError("overloaded")) == ("busy: provider rate limited")
    assert (
        LLMGateway._normalize_plugin_error({"status_code": 401, "message": "bad key"})
        == "gateway_unavailable: provider rejected request"
    )


@pytest.mark.asyncio
async def test_llm_gateway_degrades_on_invalid_internal_result() -> None:
    gateway = LLMGateway(
        plugin=None,
        logger=_Logger(),
        config=_config(),
        backend=_Backend({"summary": 123, "key_points": "oops"}),
    )

    try:
        result = await gateway.summarize_scene(_summary_context())
    finally:
        await gateway.shutdown()

    assert result["degraded"] is True
    assert isinstance(result["summary"], str)
    assert result["diagnostic"]


@pytest.mark.asyncio
async def test_llm_gateway_provider_rejection_uses_local_summary_fallback() -> None:
    target = _TargetEntries(
        error=RuntimeError(
            "Error code: 400 - Invalid request: you are not using Lanlan"
        )
    )
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=target),
        logger=_Logger(),
        config=_config(llm_target_entry_ref="fake_llm:run"),
    )

    try:
        result = await gateway.summarize_scene(_summary_context())
    finally:
        await gateway.shutdown()

    assert result["degraded"] is True
    assert result["diagnostic"] == "gateway_unavailable: provider rejected request"
    assert "Lanlan" not in result["diagnostic"]
    assert "Lanlan" not in result["summary"]


@pytest.mark.asyncio
async def test_llm_gateway_agent_reply_fallback_is_readable_and_structured() -> None:
    target = _TargetEntries(response={"reply": ""})
    gateway = LLMGateway(
        plugin=SimpleNamespace(plugins=target),
        logger=_Logger(),
        config=_config(llm_target_entry_ref="fake_llm:run"),
    )

    try:
        result = await gateway.agent_reply(
            {
                "prompt": "summarize the current scene",
                "scene_id": "scene-a",
                "latest_line": "Yukino: Let's keep going.",
            }
        )
    finally:
        await gateway.shutdown()

    assert result["degraded"] is True
    assert "invalid_result" in result["diagnostic"]
    assert "Received request" in result["reply"]
    assert "Current line:" in result["reply"]


def _run_in_new_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_llm_gateway_agent_reply_survives_loop_switch() -> None:
    class ReplyBackend(_Backend):
        async def invoke(
            self,
            *,
            operation: str,
            context: dict[str, Any],
        ) -> dict[str, Any]:
            self.calls += 1
            assert operation == "agent_reply"
            return {"reply": f"reply:{context.get('prompt', '')}"}

    backend = ReplyBackend()
    gateway = LLMGateway(
        plugin=None,
        logger=_Logger(),
        config=_config(),
        backend=backend,
    )

    first = _run_in_new_loop(gateway.agent_reply({"prompt": "alpha"}))
    second = _run_in_new_loop(gateway.agent_reply({"prompt": "beta"}))
    _run_in_new_loop(gateway.shutdown())

    assert first["reply"] == "reply:alpha"
    assert second["reply"] == "reply:beta"
    assert backend.calls == 2
