from __future__ import annotations

import json
import unittest

import _loader
from nmb.fetch import interpret_text, render_result
from nmb.sources.http import FetchError

_loader.setup()


WEATHER = {
    "latitude": 30.29,
    "longitude": 120.16,
    "daily": {
        "time": ["2026-10-23"],
        "weather_code": [61],
        "temperature_2m_max": [21.4],
        "temperature_2m_min": [15.2],
        "precipitation_probability_max": [80],
    },
}

NEWS = {
    "data": {
        "items": [
            {"title": "A", "summary": "ea", "url": "https://x.test/a"},
            {"title": "B", "summary": "eb", "url": "https://x.test/b"},
        ]
    }
}

AMAP = {
    "status": "1",
    "count": "1",
    "info": "OK",
    "infocode": "10000",
    "lives": [
        {
            "province": "湖北省",
            "city": "孝感市",
            "adcode": "420900",
            "weather": "晴",
            "temperature": "18",
            "humidity": "72",
            "reporttime": "2026-10-23 18:00:00",
        }
    ],
}


class WeatherTests(unittest.TestCase):
    def test_open_meteo_becomes_readable_weather(self):
        result = interpret_text(json.dumps(WEATHER), url="https://api.test/w", label="杭州")
        self.assertEqual(result["kind"], "weather")
        item = result["items"][0]
        self.assertIn("杭州", item["title"])
        self.assertIn("15~21℃", item["title"])
        self.assertIn("小雨", item["title"])
        self.assertIn("降水概率 80%", item["summary"])
        self.assertNotIn("weather_code", json.dumps(result))

    def test_without_label(self):
        result = interpret_text(json.dumps(WEATHER))
        self.assertEqual(result["kind"], "weather")
        self.assertFalse(result["items"][0]["title"].startswith(" "))


class JsonTests(unittest.TestCase):
    def test_list_payload_becomes_items(self):
        result = interpret_text(json.dumps(NEWS))
        self.assertEqual(result["kind"], "items")
        self.assertEqual([item["title"] for item in result["items"]], ["A", "B"])

    def test_scalar_payload_is_passed_as_raw_json(self):
        payload = {"bitcoin": {"cny": 512300}, "ok": True}
        result = interpret_text(json.dumps(payload))
        self.assertEqual(result["kind"], "json")
        self.assertIn("512300", result["text"])
        self.assertIn("bitcoin", result["text"])

    def test_amap_weather_keeps_lives_fields(self):
        result = interpret_text(json.dumps(AMAP), url="https://restapi.amap.com/v3/weather/weatherInfo")
        self.assertEqual(result["kind"], "json")
        text = render_result(result)
        self.assertIn("孝感市", text)
        self.assertIn("晴", text)
        self.assertIn("18", text)
        self.assertIn("temperature", text)

    def test_broken_json_raises(self):
        with self.assertRaises(FetchError):
            interpret_text("{broken")


class HtmlTests(unittest.TestCase):
    def test_html_title(self):
        result = interpret_text("<html><head><title>Hello</title></head></html>")
        self.assertEqual(result["kind"], "items")
        self.assertEqual(result["items"][0]["title"], "Hello")


class RenderTests(unittest.TestCase):
    def test_weather_is_one_readable_line(self):
        result = interpret_text(json.dumps(WEATHER), label="杭州")
        text = render_result(result)
        self.assertTrue(text.startswith("- 杭州"))
        self.assertIn("℃", text)
        self.assertIn("降水概率 80%", text)
        self.assertNotIn("daily", text)
        self.assertNotIn("{", text)

    def test_items_are_bullets(self):
        text = render_result(interpret_text(json.dumps(NEWS), url="https://x.test"))
        self.assertIn("- A：ea", text)
        self.assertIn("- B：eb", text)

    def test_json_fallback_is_raw_text(self):
        text = render_result(interpret_text(json.dumps({"price": 1})))
        self.assertIn("price", text)
        self.assertIn("1", text)

    def test_empty_payload_has_message(self):
        text = render_result({"kind": "items", "items": []})
        self.assertEqual(text, "没有解析到内容")

    def test_api_error_is_surfaced(self):
        with self.assertRaises(FetchError) as ctx:
            interpret_text(json.dumps({"error": True, "reason": "Latitude must be in range"}))
        self.assertEqual(ctx.exception.code, "API_ERROR")
        self.assertIn("Latitude", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
