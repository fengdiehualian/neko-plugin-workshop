from __future__ import annotations

import unittest

from mo_companion.tutorial_knowledge import recommend_tactics, tutorial_search
from mo_companion.video_strategy_knowledge import load_video_strategy_knowledge


class VideoStrategyKnowledgeTests(unittest.TestCase):
    def test_both_reference_videos_are_registered(self):
        source_ids = {
            item["id"] for item in load_video_strategy_knowledge()["sources"]
        }
        self.assertEqual(source_ids, {"BV1btQGBVEN2", "BV19pcQzpELp"})

    def test_convoy_rule_is_searchable_from_main_tutorial_knowledge(self):
        ids = {item["id"] for item in tutorial_search("convoy")}
        self.assertIn("video_convoy_role_separation", ids)

    def test_low_harvester_state_activates_video_economy_rule(self):
        result = recommend_tactics({
            "production": {"queue": []},
            "economy": {"power_surplus": 500},
            "visible_entities": [],
            "alerts": [],
        })
        self.assertIn("prioritize_harvesters", result["actions"])

    def test_convoy_rule_requires_enough_ifvs(self):
        entities = [
            {"relation": "self", "type_id": "FV"} for _ in range(8)
        ]
        result = recommend_tactics({
            "production": {"queue": []},
            "economy": {"power_surplus": 500},
            "visible_entities": entities,
            "alerts": [],
        })
        self.assertIn("lock_passengers_before_departure", result["actions"])
        self.assertIn("keep_convoy_cohesive", result["actions"])


if __name__ == "__main__":
    unittest.main()
