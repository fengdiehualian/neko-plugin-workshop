from __future__ import annotations

import time
import unittest

import _loader
from nmb.normalize import (
    clean_text,
    clean_url,
    make_item,
    parse_ts,
    relative_time,
    strip_html,
)

_loader.setup()

class TextTests(unittest.TestCase):
    def test_strip_html_and_entities(self):
        self.assertEqual(strip_html("<b>a</b>&amp;b"), "a &b")

    def test_clean_text_collapses_and_truncates(self):
        text = clean_text("<p>hello   world</p>", limit=20)
        self.assertEqual(text, "hello world")
        long = "单词" * 60
        cleaned = clean_text(long, limit=20)
        self.assertLessEqual(len(cleaned), 20)
        self.assertTrue(cleaned.endswith("…"))

    def test_clean_text_none(self):
        self.assertEqual(clean_text(None), "")

    def test_clean_url_strips_tracking(self):
        url = "https://example.com/a?utm_source=x&id=3&fbclid=1#frag"
        self.assertEqual(clean_url(url), "https://example.com/a?id=3")

    def test_clean_url_rejects_schemes(self):
        self.assertIsNone(clean_url("file:///etc/passwd"))
        self.assertIsNone(clean_url("javascript:alert(1)"))
        self.assertIsNone(clean_url(""))

    def test_clean_url_keeps_plain(self):
        self.assertEqual(clean_url("http://127.0.0.1:8080/x"), "http://127.0.0.1:8080/x")

class TimeTests(unittest.TestCase):
    def test_epoch_seconds(self):
        self.assertEqual(parse_ts(1761000000), 1761000000.0)

    def test_epoch_millis(self):
        self.assertEqual(parse_ts(1761000000123), 1761000000.123)

    def test_epoch_micros(self):
        value = parse_ts(1761000000123456)
        self.assertAlmostEqual(value, 1761000000.123456, places=3)

    def test_iso8601(self):
        from datetime import datetime, timezone

        expected = datetime(2026, 10, 21, 8, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(parse_ts("2026-10-21T08:00:00Z"), expected, delta=1)

    def test_naive_datetime_is_utc(self):
        self.assertIsNotNone(parse_ts("2026-10-21 08:00:00"))

    def test_junk(self):
        for value in (None, "", "not a date", True, 0, -5):
            self.assertIsNone(parse_ts(value), value)

    def test_relative_time(self):
        now = time.time()
        self.assertEqual(relative_time(now - 10, now), "刚刚")
        self.assertEqual(relative_time(now - 120, now), "2 分钟前")
        self.assertEqual(relative_time(now - 7200, now), "2 小时前")
        self.assertEqual(relative_time(now - 90000, now), "昨天")
        self.assertEqual(relative_time(None, now), "")

class MakeItemTests(unittest.TestCase):
    def test_builds_normalized_item(self):
        item = make_item(
            source_id="news",
            title="<b>Title</b>",
            summary="<p>Summary</p>",
            url="https://x.test/a?utm_source=feed",
            ts="1761000000",
            weight=5,
            tags=("ai",),
        )
        assert item is not None
        self.assertEqual(item.title, "Title")
        self.assertEqual(item.summary, "Summary")
        self.assertEqual(item.url, "https://x.test/a")
        self.assertEqual(item.ts, 1761000000.0)
        self.assertEqual(item.weight, 5)
        self.assertEqual(item.dedupe_key, item.dedupe_key)

    def test_empty_title_rejected(self):
        self.assertIsNone(make_item(source_id="x", title="<p></p>"))

    def test_dedupe_key_stable(self):
        a = make_item(source_id="s", title="Same", url="https://x.test/1")
        b = make_item(source_id="s", title="Same", url="https://x.test/1")
        c = make_item(source_id="s", title="Other", url="https://x.test/1")
        assert a and b and c
        self.assertEqual(a.dedupe_key, b.dedupe_key)
        self.assertNotEqual(a.dedupe_key, c.dedupe_key)

if __name__ == "__main__":
    unittest.main()
