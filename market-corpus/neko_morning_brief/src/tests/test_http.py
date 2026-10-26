from __future__ import annotations

import os
import unittest

import _loader
from nmb.sources.http import detect_system_proxy, parse_proxy_server, resolve_proxy

_loader.setup()

class ProxyParseTests(unittest.TestCase):
    def test_plain_host_port(self):
        self.assertEqual(parse_proxy_server("127.0.0.1:7890"), "http://127.0.0.1:7890")

    def test_scheme_kept(self):
        self.assertEqual(
            parse_proxy_server("http://proxy.test:8080"), "http://proxy.test:8080"
        )

    def test_scheme_mapping_prefers_https(self):
        raw = "http=127.0.0.1:7890;https=127.0.0.1:7891"
        self.assertEqual(parse_proxy_server(raw), "http://127.0.0.1:7891")

    def test_scheme_mapping_http_only(self):
        self.assertEqual(
            parse_proxy_server("http=127.0.0.1:7890"), "http://127.0.0.1:7890"
        )

    def test_empty(self):
        self.assertEqual(parse_proxy_server(""), "")
        self.assertEqual(parse_proxy_server("   "), "")

class ResolveTests(unittest.TestCase):
    def test_none_forces_direct(self):
        for value in ("none", "OFF", "direct", "0"):
            self.assertEqual(resolve_proxy(value), "", value)

    def test_explicit_value(self):
        self.assertEqual(resolve_proxy("127.0.0.1:1080"), "http://127.0.0.1:1080")

    def test_auto_uses_detection(self):
        expected = detect_system_proxy()
        self.assertEqual(resolve_proxy("auto"), expected)
        self.assertEqual(resolve_proxy(""), expected)

    def test_detection_never_raises(self):
        value = detect_system_proxy()
        self.assertIsInstance(value, str)
        if os.name != "nt":
            self.assertEqual(value, "")

if __name__ == "__main__":
    unittest.main()
