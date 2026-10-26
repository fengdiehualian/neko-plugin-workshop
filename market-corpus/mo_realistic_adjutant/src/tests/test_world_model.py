from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mo_companion.world_model import WorldModel


def entity(object_id, relation, kind, x, y, *, group=-1, health=1.0, mission=0):
    return {
        "object_id": object_id,
        "type_id": f"TYPE{object_id}",
        "display_name": f"Entity {object_id}",
        "relation": relation,
        "kind": kind,
        "alive": True,
        "visible": True,
        "controllable": relation == "self",
        "health_ratio": health,
        "cell": {"x": x, "y": y},
        "mission_id": mission,
        "group_number": group,
    }


def snapshot(frame, entities, *, credits=5000, alerts=None, running=True):
    return {
        "game": {"running": running, "mode": "skirmish", "frame": frame},
        "player": {"house_id": 0, "credits": credits},
        "map": {"origin_x": 0, "origin_y": 0, "width": 128, "height": 128},
        "snapshot_age_ms": 12,
        "visible_entities": entities,
        "entity_feed": {"count": len(entities), "capacity": 1024, "truncated": False},
        "economy": {"home_ore_value": 20000, "power_state": "normal"},
        "production": {
            "available": True,
            "queue": [],
            "sidebar_available": [{"type_id": "E1", "kind": "infantry"}],
            "owned_counts": {},
        },
        "production_telemetry_available": True,
        "markers": [{"marker_id": 2, "cell": {"x": 30, "y": 30}}],
        "marker_telemetry_available": True,
        "alerts": alerts or [],
    }


class WorldModelTests(unittest.TestCase):
    def test_large_buffer_budget_is_reported_without_preallocating_one_gibibyte(self):
        model = WorldModel(history_size=1_000_000, memory_budget_bytes=1_073_741_824)
        summary = model.update(snapshot(1, [entity(1, "self", "infantry", 1, 1)]), observed_at=1.0)
        self.assertEqual(summary["buffer"]["budget_bytes"], 1_073_741_824)
        self.assertGreater(summary["buffer"]["event_count"], 0)
        self.assertGreater(summary["buffer"]["estimated_bytes"], 0)

    def test_tracks_groups_memory_events_and_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "world_model.jsonl"
            model = WorldModel(history_size=64, entity_memory_seconds=60, log_path=log_path)
            first = snapshot(
                100,
                [
                    entity(1, "self", "building", 10, 10),
                    entity(2, "self", "infantry", 14, 10, group=2),
                    entity(3, "self", "infantry", 15, 10, group=2),
                    entity(9, "enemy", "vehicle", 22, 10),
                ],
            )
            summary = model.update(first, observed_at=1000.0)
            group = next(item for item in summary["groups"] if item["group_number"] == 2)
            self.assertEqual(group["unit_count"], 2)
            self.assertEqual(summary["forces"]["enemy"]["total"], 1)
            self.assertEqual(summary["threats"][0]["object_id"], 9)

            second = snapshot(
                110,
                [
                    entity(1, "self", "building", 10, 10),
                    entity(2, "self", "infantry", 20, 10, group=3, health=0.7, mission=5),
                    entity(3, "self", "infantry", 15, 10, group=2),
                ],
                credits=4500,
                alerts=[{
                    "event_id": 17,
                    "kind": "under_attack",
                    "object_id": 1,
                    "object_kind": "building",
                    "cell": {"x": 10, "y": 10},
                    "severity": 3,
                }],
            )
            second["production"]["queue"] = [{
                "type_id": "E1", "kind": "infantry", "queued_count": 2,
                "progress_ratio": 0.3, "on_hold": False,
            }]
            second["markers"] = [{"marker_id": 2, "cell": {"x": 50, "y": 50}}]
            model.update(second, observed_at=1001.0)
            view = model.view(event_limit=64)
            kinds = {item["kind"] for item in view["recent_events"]}
            self.assertTrue({
                "group_changed", "entity_damaged", "entity_moved", "mission_changed",
                "entity_lost_visibility", "credits_changed", "game_alert",
                "production_changed", "markers_changed",
            }.issubset(kinds))
            remembered_enemy = next(item for item in view["entities"] if item["object_id"] == 9)
            self.assertFalse(remembered_enemy["observed"])
            self.assertFalse(remembered_enemy["visible"])
            self.assertLess(remembered_enemy["confidence"], 1.0)
            self.assertTrue(log_path.exists())
            self.assertIn('"kind":"game_alert"', log_path.read_text(encoding="utf-8"))

    def test_new_match_drops_previous_entity_memory(self):
        model = WorldModel()
        model.update(snapshot(900, [entity(91, "enemy", "vehicle", 40, 40)]), observed_at=10.0)
        model.update(snapshot(901, [], running=False), observed_at=11.0)
        model.update(snapshot(1, [entity(1, "self", "building", 5, 5)]), observed_at=12.0)
        ids = {item["object_id"] for item in model.view()["entities"]}
        self.assertEqual(ids, {1})

    def test_hidden_enemy_telemetry_cannot_refresh_last_known_position(self):
        model = WorldModel(entity_memory_seconds=60)
        model.update(snapshot(10, [entity(9, "enemy", "vehicle", 20, 20)]), observed_at=100.0)
        hidden = entity(9, "enemy", "vehicle", 90, 90)
        hidden["visible"] = False
        model.update(snapshot(11, [hidden]), observed_at=101.0)
        remembered = next(item for item in model.view()["entities"] if item["object_id"] == 9)
        self.assertFalse(remembered["observed"])
        self.assertEqual(remembered["cell"], {"x": 20, "y": 20})


if __name__ == "__main__":
    unittest.main()
