"""场景 phase：IDLE / IN_GAME / DEGRADED。"""

from __future__ import annotations

from .contracts import DEGRADED, IDLE, IN_GAME, SceneState


class ScenarioResolver:
    def resolve(self, state: SceneState) -> str:
        if not state.connected:
            return IDLE
        if not state.game_running:
            return IDLE
        if state.degraded:
            return DEGRADED
        return IN_GAME
