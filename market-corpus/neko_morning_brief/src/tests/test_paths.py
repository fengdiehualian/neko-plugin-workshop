from __future__ import annotations

import unittest

import _loader
from nmb.paths import MISSING, resolve, resolve_str

_loader.setup()

class ResolveTests(unittest.TestCase):
    payload = {
        "data": {
            "items": [
                {"title": "one", "tags": ["a"]},
                {"title": "two"},
            ],
            "price": 12.5,
        },
        "list": [10, 20, 30],
        "flag": False,
    }

    def test_dotted_path(self):
        self.assertEqual(resolve(self.payload, "$.data.price"), 12.5)
        self.assertEqual(resolve(self.payload, "data.price"), 12.5)

    def test_wildcard_last(self):
        rows = resolve(self.payload, "$.data.items[*]")
        self.assertEqual([row["title"] for row in rows], ["one", "two"])

    def test_index(self):
        self.assertEqual(resolve(self.payload, "$.list[1]"), 20)
        self.assertEqual(resolve(self.payload, "$.list[-1]"), 30)

    def test_key_over_list_maps(self):
        self.assertEqual(resolve(self.payload, "$.data.items.title"), ["one", "two"])

    def test_missing_is_sentinel(self):
        self.assertIs(resolve(self.payload, "$.nope"), MISSING)
        self.assertIs(resolve(self.payload, "$.data.items[9]"), MISSING)
        self.assertIs(resolve(self.payload, "$.data.price.deeper"), MISSING)

    def test_wildcard_not_last_is_rejected(self):
        self.assertIs(resolve(self.payload, "$.data.items[*].title"), MISSING)

    def test_invalid_paths(self):
        for path in ("", "$", "$.data..price", "data[", None, 42):
            self.assertIs(resolve(self.payload, path), MISSING, path)

    def test_resolve_str(self):
        self.assertEqual(resolve_str(self.payload, "$.data.price"), "12.5")
        self.assertEqual(resolve_str(self.payload, "$.flag"), "false")
        self.assertEqual(resolve_str(self.payload, "$.data.items"), "")
        self.assertEqual(resolve_str(self.payload, "$.nope", "fallback"), "fallback")

if __name__ == "__main__":
    unittest.main()
