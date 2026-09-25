"""Detector 引擎。"""

from __future__ import annotations

from typing import Protocol

from ..core.contracts import CompanionEvent, SceneState


class Detector(Protocol):
    id: str

    def feed(self, prev: SceneState, cur: SceneState) -> list[CompanionEvent]: ...

    def reset(self) -> None: ...


class DetectorEngine:
    def __init__(self, detectors: list[Detector]) -> None:
        self.detectors = list(detectors)

    def reset(self) -> None:
        for d in self.detectors:
            d.reset()

    def feed(self, prev: SceneState, cur: SceneState) -> list[CompanionEvent]:
        out: list[CompanionEvent] = []
        for d in self.detectors:
            out.extend(d.feed(prev, cur) or [])
        return out
