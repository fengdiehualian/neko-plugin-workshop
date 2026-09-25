"""无宿主依赖的评估流水线：SceneState → 候选 → Arbiter → 输出动作。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..detectors._base import DetectorEngine
from ..detectors.chatter import build_chatter_detectors
from ..detectors.lifecycle import build_lifecycle_detectors
from ..detectors.log import build_log_detectors
from ..detectors.vision import build_vision_detectors
from .arbiter import Arbiter
from .contracts import CbqConfig, CompanionEvent, SceneState
from .output_result import output_was_committed
from .safety_guard import SafetyGuard
from .scenario import ScenarioResolver

PushFn = Callable[[CompanionEvent, bool], str]


@dataclass
class EvalResult:
    scenario: str
    candidates: list[CompanionEvent] = field(default_factory=list)
    chosen: CompanionEvent | None = None
    chain: list[dict[str, Any]] = field(default_factory=list)
    output: str | None = None


class CompanionRuntime:
    def __init__(
        self,
        cfg: CbqConfig | None = None,
        *,
        push_event: PushFn | None = None,
    ) -> None:
        self.cfg = cfg or CbqConfig()
        self.safety = SafetyGuard(self.cfg)
        self.resolver = ScenarioResolver()
        self.arbiter = Arbiter(self.safety)
        self._chatter = build_chatter_detectors(self.cfg)[0]
        self.engine = DetectorEngine(
            list(build_lifecycle_detectors())
            + list(build_log_detectors())
            + list(build_vision_detectors())
            + [self._chatter]
        )
        self.push_event = push_event
        self.state = SceneState()

    def configure(self, cfg: CbqConfig) -> None:
        """重设配置，并让依赖配置的检测器同步最新值。"""
        self.cfg = cfg
        self.safety.update(cfg)
        self._chatter.cfg = cfg

    def reset(self) -> None:
        self.engine.reset()
        self.arbiter.reset()
        self.state = SceneState()

    def evaluate(self, prev: SceneState, cur: SceneState, *, now: float | None = None) -> EvalResult:
        now = time.time() if now is None else now
        if (
            prev.data_layer_instance_id
            and cur.data_layer_instance_id
            and prev.data_layer_instance_id != cur.data_layer_instance_id
        ):
            # Event sequence numbers restart with the data-layer process. Reset
            # detector de-duplication and arbitration state at the same boundary.
            self.engine.reset()
            self.arbiter.reset()
        cur.scenario = self.resolver.resolve(cur)
        candidates = self.engine.feed(prev, cur)
        if self.cfg.require_game_process and not cur.game_running:
            # 进程退出后仍允许赛后结果播报（仅胜负；死亡/阶段事件在无进程时不播）
            candidates = [
                c
                for c in candidates
                if c.event_id in ("game_started", "game_stopped", "match_win", "match_lose")
            ]
        output_clock_checkpoint = self.safety.output_clock_checkpoint()
        arbiter_checkpoint = self.arbiter.checkpoint()
        chosen, chain = self.arbiter.decide(candidates, cur.scenario, now)
        output = None
        if chosen is not None and self.push_event is not None:
            try:
                output = self.push_event(chosen, self.cfg.dry_run)
            except Exception:
                self.arbiter.restore(arbiter_checkpoint)
                self.safety.restore_output_clock(output_clock_checkpoint)
                raise
            if output.startswith("dry_run(") or not output_was_committed(output):
                self.arbiter.restore(arbiter_checkpoint)
                self.safety.restore_output_clock(output_clock_checkpoint)
        return EvalResult(
            scenario=cur.scenario,
            candidates=candidates,
            chosen=chosen,
            chain=chain,
            output=output,
        )

    def step(self, cur: SceneState, *, now: float | None = None) -> EvalResult:
        prev = self.state
        result = self.evaluate(prev, cur, now=now)
        self.state = cur
        return result
