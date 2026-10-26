from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

from .building_command import is_valid_attack_target, select_assault_target
from .combat_doctrine import load_combat_doctrine, recommend_combat_doctrine
from .combat_response import effective_combat_strength


# Values are copied from the game's [General], [AI], and [IQ] sections. They are
# decision boundaries, not cheats: all enemy facts must come from visible telemetry.
RULES_AI = {
    "credit_reserve": 100,
    "power_surplus": 50,
    "refinery_ratio": 0.16,
    "refinery_limit": 4,
    "barracks_ratio": 0.16,
    "barracks_limit": 2,
    "war_ratio": 0.20,
    "war_limit": 3,
    "defense_ratio": 0.40,
    "defense_limit": 40,
    "aa_ratio": 0.20,
    "aa_limit": 15,
    "helipad_ratio": 0.10,
    "helipad_limit": 2,
    "minimum_defensive_teams": 1,
    "maximum_defensive_teams": 3,
    "total_team_cap": 100,
    "ai_hate_delay_seconds": 10.0,
    "team_delay_frames": (200, 1250, 2500),
    "ai_force_prediction_fudge": (70, 35, 25),
    "iq_production": 4,
    "iq_guard_area": 1,
    "iq_repair_sell": 1,
    "iq_aircraft": 3,
    "iq_harvester": 1,
    "attack_strength_floor": 2200,
    "attack_advantage_ratio": 1.65,
    "attack_confirmation_polls": 4,
    "attack_confirmation_frames": 150,
}


@dataclass(frozen=True)
class BattleAssessment:
    phase: str
    urgency: float
    own_strength: float
    enemy_strength: float
    enemy_buildings: int
    enemy_combat_units: int
    economy_rate: float | None
    power_surplus: int | None
    advantage_confirmations: int
    attack_ready: bool
    reasons: tuple[str, ...]


