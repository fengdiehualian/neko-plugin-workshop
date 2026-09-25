"""Input validation node."""

from __future__ import annotations

from .models import AnalysisConfig, ValidatedInput, WriterAnalysisError
from .node_logging import LoggedNode
from .utils import safe_str


class InputValidationNode(LoggedNode):
    """Validate article input and required credentials."""

    node_name = "input.validate"

    def run(self, article_text: str, mode: str, cfg: AnalysisConfig, *, require_api_key: bool = True) -> ValidatedInput:
        """Validate user text and API credential availability."""

        started = self._begin(mode=mode, max_article_chars=cfg.max_article_chars, require_api_key=require_api_key)
        try:
            text = safe_str(article_text)
            if not text:
                raise WriterAnalysisError("请先输入要分析的作品正文")
            if len(text) > cfg.max_article_chars:
                raise WriterAnalysisError(f"作品正文过长，最多支持 {cfg.max_article_chars} 字符")
            if require_api_key and not cfg.api_key:
                raise WriterAnalysisError("缺少 API key：请在插件配置 writer_power_analysis.api_key 中填写，或本次调用传入 api_key")
            validated = ValidatedInput(article_text=text, mode=safe_str(mode, "standard"))
            self._end(started, article_chars=len(validated.article_text), mode=validated.mode)
            return validated
        except Exception as exc:
            self._fail(started, exc)
            raise
