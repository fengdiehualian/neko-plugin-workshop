"""Host-facing delivery envelope shared with the War Thunder output design."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventDelivery:
    """Immutable delivery plan built before crossing the host push boundary."""

    text: str
    ai_behavior: str
    visibility: tuple[str, ...]
    metadata: dict[str, Any]
    target_lanlan: str = ""