def _num(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _visible_entities(state: dict[str, Any], relation: str) -> list[dict[str, Any]]:
    return [
        item for item in state.get("visible_entities", [])
        if isinstance(item, dict)
        and item.get("relation") == relation
        and item.get("visible") is True
        and item.get("alive") is True
    ]


class StrategicDirector:
    """Rules-aware finite-state commander used above individual command tasks."""

    def __init__(self) -> None:
        self.phase = "observe"
        self.last_assessment: BattleAssessment | None = None
        self.transition_count = 0
        self.last_doctrine: dict[str, Any] | None = None
        self.advantage_confirmations = 0
        self.advantage_started_frame: int | None = None
        self.advantage_last_frame: int | None = None

    def reset(self) -> None:
        self.phase = "observe"
        self.last_assessment = None
        self.transition_count = 0
        self.last_doctrine = None
        self.advantage_confirmations = 0
        self.advantage_started_frame = None
        self.advantage_last_frame = None

    def assess(self, state: dict[str, Any]) -> BattleAssessment:
        own = _visible_entities(state, "self")
        enemy = _visible_entities(state, "enemy")
        own_combat = [item for item in own if item.get("kind") in {"infantry", "vehicle", "aircraft"}]
        enemy_combat = [item for item in enemy if item.get("kind") in {"infantry", "vehicle", "aircraft"}]
        own_strength = sum(effective_combat_strength(item) for item in own_combat)
        enemy_strength = sum(effective_combat_strength(item) for item in enemy_combat)
        buildings = [item for item in own if item.get("kind") == "building"]
        enemy_buildings = [
            item for item in enemy
            if item.get("kind") == "building" and is_valid_attack_target(item)
        ]
        economy = state.get("economy") if isinstance(state.get("economy"), dict) else {}
        power = economy.get("power_surplus")
        power_surplus = int(power) if isinstance(power, int) else None
        rate = economy.get("net_per_minute")
        economy_rate = _num(rate, None) if isinstance(rate, (int, float)) else None
        reasons: list[str] = []
        urgency = 0.0
        alerts = state.get("alerts") if isinstance(state.get("alerts"), list) else []
        if any(isinstance(alert, dict) and alert.get("kind") == "under_attack" for alert in alerts):
            urgency = 1.0
            reasons.append("visible base-under-attack alert")
        if power_surplus is not None and power_surplus < 0:
            urgency = max(urgency, 0.9)
            reasons.append("power deficit")
        frame_value = state.get("game", {}).get("frame")
        frame = (
            int(frame_value)
            if isinstance(frame_value, int) and not isinstance(frame_value, bool)
            else None
        )
        attack_candidate = bool(enemy) and urgency < 0.9 and (
            own_strength >= float(RULES_AI["attack_strength_floor"])
            and own_strength >= max(1.0, enemy_strength) * float(
                RULES_AI["attack_advantage_ratio"]
            )
        )
        if attack_candidate:
            if self.advantage_confirmations <= 0:
                self.advantage_started_frame = frame
            if frame is None or frame != self.advantage_last_frame:
                self.advantage_confirmations += 1
                self.advantage_last_frame = frame
        else:
            self.advantage_confirmations = 0
            self.advantage_started_frame = None
            self.advantage_last_frame = frame
        frame_confirmed = (
            frame is None
            or self.advantage_started_frame is None
            or frame - self.advantage_started_frame
            >= int(RULES_AI["attack_confirmation_frames"])
        )
        attack_ready = (
            attack_candidate
            and self.advantage_confirmations
            >= int(RULES_AI["attack_confirmation_polls"])
            and frame_confirmed
        )
        if not buildings:
            phase = "establish"
            reasons.append("no owned foundation visible")
        elif urgency >= 0.9:
            phase = "defend"
        elif own_strength < 600:
            phase = "stabilize"
            reasons.append("combat strength below defensive floor")
        elif attack_ready:
            phase = "attack"
            reasons.append("force advantage remained valid through assembly confirmation")
        elif enemy_buildings and own_strength >= 1000:
            phase = "probe"
            reasons.append("enemy structure located; strike force remains in assembly")
        elif enemy:
            phase = "assemble"
            reasons.append(
                "visible enemy found; waiting for sustained force advantage"
            )
        elif economy_rate is not None and economy_rate > 0:
            phase = "expand"
            reasons.append("positive observed economy flow")
        else:
            phase = "assemble"
            reasons.append("waiting for a verified combat group")
        if phase != self.phase:
            self.transition_count += 1
            self.phase = phase
        result = BattleAssessment(
            phase=phase,
            urgency=urgency,
            own_strength=round(own_strength, 2),
            enemy_strength=round(enemy_strength, 2),
            enemy_buildings=len(enemy_buildings),
            enemy_combat_units=len(enemy_combat),
            economy_rate=economy_rate,
            power_surplus=power_surplus,
            advantage_confirmations=self.advantage_confirmations,
            attack_ready=attack_ready,
            reasons=tuple(reasons),
        )
        self.last_doctrine = recommend_combat_doctrine(
            state, phase=phase, own_strength=result.own_strength,
            enemy_strength=result.enemy_strength,
        )
        self.last_assessment = result
        return result

    def should_start_attack(self, assessment: BattleAssessment) -> bool:
        if assessment.enemy_buildings <= 0 and assessment.enemy_combat_units <= 0:
            return False
        doctrine_rule = (
            self.last_doctrine.get("rule")
            if isinstance(self.last_doctrine, dict) else None
        )
        if (
            isinstance(doctrine_rule, dict)
            and doctrine_rule.get("posture") in {"defend", "delay"}
        ):
            return False
        return assessment.phase == "attack" and assessment.attack_ready

    def strategic_target(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Pick the next local base-layer target for the current assault force."""
        visible_enemies = [
            item for item in _visible_entities(state, "enemy")
            if is_valid_attack_target(item)
        ]
        buildings = [
            item for item in visible_enemies
            if item.get("kind") == "building" and is_valid_attack_target(item)
        ]
        if not buildings:
            combat = [
                item for item in _visible_entities(state, "enemy")
                if item.get("kind") in {"infantry", "vehicle", "aircraft"}
                and is_valid_attack_target(item)
            ]
            if not combat:
                return None
            return max(
                combat,
                key=lambda item: (
                    effective_combat_strength(item),
                    -_num(item.get("health_ratio"), 1.0),
                    -int(item.get("object_id", 0)),
                ),
            )
        own_combat = [
            item for item in _visible_entities(state, "self")
            if item.get("kind") in {"infantry", "vehicle", "aircraft"}
            and item.get("controllable") is True
        ]
        return select_assault_target(
            own_combat, buildings, visible_enemies
        )

    def snapshot(self) -> dict[str, Any]:
        item = self.last_assessment
        return {
            "phase": self.phase,
            "transition_count": self.transition_count,
            "assessment": item.__dict__.copy() if item else None,
            "rules": dict(RULES_AI),
            "combat_doctrine": self.last_doctrine,
            "combat_doctrine_entries": int(
                load_combat_doctrine().get("metadata", {}).get("entry_count", 0)
            ),
        }
