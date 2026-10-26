from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from mo_companion.strategy_library import StrategyLibrary
from mo_companion.strategy_server import StrategyLibraryServer


class StrategyLibraryTests(unittest.TestCase):
    def test_fixed_strategy_library_roots_exist_without_strategies(self):
        with tempfile.TemporaryDirectory() as directory:
            library = StrategyLibrary(Path(directory) / "library.sqlite3")
            paths = {item["path"] for item in library.categories()}
            self.assertTrue({"战役", "遭遇战", "多人对战", "通用战术", "游戏适配"}.issubset(paths))
            self.assertIn("战役/官方任务/具体任务/通关策略", paths)
            self.assertIn("多人对战/玩家对战/4对4", paths)
            self.assertIn("游戏适配/红色警戒2原版", paths)

    def test_temporary_strategy_is_usable_then_promotable(self):
        with tempfile.TemporaryDirectory() as directory:
            library = StrategyLibrary(Path(directory) / "library.sqlite3")
            saved = library.save_strategy({
                "id": "learned_test_posture",
                "name_zh": "测试部署策略",
                "category_path": "猫娘学习/临时方案",
                "strategy_type": "action_flow",
                "library_scope": "temporary",
                "status": "experimental",
                "usable_in_match": True,
                "payload": {"flow": ["部署", "攻击"]},
            })
            self.assertEqual(saved["library_scope"], "temporary")
            self.assertTrue(any(item["id"] == "learned_test_posture" for item in library.runtime_snapshot()["strategies"]))
            library.set_enabled("learned_test_posture", False)
            self.assertFalse(any(item["id"] == "learned_test_posture" for item in library.runtime_snapshot()["strategies"]))
            promoted = library.promote("learned_test_posture")
            self.assertEqual(promoted["library_scope"], "permanent")
            self.assertEqual(promoted["status"], "stable")

    def test_delete_is_soft_and_removes_strategy_from_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            library = StrategyLibrary(Path(directory) / "library.sqlite3")
            library.save_strategy({"id": "learned_delete", "name_zh": "待删除", "library_scope": "temporary"})
            library.delete("learned_delete")
            self.assertIsNone(library.get_strategy("learned_delete"))
            self.assertEqual(library.get_strategy("learned_delete", include_deleted=True)["status"], "deleted")
            self.assertFalse(any(item["id"] == "learned_delete" for item in library.runtime_snapshot()["strategies"]))

    def test_scratch_variables_lists_and_custom_blocks_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            library = StrategyLibrary(Path(directory) / "library.sqlite3")
            payload = {
                "variables": {"当前攻击波次": "1"},
                "lists": {"待攻击目标": ["防御建筑", "兵营"]},
                "custom_blocks": [{
                    "id": "custom_assault",
                    "name_zh": "组织突击队",
                    "shape": "命令",
                    "template": "征召【单位】数量【数量】",
                    "parameters": ["单位", "数量"],
                }],
                "block_program": {"actions": [{"label_zh": "组织突击队"}]},
            }
            saved = library.save_strategy({
                "id": "learned_scratch_state",
                "name_zh": "Scratch状态测试",
                "category_path": "猫娘学习/临时方案",
                "library_scope": "temporary",
                "payload": payload,
            })
            loaded = library.get_strategy(saved["id"])
            self.assertEqual(loaded["payload"]["variables"], payload["variables"])
            self.assertEqual(loaded["payload"]["lists"], payload["lists"])
            self.assertEqual(loaded["payload"]["custom_blocks"][0]["name_zh"], "组织突击队")

    def test_existing_legacy_strategy_gets_block_program_on_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.sqlite3"
            library = StrategyLibrary(path)
            library.save_strategy({
                "id": "legacy_flow",
                "name_zh": "历史流程",
                "library_scope": "permanent",
                "status": "stable",
                "payload": {"flow": ["部署单位", "攻击目标"]},
            })
            # Simulate an older database row by removing its normalized AST.
            with library._connect() as db:
                db.execute("UPDATE strategies SET payload_json=? WHERE id=?", (json.dumps({"flow": ["部署单位"]}, ensure_ascii=False), "legacy_flow"))
            library.bootstrap()
            payload = library.get_strategy("legacy_flow")["payload"]
            self.assertIn("block_program", payload)
            self.assertIn("scripts", payload["block_program"])
            self.assertNotIn("flow", payload)
            self.assertEqual(payload["block_editor"], "原子积木树")

    def test_generated_strategy_is_searchable_from_faction_and_tactical_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "doctrine.json"
            source.write_text(json.dumps({
                "metadata": {},
                "entries": [{
                    "id": "matrix_multi_category",
                    "conditions": {"enemy_faction": "allied", "primary_threat": "heavy_armor", "stage": "tier3", "economy": "stable", "force_ratio": "ahead", "terrain": "open"},
                    "priority": 146, "weight": 80, "posture": "attack",
                    "actions": ["counter_heavy_armor", "press_verified_advantage"],
                    "production_roles": ["heavy_anti_armor"],
                    "target_priorities": ["heavy_vehicle", "vehicle_production"],
                    "formation": "crossfire", "engagement": "concentrated",
                    "tactical_controls": {"minimum_assembly_count": 6},
                    "constraints": ["visible_information_only", "preserve_player_authority", "do_not_block_independent_production_lanes", "verify_native_action_progress"],
                }],
                "unit_responses": [],
            }, ensure_ascii=False), encoding="utf-8")
            library = StrategyLibrary(root / "library.sqlite3")
            result = library.rebuild_generated_strategies(source)
            self.assertEqual(result["generated"], 1)
            item = library.get_strategy("matrix_multi_category")
            self.assertIn("遭遇战/盟军/美国", item["category_paths"])
            self.assertIn("通用战术/进攻与撤退", item["category_paths"])
            self.assertEqual(library.list_strategies(category="遭遇战/盟军")["total"], 1)
            self.assertEqual(library.list_strategies(category="通用战术/进攻与撤退")["total"], 1)
            counts = {entry["path"]: entry["count"] for entry in library.categories()}
            self.assertEqual(counts["遭遇战"], 1)
            self.assertEqual(counts["遭遇战/盟军"], 1)

    def test_local_server_exposes_categories_and_strategy_editing(self):
        with tempfile.TemporaryDirectory() as directory:
            library = StrategyLibrary(Path(directory) / "library.sqlite3")
            server = StrategyLibraryServer(library, port=0)
            port = server.start()
            try:
                base = f"http://127.0.0.1:{port}"
                with urlopen(base + "/api/health", timeout=2.0) as response:
                    health = json.loads(response.read().decode("utf-8"))
                self.assertTrue(health["ok"])
                with urlopen(base + "/api/target-catalog", timeout=5.0) as response:
                    target_catalog = json.loads(response.read().decode("utf-8"))
                self.assertTrue(target_catalog["units"])
                self.assertTrue({"建筑", "步兵", "装甲", "飞机", "海军"}.issubset(
                    {item["group"] for item in target_catalog["units"]}
                ))
                with urlopen(base + "/api/block-registry", timeout=2.0) as response:
                    registry = json.loads(response.read().decode("utf-8"))
                self.assertEqual(registry["schema"], "mo.scratch.registry")
                self.assertIn("行动.攻击", {block["id"] for group in registry["groups"] for block in group["blocks"]})
                self.assertTrue({"事件", "控制", "侦测", "运算", "变量与列表", "验证"}.issubset(
                    {group["name_zh"] for group in registry["groups"]}
                ))
                with urlopen(base + "/" + quote("策略积木编辑器.js"), timeout=2.0) as response:
                    self.assertIn("树形积木脚本", response.read().decode("utf-8"))
                with urlopen(base + "/" + quote("策略积木编辑器.css"), timeout=2.0) as response:
                    self.assertIn(".workspace-panel", response.read().decode("utf-8"))
                request = Request(
                    base + "/api/strategies",
                    data=json.dumps({"id": "learned_api", "name_zh": "API测试", "library_scope": "temporary"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urlopen(request, timeout=2.0) as response:
                    self.assertEqual(response.status, 200)
                with urlopen(base + "/api/strategies?scope=temporary", timeout=2.0) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["total"], 1)
                with urlopen(base + "/api/categories", timeout=2.0) as response:
                    self.assertTrue(json.loads(response.read().decode("utf-8"))["categories"])
                compile_request = Request(
                    base + "/api/blocks/compile",
                    data=json.dumps({"flow": ["部署单位"], "variables": {"波次": 1}}, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urlopen(compile_request, timeout=2.0) as response:
                    compiled = json.loads(response.read().decode("utf-8"))
                self.assertEqual(compiled["schema"], "mo.scratch.ast")
                self.assertIn("scripts", compiled["program"])
                self.assertNotIn("actions", compiled["program"])
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
