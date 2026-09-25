"""Model candidate node."""

from __future__ import annotations

from .node_logging import LoggedNode


class ModelCandidateNode(LoggedNode):
    """Build selected model plus unique fallbacks."""

    node_name = "models.candidates"

    def run(self, selected_model: str, fallback_models: list[str]) -> list[str]:
        """Return selected model followed by unique fallback models."""

        started = self._begin(selected_model=selected_model, fallback_count=len(fallback_models))
        try:
            ordered = [selected_model]
            for model in fallback_models:
                if model not in ordered:
                    ordered.append(model)
            candidates = ordered[:6]
            self._end(started, count=len(candidates), models=",".join(candidates))
            return candidates
        except Exception as exc:
            self._fail(started, exc)
            raise
