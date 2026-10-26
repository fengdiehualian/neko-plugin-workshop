"""Public Neko model-slot adapters. Provider credentials stay in the host."""

import json
import time

from agent_mc_autonomy.decision_profiles import profile_view
from agent_mc_autonomy.fast import CRITERIA, QUESTION
from agent_mc_autonomy.perception import has_images, text_only
from agent_mc_autonomy.planner import PlannerResponseError, decision
from openai import APIConnectionError, APIStatusError
from plugin.sdk.plugin import TransportError


def error_code(error):
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        detail = body.get("error", body)
        if isinstance(detail, dict) and detail.get("code") in {
            "model_usage_not_bound", "model_usage_not_declared", "model_capability_mismatch",
        }:
            return detail["code"]
    return "model_gateway_unavailable"


class NekoPlanner:
    def __init__(self, gateway, max_output_tokens):
        self.gateway, self.max_output_tokens = gateway, max_output_tokens
        self.client = None
        self.vision_retry_after = 0.0

    async def _client(self):
        if self.gateway is None:
            raise ValueError("model_gateway_unavailable")
        if self.client is None:
            self.client = await self.gateway.get_client()
        return self.client

    async def decide_profile(self,messages,tools,profile):
        maximum=profile['max_output_tokens']
        if type(maximum) is not int or not 128<=maximum<=self.max_output_tokens:
            raise ValueError('decision_profile_exceeds_reserved_output')
        result=await self.decide(messages,tools,max_output_tokens=maximum)
        result.setdefault('model_status',{})['decision_profile']=profile_view(profile,parameters_applied=not bool(profile['parameters']),
            fallback='host_reasoning_parameters_unavailable' if profile['parameters'] else None)
        return result

    async def decide(self, messages, tools, *, max_output_tokens=None):
        started = time.monotonic()
        images = has_images(messages)
        route = "vision" if images and started >= self.vision_retry_after else "decision"
        fallback = "vision_cooldown" if images and route == "decision" else None
        try:
            client = await self._client()
        except (TransportError, ValueError):
            return {"content": None, "tool_calls": [], "model_status": {"route": "unavailable", "reason": "model_gateway_unavailable"}}
        for _ in range(2):
            try:
                response = await client.chat.completions.create(
                    model=route, messages=messages if route == "vision" else text_only(messages),
                    tools=tools, parallel_tool_calls=False, max_completion_tokens=max_output_tokens or self.max_output_tokens,
                )
                choice = response.choices[0]
                if choice.finish_reason not in {"stop", "tool_calls"}:
                    raise PlannerResponseError({"finish_reason": choice.finish_reason})
                result = decision(choice.message.model_dump(exclude_none=True))
                usage = response.usage.model_dump(exclude_none=True) if getattr(response, "usage", None) else {}
                return {**result, "model_status": {"route": route, "fallback": fallback, "image_used": route == "vision",
                        "latency_ms": round((time.monotonic() - started) * 1000),
                        "usage": {k: v for k, v in usage.items() if k in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(v) is int}}}
            except (APIConnectionError, APIStatusError, TransportError) as error:
                code = error_code(error)
                if route == "vision":
                    self.vision_retry_after = time.monotonic() + 60
                    route, fallback = "decision", code
                    continue
                return {"content": None, "tool_calls": [], "model_status": {"route": "unavailable", "reason": code, "fallback": fallback}}
            except (PlannerResponseError, IndexError, AttributeError, TypeError, ValueError):
                if route == "vision":
                    self.vision_retry_after = time.monotonic() + 60
                    route, fallback = "decision", "invalid_vision_response"
                    continue
                raise PlannerResponseError({"reason": "invalid_model_response"}) from None

    async def select_fast(self, state):
        schema = {"type": "object", "properties": {
            "choice": {"enum": list(CRITERIA)}, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "probabilities": {"type": "object", "properties": {k: {"type": "number", "minimum": 0, "maximum": 1} for k in CRITERIA},
                              "required": list(CRITERIA), "additionalProperties": False}},
            "required": ["choice", "confidence", "probabilities"], "additionalProperties": False}
        try:
            client = await self._client()
            response = await client.chat.completions.create(model="fast", messages=[
                {"role": "system", "content": json.dumps(QUESTION, ensure_ascii=False)},
                {"role": "user", "content": json.dumps(state, ensure_ascii=False)}],
                tools=[{"type": "function", "function": {"name": "select_focus", "description": "Select planning focus only.", "parameters": schema}}],
                tool_choice="required", parallel_tool_calls=False, max_completion_tokens=256)
            choice = response.choices[0]
            result = decision(choice.message.model_dump(exclude_none=True))
            calls = result["tool_calls"]
            if choice.finish_reason not in {"stop", "tool_calls"} or len(calls) != 1 or calls[0]["function"]["name"] != "select_focus":
                raise ValueError("fast_invalid_response")
            return {**json.loads(calls[0]["function"]["arguments"]), "type": "choice"}
        except (APIConnectionError, APIStatusError, TransportError, AttributeError) as error:
            raise ValueError(error_code(error)) from None

    async def aclose(self):
        if self.gateway:
            await self.gateway.aclose()
