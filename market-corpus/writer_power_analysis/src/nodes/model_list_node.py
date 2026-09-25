"""Model list node."""

from __future__ import annotations

import httpx

from .constants import BUILTIN_MODEL_IDS
from .models import AnalysisConfig, WriterAnalysisError
from .node_logging import LoggedNode
from .utils import (
    build_model_list_url,
    completion_error,
    parse_completion_response,
    safe_str,
)


class ModelListNode(LoggedNode):
    """Fetch or build the available model list."""

    node_name = "models.list"

    async def run(self, cfg: AnalysisConfig) -> dict[str, object]:
        """Return configured upstream models or builtin fallback models."""

        url = build_model_list_url(cfg.base_url, cfg.model_list_path)
        started = self._begin(source=url, has_api_key=bool(cfg.api_key))
        try:
            if not cfg.api_key:
                result: dict[str, object] = {
                    "source": "builtin_fallback",
                    "count": len(BUILTIN_MODEL_IDS),
                    "models": [{"id": model_id, "owned_by": ""} for model_id in BUILTIN_MODEL_IDS],
                    "message": "未配置 API key，已返回内置候选列表。",
                }
                self._end(started, source=result["source"], count=result["count"])
                return result

            async with httpx.AsyncClient(timeout=min(cfg.timeout_seconds, 30.0), follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {cfg.api_key}",
                        "x-api-key": cfg.api_key,
                    },
                )
            if not response.is_success:
                parsed = parse_completion_response(response.text)
                raise WriterAnalysisError(completion_error(parsed, response.status_code))

            payload = response.json()
            data = None
            if isinstance(payload, dict):
                data = payload.get("data")
                if data is None:
                    data = payload.get("models")
            models = []
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    model_id = safe_str(item.get("id")) or safe_str(item.get("name")).removeprefix("models/")
                    if model_id:
                        models.append({"id": model_id, "owned_by": safe_str(item.get("owned_by"))})
            result = {"source": url, "count": len(models), "models": models}
            self._end(started, source=url, count=len(models), status_code=response.status_code)
            return result
        except Exception as exc:
            self._fail(started, exc)
            raise
