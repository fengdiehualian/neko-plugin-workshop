import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

ActionFeedbackHandler = importlib.import_module(
    "neko_tlm.maid_agent.feedback"
).ActionFeedbackHandler
ActionRecord = importlib.import_module("neko_tlm.maid_agent.models").ActionRecord


class ActionFeedbackTruthTests(unittest.TestCase):
    def test_partial_harvest_text_cannot_be_read_as_goal_completion(self):
        text = ActionFeedbackHandler._finished_text(ActionRecord(
            action_id="action",
            maid_id="maid",
            kind="harvest_blocks",
            status="SUCCEEDED",
            end_reason="COMPLETED",
            result={
                "harvested": 5,
                "requested": 8,
                "partial": True,
                "request_satisfied": False,
            },
        ))
        self.assertIn("实际采集 5 块", text)
        self.assertIn("尚未满足请求", text)
        self.assertIn("禁止把本动作说成已经采够", text)

    def test_satisfied_harvest_reports_actual_count(self):
        text = ActionFeedbackHandler._finished_text(ActionRecord(
            action_id="action",
            maid_id="maid",
            kind="harvest_blocks",
            status="SUCCEEDED",
            end_reason="COMPLETED",
            result={
                "harvested": 8,
                "requested": 8,
                "partial": False,
                "request_satisfied": True,
            },
        ))
        self.assertIn("已经成功完成", text)
        self.assertIn("实际采集 8 块", text)
        self.assertIn("request_satisfied=true", text)


if __name__ == "__main__":
    unittest.main()
