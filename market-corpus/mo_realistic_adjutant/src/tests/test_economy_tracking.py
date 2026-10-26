from __future__ import annotations

import unittest

from mo_companion.economy_tracking import EconomyTracker


def state(frame: int, surplus: int) -> dict:
    return {
        "game": {"running": True, "frame": frame},
        "player": {"house_id": 1, "credits": 5000},
        "economy": {
            "power_available": True,
            "power_output": 1000,
            "power_drain": 1000 - surplus,
            "power_surplus": surplus,
        },
    }


class EconomyPowerStabilityTests(unittest.TestCase):
    def test_power_is_not_stable_until_three_contiguous_samples(self):
        tracker = EconomyTracker(minimum_sample_interval=0)
        tracker.observe(state(1, 400), now=1)
        tracker.observe(state(2, 400), now=2)
        self.assertFalse(tracker.report(now=2)["power"]["stable"])
        tracker.observe(state(3, 400), now=3)
        report = tracker.report(now=3)["power"]
        self.assertTrue(report["stable"])
        self.assertEqual(report["stable_samples"], 3)

    def test_low_power_sample_breaks_stability(self):
        tracker = EconomyTracker(minimum_sample_interval=0)
        tracker.observe(state(1, 400), now=1)
        tracker.observe(state(2, 400), now=2)
        tracker.observe(state(3, -50), now=3)
        tracker.observe(state(4, 400), now=4)
        report = tracker.report(now=4)["power"]
        self.assertFalse(report["stable"])
        self.assertEqual(report["stable_samples"], 3)


if __name__ == "__main__":
    unittest.main()
