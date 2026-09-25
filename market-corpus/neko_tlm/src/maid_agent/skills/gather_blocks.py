"""Checkpointed accumulation of nearby block resources.

``harvest_blocks`` is deliberately an atomic action.  A connected tree may
contain fewer blocks than the player's requested total, so this skill keeps
the verified total across as many atomic harvests as are actually needed.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .base import Blocked, Complete, Fail, SkillRun, StartAction


class GatherBlocksSkill:
    """Gather a verified number of nearby matching blocks."""

    name = "gather_blocks"
    version = 1

    def normalize_args(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("gather_blocks args must be an object")
        allowed = {
            "selector", "target_count", "target_metric", "search_radius",
            "speed", "tool_policy", "vein_mining",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(
                "gather_blocks has unsupported fields: " + ", ".join(unknown)
            )

        selector = raw.get("selector")
        if not isinstance(selector, Mapping):
            raise ValueError("gather_blocks.selector must be an object")
        selector_unknown = sorted(set(selector) - {"type", "id"})
        if selector_unknown:
            raise ValueError(
                "gather_blocks.selector has unsupported fields: "
                + ", ".join(selector_unknown)
            )
        selector_type = str(selector.get("type") or "").strip().lower()
        selector_id = str(selector.get("id") or "").strip().lower()
        if selector_type not in {"block", "tag"}:
            raise ValueError("gather_blocks.selector.type must be block or tag")
        if not selector_id or ":" not in selector_id:
            raise ValueError(
                "gather_blocks.selector.id must be a namespaced resource id"
            )

        target_count = _integer(raw.get("target_count"), "target_count")
        if not 1 <= target_count <= 4096:
            raise ValueError("gather_blocks.target_count must be between 1 and 4096")
        target_metric = str(
            raw.get("target_metric", "blocks_harvested") or ""
        ).strip().lower()
        if target_metric != "blocks_harvested":
            raise ValueError(
                "gather_blocks.target_metric must be blocks_harvested"
            )
        search_radius = _integer(raw.get("search_radius", 12), "search_radius")
        if not 1 <= search_radius <= 12:
            raise ValueError("gather_blocks.search_radius must be between 1 and 12")
        speed = _number(raw.get("speed", 0.7), "speed")
        if not 0.4 <= speed <= 1.0:
            raise ValueError("gather_blocks.speed must be between 0.4 and 1.0")
        tool_policy = str(
            raw.get("tool_policy", "require_correct") or ""
        ).strip().lower()
        if tool_policy not in {"require_correct", "allow_wrong"}:
            raise ValueError(
                "gather_blocks.tool_policy must be require_correct or allow_wrong"
            )
        vein_mining = raw.get("vein_mining", True)
        if not isinstance(vein_mining, bool):
            raise ValueError("gather_blocks.vein_mining must be a boolean")
        return {
            "selector": {"type": selector_type, "id": selector_id},
            "target_count": target_count,
            "target_metric": target_metric,
            "search_radius": search_radius,
            "speed": speed,
            "tool_policy": tool_policy,
            "vein_mining": vein_mining,
        }

    def initialize(self, run: SkillRun) -> None:
        run.collected_count = 0
        run.result = self._progress_result(run, "searching")

    def next_directive(
        self,
        run: SkillRun,
        terminal_snapshot: Optional[Mapping[str, Any]],
    ):
        target = int(run.args["target_count"])
        if terminal_snapshot is not None:
            terminal = dict(terminal_snapshot)
            kind = str(
                terminal.get("kind")
                or run.current_action_request.get("kind")
                or ""
            ).strip().lower()
            if kind != "harvest_blocks":
                return Fail(
                    "UNKNOWN_CHILD_ACTION",
                    {"message": f"GatherBlocksSkill cannot consume child {kind!r}"},
                )
            result = _result(terminal)
            harvested = _harvested_count(result)
            run.collected_count += harvested
            if run.collected_count >= target:
                return Complete({
                    **self._progress_result(run, "completed"),
                    "message": "verified_target_count_reached",
                    "request_satisfied": True,
                    "last_child_result": result,
                })

            status = str(terminal.get("status") or "").strip().upper()
            if status != "SUCCEEDED" or harvested <= 0:
                reason = str(
                    result.get("message")
                    or terminal.get("end_reason")
                    or "RESOURCE_NOT_AVAILABLE"
                ).strip().upper()
                return Blocked(reason, {
                    **self._progress_result(run, "blocked"),
                    "message": str(
                        result.get("message")
                        or "gather_child_did_not_supply_more_matching_blocks"
                    ),
                    "request_satisfied": False,
                    "decision_required": True,
                    "child_terminal": terminal,
                    "suggestions": [
                        "move_the_maid_near_more_matching_blocks",
                        "verify_the_selector_and_required_tool",
                        "cancel_or_start_a_new_skill_after_conditions_change",
                    ],
                })
            run.result = self._progress_result(run, "continuing")

        if run.collected_count >= target:
            return Complete({
                **self._progress_result(run, "completed"),
                "message": "verified_target_count_reached",
                "request_satisfied": True,
            })

        remaining = max(1, target - run.collected_count)
        vein_mining = bool(run.args["vein_mining"])
        # Whole-component mode finishes one tree/connected resource at a time.
        # The Skill, not the Action, owns the cross-component target count.
        child_limit = min(remaining, 64 if vein_mining else 8)
        return StartAction(
            "harvest_blocks",
            {
                "selector": dict(run.args["selector"]),
                "search_radius": int(run.args["search_radius"]),
                "max_blocks": child_limit,
                "vein_mining": vein_mining,
                "tool_policy": str(run.args["tool_policy"]),
                "speed": float(run.args["speed"]),
                "mining_plan": {"mode": "nearby"},
            },
            timeout_ms=0,
        )

    @staticmethod
    def _progress_result(run: SkillRun, stage: str) -> dict[str, Any]:
        target = int(run.args["target_count"])
        collected = max(0, int(run.collected_count))
        return {
            "stage": stage,
            "selector": dict(run.args["selector"]),
            "target_count": target,
            "collected_count": collected,
            "remaining_target_count": max(0, target - collected),
            "target_metric": "blocks_harvested",
            "count_source": "server_confirmed_harvest_terminals",
        }


def _result(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    value = snapshot.get("result") if isinstance(snapshot, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _harvested_count(result: Mapping[str, Any]) -> int:
    for key in ("blocks_harvested", "harvested"):
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
    return 0


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"gather_blocks.{name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"gather_blocks.{name} must be an integer") from exc


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"gather_blocks.{name} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"gather_blocks.{name} must be a number") from exc
