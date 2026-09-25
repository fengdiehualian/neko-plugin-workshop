import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

_events = importlib.import_module("neko_tlm.events")
event_matches_assigned_maid = _events.event_matches_assigned_maid
format_event = _events.format_event


class EventScopeTests(unittest.TestCase):
    def test_unscoped_player_evidence_is_rejected(self):
        event = {
            "event_type": "block_activity",
            "player_name": "other-player",
            "count": 12,
        }

        self.assertFalse(event_matches_assigned_maid(event, "maid-a"))
        self.assertEqual((None, None, None), format_event(event, "maid-a"))

    def test_player_evidence_requires_the_assigned_maid(self):
        event = {
            "event_type": "player_kill_entity",
            "maid_id": "maid-a",
            "player_id": "owner-a",
            "player_name": "owner",
            "count": 2,
            "primary_target": "minecraft:zombie",
        }

        self.assertTrue(event_matches_assigned_maid(event, "maid-a"))
        self.assertIsNotNone(format_event(event, "maid-a")[0])
        self.assertFalse(event_matches_assigned_maid(event, "maid-b"))
        self.assertEqual((None, None, None), format_event(event, "maid-b"))

    def test_global_world_event_remains_available(self):
        event = {"event_type": "weather_change", "raining": True}

        self.assertTrue(event_matches_assigned_maid(event, "maid-a"))
        self.assertIsNotNone(format_event(event, "maid-a")[0])


if __name__ == "__main__":
    unittest.main()
