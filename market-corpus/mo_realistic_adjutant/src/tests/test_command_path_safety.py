from __future__ import annotations

import time
import sys
import types
import unittest

if "plugin.sdk.plugin" not in sys.modules:
    sdk_module = types.ModuleType("plugin.sdk.plugin")

    class _NekoPluginBase:
        pass

    def _decorator(*args, **_kwargs):
        if len(args) == 1 and isinstance(args[0], type):
            return args[0]
        return lambda value: value

    sdk_module.NekoPluginBase = _NekoPluginBase
    sdk_module.Ok = lambda value: value
    sdk_module.lifecycle = _decorator
    sdk_module.llm_tool = _decorator
    sdk_module.message = _decorator
    sdk_module.neko_plugin = _decorator
    plugin_package = types.ModuleType("plugin")
    sdk_package = types.ModuleType("plugin.sdk")
    plugin_package.sdk = sdk_package
    sdk_package.plugin = sdk_module
    sys.modules.setdefault("plugin", plugin_package)
    sys.modules.setdefault("plugin.sdk", sdk_package)
    sys.modules["plugin.sdk.plugin"] = sdk_module

from mo_companion.plugin import MOCompanionPlugin


class _Policy:
    def validate_unit(self, _state, unit_id, action, **kwargs):
        return {"unit_id": unit_id, "action": action, **kwargs}


class _Client:
    def __init__(self):
        self.commands = []
        self.placements = []

    def command_unit(self, command):
        self.commands.append(dict(command))
        return {"accepted": True, "request_id": command.get("request_id")}

    def command_building_placement(self, command):
        self.placements.append(dict(command))
        return {
            "accepted": True, "status": 1, "matched_count": 1,
            "accepted_count": 1, "failed_count": 0, "last_error": "",
        }


class _Flags:
    def clear(self, *_args, **_kwargs):
        return None

    def set(self, *_args, **_kwargs):
        return None


def _plugin():
    plugin = MOCompanionPlugin.__new__(MOCompanionPlugin)
    plugin._client = _Client()
    plugin._cfg = {"attack_task_keepalive_seconds": 3.0}
    plugin._load_tasks = {}
    plugin._base_tasks = {}
    plugin._attack_tasks = {}
    plugin._policy = lambda: _Policy()
    plugin._append_diagnostic_log = lambda *_args, **_kwargs: None
    plugin._command_runtime = type("Runtime", (), {"flags": _Flags()})()
    return plugin


def _entity(object_id, relation, kind, type_id, x, y, **extra):
    return {
        "object_id": object_id, "relation": relation, "kind": kind,
        "type_id": type_id, "cell": {"x": x, "y": y}, "alive": True,
        "visible": True, "controllable": relation == "self", **extra,
    }


