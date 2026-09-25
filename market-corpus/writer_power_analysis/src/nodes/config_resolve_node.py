"""Config resolution node."""

from __future__ import annotations

from typing import Any

from .constants import (
    DEFAULT_BASE_URL,
    DEFAULT_FALLBACK_MODELS,
    DEFAULT_MAX_ARTICLE_CHARS,
    DEFAULT_MODEL,
    DEFAULT_MODEL_LIST_PATH,
    DEFAULT_TEMPERATURE,
    DEFAULT_TIMEOUT_SECONDS,
)
from .models import AnalysisConfig
from .node_logging import LoggedNode
from .utils import (
    as_string_list,
    normalize_base_url,
    safe_bool,
    safe_float,
    safe_int,
    safe_str,
)


class ConfigResolveNode(LoggedNode):
    """Resolve TOML config plus per-call overrides."""

    node_name = "config.resolve"

    def run(self, raw_config: dict[str, Any], **kwargs: Any) -> AnalysisConfig:
        """Resolve one call's runtime configuration."""

        started = self._begin(has_override_keys=bool(kwargs))
        try:
            config = raw_config if isinstance(raw_config, dict) else {}
            base_url = safe_str(kwargs.get("base_url"), safe_str(config.get("base_url"), DEFAULT_BASE_URL))
            api_key = safe_str(kwargs.get("api_key"), safe_str(config.get("api_key")))
            model = safe_str(kwargs.get("model"), safe_str(config.get("default_model"), DEFAULT_MODEL))
            model_list_path = safe_str(
                kwargs.get("model_list_path"),
                safe_str(config.get("model_list_path"), DEFAULT_MODEL_LIST_PATH),
            )
            fallback_models = as_string_list(kwargs.get("fallback_models", config.get("fallback_models")), DEFAULT_FALLBACK_MODELS)
            resolved = AnalysisConfig(
                base_url=normalize_base_url(base_url),
                api_key=api_key,
                model=model,
                model_list_path=model_list_path,
                fallback_models=fallback_models,
                timeout_seconds=safe_float(
                    kwargs.get("timeout_seconds", config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
                    DEFAULT_TIMEOUT_SECONDS,
                    5.0,
                    300.0,
                ),
                max_article_chars=safe_int(
                    kwargs.get("max_article_chars", config.get("max_article_chars", DEFAULT_MAX_ARTICLE_CHARS)),
                    DEFAULT_MAX_ARTICLE_CHARS,
                    1_000,
                    1_000_000,
                ),
                temperature=safe_float(
                    kwargs.get("temperature", config.get("temperature", DEFAULT_TEMPERATURE)),
                    DEFAULT_TEMPERATURE,
                    0.0,
                    2.0,
                ),
                json_mode=safe_bool(kwargs.get("json_mode", config.get("json_mode")), False),
            )
            self._end(
                started,
                base_url=resolved.base_url,
                model=resolved.model,
                fallback_count=len(resolved.fallback_models),
                has_api_key=bool(resolved.api_key),
                timeout_seconds=resolved.timeout_seconds,
            )
            return resolved
        except Exception as exc:
            self._fail(started, exc)
            raise
