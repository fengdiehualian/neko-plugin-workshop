import importlib
import unittest

from ._bootstrap import bootstrap

bootstrap()

SkillFeedbackHandler = importlib.import_module(
    "neko_tlm.maid_agent.skill_feedback"
).SkillFeedbackHandler


class FakePlugin:
    def __init__(self):
        self.pushes = []
        self.logger = None

    async def _push_minecraft_context(self, text, **kwargs):
        self.pushes.append((text, kwargs))


class ReceiptPlugin(FakePlugin):
    def __init__(self, receipts):
        super().__init__()
        self.receipts = list(receipts)

    async def _push_minecraft_context(self, text, **kwargs):
        self.pushes.append((text, kwargs))
        return self.receipts.pop(0)


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class SkillFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_false_enqueue_receipt_is_retried_before_marking_delivered(self):
        plugin = ReceiptPlugin([False, True])
        feedback = SkillFeedbackHandler(plugin)
        delivered = await feedback.blocked({
            "skill_id": "receipt", "skill_name": "mine_ore",
            "revision": 2, "blocked_notification_revision": 0,
            "status": "BLOCKED", "last_failure_reason": "SAND",
            "result": {"decision_required": True},
        })
        self.assertTrue(delivered)
        self.assertEqual(2, len(plugin.pushes))

    async def test_progress_is_read_and_throttled_by_child_stage(self):
        plugin = FakePlugin()
        clock = Clock()
        feedback = SkillFeedbackHandler(plugin, clock=clock)
        base = {
            "skill_id": "s", "maid_id": "m", "skill_name": "mine_ore",
            "revision": 1, "status": "WAITING_ACTION", "collected_count": 0,
            "args": {"target_count": 5},
        }
        await feedback.progress({
            **base, "child_action": {"kind": "excavate_segment", "stage": "DIGGING"},
        })
        clock.value = 0.5
        self.assertFalse(await feedback.progress({
            **base, "child_action": {
                "kind": "excavate_segment", "stage": "DIGGING", "progress": 0.8,
            },
        }))
        self.assertTrue(await feedback.progress({
            **base, "child_action": {"kind": "harvest_blocks", "stage": "BREAKING"},
        }))
        self.assertEqual(2, len(plugin.pushes))
        self.assertTrue(all(item[1]["ai_behavior"] == "read" for item in plugin.pushes))
        self.assertTrue(all("%" not in item[0] for item in plugin.pushes))

    async def test_progress_exposes_compact_java_planner_choice(self):
        plugin = FakePlugin()
        feedback = SkillFeedbackHandler(plugin)
        await feedback.progress({
            "skill_id": "planner", "maid_id": "m", "skill_name": "mine_ore",
            "revision": 3, "status": "WAITING_ACTION", "collected_count": 0,
            "args": {"target_count": 4},
            "child_action": {
                "kind": "autonomous_mining", "stage": "SELECTING_SITE",
                "detail": {"planner_decision": {
                    "choice": "natural_passage", "direction": "east",
                    "shape": "level", "total_cost": 1.25,
                }},
            },
        })
        text, kwargs = plugin.pushes[0]
        self.assertEqual("read", kwargs["ai_behavior"])
        self.assertIn("MiningPlanner 选择 natural_passage", text)
        self.assertIn("方向 east", text)
        self.assertNotIn("candidates", text)

    async def test_blocked_responds_once_per_revision_and_preserves_suggestions(self):
        plugin = FakePlugin()
        feedback = SkillFeedbackHandler(plugin)
        snapshot = {
            "skill_id": "s", "maid_id": "m", "skill_name": "mine_ore",
            "revision": 4, "blocked_notification_revision": 0,
            "status": "BLOCKED", "last_failure_reason": "ALL_DIRECTIONS_BLOCKED",
            "collected_count": 1,
            "result": {"suggestions": [{
                "kind": "change_level", "basis": "current_dimension_unknown",
                "requires_confirmation": True,
            }]},
        }
        self.assertTrue(await feedback.blocked(snapshot))
        self.assertFalse(await feedback.blocked(snapshot))
        text, kwargs = plugin.pushes[0]
        self.assertEqual("respond", kwargs["ai_behavior"])
        self.assertIn("不会自动继续", text)
        self.assertIn("current_dimension_unknown", text)
        self.assertIn("禁止原样重启", text)

        already_notified = {
            **snapshot, "revision": 5, "blocked_notification_revision": 5,
        }
        self.assertFalse(await feedback.blocked(already_notified))

    async def test_terminal_responds_and_reports_actual_count(self):
        plugin = FakePlugin()
        feedback = SkillFeedbackHandler(plugin)
        await feedback.finished({
            "skill_id": "s", "skill_name": "mine_ore", "revision": 7,
            "status": "SUCCEEDED", "collected_count": 9,
            "result": {"target_count": 8, "blocks_harvested": 9},
        })
        text, kwargs = plugin.pushes[0]
        self.assertEqual("respond", kwargs["ai_behavior"])
        self.assertIn("实际采集数量：9", text)

    async def test_construction_blocked_feedback_forces_specific_safe_choice(self):
        plugin = FakePlugin()
        feedback = SkillFeedbackHandler(plugin)
        await feedback.blocked({
            "skill_id": "build", "skill_name": "mine_ore", "revision": 8,
            "blocked_notification_revision": 0, "status": "BLOCKED",
            "last_failure_reason": "PLACEMENT_PROTECTED",
            "result": {"decision_required": True},
        })
        text, kwargs = plugin.pushes[0]
        self.assertEqual("respond", kwargs["ai_behavior"])
        self.assertIn("绝不能绕过保护", text)
        self.assertIn("改走不需放置的路线", text)

    async def test_backpack_full_feedback_recommends_return_to_base(self):
        plugin = FakePlugin()
        feedback = SkillFeedbackHandler(plugin)
        await feedback.blocked({
            "skill_id": "build", "skill_name": "mine_ore", "revision": 9,
            "blocked_notification_revision": 0, "status": "BLOCKED",
            "last_failure_reason": "BACKPACK_FULL",
            "result": {"decision_required": True},
        })
        text, kwargs = plugin.pushes[0]
        self.assertEqual("respond", kwargs["ai_behavior"])
        self.assertIn("背包已满", text)
        self.assertIn("返回基地", text)
        self.assertIn("卸货", text)
        self.assertIn("可能尚未开始采矿", text)
        self.assertIn("只换 selector 不能创造背包容量", text)

    async def test_duplicate_backpack_full_terminal_does_not_repeat_feedback(self):
        # 重复 terminal(skill_id+revision 相同)不重复推送反馈
        plugin = FakePlugin()
        feedback = SkillFeedbackHandler(plugin)
        snapshot = {
            "skill_id": "dup", "skill_name": "mine_ore", "revision": 7,
            "blocked_notification_revision": 0, "status": "BLOCKED",
            "last_failure_reason": "BACKPACK_FULL",
            "result": {
                "decision_required": True,
                "remaining_target_count": 5,
                "restart_supported": True,
            },
        }
        first = await feedback.blocked(snapshot)
        second = await feedback.blocked(snapshot)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(1, len(plugin.pushes))


if __name__ == "__main__":
    unittest.main()
