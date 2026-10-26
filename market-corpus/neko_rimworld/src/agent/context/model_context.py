"""Default ordinary-JSON context path; historical codecs are not runtime modes."""

from __future__ import annotations

import os

MAX_SOURCE_CHARS = 4 * 1024 * 1024
CONTEXT_TOOLS = frozenset(
    {
        "look",
        "count",
        "inspect_route",
        "survey_space",
        "inspect_state",
        "inspect_threats",
        "evaluate_activity",
        "evaluate_journey",
        "evaluate_trade",
    }
)
RETIRED_ENVIRONMENT_KEYS = (
    "NEKO_CONTEXT_PRUNING",
    "NEKO_ACTIVITY",
    "NEKO_DEF_NAME_RECOVERY",
    "NEKO_PUBLIC_REFERENCES",
    "NEKO_EVIDENCE_COMPACT",
    "NEKO_PUBLIC_INPUT_TOKEN_LIMIT",
    "NEKO_SEMANTIC_SURFACE",
)


def validate_deployment():
    """Reject retired configuration keys regardless of their stored values."""
    for name in RETIRED_ENVIRONMENT_KEYS:
        if name in os.environ:
            raise ValueError(f"{name} is retired; remove it from the environment. Historical variants require frozen source.")


def make_pruner():
    from .context_pruning import ContextPruner

    return ContextPruner()


def model_size(value, *, arguments=None):
    """Standalone size, independent of earlier messages and note suppression."""
    return make_pruner().compile(value, arguments=arguments).view_chars
