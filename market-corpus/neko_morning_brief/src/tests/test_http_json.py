from __future__ import annotations

import unittest

import _loader
from nmb.models import SourceConfig
from nmb.sources.http_json import build_items, extract_rows

_loader.setup()

def cfg(**kwargs) -> SourceConfig:
    base = dict(id="api", type="http.json", url="https://api.test/x")
    base.update(kwargs)
    return SourceConfig(**base)

PAYLOAD = {
    "data": {
        "items": [
            {
                "title": "Item A",
                "excerpt": "<b>excerpt</b>",
                "permalink": "https://site.test/a?utm_source=x",
                "published_at": "2026-10-21T00:00:00Z",
            },
            {"title": "Item B", "excerpt": "", "permalink": "https://site.test/b"},
            {"excerpt": "no title"},
            {"title": ""},
        ]
    },
    "bitcoin": {"cny": 512300, "change": 2.14},
}

class ListModeTests(unittest.TestCase):
    def config(self, **kwargs) -> SourceConfig:
        fields = dict(
            mode="list",
            items_path="$.data.items",
            map={
                "title": "$.title",
                "summary": "$.excerpt",
                "url": "$.permalink",
                "ts": "$.published_at",
            },
            max_items=3,
        )
        fields.update(kwargs)
        return cfg(**fields)

    def test_maps_rows(self):
        items = build_items(PAYLOAD, self.config())
        self.assertEqual([item.title for item in items], ["Item A", "Item B"])
        self.assertEqual(items[0].summary, "excerpt")
        self.assertEqual(items[0].url, "https://site.test/a")
        self.assertIsNotNone(items[0].ts)

    def test_respects_max_items(self):
        items = build_items(PAYLOAD, self.config(max_items=1))
        self.assertEqual(len(items), 1)

    def test_wildcard_items_path(self):
        items = build_items(PAYLOAD, self.config(items_path="$.data.items[*]"))
        self.assertEqual(len(items), 2)

    def test_missing_items_path_raises(self):
        from nmb.sources.http import FetchError

        with self.assertRaises(FetchError) as ctx:
            build_items(PAYLOAD, self.config(items_path="$.data.nope"))
        self.assertEqual(ctx.exception.code, "PARSE_ERROR")

    def test_no_items_path_raises_config(self):
        from nmb.sources.http import FetchError

        with self.assertRaises(FetchError) as ctx:
            extract_rows(PAYLOAD, cfg(mode="list"))
        self.assertEqual(ctx.exception.code, "CONFIG_INVALID")

class SingleModeTests(unittest.TestCase):
    def test_template(self):
        config = cfg(
            mode="single",
            text="BTC {bitcoin.cny} 元（{bitcoin.change:+.1f}%）",
        )
        items = build_items(PAYLOAD, config)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "BTC 512300 元（+2.1%）")

    def test_missing_field_is_parse_error(self):
        from nmb.sources.http import FetchError

        config = cfg(mode="single", text="{nope}")
        with self.assertRaises(FetchError) as ctx:
            build_items(PAYLOAD, config)
        self.assertEqual(ctx.exception.code, "PARSE_ERROR")

    def test_missing_template_is_config_error(self):
        from nmb.sources.http import FetchError

        with self.assertRaises(FetchError) as ctx:
            build_items(PAYLOAD, cfg(mode="single"))
        self.assertEqual(ctx.exception.code, "CONFIG_INVALID")

    def test_bad_mode(self):
        from nmb.sources.http import FetchError

        with self.assertRaises(FetchError) as ctx:
            build_items(PAYLOAD, cfg(mode="weird"))
        self.assertEqual(ctx.exception.code, "CONFIG_INVALID")

if __name__ == "__main__":
    unittest.main()
