from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import plugin.plugins.browser_skill.runtime.agent_loop as agent_loop_module
import pytest
from plugin.plugins.browser_skill.runtime.agent_loop import LLMPlanner, LoopFailure
from plugin.plugins.browser_skill.runtime.models import NavigateAction, RuntimeSettings


class Config:
    def get_model_api_config(self, purpose: str) -> dict[str, str]:
        assert purpose == "agent"
        return {
            "model": "fake-agent",
            "base_url": "https://endpoint.invalid/v1",
            "api_key": "test",
            "provider_type": "openai_compatible",
        }


class Response:
    content = '{"action":"navigate","url":"https://example.com"}'
    usage_metadata = {
        "input_tokens": 321,
        "output_tokens": 17,
        "total_tokens": 338,
    }


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages: list[Any] = []

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        self.messages.append(messages)
        if "response_format" in kwargs:
            raise ValueError("unsupported parameter: response_format")
        return Response()

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_structured_output_capability_fallback_is_cached_by_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[FakeLLM] = []

    async def factory(*args: Any, **kwargs: Any) -> FakeLLM:
        llm = FakeLLM()
        llm.factory_kwargs = kwargs  # type: ignore[attr-defined]
        created.append(llm)
        return llm

    monkeypatch.setattr(agent_loop_module, "create_chat_llm_async", factory)
    LLMPlanner._structured_capabilities.clear()
    prompts = Path(__file__).parent.parent / "prompts"

    first = LLMPlanner(
        config_manager=Config(),
        settings=RuntimeSettings(),
        prompts_dir=prompts,
        language="en",
    )
    first_action = await first.decide(
        instruction="Open Example and read only the main heading",
        raw_request="Please open Example",
        observation="Current URL: about:blank",
        history=[],
        verification_required=False,
    )
    await first.close()
    assert first.total_usage == {
        "input_tokens": 321,
        "output_tokens": 17,
        "total_tokens": 338,
        "calls": 1,
        "estimated_calls": 0,
    }

    second = LLMPlanner(
        config_manager=Config(),
        settings=RuntimeSettings(),
        prompts_dir=prompts,
        language="en",
    )
    second_action = await second.decide(
        instruction="Open Example and read only the main heading",
        raw_request="Please open Example",
        observation="Current URL: about:blank",
        history=[],
        verification_required=False,
    )
    await second.close()
    assert second.last_usage["source"] == "provider"
    assert second.last_usage["total_tokens"] == 338

    assert isinstance(first_action, NavigateAction)
    assert isinstance(second_action, NavigateAction)
    assert created[0].factory_kwargs["max_completion_tokens"] == 1200  # type: ignore[attr-defined]
    assert created[0].calls == [{"response_format": {"type": "json_object"}}, {}]
    assert created[1].calls == [{}]
    payload = json.loads(created[0].messages[0][1]["content"])
    assert payload["execution_goal"] == "Open Example and read only the main heading"
    assert payload["latest_user_request"] == "Please open Example"
    assert "only the main model may replace it" in payload["controller_contract"]["authority"]
    assert "equivalent technical routes only" in payload["controller_contract"]["agent_autonomy"]
    assert "do not change an explicitly selected site/search engine" in payload["controller_contract"]["scope"]
    assert "when no site or route is specified" in payload["controller_contract"]["scope"]
    assert "never the task target or outcome" in payload["controller_contract"]["recovery"]
    assert "without paraphrasing" in payload["completion_evidence_contract"]
    assert "visible_evidence_ref" in payload["completion_evidence_contract"]
    assert "original_user_request" not in payload


class SequencedLLM:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.messages: list[Any] = []

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        self.messages.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ModelResponse:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.content = content
        self.response_metadata = {"finish_reason": finish_reason}
        self.usage_metadata = {
            "input_tokens": 10,
            "output_tokens": 10,
            "total_tokens": 20,
        }


