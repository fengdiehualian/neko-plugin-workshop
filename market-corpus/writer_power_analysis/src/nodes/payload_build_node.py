"""Payload build node."""

from __future__ import annotations

from typing import Any

from .models import AnalysisConfig
from .node_logging import LoggedNode
from .utils import build_openai_payload


class PayloadBuildNode(LoggedNode):
    """Build the upstream request body."""

    node_name = "payload.build"

    def run(self, model: str, system_prompt: str, article_text: str, cfg: AnalysisConfig) -> dict[str, Any]:
        """Build an OpenAI-compatible payload."""

        started = self._begin(model=model, json_mode=cfg.json_mode)
        try:
            payload = build_openai_payload(model, system_prompt, article_text, cfg.temperature, cfg.json_mode)
            self._end(
                started,
                model=model,
                message_count=len(payload.get("messages", [])),
                article_chars=len(article_text),
                prompt_chars=len(system_prompt),
            )
            return payload
        except Exception as exc:
            self._fail(started, exc)
            raise
