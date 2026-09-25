import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

_base = importlib.import_module("neko_tlm.maid_agent.skills.base")
Blocked = _base.Blocked
Complete = _base.Complete
SkillRun = _base.SkillRun
StartAction = _base.StartAction
MineOreSkill = importlib.import_module(
    "neko_tlm.maid_agent.skills.mine_ore"
).MineOreSkill


def run_for(args=None, *, execution_mode="legacy"):
    definition = MineOreSkill()
    raw = dict(args or {
        "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
        "target_count": 5,
        "target_metric": "blocks_harvested",
    })
    if execution_mode:
        raw.setdefault("execution_mode", execution_mode)
    normalized = definition.normalize_args(raw)
    run = SkillRun("00000000-0000-0000-0000-000000000001", "maid", "mine_ore", normalized)
    definition.initialize(run)
    return definition, run


def terminal(kind, *, status="SUCCEEDED", result=None, end_reason="COMPLETED"):
    return {
        "action_id": "child", "maid_id": "maid", "generation": 1,
        "sequence": 2, "kind": kind, "status": status,
        "end_reason": end_reason, "result": dict(result or {}),
    }


class MineOreSkillTests(unittest.TestCase):
    def test_normalizes_new_runs_to_java_autonomy_and_legacy_is_explicit(self):
        definition, autonomous = run_for(execution_mode=None)
        self.assertEqual("autonomous", autonomous.args["execution_mode"])
        self.assertEqual("auto", autonomous.args["direction"])
        self.assertEqual("auto", autonomous.args["shape"])
        self.assertEqual("loaded_scan", autonomous.args["discovery_mode"])
        self.assertEqual(
            "safe_support_and_water_seal", autonomous.args["placement_policy"]
        )
        self.assertEqual(0, autonomous.args["max_placements"])

        definition, run = run_for()
        self.assertEqual("fishbone", run.args["strategy"])
        self.assertEqual("legacy", run.args["execution_mode"])
        self.assertEqual("auto", run.args["direction"])
        self.assertEqual("auto", run.args["shape"])
        self.assertEqual("north", run.main_direction)
        aliased = definition.normalize_args({
            "selector": {"type": "block", "id": "mod:tin_ore"},
            "target_count": 2,
            "target_metric": "blocks_harvested",
            "execution_mode": "legacy", "strategy": "auto",
            "direction": "west", "shape": "level",
        })
        self.assertEqual("fishbone", aliased["strategy"])
        self.assertEqual("level", aliased["shape"])

    def test_autonomous_mode_is_one_java_owned_child_with_frozen_mapping(self):
        definition, run = run_for({
            "selector": {"type": "tag", "id": "minecraft:iron_ores"},
            "target_count": 12,
            "target_metric": "blocks_harvested",
            "direction": "east",
            "shape": "staircase_down",
            "segment_length": 6,
            "speed": 0.8,
            "discovery_mode": "exposed_only",
            "placement_policy": "disabled",
            "max_placements": 12,
        }, execution_mode=None)
        directive = definition.next_directive(run, None)
        self.assertIsInstance(directive, StartAction)
        self.assertEqual("autonomous_mining", directive.kind)
        self.assertEqual({
            "selector": {"type": "tag", "id": "minecraft:iron_ores"},
            "target_count": 12,
            "direction": "east",
            "shape": "staircase_down",
            "segment_length": 6,
            "speed": 0.8,
            "discovery_mode": "exposed_only",
            "placement_policy": "disabled",
            "max_placements": 12,
        }, directive.args)

    def test_old_autonomous_checkpoint_does_not_gain_placement_authority(self):
        definition = MineOreSkill()
        run = SkillRun(
            "00000000-0000-0000-0000-000000000099", "maid", "mine_ore",
            {
                "selector": {"type": "tag", "id": "minecraft:iron_ores"},
                "target_count": 4,
                "target_metric": "blocks_harvested",
                "execution_mode": "autonomous",
                "direction": "auto",
                "shape": "auto",
                "segment_length": 8,
                "speed": 0.7,
                "discovery_mode": "loaded_scan",
            },
        )
        definition.initialize(run)

        directive = definition.next_directive(run, None)

        self.assertEqual("disabled", directive.args["placement_policy"])
        self.assertEqual(0, directive.args["max_placements"])

    def test_autonomous_construction_block_returns_concrete_decision(self):
        definition, run = run_for(execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="TOOL_NOT_FOUND",
            result={
                "phase": "BLOCKED",
                "blocked_reason": "no_building_material",
                "collected_count": 0,
                "target_count": 5,
                "remaining_target_count": 5,
                "restart_supported": False,
                "vein_locked": False,
                "decision_required": True,
            },
        ))
        self.assertIsInstance(directive, Blocked)
        self.assertEqual(
            "provide_material_or_restart_without_construction",
            directive.result["decision"]["mode"],
        )
        self.assertIn(
            "placement_policy",
            directive.result["decision"]["adjustable_fields"],
        )

    def test_backpack_full_after_goal_unlocked_completes_without_restart(self):
        definition, run = run_for({
            "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
            "target_count": 10,
            "target_metric": "blocks_harvested",
        }, execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="SAFETY_PREEMPTED",
            result={
                "phase": "BLOCKED",
                "blocked_reason": "backpack_full",
                "collected_count": 12,
                "target_count": 10,
                "remaining_target_count": 0,
                "restart_supported": False,
                "vein_locked": False,
                "decision_required": True,
            },
        ))
        self.assertIsInstance(directive, Complete)
        self.assertEqual(12, directive.result["blocks_harvested"])
        self.assertEqual(
            "blocked_terminal_goal_already_reached",
            directive.result["completion_source"],
        )

    def test_autonomous_blocked_terminal_requests_restart_decision(self):
        definition, run = run_for(execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="BLOCKED",
            result={
                "phase": "BLOCKED",
                "collected_count": 3,
                "target_count": 5,
                "blocked_reason": "lava_hazard",
                "remaining_target_count": 2,
                "restart_supported": False,
                "vein_locked": False,
                "decision_required": True,
                "segments_dug": 2,
                "cleared_blocks": 8,
            },
        ))
        self.assertIsInstance(directive, Blocked)
        self.assertEqual("LAVA_HAZARD", directive.reason)
        self.assertTrue(directive.result["decision_required"])
        decision = directive.result["decision"]
        self.assertEqual("manual_review_or_abort", decision["mode"])
        self.assertIsNone(decision["restart_template"])
        self.assertFalse(decision["in_place_resume_supported"])
        self.assertEqual(3, directive.result["blocks_harvested"])

    def test_old_checkpoint_without_execution_mode_stays_legacy(self):
        definition = MineOreSkill()
        old_args = {
            "selector": {"type": "tag", "id": "minecraft:coal_ores"},
            "target_count": 1,
            "target_metric": "blocks_harvested",
            "strategy": "fishbone",
            "direction": "north",
            "shape": "staircase_down",
        }
        run = SkillRun("old", "maid", "mine_ore", old_args)
        run.result = {
            "phase": "harvest",
            "harvest_purpose": "junction_scan",
            "after_harvest_phase": "choose_direction",
        }
        directive = definition.next_directive(run, None)
        self.assertEqual("harvest_blocks", directive.kind)

    def test_initial_scan_uses_complete_nearby_vein(self):
        definition, run = run_for()
        directive = definition.next_directive(run, None)
        self.assertIsInstance(directive, StartAction)
        self.assertEqual("harvest_blocks", directive.kind)
        self.assertTrue(directive.args["vein_mining"])
        self.assertEqual(1, directive.args["max_blocks"])
        self.assertEqual({"mode": "nearby"}, directive.args["mining_plan"])

    def test_empty_initial_scan_starts_downward_main_segment(self):
        definition, run = run_for()
        definition.next_directive(run, None)
        run.current_action_request = {"kind": "harvest_blocks"}
        directive = definition.next_directive(run, terminal(
            "harvest_blocks", status="FAILED", end_reason="TARGET_CHANGED",
            result={"message": "no_matching_block_found"},
        ))
        self.assertEqual("excavate_segment", directive.kind)
        self.assertEqual("north", directive.args["direction"])
        self.assertEqual("staircase_down", directive.args["shape"])
        self.assertEqual(8, directive.args["length"])

    def test_main_segment_creates_junction_then_left_and_right_level_branches(self):
        definition, run = run_for()
        run.result.update({
            "phase": "dig", "dig_role": "main", "dig_direction": "north",
            "segment_remaining": 8, "junction_established": False,
        })
        directive = definition.next_directive(run, terminal(
            "excavate_segment",
            result={"stop_reason": "completed", "segments_dug": 8,
                    "real_end": {"x": 0, "y": 56, "z": -8}},
        ))
        self.assertEqual("harvest_blocks", directive.kind)
        self.assertEqual(1, run.main_segment_index)

        run.current_action_request = {"kind": "harvest_blocks"}
        directive = definition.next_directive(run, terminal(
            "harvest_blocks", status="FAILED", end_reason="TARGET_CHANGED",
            result={"message": "no_matching_block_found"},
        ))
        self.assertEqual("west", directive.args["direction"])
        self.assertEqual("level", directive.args["shape"])

        run.current_action_request = {"kind": "excavate_segment"}
        directive = definition.next_directive(run, terminal(
            "excavate_segment",
            result={"stop_reason": "completed", "segments_dug": 8,
                    "real_end": {"x": -8, "y": 56, "z": -8}},
        ))
        self.assertEqual("navigate", directive.kind)
        self.assertEqual({"x": 0, "y": 56, "z": -8}, directive.args["target"])

        run.current_action_request = {"kind": "navigate"}
        directive = definition.next_directive(run, terminal("navigate", result={}))
        self.assertEqual("east", directive.args["direction"])
        self.assertEqual("level", directive.args["shape"])

    def test_ore_encounter_harvests_whole_vein_and_resumes_from_real_end(self):
        definition, run = run_for()
        run.result.update({
            "phase": "dig", "dig_role": "main", "dig_direction": "north",
            "segment_remaining": 8, "junction_established": False,
        })
        directive = definition.next_directive(run, terminal(
            "excavate_segment",
            result={"stop_reason": "ore_encountered", "segments_dug": 3,
                    "real_end": {"x": 2, "y": 60, "z": -3}},
        ))
        self.assertEqual("harvest_blocks", directive.kind)
        self.assertEqual(1, directive.args["max_blocks"])
        self.assertEqual({"x": 2, "y": 63, "z": 0}, run.origin_pos)

        run.current_action_request = {"kind": "harvest_blocks"}
        directive = definition.next_directive(run, terminal(
            "harvest_blocks", result={"harvested": 2, "cleared_blocks": [1] * 99},
        ))
        self.assertEqual(2, run.collected_count)
        self.assertEqual("navigate", directive.kind)
        self.assertEqual({"x": 2, "y": 60, "z": -3}, directive.args["target"])

        run.current_action_request = {"kind": "navigate"}
        directive = definition.next_directive(run, terminal("navigate"))
        self.assertEqual("excavate_segment", directive.kind)
        self.assertEqual(5, directive.args["length"])

    def test_target_is_minimum_and_whole_vein_may_overshoot(self):
        definition, run = run_for()
        directive = definition.next_directive(run, terminal(
            "harvest_blocks", result={"blocks_harvested": 7},
        ))
        self.assertIsInstance(directive, Complete)
        self.assertEqual(7, run.collected_count)
        self.assertEqual(2, directive.result["target_overshoot"])

    def test_all_directions_blocked_has_strict_structured_suggestions(self):
        definition, run = run_for()
        run.tried_directions_at_current = ["north", "west", "east", "south"]
        run.result.update({
            "phase": "choose_direction", "junction_established": True,
            "junction_pos": {"x": 1, "y": 30, "z": 2},
        })
        directive = definition.next_directive(run, None)
        self.assertIsInstance(directive, Blocked)
        self.assertEqual("ALL_DIRECTIONS_BLOCKED", directive.reason)
        allowed = {"kind", "target_y", "basis", "requires_confirmation"}
        for suggestion in directive.result["suggestions"]:
            self.assertLessEqual(set(suggestion), allowed)
        change_level = next(
            item for item in directive.result["suggestions"]
            if item["kind"] == "change_level"
        )
        self.assertNotIn("target_y", change_level)
        self.assertEqual("current_dimension_unknown", change_level["basis"])

    def test_rejects_unfrozen_metric_strategy_and_shape(self):
        definition = MineOreSkill()
        base = {
            "selector": {"type": "tag", "id": "minecraft:iron_ores"},
            "target_count": 1, "target_metric": "blocks_harvested",
        }
        for field, value in (
            ("target_metric", "items_in_inventory"),
            ("strategy", "random_walk"),
            ("shape", "vertical_shaft"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    definition.normalize_args({**base, field: value})

    # ===== M0b: remaining_target_count 容量感知重启事实 =====

    def _backpack_full_directive(self, collected, target):
        """构造一个 collected/target 的 BACKPACK_FULL 终态指令,用于 remaining 断言。"""
        definition, run = run_for({
            "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
            "target_count": target,
            "target_metric": "blocks_harvested",
        }, execution_mode=None)
        remaining = max(0, target - collected)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="SAFETY_PREEMPTED",
            result={
                "phase": "BLOCKED",
                "blocked_reason": "backpack_full",
                "collected_count": collected,
                "target_count": target,
                "remaining_target_count": remaining,
                "restart_supported": remaining > 0,
                "vein_locked": False,
                "decision_required": True,
            },
        ))
        return directive

    def test_backpack_full_partial_progress_5_of_10_restarts_with_5(self):
        # 已采 5/10 → remaining=5, restart_supported=true,
        # restart_parameters.target_count=5, 保留原 selector
        directive = self._backpack_full_directive(collected=5, target=10)
        self.assertIsInstance(directive, Blocked)
        decision = directive.result["decision"]
        self.assertEqual(5, decision["remaining_target_count"])
        self.assertTrue(decision["conditional_restart_supported"])
        self.assertTrue(decision["java_restart_supported"])
        restart_params = decision["restart_template"]
        self.assertIsNotNone(restart_params)
        self.assertEqual(5, restart_params["target_count"])
        self.assertEqual("blocks_harvested", restart_params["target_metric"])
        self.assertEqual(
            {"type": "tag", "id": "minecraft:diamond_ores"},
            restart_params["selector"],
        )
        self.assertEqual(
            restart_params,
            MineOreSkill().normalize_args(restart_params),
        )
        # 保留原路线参数
        self.assertIn("direction", restart_params)
        self.assertIn("shape", restart_params)
        self.assertIn("segment_length", restart_params)

    def test_backpack_full_zero_progress_0_of_10_restarts_with_10(self):
        # 已采 0/10 → remaining=10, restart_supported=true,
        # restart_parameters.target_count=10 (原值)
        directive = self._backpack_full_directive(collected=0, target=10)
        self.assertIsInstance(directive, Blocked)
        decision = directive.result["decision"]
        self.assertEqual(10, decision["remaining_target_count"])
        self.assertTrue(decision["conditional_restart_supported"])
        restart_params = decision["restart_template"]
        self.assertIsNotNone(restart_params)
        self.assertEqual(10, restart_params["target_count"])

    def test_backpack_full_collected_exceeds_target_without_vein_fact_fails(self):
        directive = self._backpack_full_directive(collected=12, target=10)
        # helper supplies vein_locked=False, so an achieved minimum is complete.
        self.assertIsInstance(directive, Complete)

    def test_backpack_full_checkpoint_and_decision_carry_same_remaining(self):
        # checkpoint(run.result) 和 LLM(decision) 收到相同的 remaining_target_count
        directive = self._backpack_full_directive(collected=5, target=10)
        self.assertIsInstance(directive, Blocked)
        # directive.result 是 Blocked 的结果,包含 Java 原始字段(**result)和 decision
        # checkpoint 侧保存 directive.result(含 remaining_target_count from Java)
        # LLM 侧读取 directive.result["decision"]["remaining_target_count"]
        java_remaining = directive.result.get("remaining_target_count")
        llm_remaining = directive.result["decision"]["remaining_target_count"]
        self.assertEqual(5, java_remaining)
        self.assertEqual(java_remaining, llm_remaining)
        # collected_count / target_count 也应一致
        self.assertEqual(
            directive.result.get("collected_count"),
            directive.result["decision"]["collected_so_far"],
        )
        self.assertEqual(
            directive.result.get("target_count"),
            directive.result["decision"]["original_target_count"],
        )

    def test_backpack_full_restart_parameters_preserves_all_route_fields(self):
        # restart_parameters 必须保留所有原路线参数,仅 target_count 被缩减
        directive = self._backpack_full_directive(collected=3, target=7)
        self.assertIsInstance(directive, Blocked)
        restart_params = directive.result["decision"]["restart_template"]
        self.assertEqual(4, restart_params["target_count"])  # 7 - 3 = 4
        # 验证所有 adjustable_fields 都被保留
        for field in (
            "selector", "direction", "shape", "segment_length",
            "speed", "discovery_mode", "placement_policy", "max_placements",
        ):
            self.assertIn(field, restart_params)

    def test_backpack_full_without_java_restart_facts_is_inconsistent(self):
        definition, run = run_for({
            "selector": {"type": "tag", "id": "minecraft:coal_ores"},
            "target_count": 8,
            "target_metric": "blocks_harvested",
        }, execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="SAFETY_PREEMPTED",
            result={
                "phase": "BLOCKED",
                "blocked_reason": "backpack_full",
                "collected_count": 3,
                "target_count": 8,
                "decision_required": True,
                # 注意:故意不包含 remaining_target_count / restart_supported
            },
        ))
        self.assertEqual("SERVER_RESULT_INCONSISTENT", directive.reason)
        self.assertIsNone(directive.result["restart_parameters"])

    def test_non_backpack_path_block_uses_same_selector_remaining_template(self):
        definition, run = run_for({
            "selector": {"type": "tag", "id": "minecraft:coal_ores"},
            "target_count": 10,
            "target_metric": "blocks_harvested",
        }, execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="PATH_NOT_FOUND",
            result={
                "phase": "BLOCKED", "blocked_reason": "path_not_found",
                "collected_count": 5, "target_count": 10,
                "remaining_target_count": 5, "restart_supported": False,
                "vein_locked": False, "decision_required": True,
            },
        ))
        self.assertIsInstance(directive, Blocked)
        decision = directive.result["decision"]
        self.assertEqual(5, decision["restart_template"]["target_count"])
        self.assertFalse(decision["java_restart_supported"])
        self.assertTrue(decision["conditional_restart_supported"])
        self.assertTrue(decision["same_selector_progress_credit_applied"])
        self.assertIn("direction", decision["allowed_overrides"])
        self.assertIn(
            "change_at_least_one_allowed_route_override",
            decision["required_preconditions"],
        )
        self.assertEqual(1, decision["minimum_required_override_count"])
        self.assertTrue(
            decision["restart_template_must_not_be_submitted_unchanged"]
        )

    def test_protected_block_never_emits_restart_template(self):
        definition, run = run_for(execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="BLOCK_PROTECTED",
            result={
                "phase": "BLOCKED", "blocked_reason": "placement_protected",
                "collected_count": 1, "target_count": 5,
                "remaining_target_count": 4, "restart_supported": False,
                "vein_locked": False, "decision_required": True,
            },
        ))
        self.assertIsInstance(directive, Blocked)
        decision = directive.result["decision"]
        self.assertFalse(decision["conditional_restart_supported"])
        self.assertIsNone(decision["restart_template"])
        self.assertFalse(decision["same_selector_progress_credit_applied"])
        self.assertEqual([], decision["allowed_overrides"])

    def test_terminal_count_below_checkpoint_disables_restart(self):
        definition, run = run_for({
            "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
            "target_count": 10, "target_metric": "blocks_harvested",
        }, execution_mode=None)
        run.collected_count = 5
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="SAFETY_PREEMPTED",
            result={
                "phase": "BLOCKED", "blocked_reason": "backpack_full",
                "collected_count": 4, "target_count": 10,
                "remaining_target_count": 6, "restart_supported": True,
                "vein_locked": False, "decision_required": True,
            },
        ))
        self.assertEqual("SERVER_RESULT_INCONSISTENT", directive.reason)
        self.assertIn(
            "terminal_collected_count_regressed_below_checkpoint",
            directive.result["fact_errors"],
        )

    def test_inconsistent_java_remaining_disables_restart(self):
        definition, run = run_for({
            "selector": {"type": "tag", "id": "minecraft:diamond_ores"},
            "target_count": 10, "target_metric": "blocks_harvested",
        }, execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="SAFETY_PREEMPTED",
            result={
                "phase": "BLOCKED", "blocked_reason": "backpack_full",
                "collected_count": 5, "target_count": 10,
                "remaining_target_count": 9, "restart_supported": True,
                "vein_locked": False, "decision_required": True,
            },
        ))
        self.assertEqual("SERVER_RESULT_INCONSISTENT", directive.reason)
        self.assertIsNone(directive.result["restart_parameters"])

    def test_goal_reached_with_locked_vein_stays_blocked_without_template(self):
        definition, run = run_for(execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="SAFETY_PREEMPTED",
            result={
                "phase": "BLOCKED", "blocked_reason": "backpack_full",
                "collected_count": 5, "target_count": 5,
                "remaining_target_count": 0, "restart_supported": False,
                "vein_locked": True, "decision_required": True,
            },
        ))
        self.assertIsInstance(directive, Blocked)
        decision = directive.result["decision"]
        self.assertEqual(
            "committed_vein_requires_recovery_or_explicit_abandon",
            decision["mode"],
        )
        self.assertEqual(0, decision["remaining_target_count"])
        self.assertIsNone(decision["restart_template"])
        self.assertTrue(decision["committed_vein_must_not_be_superseded"])

    def test_target_changed_with_locked_vein_has_no_new_skill_template(self):
        definition, run = run_for(execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="TARGET_CHANGED",
            result={
                "phase": "BLOCKED", "blocked_reason": "target_changed",
                "collected_count": 1, "target_count": 5,
                "remaining_target_count": 4, "restart_supported": False,
                "vein_locked": True, "decision_required": True,
            },
        ))
        self.assertIsInstance(directive, Blocked)
        decision = directive.result["decision"]
        self.assertEqual(
            "committed_vein_requires_recovery_or_explicit_abandon",
            decision["mode"],
        )
        self.assertIsNone(decision["restart_template"])
        self.assertFalse(decision["conditional_restart_supported"])

    def test_missing_vein_lock_fact_is_inconsistent(self):
        definition, run = run_for(execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED", end_reason="PATH_NOT_FOUND",
            result={
                "phase": "BLOCKED", "blocked_reason": "path_not_found",
                "collected_count": 1, "target_count": 5,
                "remaining_target_count": 4, "restart_supported": False,
                "decision_required": True,
            },
        ))
        self.assertEqual("SERVER_RESULT_INCONSISTENT", directive.reason)
        self.assertIn(
            "terminal_vein_locked_is_missing", directive.result["fact_errors"]
        )

    def test_danger_committed_internal_and_unloaded_blocks_have_no_template(self):
        definition, run = run_for(execution_mode=None)
        for reason in (
            "lava_hazard", "committed_vein_remaining_unreachable",
            "internal_error", "entity_unloaded", "hand_conflict",
            "superseded",
        ):
            with self.subTest(reason=reason):
                run.collected_count = 0
                directive = definition.next_directive(run, terminal(
                    "autonomous_mining", status="FAILED", end_reason="BLOCKED",
                    result={
                        "phase": "BLOCKED", "blocked_reason": reason,
                        "collected_count": 0, "target_count": 5,
                        "remaining_target_count": 5,
                        "restart_supported": False, "vein_locked": False,
                        "decision_required": True,
                    },
                ))
                self.assertIsInstance(directive, Blocked)
                self.assertIsNone(
                    directive.result["decision"]["restart_template"]
                )

    def test_invalid_restart_fact_types_and_target_mismatch_fail(self):
        definition, run = run_for(execution_mode=None)
        cases = (
            ({
                "collected_count": True, "target_count": 5,
                "remaining_target_count": 5, "restart_supported": True,
            }, "terminal_collected_count_must_be_a_nonnegative_integer"),
            ({
                "collected_count": 0, "target_count": 6,
                "remaining_target_count": 6, "restart_supported": True,
            }, "terminal_target_count_does_not_match_skill_args"),
            ({
                "collected_count": 0, "target_count": 5,
                "remaining_target_count": 5, "restart_supported": "true",
            }, "terminal_restart_supported_must_be_boolean"),
        )
        for facts, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                result = {
                    "phase": "BLOCKED", "blocked_reason": "backpack_full",
                    "vein_locked": False, "decision_required": True,
                    **facts,
                }
                directive = definition.next_directive(run, terminal(
                    "autonomous_mining", status="FAILED",
                    end_reason="SAFETY_PREEMPTED", result=result,
                ))
                self.assertEqual("SERVER_RESULT_INCONSISTENT", directive.reason)
                self.assertIn(expected_error, directive.result["fact_errors"])

    def test_missing_required_restart_fact_fails(self):
        definition, run = run_for(execution_mode=None)
        directive = definition.next_directive(run, terminal(
            "autonomous_mining", status="FAILED",
            end_reason="SAFETY_PREEMPTED", result={
                "phase": "BLOCKED", "blocked_reason": "backpack_full",
                "target_count": 5, "collected_count": 0,
                "restart_supported": True, "vein_locked": False,
                "decision_required": True,
            },
        ))
        self.assertEqual("SERVER_RESULT_INCONSISTENT", directive.reason)
        self.assertIn(
            "terminal_remaining_target_count_is_missing",
            directive.result["fact_errors"],
        )


if __name__ == "__main__":
    unittest.main()
