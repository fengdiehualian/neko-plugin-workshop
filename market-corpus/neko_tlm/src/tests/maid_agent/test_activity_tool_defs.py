import importlib
import unittest

from ._bootstrap import bootstrap_sdk

bootstrap_sdk()

_tool_defs = importlib.import_module("neko_tlm.tool_defs")
MC_GET_MAID_ACTIVITY = _tool_defs.MC_GET_MAID_ACTIVITY
MC_GET_MAID_CAPABILITIES = _tool_defs.MC_GET_MAID_CAPABILITIES
MC_SET_MAID_ACTIVITY = _tool_defs.MC_SET_MAID_ACTIVITY
MC_STOP_MAID_ACTIVITY = _tool_defs.MC_STOP_MAID_ACTIVITY


class ActivityToolDefinitionTests(unittest.TestCase):
    def test_public_activity_tool_names_are_stable(self):
        self.assertEqual("mc_get_maid_activity", MC_GET_MAID_ACTIVITY["name"])
        self.assertEqual(
            "mc_get_maid_capabilities", MC_GET_MAID_CAPABILITIES["name"]
        )
        self.assertEqual("mc_set_maid_activity", MC_SET_MAID_ACTIVITY["name"])
        self.assertEqual("mc_stop_maid_activity", MC_STOP_MAID_ACTIVITY["name"])

    def test_set_activity_schema_exposes_only_safe_switch_policies(self):
        parameters = MC_SET_MAID_ACTIVITY["parameters"]
        self.assertEqual(["activity_type"], parameters["required"])
        self.assertEqual(
            ["agent_action", "skill"],
            parameters["properties"]["activity_type"]["enum"],
        )
        self.assertEqual(
            ["cancel_then_switch", "after_current", "reject_if_busy"],
            parameters["properties"]["switch_policy"]["enum"],
        )
        self.assertNotIn("pause", parameters["properties"])
        self.assertNotIn("resume", parameters["properties"])

    def test_activity_query_accepts_terminal_action_and_skill_ids(self):
        properties = MC_GET_MAID_ACTIVITY["parameters"]["properties"]
        self.assertEqual({"action_id", "skill_id"}, set(properties))


if __name__ == "__main__":
    unittest.main()
