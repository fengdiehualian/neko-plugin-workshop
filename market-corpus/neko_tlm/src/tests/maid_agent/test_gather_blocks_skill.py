import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

_base = importlib.import_module("neko_tlm.maid_agent.skills.base")
Blocked = _base.Blocked
Complete = _base.Complete
SkillRun = _base.SkillRun
StartAction = _base.StartAction
GatherBlocksSkill = importlib.import_module(
    "neko_tlm.maid_agent.skills.gather_blocks"
).GatherBlocksSkill


def make_run(target_count=64):
    definition = GatherBlocksSkill()
    args = definition.normalize_args({
        "selector": {"type": "tag", "id": "minecraft:logs"},
        "target_count": target_count,
    })
    run = SkillRun("skill", "maid", definition.name, args)
    definition.initialize(run)
    return definition, run


def terminal(*, harvested=0, status="SUCCEEDED", message=""):
    result = {
        "harvested": harvested,
        "requested": 64,
        "request_satisfied": status == "SUCCEEDED",
    }
    if message:
        result["message"] = message
    return {
        "action_id": "child",
        "maid_id": "maid",
        "kind": "harvest_blocks",
        "status": status,
        "end_reason": "COMPLETED" if status == "SUCCEEDED" else "TARGET_CHANGED",
        "result": result,
    }


class GatherBlocksSkillTests(unittest.TestCase):
    def test_defaults_keep_llm_contract_small(self):
        definition, run = make_run()
        self.assertEqual("blocks_harvested", run.args["target_metric"])
        self.assertEqual(12, run.args["search_radius"])
        self.assertTrue(run.args["vein_mining"])
        self.assertEqual("require_correct", run.args["tool_policy"])

    def test_starts_whole_component_harvest_without_eight_block_cap(self):
        definition, run = make_run()
        directive = definition.next_directive(run, None)
        self.assertIsInstance(directive, StartAction)
        self.assertEqual("harvest_blocks", directive.kind)
        self.assertTrue(directive.args["vein_mining"])
        self.assertEqual(64, directive.args["max_blocks"])
        self.assertEqual({"mode": "nearby"}, directive.args["mining_plan"])
        self.assertEqual(0, directive.timeout_ms)

    def test_accumulates_multiple_trees_before_completing(self):
        definition, run = make_run()
        next_tree = definition.next_directive(run, terminal(harvested=7))
        self.assertIsInstance(next_tree, StartAction)
        self.assertEqual(7, run.collected_count)
        self.assertEqual(57, next_tree.args["max_blocks"])

        completed = definition.next_directive(run, terminal(harvested=57))
        self.assertIsInstance(completed, Complete)
        self.assertEqual(64, run.collected_count)
        self.assertTrue(completed.result["request_satisfied"])

    def test_failed_search_reports_verified_partial_total_and_blocks(self):
        definition, run = make_run()
        definition.next_directive(run, terminal(harvested=5))
        blocked = definition.next_directive(
            run,
            terminal(
                harvested=0,
                status="FAILED",
                message="no_matching_block_found",
            ),
        )
        self.assertIsInstance(blocked, Blocked)
        self.assertEqual(5, blocked.result["collected_count"])
        self.assertEqual(59, blocked.result["remaining_target_count"])
        self.assertFalse(blocked.result["request_satisfied"])
        self.assertTrue(blocked.result["decision_required"])

    def test_rejects_inventory_or_drop_metrics(self):
        definition = GatherBlocksSkill()
        with self.assertRaisesRegex(ValueError, "blocks_harvested"):
            definition.normalize_args({
                "selector": {"type": "tag", "id": "minecraft:logs"},
                "target_count": 64,
                "target_metric": "inventory_items",
            })


if __name__ == "__main__":
    unittest.main()
