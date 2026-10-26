from __future__ import annotations

import unittest

import _loader
from nmb.template import TemplateError, render

_loader.setup()

class TemplateTests(unittest.TestCase):
    payload = {
        "data": {
            "bitcoin": {"cny": 512300, "change": 2.14},
            "ethereum": {"cny": 21000, "change": -1.5},
            "missing": None,
        },
        "alerts": 0,
    }

    def test_plain_substitution(self):
        self.assertEqual(render("BTC {data.bitcoin.cny} 元", self.payload), "BTC 512300 元")

    def test_format_spec(self):
        self.assertEqual(
            render("{data.bitcoin.change:+.2f}", self.payload), "+2.14"
        )

    def test_multiple_placeholders(self):
        text = "BTC {data.bitcoin.cny} / ETH {data.ethereum.cny}"
        self.assertEqual(render(text, self.payload), "BTC 512300 / ETH 21000")

    def test_optional_placeholder(self):
        self.assertEqual(render("告警 {data.missing?} 个", self.payload), "告警  个")
        self.assertEqual(render("值 {data.nope?}", self.payload), "值 ")

    def test_missing_required_raises(self):
        with self.assertRaises(TemplateError):
            render("值 {data.nope}", self.payload)

    def test_none_required_raises(self):
        with self.assertRaises(TemplateError):
            render("{data.missing}", self.payload)

    def test_literal_braces(self):
        self.assertEqual(render("{{literal}} {alerts}", self.payload), "{literal} 0")

    def test_bad_spec_raises(self):
        with self.assertRaises(TemplateError):
            render("{data.bitcoin.cny:not-a-spec}", self.payload)

    def test_non_scalar_raises(self):
        with self.assertRaises(TemplateError):
            render("{data.bitcoin}", self.payload)

    def test_unbalanced_braces_raise(self):
        with self.assertRaises(TemplateError):
            render("oops {data.bitcoin.cny", self.payload)

if __name__ == "__main__":
    unittest.main()
