import ast
import importlib
import re
import tomllib
import unittest
from pathlib import Path

from ._bootstrap import bootstrap

bootstrap()

_instructions = importlib.import_module("neko_tlm.instructions")
_tool_defs = importlib.import_module("neko_tlm.tool_defs")


class LlmToolSurfaceTests(unittest.TestCase):
    def _plugin_source(self):
        source_path = Path(__file__).resolve().parents[2] / "__init__.py"
        return source_path.read_text(encoding="utf-8")

    def _decorated_definition_names(self):
        tree = ast.parse(self._plugin_source())
        names = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Name):
                    continue
                if decorator.func.id != "llm_tool":
                    continue
                for keyword in decorator.keywords:
                    if keyword.arg is None and isinstance(keyword.value, ast.Name):
                        names.add(keyword.value.id)
        return names

    def test_llm_surface_is_small_and_non_overlapping(self):
        exposed = self._decorated_definition_names()
        self.assertEqual(
            {
                "MC_MAID_STATUS",
                "MC_SWITCH_FOLLOW",
                "MC_SWITCH_SIT",
                "MC_SWITCH_TASK",
                "MC_SWITCH_SCHEDULE",
                "MC_EQUIP_ITEM",
                "MC_MINE_ORE",
                "MC_GATHER_BLOCKS",
                "MC_GAME_CONTEXT",
                "MC_MOVE_MAID_TO",
                "MC_STOP_MAID_ACTIVITY",
                "MC_EXECUTE_COMMAND",
            },
            exposed,
        )
        self.assertEqual(12, len(exposed))

    def test_compatibility_tools_are_not_advertised_to_the_llm(self):
        exposed = self._decorated_definition_names()
        self.assertTrue(
            {
                "MC_START_MAID_ACTION",
                "MC_CANCEL_MAID_ACTION",
                "MC_GET_MAID_ACTION_STATUS",
                "MC_LIST_ACTIVE_MAID_ACTIONS",
                "MC_START_SKILL",
                "MC_CANCEL_SKILL",
                "MC_GET_SKILL_STATUS",
                "MC_LIST_SKILLS",
                "MC_USE_SKILL",
                "MC_SET_PLAN",
            }.isdisjoint(exposed)
        )

    def test_unified_action_tool_is_explicit_and_structured(self):
        definition = _tool_defs.MC_SET_MAID_ACTIVITY
        parameters = definition["parameters"]["properties"]
        self.assertIn("必须调用", definition["description"])
        self.assertEqual(
            ["agent_action", "skill"],
            parameters["activity_type"]["enum"],
        )
        self.assertEqual(
            ["navigate", "harvest_blocks", "return_to_position"],
            parameters["kind"]["enum"],
        )
        self.assertEqual(
            ["mine_ore", "gather_blocks"], parameters["skill"]["enum"]
        )
        branches = {
            branch["title"]: branch
            for branch in definition["parameters"]["oneOf"]
        }
        self.assertEqual(7, len(branches))
        self.assertTrue(all(
            branch["additionalProperties"] is False
            for branch in branches.values()
        ))

        navigate = branches["非破坏寻路"]
        self.assertEqual(
            ["activity_type", "kind", "args"], navigate["required"]
        )
        self.assertEqual(
            ["target"], navigate["properties"]["args"]["required"]
        )

        mine_args = branches["自主找矿 Skill"]["properties"]["args"]
        self.assertEqual(["selector", "target_count"], mine_args["required"])
        self.assertNotIn("search_radius", mine_args["properties"])
        selector = mine_args["properties"]["selector"]
        self.assertEqual(["type", "id"], selector["required"])
        self.assertFalse(selector["additionalProperties"])
        target_count = mine_args["properties"]["target_count"]
        self.assertEqual((1, 4096), (
            target_count["minimum"], target_count["maximum"]
        ))

        gather_args = branches["累计附近资源 Skill"]["properties"]["args"]
        self.assertIn("search_radius", gather_args["properties"])
        self.assertNotIn("max_blocks", gather_args["properties"])

    def test_public_activity_guidance_preserves_combat_equipment_precheck(self):
        text = (
            _tool_defs.MC_SWITCH_TASK["description"]
            + _instructions._TLM_DIALOG_INSTRUCTIONS
        )
        self.assertIn("mc_game_context", text)
        self.assertIn("equipment", text)
        self.assertIn("不是武器", text)
        self.assertIn("禁止在未装备武器时启动攻击工作", text)

    def test_dedicated_skill_tools_are_flat_and_intent_specific(self):
        mine = _tool_defs.MC_MINE_ORE
        gather = _tool_defs.MC_GATHER_BLOCKS
        self.assertEqual("mc_mine_ore", mine["name"])
        self.assertEqual("mc_gather_blocks", gather["name"])
        self.assertEqual(["ore"], mine["parameters"]["required"])
        self.assertEqual(["resource"], gather["parameters"]["required"])
        for definition in (mine, gather):
            parameters = definition["parameters"]
            self.assertFalse(parameters["additionalProperties"])
            self.assertNotIn("oneOf", parameters)
            self.assertNotIn("args", parameters["properties"])
            count = parameters["properties"]["target_count"]
            self.assertEqual((1, 4096), (count["minimum"], count["maximum"]))
        self.assertIn("不要为找矿调用 mc_switch_task", mine["description"])
        self.assertIn("不要为“把附近的树砍了”调用 mc_switch_task", gather["description"])

    def test_prompt_only_names_the_public_activity_controls(self):
        text = _instructions._TLM_DIALOG_INSTRUCTIONS
        for hidden_name in (
            "mc_start_maid_action",
            "mc_cancel_maid_action",
            "mc_get_maid_action_status",
            "mc_list_active_maid_actions",
            "mc_start_skill",
            "mc_cancel_skill",
            "mc_get_skill_status",
            "mc_list_skills",
            "mc_use_skill",
            "mc_set_plan",
            "mc_send_chat",
            "mc_get_maid_activity",
            "mc_get_maid_capabilities",
            "mc_set_maid_activity",
        ):
            self.assertNotIn(hidden_name, text)
        self.assertIn("mc_switch_task", text)
        self.assertIn("mc_mine_ore", text)
        self.assertIn("mc_gather_blocks", text)
        self.assertIn("mc_stop_maid_activity", text)
        self.assertIn("mc_execute_command", text)

    def test_command_execution_tool_is_advertised_with_confirmation_guard(self):
        exposed = self._decorated_definition_names()
        self.assertIn("MC_EXECUTE_COMMAND", exposed)
        definition = _tool_defs.MC_EXECUTE_COMMAND
        self.assertEqual("mc_execute_command", definition["name"])
        self.assertEqual(["command"], definition["parameters"]["required"])
        self.assertIn("确认", definition["description"])
        self.assertIn("客户端", definition["description"])
        self.assertIn("权限", definition["description"])
        self.assertFalse(definition["parameters"]["additionalProperties"])
        self.assertEqual(120, definition["timeout"])

    def test_default_dialog_prompt_is_small(self):
        self.assertLess(len(_instructions._TLM_DIALOG_INSTRUCTIONS), 3000)
        self.assertLess(
            len(_instructions._TLM_DIALOG_INSTRUCTIONS),
            len(_instructions._TLM_AI_INSTRUCTIONS) // 4,
        )

    def test_plugin_injects_compact_prompt_and_exposes_agent_fallback(self):
        source = self._plugin_source()
        tree = ast.parse(source)
        entry_ids = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Name):
                    continue
                if decorator.func.id != "plugin_entry":
                    continue
                for keyword in decorator.keywords:
                    if keyword.arg == "id" and isinstance(keyword.value, ast.Constant):
                        entry_ids.add(keyword.value.value)
        self.assertIn("switch_maid_work", entry_ids)
        self.assertIn("mine_ore_autonomously", entry_ids)
        self.assertIn("gather_nearby_blocks", entry_ids)
        self.assertIn("switch_maid_follow", entry_ids)
        self.assertIn("move_maid_to_destination", entry_ids)
        self.assertIn("navigate_maid_to_coordinates", entry_ids)
        self.assertIn("self._re_register_agent_entries()", source)
        self.assertIn("instructions = _TLM_DIALOG_INSTRUCTIONS", source)

    def test_agent_gate_keywords_cover_gameplay_action_phrases(self):
        toml_path = Path(__file__).resolve().parents[2] / "plugin.toml"
        with toml_path.open("rb") as stream:
            keywords = tomllib.load(stream)["plugin"]["keywords"]
        for phrase in (
            "帮我挖3个铁矿",
            "把附近的树砍了",
            "跟我来",
            "过来到我身边",
            "寻路到坐标 12 70 -4",
        ):
            self.assertTrue(
                any(re.search(pattern, phrase, re.IGNORECASE) for pattern in keywords),
                phrase,
            )

    def test_tool_definitions_use_only_the_current_sdk_contract(self):
        for name, definition in vars(_tool_defs).items():
            if not name.startswith("MC_") or not isinstance(definition, dict):
                continue
            self.assertNotIn("force_for_phrases", definition, name)
            self.assertNotIn("force_priority", definition, name)


if __name__ == "__main__":
    unittest.main()
