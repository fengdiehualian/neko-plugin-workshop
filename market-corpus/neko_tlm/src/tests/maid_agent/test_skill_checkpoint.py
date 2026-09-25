import importlib
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from ._bootstrap import bootstrap

bootstrap()

SkillRun = importlib.import_module(
    "neko_tlm.maid_agent.skills.base"
).SkillRun
_checkpoint = importlib.import_module("neko_tlm.maid_agent.skills.checkpoint")
SkillCheckpointStore = _checkpoint.SkillCheckpointStore
StaleCheckpointError = _checkpoint.StaleCheckpointError


class SkillCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_contains_frozen_fields_and_uses_uuid_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_id = str(uuid.uuid4())
            run = SkillRun(
                skill_id=skill_id,
                maid_id="maid",
                skill_name="mine_ore",
                args={"ore": "minecraft:coal_ores"},
                origin_pos={"x": 1, "y": 64, "z": 2},
                current_pos={"x": 2, "y": 63, "z": 2},
                main_direction="east",
                tried_directions_at_current=["east", "south"],
                status="WAITING_ACTION",
                current_action_id=str(uuid.uuid4()),
                current_action_generation=3,
                current_action_fingerprint="fingerprint",
                revision=4,
            )
            store = SkillCheckpointStore(directory)
            await store.save(run)

            path = Path(directory) / f"{skill_id}.json"
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            for field in (
                "schema_version", "skill_id", "maid_id", "skill_name", "args",
                "collected_count", "origin_pos", "current_pos", "main_direction",
                "main_segment_index", "branch_index",
                "tried_directions_at_current", "status", "current_action_id",
                "current_action_generation", "current_action_fingerprint",
                "revision", "blocked_notification_revision",
                "last_failure_reason", "decision_required", "decision_context",
                "created_at", "updated_at",
            ):
                self.assertIn(field, payload)
            loaded = await store.load_all()
            self.assertEqual(1, len(loaded))
            self.assertEqual(run.as_dict(), loaded[0].as_dict())

    async def test_invalid_uuid_and_stale_revision_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillCheckpointStore(directory)
            invalid = SkillRun("not-an-id", "maid", "skill", {})
            with self.assertRaises(ValueError):
                await store.save(invalid)

            skill_id = str(uuid.uuid4())
            latest = SkillRun(skill_id, "maid", "skill", {}, revision=5)
            await store.save(latest)
            stale = SkillRun(skill_id, "maid", "skill", {}, revision=4)
            with self.assertRaises(StaleCheckpointError):
                await store.save(stale)
            self.assertEqual(5, (await store.load_all())[0].revision)

    async def test_corrupt_unknown_schema_are_ignored_and_terminal_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            corrupt_id = str(uuid.uuid4())
            (directory / f"{corrupt_id}.json").write_text("{broken", encoding="utf-8")
            unknown_id = str(uuid.uuid4())
            (directory / f"{unknown_id}.json").write_text(json.dumps({
                "schema_version": 99,
                "skill_id": unknown_id,
            }), encoding="utf-8")
            expired_id = str(uuid.uuid4())
            expired = SkillRun(
                expired_id, "maid", "skill", {}, status="BLOCKED",
                updated_at=time.time() - 100,
            )
            store = SkillCheckpointStore(directory, terminal_ttl=10)
            await store.save(expired)

            self.assertEqual([], await store.load_all())
            self.assertTrue((directory / f"{corrupt_id}.json").exists())
            self.assertTrue((directory / f"{unknown_id}.json").exists())
            self.assertFalse((directory / f"{expired_id}.json").exists())


if __name__ == "__main__":
    unittest.main()
