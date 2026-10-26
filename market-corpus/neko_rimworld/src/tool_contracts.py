"""Tool capabilities and negotiated game setup, independent of dispatch policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .game.observation import Observation

MAX_TOOL_RESULT_CHARS = 131_072


class ToolExecutionError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, recoverable: bool = True, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.recoverable = recoverable
        self.details = details or {}


ToolHandler = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any] | Observation]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    terminal: bool = False
    exclusive: bool = False
    max_result_chars: int = MAX_TOOL_RESULT_CHARS

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True, slots=True)
class ToolSetup:
    tools: tuple[ToolSpec, ...]
    directory: dict[str, Any] | None = None
    #: Ready immutable Def-knowledge view for this consultation, attached by
    #: compose_wiki_tools. Consumers (wiki tool, Def-name recovery) share one
    #: frozen snapshot; None keeps every lookup on the native exact path.
    knowledge_view: Any = None
    #: Internal preparation source, deliberately absent from the model tool set.
    situation_provider: Any = None


PUBLIC_QUERY_VERSION = "public_query.v2"
WORLD_OBSERVATION_VERSION = "world_observation.v2.3"
