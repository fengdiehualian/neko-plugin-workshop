import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

_instructions = importlib.import_module("neko_tlm.instructions")
_tool_defs = importlib.import_module("neko_tlm.tool_defs")
_TLM_AI_INSTRUCTIONS = _instructions._TLM_AI_INSTRUCTIONS
MC_CANCEL_SKILL = _tool_defs.MC_CANCEL_SKILL
MC_GET_SKILL_STATUS = _tool_defs.MC_GET_SKILL_STATUS
MC_LIST_SKILLS = _tool_defs.MC_LIST_SKILLS
MC_MOVE_MAID_TO = _tool_defs.MC_MOVE_MAID_TO
MC_START_MAID_ACTION = _tool_defs.MC_START_MAID_ACTION
MC_START_SKILL = _tool_defs.MC_START_SKILL


class MaidActionGuidanceTests(unittest.TestCase):
    def test_simple_move_tool_routes_companion_destinations(self):
        self.assertEqual("mc_move_maid_to", MC_MOVE_MAID_TO["name"])
        parameters = MC_MOVE_MAID_TO["parameters"]
        self.assertEqual(["destination"], parameters["required"])
        self.assertEqual(
            ["player", "surface", "mine_entry"],
            parameters["properties"]["destination"]["enum"],
        )
        self.assertFalse(parameters["additionalProperties"])
        text = MC_MOVE_MAID_TO["description"] + _TLM_AI_INSTRUCTIONS
        for expected in (
            "挖过来", 'destination="player"', "严禁", "harvest_blocks",
            "completion_confirmed", "maid_action_finished", "SUCCEEDED",
        ):
            self.assertIn(expected, text)

    def test_stone_example_uses_overworld_base_stone_tag(self):
        expected = "{type:'tag', id:'minecraft:base_stone_overworld'}"
        tool_text = MC_START_MAID_ACTION["description"] + str(
            MC_START_MAID_ACTION["parameters"]
        )
        self.assertIn(expected, _TLM_AI_INSTRUCTIONS)
        self.assertIn(expected, tool_text)
        self.assertNotIn("{type:'block', id:'minecraft:stone'}", tool_text)

    def test_guidance_declares_terrain_aware_harvest_boundary(self):
        self.assertIn("规划清理安全", _TLM_AI_INSTRUCTIONS)
        self.assertIn("短距离下挖或开通道", _TLM_AI_INSTRUCTIONS)
        self.assertIn("普通 navigate 始终是非破坏性寻路", _TLM_AI_INSTRUCTIONS)
        self.assertIn("不会强制加载未加载区块", _TLM_AI_INSTRUCTIONS)
        tool_text = MC_START_MAID_ACTION["description"] + str(
            MC_START_MAID_ACTION["parameters"]
        )
        self.assertIn("清理安全", tool_text)
        self.assertIn("短距离下挖", tool_text)
        self.assertIn("navigate 始终非破坏性", tool_text)
        self.assertIn("不会搭桥", tool_text)
        self.assertIn("不会强制加载", tool_text)

    def test_return_to_position_extends_existing_action_protocol(self):
        parameters = MC_START_MAID_ACTION["parameters"]
        self.assertEqual("object", parameters["type"])
        self.assertEqual(["kind", "args"], parameters["required"])
        self.assertEqual(
            {"kind", "args", "action_id", "timeout_ms", "replace_existing"},
            set(parameters["properties"]),
        )
        self.assertEqual(
            ["navigate", "harvest_blocks", "return_to_position"],
            parameters["properties"]["kind"]["enum"],
        )

    def test_return_guidance_requires_walkable_route_and_real_materials(self):
        tool_text = MC_START_MAID_ACTION["description"] + str(
            MC_START_MAID_ACTION["parameters"]
        )
        for value in (
            "return_to_position",
            "destination:'surface'|'mine_entry'|'player'",
            "两格高",
            "真实消耗",
            "timeout_ms=0",
        ):
            self.assertIn(value, tool_text + _TLM_AI_INSTRUCTIONS)
        for hidden_engineering_field in (
            "route_policy", "operation_id", "max_placements"
        ):
            self.assertNotIn(hidden_engineering_field, tool_text)
        self.assertIn("不得在身后回填封路", _TLM_AI_INSTRUCTIONS)
        self.assertIn("禁止猜测", _TLM_AI_INSTRUCTIONS)
        self.assertIn("navigate 始终是非破坏性寻路", _TLM_AI_INSTRUCTIONS)

    def test_guidance_routes_high_level_mining_and_emergency_stop_to_skill(self):
        tool_text = MC_START_MAID_ACTION["description"] + str(
            MC_START_MAID_ACTION["parameters"]
        )
        for value in (
            "mining_plan", "forward_tunnel", "staircase_down", "auto",
            "max_distance", "max_depth", "max_segments", "excavation_budget",
        ):
            self.assertIn(value, tool_text)
            self.assertIn(value, _TLM_AI_INSTRUCTIONS)
        self.assertIn("mc_set_maid_activity", _TLM_AI_INSTRUCTIONS)
        self.assertIn("autonomous_mining", _TLM_AI_INSTRUCTIONS)
        self.assertIn("Java 自主完成", _TLM_AI_INSTRUCTIONS)
        self.assertIn("staircase_down", _TLM_AI_INSTRUCTIONS)
        self.assertIn("F8", _TLM_AI_INSTRUCTIONS)
        self.assertIn("mc_stop_maid_activity", _TLM_AI_INSTRUCTIONS)
        self.assertIn("底层兼容能力", _TLM_AI_INSTRUCTIONS)
        self.assertIn("协议兼容能力", tool_text)

    def test_guidance_keeps_low_level_mining_plan_as_compatibility_only(self):
        tool_text = MC_START_MAID_ACTION["description"] + str(
            MC_START_MAID_ACTION["parameters"]
        )
        for text in (tool_text, _TLM_AI_INSTRUCTIONS):
            self.assertIn("max_segments", text)
            self.assertIn("兼容", text)
            self.assertIn("256", text)
        self.assertIn("不是默认高层方案", tool_text)
        self.assertIn("不要用它代替普通高级找矿 Skill", _TLM_AI_INSTRUCTIONS)

    def test_guidance_exposes_frozen_skill_contract(self):
        self.assertEqual("mc_start_skill", MC_START_SKILL["name"])
        self.assertEqual("mc_cancel_skill", MC_CANCEL_SKILL["name"])
        self.assertEqual("mc_get_skill_status", MC_GET_SKILL_STATUS["name"])
        self.assertEqual("mc_list_skills", MC_LIST_SKILLS["name"])
        parameters = MC_START_SKILL["parameters"]
        self.assertEqual(["skill", "args"], parameters["required"])
        self.assertNotIn("skill_name", parameters["properties"])
        skill_text = MC_START_SKILL["description"] + str(parameters)
        for value in (
            "mine_ore", "gather_blocks", "minecraft:logs", "target_count", "blocks_harvested",
            "autonomous_mining", "execution_mode", "segment_length",
            "loaded_scan", "decision_required",
            "placement_policy", "safe_support_and_water_seal",
            "max_placements",
        ):
            self.assertIn(value, skill_text)
            self.assertIn(value, _TLM_AI_INSTRUCTIONS)
        self.assertIn("Java 全程自主", skill_text)
        self.assertIn("LLM 不得逐段遥控", _TLM_AI_INSTRUCTIONS)
        self.assertIn("当前没有暂停", _TLM_AI_INSTRUCTIONS)
        self.assertIn("fishbone", skill_text)
        self.assertIn("legacy", skill_text)
        self.assertIn("旧检查点", _TLM_AI_INSTRUCTIONS)
        self.assertIn("一组/64个原木", _TLM_AI_INSTRUCTIONS)
        self.assertIn("不能证明更大的会话总目标", _TLM_AI_INSTRUCTIONS)
        self.assertIn("目标板只记录", _TLM_AI_INSTRUCTIONS)
        self.assertIn("不能主动替玩家打开女仆背包界面", _TLM_AI_INSTRUCTIONS)
        self.assertIn("只证明这个精确 ID 不存在", _TLM_AI_INSTRUCTIONS)

    def test_autonomous_miner_documents_route_ore_and_safe_construction(self):
        skill_text = MC_START_SKILL["description"] + str(
            MC_START_SKILL["parameters"]
        )
        for value in (
            "其他矿石", "搭桥", "封水", "普通实心方块",
            "placement_policy", "max_placements",
        ):
            self.assertIn(value, skill_text)
            self.assertIn(value, _TLM_AI_INSTRUCTIONS)
        self.assertIn("不绕过领地保护", skill_text)
        self.assertIn("placement_protected", _TLM_AI_INSTRUCTIONS)
        self.assertIn("MiningPlanner", skill_text)
        self.assertIn("综合预计成本", _TLM_AI_INSTRUCTIONS)

    def test_guidance_requires_a_concrete_llm_recovery_plan(self):
        self.assertIn("具体解决方案", _TLM_AI_INSTRUCTIONS)
        self.assertIn("禁止只道歉", _TLM_AI_INSTRUCTIONS)
        self.assertIn("立即调用", _TLM_AI_INSTRUCTIONS)
        self.assertIn("禁止相同参数无限重试", _TLM_AI_INSTRUCTIONS)

    def test_guidance_prefers_ore_tags_and_whole_veins(self):
        tool_text = MC_START_MAID_ACTION["description"] + str(
            MC_START_MAID_ACTION["parameters"]
        )
        for text in (tool_text, _TLM_AI_INSTRUCTIONS):
            self.assertIn("minecraft:diamond_ores", text)
            self.assertIn("vein_mining", text)
            self.assertIn("max_blocks", text)
            self.assertIn("26", text)
            self.assertIn("最低目标", text)
            self.assertIn("必须", text)
        self.assertIn("只挖一块", _TLM_AI_INSTRUCTIONS)
        self.assertIn("vein_mining=false", _TLM_AI_INSTRUCTIONS)
        self.assertIn("timeout_ms=0", _TLM_AI_INSTRUCTIONS)
        self.assertIn("120000", tool_text)
        self.assertIn("no_matching_block_found", _TLM_AI_INSTRUCTIONS)
        self.assertIn("不要自动重复", _TLM_AI_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
