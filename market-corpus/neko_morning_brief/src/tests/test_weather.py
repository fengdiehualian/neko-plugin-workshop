from __future__ import annotations

import unittest

import _loader
from nmb.models import SourceConfig
from nmb.sources.http import FetchError
from nmb.sources.weather import build_item, parse_geo

GEO = {
    "results": [
        {
            "name": "杭州",
            "latitude": 30.29365,
            "longitude": 120.16142,
            "country": "中国",
            "admin1": "浙江",
        }
    ]
}

FORECAST = {
    "daily": {
        "time": ["2026-10-21"],
        "weather_code": [61],
        "temperature_2m_max": [21.4],
        "temperature_2m_min": [15.2],
        "precipitation_probability_max": [80],
    }
}

_loader.setup()

def cfg(**kwargs) -> SourceConfig:
    base = dict(id="weather", type="builtin.weather", label="天气", params={"city": "杭州"})
    base.update(kwargs)
    return SourceConfig(**base)

class GeoTests(unittest.TestCase):
    def test_parse(self):
        geo = parse_geo(GEO)
        assert geo is not None
        self.assertAlmostEqual(geo["latitude"], 30.29365)
        self.assertEqual(geo["name"], "杭州 浙江")

    def test_empty(self):
        self.assertIsNone(parse_geo({"results": []}))
        self.assertIsNone(parse_geo({}))
        self.assertIsNone(parse_geo(None))

class BuildItemTests(unittest.TestCase):
    def test_builds_one_item(self):
        items = build_item(cfg(), place="杭州", payload=FORECAST)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertIn("杭州", item.title)
        self.assertIn("15~21℃", item.title)
        self.assertIn("小雨", item.title)
        self.assertIn("降水概率 80%", item.summary)
        self.assertIn("weather", item.tags)

    def test_missing_daily_raises(self):
        with self.assertRaises(FetchError):
            build_item(cfg(), place="杭州", payload={})

    def test_incomplete_series_raises(self):
        with self.assertRaises(FetchError):
            build_item(cfg(), place="杭州", payload={"daily": {"temperature_2m_max": [1]}})

    def test_unknown_code(self):
        payload = {
            "daily": {
                "weather_code": [123],
                "temperature_2m_max": [10],
                "temperature_2m_min": [5],
                "precipitation_probability_max": [None],
            }
        }
        items = build_item(cfg(), place="X", payload=payload)
        self.assertIn("123", items[0].title)
        self.assertNotIn("降水概率", items[0].summary)

if __name__ == "__main__":
    unittest.main()
