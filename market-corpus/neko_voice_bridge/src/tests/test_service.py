"""Match the host: startup, command and shutdown use separate asyncio.run loops."""
import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path

from test_connection import CONFIG, Bridge, Worker, module

Service = importlib.import_module("nvb_connection_tests.service").ConnectionService


class LifecycleTests(unittest.TestCase):
    def test_connection_survives_startup_loop_shutdown_and_ui_loop_changes(self):
        bridge = Bridge()

        async def character():
            return {"name": "宿主称呼", "revision": "one"}

        def factory(path):
            return module.Connection(path, character_reader=character,
                                     worker_factory=Worker, client_factory=lambda _: bridge)

        with tempfile.TemporaryDirectory() as folder:
            service = Service(Path(folder) / "bridge.json", connection_factory=factory)
            try:
                # Host closes the lifecycle loop as soon as startup returns.
                asyncio.run(service.call("snapshot"))
                code = CONFIG["bridge_url"] + "/#token=" + CONFIG["bridge_token"]
                asyncio.run(service.call("pair", code))
                state = asyncio.run(service.call("snapshot"))
                self.assertTrue(state["connected"])
                self.assertEqual(state["character_name"], "宿主称呼")
                asyncio.run(service.call("control", "start"))
                self.assertEqual(asyncio.run(service.call("snapshot"))["phase"], "receiving")
                self.assertFalse(service.connection.task.done())
                self.assertFalse(service.connection.worker.task.done())
            finally:
                asyncio.run(service.close())
            self.assertFalse(service.thread.is_alive())
            self.assertTrue(service.loop.is_closed())

    def test_link_rejects_wrong_transport_remote_host_and_missing_token(self):
        for code in ("tcp://127.0.0.1:38765", "http://example.com/#token=" + CONFIG["bridge_token"],
                     "http://127.0.0.1:18765", "http://127.0.0.1:18765/?token=secret",
                     "http://127.0.0.1:18765/#token=a&token=b"):
            with self.assertRaises((ValueError, KeyError)):
                module.parse_connection_code(code)
