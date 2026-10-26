from __future__ import annotations

import random
from dataclasses import dataclass

from .strategic_ai import StrategicDirector


@dataclass(frozen=True)
class SimulationResult:
    seed: int
    ticks: int
    deadlock: bool
    unsafe_attack: bool
    phase: str


def _state(seed: int, tick: int) -> dict:
    rng = random.Random(seed)
    buildings = [
        {"object_id": 1, "relation": "self", "visible": True, "alive": True, "kind": "building", "building_class": "construction_yard"},
        {"object_id": 2, "relation": "self", "visible": True, "alive": True, "kind": "building", "building_class": "power"},
    ]
    own_units = [
        {"object_id": 10 + i, "relation": "self", "visible": True, "alive": True, "kind": "vehicle", "strength": 120, "health_ratio": rng.uniform(.55, 1.0)}
        for i in range((tick * 3 + seed) % 24)
    ]
    enemy_units = [
        {"object_id": 100 + i, "relation": "enemy", "visible": True, "alive": True, "kind": "vehicle", "strength": 100, "health_ratio": 1.0}
        for i in range((seed + tick * 2) % 9)
    ]
    enemy_buildings = [
        {"object_id": 200, "type_id": "GAWEAP", "relation": "enemy", "visible": True, "alive": True, "kind": "building", "building_class": "vehicle_production", "health_ratio": .8}
    ] if tick > 2 else []
    return {
        "game": {"running": True, "frame": tick},
        "visible_entities": buildings + own_units + enemy_units + enemy_buildings,
        "economy": {"power_surplus": 50 + ((seed + tick) % 6) * 20, "net_per_minute": 100 + tick * 3},
        "alerts": [],
    }


def run_trials(trials: int = 10000, ticks: int = 30) -> list[SimulationResult]:
    results: list[SimulationResult] = []
    for seed in range(max(1, int(trials))):
        director = StrategicDirector()
        last_phase = "observe"
        deadlock = True
        unsafe = False
        for tick in range(max(1, int(ticks))):
            state = _state(seed, tick)
            assessment = director.assess(state)
            last_phase = assessment.phase
            if assessment.phase not in {"stabilize", "assemble", "expand", "observe"}:
                deadlock = False
            if director.should_start_attack(assessment):
                target = director.strategic_target(state)
                unsafe = unsafe or target is None or assessment.own_strength < assessment.enemy_strength * 1.1
        results.append(SimulationResult(seed, ticks, deadlock, unsafe, last_phase))
    return results


def validate_trials(trials: int = 10000, ticks: int = 30) -> dict[str, int | bool]:
    results = run_trials(trials, ticks)
    return {
        "trials": len(results),
        "deadlocks": sum(item.deadlock for item in results),
        "unsafe_attacks": sum(item.unsafe_attack for item in results),
        "passed": not any(item.deadlock or item.unsafe_attack for item in results),
    }
