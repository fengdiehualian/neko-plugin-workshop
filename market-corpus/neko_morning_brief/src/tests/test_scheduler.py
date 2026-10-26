from __future__ import annotations

import unittest
from datetime import datetime

import _loader
from nmb.scheduler import due_slot, next_run_at, normalize_times, parse_time

_loader.setup()

class ParseTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time("8:05"), (8, 5))
        self.assertEqual(parse_time("08:05"), (8, 5))
        self.assertEqual(parse_time("23:59"), (23, 59))
        self.assertIsNone(parse_time("24:00"))
        self.assertIsNone(parse_time("8:60"))
        self.assertIsNone(parse_time(800))
        self.assertIsNone(parse_time(None))

    def test_normalize_times(self):
        self.assertEqual(normalize_times(["22:30", "8:00", "08:00"]), ("08:00", "22:30"))
        self.assertEqual(normalize_times("09:15"), ("09:15",))
        self.assertEqual(normalize_times([]), ("08:00",))
        self.assertEqual(normalize_times(["junk"]), ("08:00",))
        self.assertEqual(normalize_times(None), ("08:00",))

class DueSlotTests(unittest.TestCase):
    times = ("08:00", "22:30")

    def at(self, hh: int, mm: int, ss: int = 0) -> datetime:
        return datetime(2026, 10, 21, hh, mm, ss)

    def never(self, day, slot):
        return False

    def test_not_yet(self):
        self.assertIsNone(due_slot(self.at(7, 59), self.times, 120, self.never))

    def test_exact_time(self):
        self.assertEqual(due_slot(self.at(8, 0), self.times, 120, self.never), "08:00")

    def test_inside_window(self):
        self.assertEqual(due_slot(self.at(9, 30), self.times, 120, self.never), "08:00")

    def test_outside_window_skipped(self):
        self.assertIsNone(due_slot(self.at(10, 1), self.times, 120, self.never))

    def test_zero_window(self):
        self.assertIsNone(due_slot(self.at(8, 1), self.times, 0, self.never))
        self.assertEqual(due_slot(self.at(8, 0), self.times, 0, self.never), "08:00")

    def test_already_ran(self):
        def ran(day, slot):
            return slot == "08:00"

        self.assertIsNone(due_slot(self.at(8, 5), self.times, 120, ran))
        # 22:30 still pending
        self.assertEqual(due_slot(self.at(22, 30), self.times, 120, ran), "22:30")

    def test_second_slot_after_missing_first(self):
        self.assertEqual(due_slot(self.at(22, 31), self.times, 120, self.never), "22:30")

    def test_missed_first_does_not_block_second(self):
        self.assertEqual(due_slot(self.at(22, 30), self.times, 30, self.never), "22:30")

class NextRunTests(unittest.TestCase):
    def test_later_today(self):
        now = datetime(2026, 10, 21, 9, 0)
        self.assertEqual(next_run_at(now, ("08:00", "22:30")), datetime(2026, 10, 21, 22, 30))

    def test_tomorrow(self):
        now = datetime(2026, 10, 21, 23, 0)
        self.assertEqual(next_run_at(now, ("08:00",)), datetime(2026, 10, 22, 8, 0))

    def test_empty(self):
        self.assertIsNone(next_run_at(datetime(2026, 10, 21, 9, 0), ()))

if __name__ == "__main__":
    unittest.main()