def planner_with_llm(llm: Any, **settings: Any) -> LLMPlanner:
    planner = LLMPlanner(
        config_manager=Config(),
        settings=RuntimeSettings(**settings),
        prompts_dir=Path(__file__).parent.parent / "prompts",
        language="en",
    )
    planner._llm = llm
    planner._structured_mode = False
    return planner


@pytest.mark.asyncio
async def test_planner_context_removes_volatile_url_trackers_but_keeps_query() -> None:
    llm = SequencedLLM(ModelResponse('{"action":"navigate","url":"https://example.com"}'))
    planner = planner_with_llm(llm)

    await planner.decide(
        instruction="Search for C++ on Bing",
        raw_request="Search for C++ on Bing",
        observation=(
            "Current URL: https://www.bing.com/search?q=C%2B%2B&form=QBRE&cvid=random-one\n"
            "search results for C++"
        ),
        history=[
            "Navigated to https://www.bing.com/search?q=C%2B%2B&form=ANSPH1&cvid=random-two"
        ],
        verification_required=False,
    )

    payload = json.loads(llm.messages[0][1]["content"])
    assert "q=C%2B%2B" in payload["latest_observation"]
    assert "form=" not in payload["latest_observation"]
    assert "cvid=" not in payload["latest_observation"]
    assert "form=" not in payload["recent_actions"][0]
    assert "cvid=" not in payload["recent_actions"][0]


@pytest.mark.asyncio
async def test_truncated_plan_gets_one_compact_larger_budget_correction() -> None:
    llm = SequencedLLM(
        ModelResponse('{"action":"navigate","url":"https://exa', "length"),
        ModelResponse('{"action":"navigate","url":"https://example.com"}'),
    )
    planner = planner_with_llm(
        llm,
        planner_max_completion_tokens=900,
        planner_correction_max_completion_tokens=1700,
    )

    action = await planner.decide(
        instruction="Open Example",
        raw_request="Open Example",
        observation="x" * 30000,
        history=[f"step {index}" for index in range(12)],
        verification_required=False,
    )

    assert isinstance(action, NavigateAction)
    assert llm.calls == [{}, {"max_completion_tokens": 1700}]
    correction_payload = json.loads(llm.messages[1][1]["content"])
    assert len(correction_payload["latest_observation"]) == 12000
    assert correction_payload["recent_actions"] == [f"step {index}" for index in range(6, 12)]
    assert planner.total_usage["calls"] == 2


@pytest.mark.asyncio
async def test_second_truncation_has_specific_error_code_and_no_third_retry() -> None:
    llm = SequencedLLM(
        ModelResponse('{"action":"navigate"', "length"),
        ModelResponse('{"action":"navigate"', "max_tokens"),
    )
    planner = planner_with_llm(llm)

    with pytest.raises(LoopFailure) as caught:
        await planner.decide(
            instruction="Open Example",
            raw_request="Open Example",
            observation="Current URL: about:blank",
            history=[],
            verification_required=False,
        )

    assert caught.value.code == "AGENT_OUTPUT_TRUNCATED"
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_agent_request_timeout_has_specific_error_code() -> None:
    planner = planner_with_llm(SequencedLLM(asyncio.TimeoutError()))

    with pytest.raises(LoopFailure) as caught:
        await planner.decide(
            instruction="Open Example",
            raw_request="Open Example",
            observation="Current URL: about:blank",
            history=[],
            verification_required=False,
        )

    assert caught.value.code == "AGENT_MODEL_TIMEOUT"


@pytest.mark.asyncio
async def test_invalid_action_after_one_correction_has_specific_error_code() -> None:
    llm = SequencedLLM(
        ModelResponse('{"action":"unknown"}'),
        ModelResponse('{"still":"not an action"}'),
    )
    planner = planner_with_llm(llm)

    with pytest.raises(LoopFailure) as caught:
        await planner.decide(
            instruction="Open Example",
            raw_request="Open Example",
            observation="Current URL: about:blank",
            history=[],
            verification_required=False,
        )

    assert caught.value.code == "AGENT_ACTION_INVALID"
    assert len(llm.calls) == 2
