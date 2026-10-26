"""Adapter base class and the registry the plugin resolves ``type`` through."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import BriefItem, SourceConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .http import HttpClient


class BaseSource:
    """One protocol. Implementations must be side-effect free apart from I/O."""

    type: str = ""

    def __init__(self, http: "HttpClient") -> None:
        self.http = http

    async def fetch(self, cfg: SourceConfig) -> list[BriefItem]:
        raise NotImplementedError


def source_timeout(cfg: SourceConfig, default: float) -> float:
    if cfg.timeout and cfg.timeout > 0:
        return min(float(cfg.timeout), 30.0)
    return default


__all__ = ["BaseSource", "source_timeout"]
