from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from mo_companion.real_bridge.shared_memory import (
    ACTION_CODES,
    COMMAND,
    COMMAND_OFFSET,
    PROTOCOL_VERSION,
    SHARED_SIZE,
    SharedMemoryBridge,
)
from mo_companion.support_power_control import SupportPowerController


class SupportPowerTests(unittest.TestCase):
    def test_native_command_uses_frozen_mailbox_action_and_mode(self):
        bridge = SharedMemoryBridge.__new__(SharedMemoryBridge)
        bridge._memory = bytearray(SHARED_SIZE)
        bridge._native_protocol = PROTOCOL_VERSION
        bridge._write_lock = threading.Lock()
        bridge._require_connected = lambda: None
        bridge._wait_for = lambda *_args, **_kwargs: None
        bridge._read_last_error = lambda: ""
        bridge.command_support_power({
            "request_id": "support-test",
            "type_id": "LIGHTNING",
            "target_cell": {"x": 17, "y": 23},
        })
        command = COMMAND.unpack_from(bridge._memory, COMMAND_OFFSET)
        self.assertEqual(ACTION_CODES["support_power"], 21)
        self.assertEqual(command[2:6], (21, 6, 17, 23))
        self.assertEqual(command[8], 1)
        self.assertEqual(command[9][:24].split(b"\0", 1)[0], b"LIGHTNING")

    def test_unknown_power_is_never_automatically_fired(self):
        calls = []
        with tempfile.TemporaryDirectory() as root:
            controller = SupportPowerController(calls.append, Path(root) / "log.jsonl")
            state = self._state("MYSTERYPOWER")
            self.assertIsNone(controller.run_once(state))
            self.assertEqual(calls, [])

    def test_enemy_cluster_excludes_neutral_scenery(self):
        entities = [
            self._entity(1, "enemy", 20, 20, "vehicle"),
            self._entity(2, "enemy", 21, 20, "vehicle"),
            self._entity(3, "enemy", 20, 21, "building", "production"),
            self._entity(4, "neutral", 20, 20, "building", "civilian"),
        ]
        target = SupportPowerController.score_target(entities, "enemy", 4.0)
        self.assertIsNotNone(target)
        self.assertEqual(target["matched"], 3)
        self.assertEqual(target["score"], 9.0)

    def test_ready_transition_verifies_native_effect(self):
        def submit(_request):
            return {"accepted": True, "accepted_count": 1, "status": 1}
        with tempfile.TemporaryDirectory() as root:
            controller = SupportPowerController(
                submit, Path(root) / "log.jsonl", verification_seconds=0.01
            )
            state = self._state("LIGHTNING")
            result = controller.run_once(state)
            self.assertTrue(result["accepted"])
            time.sleep(0.12)
            state["production"]["support_powers"][0]["ready"] = False
            verified = controller.observe(state)
            self.assertEqual(verified[0]["verification"], "confirmed")

    @staticmethod
    def _entity(object_id, relation, x, y, kind, building_class=None):
        return {
            "object_id": object_id, "relation": relation, "alive": True,
            "visible": True, "cell": {"x": x, "y": y}, "kind": kind,
            "building_class": building_class,
        }

    @classmethod
    def _state(cls, type_id):
        enemies = [cls._entity(index, "enemy", 20 + index % 3, 20, "vehicle") for index in range(1, 9)]
        return {
            "production": {"support_powers": [{
                "type_id": type_id, "ready": True, "available": True,
            }]},
            "visible_entities": enemies,
        }


if __name__ == "__main__":
    unittest.main()
