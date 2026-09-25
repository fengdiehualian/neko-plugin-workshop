"""Neko LLM request node – reuse Neko's model config through the HTTP pipeline."""

from __future__ import annotations

from typing import Any

import httpx

from .models import UpstreamResponse, WriterAnalysisError
from .node_logging import LoggedNode
from .utils import build_endpoint_candidates, is_json_mode_rejection, to_gemini_payload


class NekoLlmRequestNode(LoggedNode):
    """Read Neko's conversation model config and call via HTTP.

    Does NOT use ``create_chat_llm_async`` because Neko's free-model
    (lanlan.app) requires client-side auth headers that only the main
    app injects.  The standard HTTP path works for all other providers.
    """

    node_name = "neko_llm.request"

    async def run(
        self,
        system_prompt: str,
        article_text: str,
        temperature: float,
        json_mode: bool,
        timeout_seconds: float,
    ) -> UpstreamResponse:
        """Resolve Neko's model config and call via HTTP."""

        from utils.config_manager import get_config_manager

        neko_cfg = get_config_manager().get_model_api_config("conversation")
        model = str(neko_cfg.get("model") or "")
        base_url = str(neko_cfg.get("base_url") or "")
        api_key = str(neko_cfg.get("api_key") or "")

        if not model:
            raise WriterAnalysisError(
                "Neko 当前未配置对话模型。请在 N.E.K.O 设置中配置模型后再使用「跟随 Neko」。"
            )
        if not base_url:
            raise WriterAnalysisError(
                "Neko 当前未配置模型端点 URL。请在 N.E.K.O 设置中配置后再使用「跟随 Neko」。"
            )
        if not api_key:
            raise WriterAnalysisError(
                "Neko 当前模型未配置 API key。请在 N.E.K.O 设置中配置后再使用「跟随 Neko」。"
            )

        started = self._begin(model=model, base_url=base_url, json_mode=json_mode)
        try:
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": article_text},
                ],
                "temperature": temperature,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            endpoints = build_endpoint_candidates(base_url, model)
            last_text = ""
            last_endpoint = base_url
            last_status = 0
            retried_without_json_mode = False
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
                for endpoint in endpoints:
                    body = to_gemini_payload(payload) if endpoint.kind == "gemini" else payload
                    resp = await client.post(
                        endpoint.url,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}",
                            "x-api-key": api_key,
                        },
                        json=body,
                    )
                    if payload.get("response_format") and is_json_mode_rejection(resp.text, resp.status_code):
                        retry_payload = dict(payload)
                        retry_payload.pop("response_format", None)
                        retry_body = to_gemini_payload(retry_payload) if endpoint.kind == "gemini" else retry_payload
                        resp = await client.post(
                            endpoint.url,
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": f"Bearer {api_key}",
                                "x-api-key": api_key,
                            },
                            json=retry_body,
                        )
                        retried_without_json_mode = True
                    last_status = resp.status_code
                    last_text = resp.text
                    last_endpoint = endpoint.url
                    if resp.status_code != 404:
                        break

            result = UpstreamResponse(
                status_code=last_status,
                is_success=200 <= last_status < 300,
                text=last_text,
                endpoint=last_endpoint,
                model=model,
            )
            self._end(
                started,
                model=model,
                endpoint=last_endpoint,
                status_code=last_status,
                response_chars=len(last_text),
                retried_without_json_mode=retried_without_json_mode,
            )

            # Detect free-model rejection
            if "not using Lanlan" in last_text or "STOP ABUSE" in last_text:
                raise WriterAnalysisError(
                    "Neko 当前使用的是 free-model（lanlan.app），该端点不支持外部 HTTP 调用。"
                    "请在 N.E.K.O 设置 → 模型配置中将对话模型切换为其他 API 提供商（如 OpenAI、DeepSeek 等），"
                    "或关闭「跟随 Neko」后手动填写 API 信息。"
                )
            if not result.is_success:
                raise WriterAnalysisError(
                    f"Neko 模型返回错误 (HTTP {last_status})，请检查 N.E.K.O 的模型配置是否正确。"
                )

            return result
        except WriterAnalysisError:
            raise
        except httpx.TimeoutException as exc:
            self._fail(started, exc, model=model)
            raise WriterAnalysisError(
                f"Neko 内置模型调用超时（{timeout_seconds:.0f} 秒）。"
                "长文本分析耗时较长，请换更快的模型、缩短原文，或关闭「跟随 Neko」后使用更稳定的分析平台。"
            ) from exc
        except Exception as exc:
            self._fail(started, exc, model=model)
            raise WriterAnalysisError(f"Neko 内置模型调用失败: {exc}") from exc
