"""Status node."""

from __future__ import annotations

from .models import AnalysisConfig
from .node_logging import LoggedNode
from .utils import build_model_list_url


class StatusNode(LoggedNode):
    """Build non-secret plugin status."""

    node_name = "status.build"

    def run(self, cfg: AnalysisConfig) -> dict[str, object]:
        """Return a status payload without secret values."""

        started = self._begin(model=cfg.model, base_url=cfg.base_url)
        try:
            result: dict[str, object] = {
                "status": "ready",
                "base_url": cfg.base_url,
                "model_list_url": build_model_list_url(cfg.base_url, cfg.model_list_path),
                "default_model": cfg.model,
                "fallback_models": cfg.fallback_models,
                "has_api_key": bool(cfg.api_key),
                "timeout_seconds": cfg.timeout_seconds,
                "max_article_chars": cfg.max_article_chars,
            }
            self._end(started, has_api_key=result["has_api_key"])
            return result
        except Exception as exc:
            self._fail(started, exc)
            raise
