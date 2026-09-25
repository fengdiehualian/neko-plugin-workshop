import importlib
import sys
import types
import unittest

from ._bootstrap import bootstrap

bootstrap()

# The unit under test is the pure log-preview helper. The standalone test
# environment does not install the runtime WebSocket dependency, so provide
# the minimum import surface required by bridge.py.
if "websockets" not in sys.modules:
    websockets = types.ModuleType("websockets")
    exceptions = types.ModuleType("websockets.exceptions")
    exceptions.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
    exceptions.InvalidMessage = type("InvalidMessage", (Exception,), {})
    websockets.exceptions = exceptions
    sys.modules["websockets"] = websockets
    sys.modules["websockets.exceptions"] = exceptions

_bridge = importlib.import_module("neko_tlm.bridge")
_DEFAULT_RECV_LOG_LIMIT = _bridge._DEFAULT_RECV_LOG_LIMIT
_MAID_ACTION_FINISHED_LOG_LIMIT = _bridge._MAID_ACTION_FINISHED_LOG_LIMIT
_received_log_preview = _bridge._received_log_preview


class BridgeLoggingTests(unittest.TestCase):
    def test_regular_messages_keep_short_console_preview(self):
        raw = "x" * 1000
        preview = _received_log_preview(raw, "maid_action_progress")
        self.assertEqual(_DEFAULT_RECV_LOG_LIMIT, len(preview))

    def test_finished_message_keeps_reason_and_result_beyond_regular_prefix(self):
        raw = (
            '{"type":"maid_action_finished","padding":"'
            + ("x" * 350)
            + '","end_reason":"PATH_NOT_FOUND",'
              '"result":{"message":"no safe reachable face"}}'
        )
        preview = _received_log_preview(raw, "maid_action_finished")
        self.assertIn('"end_reason":"PATH_NOT_FOUND"', preview)
        self.assertIn('"result":{"message":"no safe reachable face"}', preview)
        self.assertEqual(raw, preview)

    def test_finished_message_is_bounded_to_4096_characters(self):
        raw = "x" * 5000
        preview = _received_log_preview(raw, "maid_action_finished")
        self.assertEqual(_MAID_ACTION_FINISHED_LOG_LIMIT, len(preview))


if __name__ == "__main__":
    unittest.main()
