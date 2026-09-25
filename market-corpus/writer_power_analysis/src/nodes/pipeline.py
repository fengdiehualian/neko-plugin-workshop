"""Writer analysis pipeline node."""

from __future__ import annotations

from typing import Any

import httpx

from .completion_request_node import CompletionRequestNode
from .input_validation_node import InputValidationNode
from .model_candidate_node import ModelCandidateNode
from .models import AnalysisConfig, RetryableModelError, WriterAnalysisError
from .neko_llm_node import NekoLlmRequestNode
from .node_logging import LoggedNode, emit_log
from .payload_build_node import PayloadBuildNode
from .prompt_build_node import PromptBuildNode
from .report_format_node import ReportFormatNode
from .response_parse_node import ResponseParseNode


class WriterAnalysisPipelineNode(LoggedNode):
    """Run the full writer analysis node pipeline."""

    node_name = "analysis.pipeline"

    def __init__(self, logger: Any):
        super().__init__(logger)
        self.input_validation = InputValidationNode(logger)
        self.prompt_build = PromptBuildNode(logger)
        self.model_candidates = ModelCandidateNode(logger)
        self.payload_build = PayloadBuildNode(logger)
        self.completion_request = CompletionRequestNode(logger)
        self.neko_llm_request = NekoLlmRequestNode(logger)
        self.response_parse = ResponseParseNode(logger)
        self.report_format = ReportFormatNode(logger)

    async def run(
        self,
        cfg: AnalysisConfig,
        article_text: str,
        mode: str,
        *,
        use_neko_model: bool = False,
    ) -> dict[str, Any]:
        """Run validation, prompt building, upstream calls, parsing and report formatting."""

        started = self._begin(model=cfg.model, mode=mode, use_neko_model=use_neko_model)
        last_error = ""
        try:
            validated = self.input_validation.run(article_text, mode, cfg, require_api_key=not use_neko_model)
            system_prompt = self.prompt_build.run(validated.mode)

            # ── Neko built-in LLM path ──────────────────────────────
            if use_neko_model:
                upstream = await self.neko_llm_request.run(
                    system_prompt=system_prompt,
                    article_text=validated.article_text,
                    temperature=cfg.temperature,
                    json_mode=cfg.json_mode,
                    timeout_seconds=cfg.timeout_seconds,
                )
                analysis = self.response_parse.run(upstream)
                report = self.report_format.run(analysis)
                result = {
                    "analysis": analysis,
                    "report_markdown": report,
                    "overallScore": analysis["overallScore"],
                    "ratingTag": analysis["ratingTag"],
                    "summary": analysis["summary"],
                    "model": upstream.model,
                    "endpoint": upstream.endpoint,
                    "fallback_used": False,
                }
                self._end(started, model=upstream.model, use_neko_model=True)
                return result

            # ── External upstream path ──────────────────────────────
            candidates = self.model_candidates.run(cfg.model, cfg.fallback_models)

            for candidate_model in candidates:
                payload = self.payload_build.run(candidate_model, system_prompt, validated.article_text, cfg)
                try:
                    upstream = await self.completion_request.run(cfg, candidate_model, payload)
                    analysis = self.response_parse.run(upstream)
                except RetryableModelError as exc:
                    last_error = str(exc)
                    emit_log(
                        self.logger,
                        "warning",
                        f"[writer_power_analysis] model.retry model={candidate_model} reason={last_error}",
                    )
                    continue
                except (httpx.HTTPError, RuntimeError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    emit_log(
                        self.logger,
                        "warning",
                        f"[writer_power_analysis] model.retry model={candidate_model} reason={last_error}",
                    )
                    continue

                report = self.report_format.run(analysis)
                result = {
                    "analysis": analysis,
                    "report_markdown": report,
                    "overallScore": analysis["overallScore"],
                    "ratingTag": analysis["ratingTag"],
                    "summary": analysis["summary"],
                    "model": candidate_model,
                    "endpoint": upstream.endpoint,
                    "fallback_used": candidate_model != cfg.model,
                }
                self._end(started, model=candidate_model, fallback_used=result["fallback_used"])
                return result

            raise WriterAnalysisError(last_error or "所有候选模型都不可用，请检查模型列表和 API key")
        except Exception as exc:
            self._fail(started, exc)
            raise
