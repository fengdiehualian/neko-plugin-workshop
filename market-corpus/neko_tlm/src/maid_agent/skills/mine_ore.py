"""Deterministic checkpointed ore-mining skill."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .base import Blocked, Complete, Fail, SkillRun, StartAction

_DIRECTIONS = ("north", "east", "south", "west")
_LEFT = {"north": "west", "west": "south", "south": "east", "east": "north"}
_RIGHT = {value: key for key, value in _LEFT.items()}
_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
_BENIGN_SCAN_MESSAGES = frozenset({
    "no_matching_block_found",
    "all_targets_changed_before_planning",
    "target_is_air",
})


class MineOreSkill:
    """Delegate mining to Java, with the former fishbone runner as a fallback."""

    name = "mine_ore"
    version = 1

    def normalize_args(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("mine_ore args must be an object")
        allowed = {
            "selector",
            "target_count",
            "target_metric",
            "execution_mode",
            "strategy",
            "direction",
            "shape",
            "segment_length",
            "speed",
            "discovery_mode",
            "placement_policy",
            "max_placements",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"mine_ore has unsupported fields: {', '.join(unknown)}")

        selector = raw.get("selector")
        if not isinstance(selector, Mapping):
            raise ValueError("mine_ore.selector must be an object")
        selector_unknown = sorted(set(selector) - {"type", "id"})
        if selector_unknown:
            raise ValueError(
                "mine_ore.selector has unsupported fields: "
                + ", ".join(selector_unknown)
            )
        selector_type = str(selector.get("type") or "").strip().lower()
        selector_id = str(selector.get("id") or "").strip().lower()
        if selector_type not in {"block", "tag"}:
            raise ValueError("mine_ore.selector.type must be block or tag")
        if not selector_id or ":" not in selector_id:
            raise ValueError("mine_ore.selector.id must be a namespaced resource id")

        target_count = _positive_integer(raw.get("target_count"), "target_count")
        if target_count > 4096:
            raise ValueError("mine_ore.target_count must be between 1 and 4096")
        target_metric = str(
            raw.get("target_metric", "blocks_harvested") or ""
        ).strip().lower()
        if target_metric != "blocks_harvested":
            raise ValueError("mine_ore.target_metric must be blocks_harvested")

        strategy = str(raw.get("strategy", "fishbone") or "").strip().lower()
        if strategy == "auto":
            strategy = "fishbone"
        if strategy != "fishbone":
            raise ValueError("mine_ore.strategy must be fishbone (auto is accepted as an alias)")
        execution_mode = str(
            raw.get("execution_mode", "autonomous") or ""
        ).strip().lower()
        if execution_mode not in {"autonomous", "legacy"}:
            raise ValueError("mine_ore.execution_mode must be autonomous or legacy")
        direction = str(raw.get("direction", "auto") or "").strip().lower()
        if direction not in {*_DIRECTIONS, "auto"}:
            raise ValueError(
                "mine_ore.direction must be auto, north, east, south or west"
            )
        shape = str(raw.get("shape", "auto") or "").strip().lower()
        if shape not in {"auto", "level", "staircase_down"}:
            raise ValueError(
                "mine_ore.shape must be auto, level or staircase_down"
            )
        segment_length = _bounded_integer(
            raw.get("segment_length", 8), "segment_length", 1, 8
        )
        speed = _bounded_number(raw.get("speed", 0.7), "speed", 0.4, 1.0)
        discovery_mode = str(
            raw.get("discovery_mode", "loaded_scan") or ""
        ).strip().lower()
        if discovery_mode not in {"loaded_scan", "exposed_only"}:
            raise ValueError(
                "mine_ore.discovery_mode must be loaded_scan or exposed_only"
            )
        placement_policy = str(
            raw.get("placement_policy", "safe_support_and_water_seal") or ""
        ).strip().lower()
        if placement_policy not in {"disabled", "safe_support_and_water_seal"}:
            raise ValueError(
                "mine_ore.placement_policy must be disabled or "
                "safe_support_and_water_seal"
            )
        max_placements = _bounded_nonnegative_integer(
            raw.get("max_placements", 0), "max_placements", 4096
        )
        return {
            "selector": {"type": selector_type, "id": selector_id},
            "target_count": target_count,
            "target_metric": target_metric,
            "execution_mode": execution_mode,
            "strategy": strategy,
            "direction": direction,
            "shape": shape,
            "segment_length": segment_length,
            "speed": speed,
            "discovery_mode": discovery_mode,
            "placement_policy": placement_policy,
            "max_placements": max_placements,
        }

    def initialize(self, run: SkillRun) -> None:
        run.collected_count = 0
        if _execution_mode(run) == "autonomous":
            run.main_direction = str(run.args.get("direction") or "auto")
            run.main_segment_index = 0
            run.branch_index = 0
            run.tried_directions_at_current = []
            run.origin_pos = None
            run.current_pos = None
            run.result = {
                "stage": "delegating_to_java",
                "phase": "autonomous",
                "execution_mode": "autonomous",
                "planner_owner": "java",
            }
            return

        run.main_direction = _legacy_direction(run)
        run.main_segment_index = 0
        run.branch_index = 0
        run.tried_directions_at_current = []
        run.origin_pos = None
        run.current_pos = None
        run.result = {
            "stage": "scanning_origin",
            "phase": "harvest",
            "harvest_purpose": "junction_scan",
            "after_harvest_phase": "choose_direction",
            "junction_established": False,
            "junction_pos": None,
            "execution_mode": "legacy",
        }

    def next_directive(
        self,
        run: SkillRun,
        terminal_snapshot: Optional[Mapping[str, Any]],
    ):
        if _execution_mode(run) == "autonomous":
            return self._next_autonomous(run, terminal_snapshot)
        if run.collected_count >= int(run.args["target_count"]):
            return self._complete(run)
        if terminal_snapshot is not None:
            terminal = dict(terminal_snapshot)
            kind = str(
                terminal.get("kind")
                or run.current_action_request.get("kind")
                or ""
            ).strip().lower()
            if kind == "harvest_blocks":
                directive = self._consume_harvest(run, terminal)
            elif kind == "excavate_segment":
                directive = self._consume_excavation(run, terminal)
            elif kind == "navigate":
                directive = self._consume_navigation(run, terminal)
            else:
                return Fail(
                    "UNKNOWN_CHILD_ACTION",
                    {"message": f"MineOreSkill cannot consume child kind {kind!r}"},
                )
            if directive is not None:
                return directive
        return self._directive_for_phase(run)

    def _next_autonomous(
        self,
        run: SkillRun,
        terminal_snapshot: Optional[Mapping[str, Any]],
    ):
        """Delegate sensing, route planning and execution to one Java child."""
        if terminal_snapshot is None:
            run.result.update({
                "stage": "delegating_to_java",
                "phase": "autonomous",
                "execution_mode": "autonomous",
                "planner_owner": "java",
            })
            return StartAction(
                "autonomous_mining",
                self._autonomous_action_args(run),
                timeout_ms=0,
            )

        terminal = dict(terminal_snapshot)
        result = _result(terminal)
        status = _status(terminal)
        phase = str(result.get("phase") or "").strip().upper()
        blocked_reason = str(
            result.get("blocked_reason")
            or terminal.get("end_reason")
            or "autonomous_mining_blocked"
        ).strip()
        decision_required = bool(result.get("decision_required", False))

        if decision_required or phase == "BLOCKED":
            facts, fact_errors = _validate_blocked_restart_facts(
                run, result, blocked_reason
            )
            if fact_errors:
                return Fail(
                    "SERVER_RESULT_INCONSISTENT",
                    {
                        **result,
                        "message": "Java BLOCKED terminal restart facts are inconsistent",
                        "fact_errors": fact_errors,
                        "restart_supported": False,
                        "restart_parameters": None,
                        "execution_mode": "autonomous",
                        "planner_owner": "java",
                        "child_terminal": terminal,
                    },
                    tuple(terminal.get("warnings") or ()),
                )

            run.collected_count = facts["collected_count"]
            if facts["remaining_target_count"] == 0:
                if facts["vein_locked"] is False:
                    return Complete({
                        **result,
                        "message": "mine_ore_target_reached_before_blocked_restart",
                        "completion_source": "blocked_terminal_goal_already_reached",
                        "execution_mode": "autonomous",
                        "planner_owner": "java",
                        "selector": dict(run.args["selector"]),
                        "target_metric": "blocks_harvested",
                        "target_count": int(run.args["target_count"]),
                        "blocks_harvested": run.collected_count,
                        "target_overshoot": max(
                            0,
                            run.collected_count - int(run.args["target_count"]),
                        ),
                    }, tuple(terminal.get("warnings") or ()))

            current_parameters = dict(run.args)
            policy = _blocked_restart_policy(blocked_reason)
            if facts["vein_locked"] is True:
                policy = {
                    "mode": "committed_vein_requires_recovery_or_explicit_abandon",
                    "restart_template_allowed": False,
                    "required_preconditions": [
                        "use_a_future_committed_vein_resume_protocol_or_explicitly_abandon_the_committed_vein",
                    ],
                    "allowed_overrides": [],
                    "requires_player_confirmation": True,
                    "committed_vein_must_not_be_superseded": True,
                }
            restart_template = None
            if policy.pop("restart_template_allowed", False):
                restart_template = dict(current_parameters)
                restart_template["target_count"] = facts["remaining_target_count"]
            decision = {
                "mode": "manual_review_or_abort",
                "adjustable_fields": list(policy.get("allowed_overrides", ())),
                "allowed_overrides": list(policy.get("allowed_overrides", ())),
                "required_preconditions": list(
                    policy.get("required_preconditions", ())
                ),
                "current_parameters": current_parameters,
                "restart_template": restart_template,
                "restart_parameters": restart_template,
                "restart_parameters_are_skill_args": restart_template is not None,
                "java_restart_supported": facts["java_restart_supported"],
                "conditional_restart_supported": restart_template is not None,
                "restart_template_available": restart_template is not None,
                "same_selector_progress_credit_applied": restart_template is not None,
                "different_selector_progress_credit_applied": False,
                "different_selector_requires_new_target_count": True,
                "progress_facts": {
                    "collected_so_far": facts["collected_count"],
                    "original_target_count": facts["target_count"],
                    "remaining_target_count": facts["remaining_target_count"],
                    "remaining_target_count_is_minimum": True,
                },
                "collected_so_far": facts["collected_count"],
                "original_target_count": facts["target_count"],
                "remaining_target_count": facts["remaining_target_count"],
                "remaining_target_count_is_minimum": True,
                "requires_player_confirmation": True,
                "in_place_resume_supported": False,
            }
            decision.update(policy)
            blocked_result = {
                **result,
                "execution_mode": "autonomous",
                "planner_owner": "java",
                "selector": dict(run.args["selector"]),
                "target_metric": "blocks_harvested",
                "blocks_harvested": run.collected_count,
                "decision_required": True,
                "decision": decision,
                "child_terminal": terminal,
            }
            return Blocked(
                _reason_code(blocked_reason, "AUTONOMOUS_MINING_BLOCKED"),
                blocked_result,
                tuple(terminal.get("warnings") or ()),
            )

        reported_count = max(
            0,
            _integer(
                result.get(
                    "collected_count",
                    result.get("blocks_harvested", result.get("harvested", 0)),
                ),
                0,
            ),
        )
        run.collected_count = max(run.collected_count, reported_count)

        if status == "SUCCEEDED" and (phase in {"", "COMPLETED"}):
            if run.collected_count < int(run.args["target_count"]):
                return Fail(
                    "SERVER_RESULT_INCONSISTENT",
                    {
                        **result,
                        "message": (
                            "Java reported COMPLETED before the requested target_count "
                            "was observed"
                        ),
                        "execution_mode": "autonomous",
                        "planner_owner": "java",
                        "blocks_harvested": run.collected_count,
                        "child_terminal": terminal,
                    },
                    tuple(terminal.get("warnings") or ()),
                )
            return Complete({
                **result,
                "message": "mine_ore_target_reached",
                "execution_mode": "autonomous",
                "planner_owner": "java",
                "selector": dict(run.args["selector"]),
                "target_metric": "blocks_harvested",
                "target_count": int(run.args["target_count"]),
                "blocks_harvested": run.collected_count,
                "target_overshoot": max(
                    0,
                    run.collected_count - int(run.args["target_count"]),
                ),
            }, tuple(terminal.get("warnings") or ()))

        reason = _reason_code(
            terminal.get("end_reason") or result.get("blocked_reason") or status,
            "AUTONOMOUS_MINING_FAILED",
        )
        return Fail(
            reason,
            {
                **result,
                "execution_mode": "autonomous",
                "planner_owner": "java",
                "blocks_harvested": run.collected_count,
                "child_terminal": terminal,
            },
            tuple(terminal.get("warnings") or ()),
        )

    @staticmethod
    def _autonomous_action_args(run: SkillRun) -> dict[str, Any]:
        return {
            "selector": dict(run.args["selector"]),
            "target_count": int(run.args["target_count"]),
            "direction": str(run.args.get("direction") or "auto"),
            "shape": str(run.args.get("shape") or "auto"),
            "segment_length": _segment_length(run),
            "speed": float(run.args.get("speed", 0.7)),
            "discovery_mode": str(
                run.args.get("discovery_mode") or "loaded_scan"
            ),
            "placement_policy": str(
                run.args.get("placement_policy")
                or "disabled"
            ),
            "max_placements": max(
                0, min(4096, _integer(run.args.get("max_placements"), 0))
            ),
        }

    def _directive_for_phase(self, run: SkillRun):
        scratch = run.result
        phase = str(scratch.get("phase") or "")
        if phase == "harvest":
            scratch["stage"] = "harvesting_nearby_vein"
            return self._harvest_action(run)
        if phase == "choose_direction":
            return self._choose_direction(run)
        if phase == "dig":
            direction = str(scratch.get("dig_direction") or "")
            segment_length = _segment_length(run)
            remaining = max(
                1,
                min(
                    segment_length,
                    _integer(scratch.get("segment_remaining"), segment_length),
                ),
            )
            scratch["stage"] = f"excavating_{scratch.get('dig_role') or 'segment'}"
            return StartAction(
                "excavate_segment",
                {
                    "direction": direction,
                    "shape": _legacy_shape(run)
                    if scratch.get("dig_role") == "main" else "level",
                    "length": remaining,
                },
                timeout_ms=0,
            )
        if phase == "navigate":
            target = _position(scratch.get("navigate_target"))
            if target is None:
                return self._blocked(
                    run, "RETURN_POSITION_UNKNOWN",
                    message="The skill cannot safely return because its checkpoint has no target",
                )
            scratch["stage"] = "returning_to_mining_anchor"
            return StartAction(
                "navigate",
                {"target": target, "speed": 0.7, "stop_distance": 1.0},
                timeout_ms=60000,
            )
        if phase == "finish_dig":
            return self._finish_direction(
                run,
                str(scratch.get("dig_role") or "opposite"),
                run.current_pos,
            )
        if phase == "blocked":
            return self._blocked(run, str(scratch.get("blocked_reason") or "BLOCKED"))
        return Fail(
            "INVALID_SKILL_PHASE",
            {"message": f"Unknown MineOreSkill phase: {phase or '<empty>'}"},
        )

    def _consume_harvest(self, run: SkillRun, terminal: Mapping[str, Any]):
        scratch = run.result
        result = _result(terminal)
        harvested = _harvested_count(result)
        if harvested > 0:
            run.collected_count += harvested
        if run.collected_count >= int(run.args["target_count"]):
            return self._complete(run)

        status = _status(terminal)
        message = str(result.get("message") or "")
        if status != "SUCCEEDED" and message not in _BENIGN_SCAN_MESSAGES:
            reason = str(terminal.get("end_reason") or message or status or "HARVEST_FAILED")
            return self._blocked(
                run,
                reason,
                message="A discovered or nearby ore vein could not be harvested safely",
                child=terminal,
            )

        after_phase = str(scratch.get("after_harvest_phase") or "choose_direction")
        return_target = _position(scratch.get("return_after_harvest"))
        scratch.pop("return_after_harvest", None)
        scratch.pop("harvest_purpose", None)
        if return_target is not None:
            scratch["phase"] = "navigate"
            scratch["navigate_target"] = return_target
            scratch["after_navigate_phase"] = after_phase
            return None
        scratch["phase"] = after_phase
        return None

    def _consume_excavation(self, run: SkillRun, terminal: Mapping[str, Any]):
        scratch = run.result
        result = _result(terminal)
        status = _status(terminal)
        stop_reason = str(result.get("stop_reason") or "").strip().lower()
        direction = str(scratch.get("dig_direction") or result.get("direction") or "")
        role = str(scratch.get("dig_role") or _role_for_direction(run.main_direction, direction))
        real_end = _position(result.get("real_end"))
        dug = max(0, _integer(result.get("segments_dug"), 0))
        if real_end is not None:
            run.current_pos = real_end
            if run.origin_pos is None:
                shape = str(
                    result.get("shape")
                    or (_legacy_shape(run) if role == "main" else "level")
                ).lower()
                run.origin_pos = _segment_origin(real_end, direction, shape, dug)
        remaining = max(
            0,
            _integer(scratch.get("segment_remaining"), _segment_length(run)) - dug,
        )
        scratch["segment_remaining"] = remaining

        if status == "SUCCEEDED" and stop_reason == "ore_encountered":
            if real_end is None:
                return self._blocked(
                    run, "ORE_ENCOUNTER_POSITION_UNKNOWN",
                    message="excavate_segment found ore but did not report real_end",
                    child=terminal,
                )
            scratch["phase"] = "harvest"
            scratch["harvest_purpose"] = "ore_encountered"
            scratch["after_harvest_phase"] = "dig" if remaining > 0 else "finish_dig"
            scratch["return_after_harvest"] = real_end
            return None

        if status == "SUCCEEDED" and stop_reason == "completed":
            return self._finish_direction(run, role, real_end)

        failure = str(
            result.get("message") or stop_reason
            or terminal.get("end_reason") or status or "EXCAVATION_FAILED"
        )
        return self._handle_direction_end(run, role, real_end, failure, terminal)

    def _consume_navigation(self, run: SkillRun, terminal: Mapping[str, Any]):
        scratch = run.result
        if _status(terminal) != "SUCCEEDED":
            return self._blocked(
                run,
                str(terminal.get("end_reason") or "RETURN_PATH_FAILED"),
                message="The maid could not safely return to the fishbone junction",
                child=terminal,
            )
        target = _position(scratch.get("navigate_target"))
        if target is not None:
            run.current_pos = target
        scratch.pop("navigate_target", None)
        scratch["phase"] = str(scratch.pop("after_navigate_phase", "choose_direction"))
        return None

    def _choose_direction(self, run: SkillRun):
        scratch = run.result
        order = _direction_order(
            run.main_direction,
            bool(scratch.get("junction_established")),
        )
        tried = set(run.tried_directions_at_current)
        direction = next((value for value in order if value not in tried), None)
        if direction is None:
            return self._blocked(
                run,
                "ALL_DIRECTIONS_BLOCKED",
                message="Every horizontal direction at the current fishbone junction was tried",
            )
        run.tried_directions_at_current.append(direction)
        role = _role_for_direction(run.main_direction, direction)
        run.branch_index = {"left": 0, "right": 1, "opposite": 2}.get(role, 0)
        scratch["phase"] = "dig"
        scratch["dig_direction"] = direction
        scratch["dig_role"] = role
        scratch["segment_remaining"] = _segment_length(run)
        return self._directive_for_phase(run)

    def _finish_direction(
        self, run: SkillRun, role: str, real_end: Optional[dict[str, int]]
    ):
        scratch = run.result
        if real_end is None:
            return self._blocked(
                run, "SEGMENT_END_UNKNOWN",
                message="excavate_segment completed without a real_end position",
            )
        if role == "main":
            run.main_segment_index += 1
            run.current_pos = real_end
            run.tried_directions_at_current = []
            scratch.clear()
            scratch.update({
                "stage": "scanning_junction",
                "phase": "harvest",
                "harvest_purpose": "junction_scan",
                "after_harvest_phase": "choose_direction",
                "junction_established": True,
                "junction_pos": real_end,
            })
            return self._harvest_action(run)
        return self._return_to_junction_or_continue(run)

    def _handle_direction_end(
        self,
        run: SkillRun,
        role: str,
        real_end: Optional[dict[str, int]],
        failure: str,
        terminal: Mapping[str, Any],
    ):
        scratch = run.result
        scratch["last_direction_failure"] = {
            "direction": scratch.get("dig_direction"),
            "role": role,
            "reason": failure,
            "child_result": _result(terminal),
        }
        if scratch.get("junction_pos") is None and real_end is not None:
            scratch["junction_pos"] = real_end
        if len(set(run.tried_directions_at_current)) >= 4:
            return self._blocked(run, "ALL_DIRECTIONS_BLOCKED", child=terminal)
        return self._return_to_junction_or_continue(run)

    def _return_to_junction_or_continue(self, run: SkillRun):
        scratch = run.result
        junction = _position(scratch.get("junction_pos"))
        if junction is None:
            scratch["phase"] = "choose_direction"
            return None
        if run.current_pos == junction:
            scratch["phase"] = "choose_direction"
            return None
        scratch["phase"] = "navigate"
        scratch["navigate_target"] = junction
        scratch["after_navigate_phase"] = "choose_direction"
        return None

    def _harvest_action(self, run: SkillRun) -> StartAction:
        # target_count is a minimum target.  Always collect the complete
        # connected vein, even when that makes the final count overshoot.
        return StartAction(
            "harvest_blocks",
            {
                "selector": dict(run.args["selector"]),
                "max_blocks": 1,
                "vein_mining": True,
                "tool_policy": "require_correct",
                "mining_plan": {"mode": "nearby"},
            },
            timeout_ms=0,
        )

    def _complete(self, run: SkillRun) -> Complete:
        return Complete({
            "message": "mine_ore_target_reached",
            "selector": dict(run.args["selector"]),
            "target_metric": "blocks_harvested",
            "target_count": int(run.args["target_count"]),
            "blocks_harvested": run.collected_count,
            "target_overshoot": max(
                0, run.collected_count - int(run.args["target_count"])
            ),
            "main_segments_completed": run.main_segment_index,
            "strategy": "fishbone",
            "shape": _legacy_shape(run),
            "execution_mode": "legacy",
        })

    def _blocked(
        self,
        run: SkillRun,
        reason: str,
        *,
        message: str = "",
        child: Optional[Mapping[str, Any]] = None,
    ) -> Blocked:
        scratch = run.result
        junction = _position(scratch.get("junction_pos"))
        failure = scratch.get("last_direction_failure")
        suggestions: list[dict[str, Any]] = []
        reason_upper = str(reason or "BLOCKED").upper()
        child_result = _result(child or {})
        combined_text = " ".join((
            reason_upper,
            str(child_result.get("message") or ""),
            str(child_result.get("stop_reason") or ""),
        )).lower()
        if "tool" in combined_text:
            suggestions.append({
                "kind": "provide_tool",
                "basis": "correct_harvesting_tool_required",
                "requires_confirmation": True,
            })
        suggestions.append({
            "kind": "reposition",
            "basis": "verified_safe_supported_two_block_clearance"
            if junction is not None else "safe_position_unknown",
            "requires_confirmation": junction is None,
        })
        suggestions.append({
            "kind": "clear_obstruction",
            "basis": f"blocked_direction:{scratch.get('dig_direction') or 'unknown'}",
            "requires_confirmation": True,
        })
        suggestions.append({
            "kind": "change_level",
            "basis": "current_dimension_unknown",
            "requires_confirmation": True,
        })
        suggestions.append({
            "kind": "return_to_origin",
            "basis": "skill_origin_unknown" if run.origin_pos is None else "checkpoint_origin",
            "requires_confirmation": run.origin_pos is None,
        })
        suggestions.append({
            "kind": "abort",
            "basis": "keep_current_safe_terminal_state",
            "requires_confirmation": False,
        })
        return Blocked(
            reason_upper,
            {
                "message": message or reason_upper.lower(),
                "selector": dict(run.args["selector"]),
                "target_metric": "blocks_harvested",
                "target_count": int(run.args["target_count"]),
                "blocks_harvested": run.collected_count,
                "junction_pos": junction,
                "main_direction": run.main_direction,
                "tried_directions": list(run.tried_directions_at_current),
                "last_direction_failure": failure,
                "child_terminal": dict(child or {}),
                "suggestions": suggestions,
            },
        )


def _direction_order(main: str, junction_established: bool) -> tuple[str, ...]:
    sides = (_LEFT[main], _RIGHT[main])
    return (*sides, main, _OPPOSITE[main]) if junction_established \
        else (main, *sides, _OPPOSITE[main])


def _role_for_direction(main: str, direction: str) -> str:
    if direction == main:
        return "main"
    if direction == _LEFT[main]:
        return "left"
    if direction == _RIGHT[main]:
        return "right"
    return "opposite"


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"mine_ore.{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"mine_ore.{name} must be an integer") from None
    if str(value).strip() not in {str(number), f"{number}.0"} and not isinstance(value, int):
        raise ValueError(f"mine_ore.{name} must be an integer")
    if number < 1:
        raise ValueError(f"mine_ore.{name} must be positive")
    return number


def _bounded_integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    number = _positive_integer(value, name)
    if number < minimum or number > maximum:
        raise ValueError(
            f"mine_ore.{name} must be between {minimum} and {maximum}"
        )
    return number


def _bounded_nonnegative_integer(value: Any, name: str, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"mine_ore.{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"mine_ore.{name} must be an integer") from None
    if str(value).strip() not in {str(number), f"{number}.0"} \
            and not isinstance(value, int):
        raise ValueError(f"mine_ore.{name} must be an integer")
    if number < 0 or number > maximum:
        raise ValueError(f"mine_ore.{name} must be between 0 and {maximum}")
    return number


def _bounded_number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"mine_ore.{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"mine_ore.{name} must be a number") from None
    if number < minimum or number > maximum:
        raise ValueError(
            f"mine_ore.{name} must be between {minimum} and {maximum}"
        )
    return number


def _blocked_restart_policy(reason: Any) -> dict[str, Any]:
    """Return a conservative restart-template policy for one BLOCKED reason."""
    code = _reason_code(reason, "AUTONOMOUS_MINING_BLOCKED")
    if code == "NO_BUILDING_MATERIAL":
        return {
            "mode": "provide_material_or_restart_without_construction",
            "recommended_actions": [
                "put ordinary full-cube blocks in the maid backpack",
                "restart with placement_policy=disabled and a different direction",
                "abort mining",
            ],
            "restart_template_allowed": True,
            "required_preconditions": [
                "provide_safe_full_cube_building_material_or_disable_construction",
                "if_construction_is_disabled_change_to_a_route_that_needs_no_placement",
            ],
            "allowed_overrides": [
                "direction", "shape", "segment_length",
                "placement_policy", "max_placements",
            ],
            "requires_player_confirmation": True,
        }
    if code == "PLACEMENT_BUDGET_EXHAUSTED":
        return {
            "mode": "raise_placement_limit_or_reroute",
            "recommended_actions": [
                "restart with max_placements=0 for no placement limit",
                "choose a different direction or shape",
                "abort mining",
            ],
            "restart_template_allowed": True,
            "required_preconditions": [
                "raise_or_remove_the_placement_budget_or_choose_a_route_with_fewer_placements",
            ],
            "allowed_overrides": [
                "direction", "shape", "segment_length", "max_placements",
            ],
            "requires_player_confirmation": True,
        }
    if code == "WATER_SEAL_FAILED":
        return {
            "mode": "reroute_or_abort_water_hazard",
            "recommended_actions": [
                "choose a different direction or shape",
                "abort mining",
            ],
            "restart_template_allowed": False,
            "required_preconditions": [
                "obtain_player_confirmation_for_a_verified_safe_route_away_from_the_water_hazard",
            ],
            "allowed_overrides": [],
            "requires_player_confirmation": True,
        }
    if code == "WATER_SEAL_REQUIRES_DRY_START":
        return {
            "mode": "relocate_to_dry_stance_then_restart",
            "recommended_actions": [
                "move the maid to nearby dry supported ground",
                "restart with a different direction or shape",
                "abort mining",
            ],
            "restart_template_allowed": False,
            "required_preconditions": [
                "move_the_maid_to_verified_dry_supported_ground",
                "reassess_the_water_hazard_before_creating_a_new_skill",
            ],
            "allowed_overrides": [],
            "requires_player_confirmation": True,
        }
    if code == "PLACEMENT_PROTECTED":
        return {
            "mode": "leave_protected_area_or_abort",
            "recommended_actions": [
                "move the maid outside the protected area before starting a new skill",
                "choose a route that does not require placement",
                "abort mining",
            ],
            "restart_template_allowed": False,
            "required_preconditions": [
                "leave_the_protected_area_or_obtain_authorized_player_action",
            ],
            "allowed_overrides": [],
            "requires_player_confirmation": True,
            "must_not_bypass_protection": True,
        }
    if code == "BACKPACK_FULL":
        return {
            "mode": "unload_or_free_space_before_restart_or_abort",
            "recommended_actions": [
                "navigate the maid back to a storage chest or player to unload the collected ore",
                "explicitly remove or discard items to create backpack capacity before restarting mine_ore",
                "abort mining",
            ],
            "restart_template_allowed": True,
            "required_preconditions": [
                "create_real_backpack_capacity_by_unloading_or_removing_items",
                "recheck_capacity_before_starting_the_new_skill",
            ],
            "allowed_overrides": [],
            "requires_player_confirmation": True,
            "in_place_resume_supported": False,
            "selector_change_without_free_space_supported": False,
            "automatic_unload_supported": False,
            "requires_manual_inventory_change": True,
            "restart_requires_capacity_recheck": True,
        }
    if code in {"TOOL_NOT_FOUND", "NO_CORRECT_TOOL"}:
        return {
            "mode": "equip_correct_tool_then_restart_or_abort",
            "restart_template_allowed": True,
            "required_preconditions": [
                "equip_and_verify_a_correct_harvest_tool_in_the_maid_main_hand",
            ],
            "allowed_overrides": [],
            "recommended_actions": [
                "equip the correct tool and verify it before restarting",
                "abort mining",
            ],
        }
    if code == "TARGET_CHANGED":
        return {
            "mode": "rescan_then_restart_or_abort",
            "restart_template_allowed": True,
            "required_preconditions": [
                "rescan_and_confirm_the_selector_still_has_a_valid_target",
            ],
            "allowed_overrides": ["discovery_mode"],
        }

    denied_fragments = (
        "PROTECTED", "HAZARD", "LAVA", "WATER", "ENTITY_UNLOADED",
        "COMMITTED_VEIN", "INTERNAL", "SUPERSEDED", "HAND_CONFLICT",
        "SERVER_STATE_LOST", "UNBREAKABLE", "BLOCK_PROTECTED", "UNSAFE",
    )
    if any(fragment in code for fragment in denied_fragments):
        return {
            "mode": "manual_review_or_abort",
            "restart_template_allowed": False,
            "required_preconditions": [
                "obtain_new_server_verified_safety_or_authorization_evidence",
            ],
            "allowed_overrides": [],
        }

    route_failure = (
        code in {"PATH_NOT_FOUND", "STUCK"}
        or "NAVIGATION" in code
        or "TERRAIN_STEP" in code
        or code.endswith("_STUCK")
        or "NO_PROGRESS" in code
    )
    if route_failure:
        return {
            "mode": "restart_with_verified_route_change_or_abort",
            "restart_template_allowed": True,
            "restart_template_ready_without_overrides": False,
            "restart_template_must_not_be_submitted_unchanged": True,
            "minimum_required_override_count": 1,
            "required_preconditions": [
                "change_at_least_one_allowed_route_override",
                "use_new_server_verified_route_evidence",
            ],
            "allowed_overrides": [
                "direction", "shape", "segment_length", "speed",
                "discovery_mode", "placement_policy", "max_placements",
            ],
        }
    return {
        "mode": "manual_review_or_abort",
        "restart_template_allowed": False,
        "required_preconditions": [
            "obtain_reason_specific_server_or_player_evidence_before_restarting",
        ],
        "allowed_overrides": [],
    }


def _validate_blocked_restart_facts(
    run: SkillRun,
    terminal_result: Mapping[str, Any],
    blocked_reason: Any,
) -> tuple[dict[str, Any], list[str]]:
    """Validate Java restart facts against the durable Python checkpoint."""
    result = dict(terminal_result or {})
    errors: list[str] = []

    target_count = _strict_nonnegative_integer_field(
        result, "target_count", errors
    )
    collected_count = _strict_nonnegative_integer_field(
        result, "collected_count", errors
    )
    remaining_target_count = _strict_nonnegative_integer_field(
        result, "remaining_target_count", errors
    )
    restart_supported = _strict_boolean_field(
        result, "restart_supported", errors
    )

    expected_target = int(run.args["target_count"])
    checkpoint_collected = max(0, int(run.collected_count))
    if target_count is not None and target_count != expected_target:
        errors.append("terminal_target_count_does_not_match_skill_args")
    if collected_count is not None and collected_count < checkpoint_collected:
        errors.append("terminal_collected_count_regressed_below_checkpoint")

    effective_collected = (
        collected_count if collected_count is not None else checkpoint_collected
    )
    expected_remaining = max(0, expected_target - effective_collected)
    if (remaining_target_count is not None
            and remaining_target_count != expected_remaining):
        errors.append("terminal_remaining_target_count_is_inconsistent")

    code = _reason_code(blocked_reason, "AUTONOMOUS_MINING_BLOCKED")
    if code == "BACKPACK_FULL":
        expected_java_restart = expected_remaining > 0
        if (restart_supported is not None
                and restart_supported != expected_java_restart):
            errors.append("terminal_restart_supported_is_inconsistent")

    if "vein_locked" not in result:
        errors.append("terminal_vein_locked_is_missing")
        vein_locked = None
    else:
        vein_locked_value = result.get("vein_locked")
        if not isinstance(vein_locked_value, bool):
            errors.append("terminal_vein_locked_must_be_boolean")
            vein_locked = None
        else:
            vein_locked = vein_locked_value

    return {
        "target_count": expected_target,
        "collected_count": effective_collected,
        "remaining_target_count": expected_remaining,
        "java_restart_supported": restart_supported,
        "vein_locked": vein_locked,
    }, errors


def _strict_nonnegative_integer_field(
    source: Mapping[str, Any], name: str, errors: list[str]
) -> Optional[int]:
    if name not in source:
        errors.append(f"terminal_{name}_is_missing")
        return None
    value = source.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"terminal_{name}_must_be_a_nonnegative_integer")
        return None
    return value


def _strict_boolean_field(
    source: Mapping[str, Any], name: str, errors: list[str]
) -> Optional[bool]:
    if name not in source:
        errors.append(f"terminal_{name}_is_missing")
        return None
    value = source.get(name)
    if not isinstance(value, bool):
        errors.append(f"terminal_{name}_must_be_boolean")
        return None
    return value


def _execution_mode(run: SkillRun) -> str:
    """Missing mode identifies a v1 fishbone checkpoint from before Java autonomy."""
    explicit = str(run.args.get("execution_mode") or "").strip().lower()
    if explicit in {"autonomous", "legacy"}:
        return explicit
    return "legacy"


def _legacy_direction(run: SkillRun) -> str:
    direction = str(run.args.get("direction") or "auto").strip().lower()
    return direction if direction in _DIRECTIONS else "north"


def _legacy_shape(run: SkillRun) -> str:
    shape = str(run.args.get("shape") or "auto").strip().lower()
    return shape if shape in {"level", "staircase_down"} else "staircase_down"


def _segment_length(run: SkillRun) -> int:
    return max(1, min(8, _integer(run.args.get("segment_length"), 8)))


def _reason_code(value: Any, fallback: str) -> str:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return text if text and text != "NONE" else fallback


def _integer(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _position(value: Any) -> Optional[dict[str, int]]:
    if not isinstance(value, Mapping):
        return None
    try:
        return {axis: int(value[axis]) for axis in ("x", "y", "z")}
    except (KeyError, TypeError, ValueError):
        return None


def _result(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    value = snapshot.get("result") if isinstance(snapshot, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _status(snapshot: Mapping[str, Any]) -> str:
    return str(snapshot.get("status") or "").strip().upper()


def _harvested_count(result: Mapping[str, Any]) -> int:
    # Never infer collection from cleared blocks, inventory deltas or vein size.
    for key in ("blocks_harvested", "harvested"):
        if key in result:
            return max(0, _integer(result.get(key), 0))
    return 0


def _segment_origin(
    real_end: Mapping[str, int], direction: str, shape: str, segments_dug: int
) -> dict[str, int]:
    origin = {axis: int(real_end[axis]) for axis in ("x", "y", "z")}
    distance = max(0, int(segments_dug))
    if direction == "north":
        origin["z"] += distance
    elif direction == "south":
        origin["z"] -= distance
    elif direction == "east":
        origin["x"] -= distance
    elif direction == "west":
        origin["x"] += distance
    if shape == "staircase_down":
        origin["y"] += distance
    return origin
