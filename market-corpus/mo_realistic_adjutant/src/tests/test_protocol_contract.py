from __future__ import annotations

import unittest

from mo_companion.bridge_client import PROTOCOL_VERSION as HTTP_PROTOCOL_VERSION
from mo_companion.real_bridge.shared_memory import (
    ENTITY_CAPACITY,
    MAPPING_NAME,
    PROTOCOL_VERSION as NATIVE_PROTOCOL_VERSION,
    SHARED_SIZE,
)


class FrozenProtocolContractTests(unittest.TestCase):
    """The native DLL and Python bridge share a deliberately frozen ABI."""

    def test_native_protocol_contract_is_not_versioned_by_business_changes(self):
        self.assertEqual(HTTP_PROTOCOL_VERSION, 10)
        self.assertEqual(NATIVE_PROTOCOL_VERSION, 10)
        self.assertEqual(MAPPING_NAME, "Local\\NekoMOBridge-v10")
        self.assertEqual(ENTITY_CAPACITY, 32768)
        self.assertEqual(SHARED_SIZE, 4352100)


if __name__ == "__main__":
    unittest.main()
