"""Adapter registry: ``type`` string -> implementation."""

from __future__ import annotations

from .auto import AutoSource
from .base import BaseSource
from .http import HttpClient
from .http_json import HttpJsonSource
from .rss import RssSource
from .weather import WeatherSource

REGISTRY: dict[str, type[BaseSource]] = {
    AutoSource.type: AutoSource,
    WeatherSource.type: WeatherSource,
    RssSource.type: RssSource,
    HttpJsonSource.type: HttpJsonSource,
}


def build_sources(http: HttpClient) -> dict[str, BaseSource]:
    return {name: cls(http) for name, cls in REGISTRY.items()}


__all__ = ["REGISTRY", "build_sources"]
