from __future__ import annotations

import unittest

import _loader
from nmb.sources.feedparse import parse_feed


class FeedParseTests(unittest.TestCase):
    def read(self, name: str) -> bytes:
        return (_loader.FIXTURES / name).read_bytes()

    def test_rss2(self):
        entries = parse_feed(self.read("rss2.xml"))
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["title"], "First <b>post</b>")
        self.assertEqual(entries[0]["link"], "https://example.com/posts/1?utm_source=rss")
        # The parser keeps the publisher's bytes; entity decoding happens in normalize.
        self.assertIn("Hello &amp; welcome", entries[0]["summary"])
        self.assertEqual(entries[0]["published"], "Tue, 21 Oct 2026 00:00:00 +0000")
        self.assertEqual(entries[1]["published"], "2026-10-20T12:00:00Z")

    def test_atom(self):
        entries = parse_feed(self.read("atom.xml"))
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["title"], "Atom entry one")
        self.assertEqual(entries[0]["link"], "https://example.org/atom/1")
        self.assertIn("atom summary", entries[0]["summary"])
        self.assertEqual(entries[1]["link"], "https://example.org/atom/2")
        self.assertIn("Body content", entries[1]["summary"])

    def test_rdf(self):
        rdf = b"""<?xml version="1.0"?>
        <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                 xmlns="http://purl.org/rss/1.0/">
          <channel><title>rdf</title></channel>
          <item>
            <title>RDF item</title>
            <link>https://rdf.test/1</link>
            <description>rdf desc</description>
            <dc:date xmlns:dc="http://purl.org/dc/elements/1.1/">2026-10-19T00:00:00Z</dc:date>
          </item>
        </rdf:RDF>"""
        entries = parse_feed(rdf)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["link"], "https://rdf.test/1")

    def test_invalid_xml(self):
        with self.assertRaises(ValueError):
            parse_feed(b"<rss><channel>")

    def test_empty(self):
        with self.assertRaises(ValueError):
            parse_feed(b"")

    def test_unknown_root_falls_back(self):
        payload = b"<root><item><title>loose</title><link>https://x.test</link></item></root>"
        entries = parse_feed(payload)
        self.assertEqual(entries[0]["title"], "loose")


if __name__ == "__main__":
    unittest.main()
