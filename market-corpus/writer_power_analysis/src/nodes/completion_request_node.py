"""Completion request node."""

from __future__ import annotations

from typing import Any

import httpx

from .models import (
    AnalysisConfig,
    RetryableModelError,
    UpstreamResponse,
    WriterAnalysisError,
)
from .node_logging import LoggedNode
from .utils import build_endpoint_candidates, is_json_mode_rejection, to_gemini_payload


class CompletionRequestNode(LoggedNode):
    """Call the upstream model endpoint."""

    node_name = "completion.request"

    async def run(self, cfg: AnalysisConfig, model: str, payload: dict[str, Any]) -> UpstreamResponse:
        """Call upstream, trying endpoint URL variants on 404."""

        endpoints = build_endpoint_candidates(cfg.base_url, model)
        started = self._begin(model=model, endpoint_count=len(endpoints), base_url=cfg.base_url)
        try:
            last_response: httpx.Response | None = None
            last_endpoint = cfg.base_url
            async with httpx.AsyncClient(timeout=cfg.timeout_seconds, follow_redirects=True) as client:
                for index, endpoint in enumerate(endpoints):
                    body = to_gemini_payload(payload) if endpoint.kind == "gemini" else payload
                    retried_without_json_mode = False
                    response = await client.post(
                        endpoint.url,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {cfg.api_key}",
                            "x-api-key": cfg.api_key,
                        },
                        json=body,
                    )
                    if payload.get("response_format") and is_json_mode_rejection(response.text, response.status_code):
                        retry_payload = dict(payload)
                        retry_payload.pop("response_format", None)
                        retry_body = to_gemini_payload(retry_payload) if endpoint.kind == "gemini" else retry_payload
                        response = await client.post(
                            endpoint.url,
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": f"Bearer {cfg.api_key}",
                                "x-api-key": cfg.api_key,
                            },
                            json=retry_body,
                        )
                        retried_without_json_mode = True
                    last_response = response
                    last_endpoint = endpoint.url
                    if response.status_code != 404 or index == len(endpoints) - 1:
                        result = UpstreamResponse(
                            status_code=response.status_code,
                            is_success=response.is_success,
                            text=response.text,
                            endpoint=endpoint.url,
                            model=model,
                        )
                        self._end(
                            started,
                            model=model,
                            endpoint=endpoint.url,
                            endpoint_kind=endpoint.kind,
                            status_code=response.status_code,
                            response_chars=len(response.text),
                            retried_without_json_mode=retried_without_json_mode,
                        )
                        return result

            if last_response is not None:
                result = UpstreamResponse(
                    status_code=last_response.status_code,
                    is_success=last_response.is_success,
                    text=last_response.text,
                    endpoint=last_endpoint,
                    model=model,
                )
                self._end(started, model=model, endpoint=last_endpoint, status_code=last_response.status_code)
                return result
            raise WriterAnalysisError("模型接口地址配置无效")
        except httpx.TimeoutException as exc:
            self._fail(started, exc, model=model)
            raise RetryableModelError(
                f"模型请求超时（{cfg.timeout_seconds:.0f} 秒）：{model} 未在限定时间内返回结果。"
                "长文本分析可能需要更长时间，请换更快的模型、缩短原文，或提高 writer_power_analysis.timeout_seconds。"
            ) from exc
        except Exception as exc:
            self._fail(started, exc, model=model)
            raise
