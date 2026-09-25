"""Dataclasses and business errors for writer power analysis nodes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EndpointCandidate:
    """One upstream endpoint candidate."""

    url: str
    kind: str


@dataclass(frozen=True)
class AnalysisConfig:
    """Resolved runtime configuration for one call."""

    base_url: str
    api_key: str
    model: str
    model_list_path: str
    fallback_models: list[str]
    timeout_seconds: float
    max_article_chars: int
    temperature: float
    json_mode: bool


@dataclass(frozen=True)
class ValidatedInput:
    """Validated user analysis input."""

    article_text: str
    mode: str


@dataclass(frozen=True)
class UpstreamResponse:
    """Normalized upstream HTTP response payload."""

    status_code: int
    is_success: bool
    text: str
    endpoint: str
    model: str


class WriterAnalysisError(Exception):
    """Business error that can be returned to the caller."""


class RetryableModelError(WriterAnalysisError):
    """Model-level error that should try the next fallback model."""
