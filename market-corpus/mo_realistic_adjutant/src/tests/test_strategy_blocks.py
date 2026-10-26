from __future__ import annotations

import unittest

from mo_companion.strategy_blocks import BlockCompileError, BlockRuntime, compile_program
from mo_companion.combat_doctrine_generator import _unit_responses


class StrategyBlockTests(unittest.TestCase):
    def test_typed_scratch_script_compiles_nested_boolean_and_command_stack(self):
        payload = {
            "block_program": {
                "scripts": [{
                    "block_id": "事件.自主授权",
                    "stacks": {"执行": [{
                        "block_id": "控制.如果",
                        "inputs": {"条件": {
                            "block_id": "运算.大于",
                            "inputs": {
                                "左值": {"block_id": "侦测.我方资金", "inputs": {}},
                                "右值": 5000,
                            },
                        }},
                        "stacks": {"那么": [{"block_id": "控制.重新判断", "inputs": {}}]},
                    }]},
                }],
            },
        }
        ast = compile_program(payload)
        script = ast["program"]["scripts"][0]
        self.assertEqual(script["block_id"], "事件.自主授权")
        condition = script["stacks"]["执行"][0]["inputs"]["条件"]
        self.assertEqual(condition["block_id"], "运算.大于")
        self.assertEqual(condition["inputs"]["右值"], 5000)

    def test_typed_scratch_rejects_wrong_slot_shape_and_incomplete_script(self):
        wrong_type = {"block_program": {"scripts": [{
            "block_id": "事件.对局开始",
            "stacks": {"执行": [{
                "block_id": "控制.等待条件",
                "inputs": {"条件": {"block_id": "侦测.我方资金", "inputs": {}}},
            }]},
        }]}}
        with self.assertRaises(BlockCompileError):
            compile_program(wrong_type)
        with self.assertRaises(BlockCompileError):
            compile_program({"block_program": {"scripts": [{"block_id": "事件.对局开始", "stacks": {"执行": []}}]}})

    def test_legacy_actions_generate_standard_editable_script(self):
        ast = compile_program({
            "conditions": {"primary_threat": "transport_drop"},
            "actions": ["counter_transport_drop", "commit_to_decisive_objective"],
        })
        body = ast["program"]["scripts"][0]["stacks"]["执行"]
        self.assertEqual(body[0]["block_id"], "克制.切换方案")
        self.assertEqual(body[0]["inputs"]["威胁类型"], "运输空降")
        self.assertEqual(body[1]["block_id"], "战术.发起决战")

    def test_legacy_flow_compiles_to_command_nodes(self):
        ast = compile_program({"flow": ["部署单位", "攻击优先目标"], "variables": {"波次": 1}})
        self.assertEqual(ast["schema"], "mo.scratch.ast")
        body = ast["program"]["scripts"][0]["stacks"]["执行"]
        self.assertEqual(len(body), 2)
        self.assertNotIn("actions", ast["program"])
        self.assertEqual(ast["variables"]["波次"], 1)

    def test_custom_block_expands_and_recursive_definition_is_rejected(self):
        payload = {
            "custom_blocks": [{"id": "突击", "name_zh": "突击", "shape": "命令", "template": "执行突击", "parameters": []}],
            "block_program": {"actions": [{"custom_id": "突击", "label_zh": "突击", "inputs": {}}]},
        }
        ast = compile_program(payload)
        body = ast["program"]["scripts"][0]["stacks"]["执行"]
        self.assertEqual(body[0]["custom_definition"], "突击")
        recursive = {"custom_blocks": [{"id": "循环", "name_zh": "循环", "shape": "命令", "template": "循环", "parameters": [], "children": [{"custom_id": "循环", "label_zh": "循环"}]}], "block_program": {"actions": [{"custom_id": "循环", "label_zh": "循环"}]}}
        with self.assertRaises(BlockCompileError):
            compile_program(recursive)

    def test_unit_count_comparison_is_a_real_nested_atomic_tree(self):
        gi = {
            "block_id": "单位.选择",
            "inputs": {"关系": "我方", "子阵营": "美国", "类别": "步兵", "具体单位": "E1"},
        }
        payload = {"block_program": {"scripts": [{
            "block_id": "事件.战场变化",
            "stacks": {"执行": [{
                "block_id": "控制.如果",
                "inputs": {"条件": {
                    "block_id": "运算.大于",
                    "inputs": {"左值": {"block_id": "侦测.单位数量", "inputs": {"单位": gi}}, "右值": 20},
                }},
                "stacks": {"那么": [{"block_id": "战术.扩大优势", "inputs": {}}]},
            }]},
        }]}}
        ast = compile_program(payload)
        branch = ast["program"]["scripts"][0]["stacks"]["执行"][0]
        selector = branch["inputs"]["条件"]["inputs"]["左值"]["inputs"]["单位"]
        self.assertEqual(selector["inputs"]["具体单位"], "E1")
        self.assertEqual(branch["inputs"]["条件"]["inputs"]["右值"], 20)

    def test_generated_unit_response_exposes_threshold_priority_and_target_order(self):
        response = next(item for item in _unit_responses() if item["enemy_name"] == "加特林坦克" and item["force_ratio"] == "ahead")
        ast = compile_program(response, strategy_id=response["id"], strategy_type="unit_response")

        def walk(node):
            yield node
            for value in (node.get("inputs") or {}).values():
                if isinstance(value, dict) and value.get("block_id"):
                    yield from walk(value)
            for children in (node.get("stacks") or {}).values():
                for child in children:
                    yield from walk(child)

        nodes = list(walk(ast["program"]["scripts"][0]))
        ids = {node["block_id"] for node in nodes}
        self.assertIn("运算.大于等于", ids)
        self.assertIn("数量.战斗力", ids)
        self.assertIn("战术.策略优先级", ids)
        self.assertIn("战术.最低触发数量", ids)
        self.assertIn("战术.最低威胁值", ids)
        self.assertIn("战术.指定目标优先级", ids)
        comparisons = [node for node in nodes if node["block_id"] == "运算.大于等于"]
        self.assertIn(1, [node["inputs"]["右值"] for node in comparisons])

    def test_runtime_variables_and_lists_are_isolated(self):
        ast = compile_program({"variables": {"资金底线": 5000}, "lists": {"目标": ["兵营"]}})
        first = BlockRuntime.from_ast(ast)
        second = BlockRuntime.from_ast(ast)
        first.change("资金底线", 100)
        first.add("目标", "重工")
        self.assertEqual(first.get("资金底线"), 5100)
        self.assertEqual(first.length("目标"), 2)
        self.assertEqual(second.get("资金底线"), 5000)
        self.assertEqual(second.length("目标"), 1)

    def test_typed_variable_and_list_defaults_enter_runtime_as_plain_values(self):
        ast = compile_program({
            "variables": {"资金底线": {"type": "数字", "value": 5000}},
            "lists": {"待攻击目标": {"type": "目标引用", "items": ["兵营"]}},
        })
        runtime = BlockRuntime.from_ast(ast)
        self.assertEqual(runtime.get("资金底线"), 5000)
        self.assertEqual(runtime.length("待攻击目标"), 1)


if __name__ == "__main__":
    unittest.main()
