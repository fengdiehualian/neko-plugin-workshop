from __future__ import annotations

import unittest

from mo_companion.autonomy_nudge import build_autonomy_activation_nudge, build_reasoning_nudge


class AutonomyNudgeTests(unittest.TestCase):
    def test_idle_game_activation_check_routes_explicit_authorization(self):
        text = build_autonomy_activation_nudge({
            "revision": 7,
            "source_frame": 120,
            "running": True,
            "session_started_at": 1.0,
        })
        self.assertIn("mental_omega_autonomous_victory", text)
        self.assertIn("全权操作", text)
        self.assertIn("不得启动自主控制", text)
        self.assertIn('"world_revision":7', text)

    def test_nudge_contains_world_cursor_and_task_identity(self):
        text = build_reasoning_nudge(
            {"revision": 9, "source_frame": 120, "running": True, "recent_events": list(range(10))},
            [{"task_id": "abc123", "goal": "持续进攻", "status": "running", "objectives": []}],
        )
        self.assertIn("world_revision", text)
        self.assertIn("abc123", text)
        self.assertIn("不要把记忆中的敌人当作当前可攻击目标", text)

    def test_nudge_is_bounded_to_recent_events_and_active_plans(self):
        text = build_reasoning_nudge(
            {"revision": 1, "recent_events": list(range(100))},
            [{"task_id": str(i), "goal": "x", "status": "running", "objectives": []} for i in range(20)],
        )
        self.assertNotIn('"recent_events":[0,1,2', text)
        self.assertIn('"task_id":"0"', text)
        self.assertNotIn('"task_id":"8"', text)

    def test_nudge_contains_live_entities_but_not_hidden_enemy_records(self):
        text = build_reasoning_nudge(
            {"revision": 3}, [],
            [
                {"object_id": 1, "relation": "self", "alive": True, "visible": True, "type_id": "GI", "cell": {"x": 1, "y": 2}},
                {"object_id": 9, "relation": "enemy", "alive": True, "visible": True, "type_id": "E1", "cell": {"x": 9, "y": 9}},
                {"object_id": 10, "relation": "enemy", "alive": True, "visible": False, "type_id": "HIDDEN"},
            ],
        )
        self.assertIn('"type":"GI"', text)
        self.assertIn('"type":"E1"', text)
        self.assertNotIn("HIDDEN", text)


if __name__ == "__main__":
    unittest.main()
