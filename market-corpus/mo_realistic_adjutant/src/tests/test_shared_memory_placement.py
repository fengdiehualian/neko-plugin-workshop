from __future__ import annotations

import threading
import unittest

from mo_companion.real_bridge.shared_memory import (
    ENTITY,
    COMMAND,
    COMMAND_OFFSET,
    PROTOCOL_VERSION,
    SHARED_SIZE,
    SharedMemoryBridge,
)


class SharedMemoryPlacementTests(unittest.TestCase):
    def test_entity_capability_high_bits_preserve_upgrade_slot_counts(self):
        flags = (3 << 24) | (2 << 27) | (5 << 8) | (8 << 16)
        packed = ENTITY.pack(
            1, b"GAPOWR", b"Power Plant",
            0, 1, 4, 1, 1, 1, 1.0,
            10, 20, 0, flags, 0, 0, -1,
        )
        record = ENTITY.unpack(packed)
        self.assertEqual((record[13] >> 24) & 0x7, 3)
        self.assertEqual((record[13] >> 27) & 0x7, 2)
        self.assertEqual((record[13] >> 8) & 0xFF, 5)
        self.assertEqual((record[13] >> 16) & 0xFF, 8)

    def test_placement_packs_exact_upgrade_host_object_id(self):
        bridge = SharedMemoryBridge.__new__(SharedMemoryBridge)
        bridge._memory = bytearray(SHARED_SIZE)
        bridge._native_protocol = PROTOCOL_VERSION
        bridge._write_lock = threading.Lock()
        bridge._require_connected = lambda: None
        bridge._wait_for = lambda *_args, **_kwargs: None
        bridge._read_last_error = lambda: ""

        bridge.command_building_placement({
            "request_id": "upgrade-test",
            "type_id": "GAPOWRUP",
            "target_cell": {"x": 17, "y": 23},
            "target_object_id": 0x1234ABCD,
        })

        command = COMMAND.unpack_from(bridge._memory, COMMAND_OFFSET)
        self.assertEqual(command[6], 0x1234ABCD)
        self.assertEqual(command[4:6], (17, 23))


if __name__ == "__main__":
    unittest.main()