class CommandPathSafetyTests(unittest.TestCase):
    def test_empty_ifv_is_removed_and_never_receives_attack(self):
        plugin = _plugin()
        state = {"visible_entities": [
            _entity(1, "self", "vehicle", "FV", 1, 1,
                    passenger_count=0, transport_capacity=True, mission_id=1),
            _entity(90, "enemy", "vehicle", "HTNK", 8, 8),
        ]}
        task = {
            "unit_ids": [1], "assignments": {1: 90}, "last_order_at": {},
            "orders_issued": 0, "failed_orders": 0, "mode": "target",
            "target_query": "", "status": "engaging",
        }
        plugin._run_attack_task(task, state)
        self.assertEqual(task["unit_ids"], [])
        self.assertEqual(task["blocked_empty_ifv_ids"], [1])
        self.assertFalse(any(row.get("action") == "attack" for row in plugin._client.commands))
        self.assertTrue(any(row.get("action") == "stop" for row in plugin._client.commands))

    def test_loaded_ifv_attacks_combat_then_defense_then_factory(self):
        def issued_target(enemies):
            plugin = _plugin()
            state = {"visible_entities": [
                _entity(1, "self", "vehicle", "FV", 1, 1,
                        passenger_count=1, transport_capacity=True),
                *enemies,
            ]}
            task = {
                "unit_ids": [1], "assignments": {}, "last_order_at": {},
                "orders_issued": 0, "failed_orders": 0, "mode": "target",
                "target_query": "", "fire_control": "concentrated",
                "status": "waiting_for_enemy",
            }
            plugin._run_attack_task(task, state)
            attack = next(row for row in plugin._client.commands if row["action"] == "attack")
            return attack["target_object_id"]

        defense = _entity(20, "enemy", "building", "TESLA", 5, 5,
                          building_class="defense", combat_relevant=True)
        factory = _entity(30, "enemy", "building", "GAWEAP", 4, 4,
                          building_class="vehicle_production")
        tech = _entity(40, "enemy", "building", "GATECH", 3, 3,
                       building_class="technology")
        yard = _entity(50, "enemy", "building", "GACNST", 2, 2,
                       building_class="construction_yard")
        combat = _entity(10, "enemy", "infantry", "E1", 9, 9)
        self.assertEqual(issued_target([yard, tech, factory, defense, combat]), 10)
        self.assertEqual(issued_target([yard, tech, factory, defense]), 20)
        self.assertEqual(issued_target([yard, tech, factory]), 30)
        self.assertEqual(issued_target([yard, tech]), 40)
        self.assertEqual(issued_target([yard]), 50)

    def test_convoy_requires_fifteen_distinct_occupied_ifvs(self):
        plugin = _plugin()
        transports = [
            _entity(index, "self", "vehicle", "FV", index, 1,
                    passenger_count=(2 if index == 1 else 1 if index <= 14 else 0),
                    transport_capacity=True)
            for index in range(1, 16)
        ]
        task = {
            "passenger_ids": list(range(101, 116)),
            "transport_ids": list(range(1, 16)),
            "baseline_counts": {index: 0 for index in range(1, 16)},
            "assignments": {}, "last_order_at": {}, "last_positions": {},
            "approach_attempts": {}, "loaded_count": 0, "orders_issued": 0,
            "failed_orders": 0, "require_one_passenger_per_transport": True,
            "required_occupied_transports": 15, "status": "loading",
        }
        plugin._run_load_task(task, {"visible_entities": transports})
        self.assertEqual(task["loaded_count"], 15)
        self.assertEqual(task["status"], "loading")
        transports[-1]["passenger_count"] = 1
        transports[0]["passenger_count"] = 1
        plugin._run_load_task(task, {"visible_entities": transports})
        self.assertEqual(task["status"], "completed")
        self.assertEqual(len(task["occupied_transport_ids"]), 15)

    def test_upgrade_placement_submits_exact_power_plant_object(self):
        plugin = _plugin()
        state = {"visible_entities": [
            _entity(77, "self", "building", "GAPOWR", 12, 14,
                    upgrade_occupied_count=0),
        ]}
        task = {
            "task_id": "upgrade", "type_id": "GAPOWRUP",
            "last_placement_order_at": 0.0, "placement_epoch": 0,
        }
        self.assertTrue(plugin._attempt_building_placement(task, state))
        self.assertEqual(plugin._client.placements[0]["target_object_id"], 77)
        self.assertEqual(plugin._client.placements[0]["target_cell"], {"x": 12, "y": 14})

    def test_upgrade_only_completes_after_host_slot_changes(self):
        plugin = _plugin()
        task = {
            "task_id": "upgrade", "type_id": "GAPOWRUP", "kind": "building",
            "baseline_ids": set(), "observed_ids": set(), "requested_count": 1,
            "submitted_count": 1, "placement_accepted_at": time.monotonic() - 2,
            "placement_host_object_id": 77, "last_submit_at": time.monotonic(),
            "submit_attempts": 1,
        }
        empty_state = {"visible_entities": [
            _entity(77, "self", "building", "GAPOWR", 12, 14,
                    upgrade_occupied_count=0),
        ], "production": {"queue": []}}
        plugin._run_production_task(task, empty_state)
        self.assertNotEqual(task["status"], "completed")
        empty_state["visible_entities"][0]["upgrade_occupied_count"] = 1
        plugin._run_production_task(task, empty_state)
        self.assertEqual(task["status"], "completed")
        self.assertTrue(task["placement_verified"])


if __name__ == "__main__":
    unittest.main()
