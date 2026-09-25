"""Response parse node."""

from __future__ import annotations

import json

from .models import RetryableModelError, UpstreamResponse, WriterAnalysisError
from .node_logging import LoggedNode
from .utils import (
    completion_content,
    completion_error,
    is_unavailable_model_error,
    normalize_analysis,
    parse_completion_response,
    parse_model_json,
)


class ResponseParseNode(LoggedNode):
    """Parse upstream response into normalized analysis data."""

    node_name = "response.parse"

    def run(self, upstream: UpstreamResponse) -> dict[str, object]:
        """Parse one upstream response."""

        started = self._begin(model=upstream.model, status_code=upstream.status_code)
        try:
            parsed_response = parse_completion_response(upstream.text)
            if not upstream.is_success:
                error = completion_error(parsed_response, upstream.status_code)
                if is_unavailable_model_error(error):
                    raise RetryableModelError(error)
                raise WriterAnalysisError(error)

            content = completion_content(parsed_response)
            if not content:
                raise RetryableModelError("模型未返回分析内容")

            try:
                analysis = normalize_analysis(parse_model_json(content))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise WriterAnalysisError(f"解析模型分析结果失败: {exc}") from exc

            self._end(
                started,
                model=upstream.model,
                dimension_count=len(analysis.get("dimensions", [])),
                overallScore=analysis.get("overallScore"),
            )
            return analysis
        except Exception as exc:
            self._fail(started, exc, model=upstream.model)
            raise
