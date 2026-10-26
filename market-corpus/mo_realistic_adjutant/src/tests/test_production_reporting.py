from __future__ import annotations

import unittest

from mo_companion.report_policy import should_report_production_completion


class ProductionReportingTests(unittest.TestCase):
    def test_completion_is_silent_by_default(self):
        self.assertFalse(should_report_production_completion(False, "completed"))

    def test_failures_are_still_reportable_when_completion_is_silent(self):
        self.assertTrue(should_report_production_completion(False, "failed"))
        self.assertTrue(should_report_production_completion(False, "cancelled"))

    def test_explicit_enable_restores_completion_reports(self):
        self.assertTrue(should_report_production_completion(True, "completed"))


if __name__ == "__main__":
    unittest.main()
