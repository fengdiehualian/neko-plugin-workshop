from __future__ import annotations

import unittest

import _loader
from nmb.models import SourceConfig
from nmb.sources.auto import (
    build_auto_items,
    build_json_items,
    build_text_items,
    host_label,
    sniff,
)
from nmb.sources.http import FetchError

_loader.setup()

def cfg(**kwargs) -> SourceConfig:
    base = dict(id="auto", type="auto", label="测试", url="https://x.test/feed")
    base.update(kwargs)
    return SourceConfig(**base)

JSON_LIST = {
    "data": {
        "items": [
            {"title": "A", "excerpt": "ea", "link": "https://x.test/a", "date": "2026-10-21T00:00:00Z"},
            {"headline": "B", "description": "eb", "url": "https://x.test/b"},
            {"nothing": "here"},
        ]
    }
}

class SniffTests(unittest.TestCase):
    def test_json(self):
        self.assertEqual(sniff('  {"a":1}'), "json")
        self.assertEqual(sniff("[1,2]"), "json")

    def test_xml(self):
        self.assertEqual(sniff('<?xml version="1.0"?><rss></rss>'), "xml")
        self.assertEqual(sniff("<feed></feed>"), "xml")

    def test_html(self):
        self.assertEqual(sniff("<!DOCTYPE html><html></html>"), "html")
        self.assertEqual(sniff('<html lang="zh"></html>'), "html")

    def test_text_and_empty(self):
        self.assertEqual(sniff("hello world"), "text")
        self.assertEqual(sniff("   "), "empty")

class JsonTests(unittest.TestCase):
    def test_detects_list_and_maps_common_keys(self):
        items = build_json_items(JSON_LIST, cfg())
        self.assertEqual([item.title for item in items], ["A", "B"])
        self.assertEqual(items[0].summary, "ea")
        self.assertEqual(items[0].url, "https://x.test/a")
        self.assertIsNotNone(items[0].ts)
        self.assertFalse(items[0].snapshot)

    def test_respects_max_items(self):
        self.assertEqual(len(build_json_items(JSON_LIST, cfg(max_items=1))), 1)

    def test_scalar_becomes_snapshot(self):
        payload = {"bitcoin": {"cny": 512300, "change": 2.14}, "ok": True}
        items = build_json_items(payload, cfg(label="币价"))
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].snapshot)
        self.assertIn("512300", items[0].title)
        self.assertIn("币价", items[0].title)

    def test_nested_scalar(self):
        payload = {"data": {"free_gb": 12.5, "alerts": 0}}
        items = build_json_items(payload, cfg(label="NAS"))
        self.assertTrue(items[0].snapshot)
        self.assertIn("12.5", items[0].title)

class HtmlTests(unittest.TestCase):
    def test_title_and_description(self):
        html = """
        <!doctype html><html><head><title>站点标题</title>
        <meta name="description" content="这是描述"></head>
        <body><h1>我是标题</h1><p>正文段落</p></body></html>
        """
        items = build_auto_items(html, cfg())
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "我是标题")
        self.assertIn("描述", items[0].summary)
        self.assertTrue(items[0].snapshot)

    def test_fallback_to_title_tag(self):
        items = build_auto_items("<html><head><title>Only Title</title></head></html>", cfg())
        self.assertEqual(items[0].title, "Only Title")

    def test_no_title_is_error(self):
        with self.assertRaises(FetchError) as ctx:
            build_auto_items("<html><body>nothing</body></html>", cfg())
        self.assertEqual(ctx.exception.code, "PARSE_ERROR")

class FeedTests(unittest.TestCase):
    def test_rss_is_detected(self):
        payload = (_loader.FIXTURES / "rss2.xml").read_text(encoding="utf-8")
        items = build_auto_items(payload, cfg())
        self.assertGreaterEqual(len(items), 2)
        self.assertEqual(items[0].title, "First post")

    def test_atom_is_detected(self):
        payload = (_loader.FIXTURES / "atom.xml").read_text(encoding="utf-8")
        items = build_auto_items(payload, cfg())
        self.assertEqual(len(items), 2)

class TextTests(unittest.TestCase):
    def test_first_lines(self):
        items = build_text_items("第一行\n第二行\n第三行", cfg())
        self.assertEqual(items[0].title, "第一行")
        self.assertIn("第二行", items[0].summary)
        self.assertTrue(items[0].snapshot)

    def test_empty_is_error(self):
        with self.assertRaises(FetchError):
            build_text_items("   \n  ", cfg())

class MiscTests(unittest.TestCase):
    def test_invalid_json(self):
        with self.assertRaises(FetchError) as ctx:
            build_auto_items('{"broken": ', cfg())
        self.assertEqual(ctx.exception.code, "PARSE_ERROR")

    def test_host_label(self):
        self.assertEqual(host_label("https://Example.COM/a/b"), "example.com")
        self.assertEqual(host_label(None), "自定义源")

if __name__ == "__main__":
    unittest.main()
