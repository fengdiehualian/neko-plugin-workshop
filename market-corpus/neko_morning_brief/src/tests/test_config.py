from __future__ import annotations

import unittest

import _loader
from nmb.config import parse_config

BASE = {
    "brief": {"times": ["08:00"], "mode": "hybrid"},
    "sources": [
        {
            "id": "weather",
            "type": "builtin.weather",
            "params": {"city": "杭州"},
        }
    ],
}

_loader.setup()

class ParseTests(unittest.TestCase):
    def test_minimal(self):
        parsed = parse_config(BASE)
        self.assertEqual(parsed.errors, [])
        self.assertEqual(len(parsed.sources), 1)
        self.assertEqual(parsed.sources[0].type, "builtin.weather")
        self.assertEqual(parsed.brief.times, ("08:00",))

    def test_defaults_without_brief(self):
        parsed = parse_config({"sources": BASE["sources"]})
        self.assertEqual(parsed.brief.times, ("08:00",))
        self.assertEqual(parsed.brief.mode, "hybrid")
        self.assertEqual(parsed.brief.catch_up_minutes, 120)

    def test_multiple_times_sorted(self):
        parsed = parse_config(
            {"brief": {"times": ["22:30", "8:00"]}, "sources": BASE["sources"]}
        )
        self.assertEqual(parsed.brief.times, ("08:00", "22:30"))

    def test_single_time_wins_for_form_editing(self):
        parsed = parse_config(
            {"brief": {"time": "09:15", "times": ["22:30"]}, "sources": BASE["sources"]}
        )
        self.assertEqual(parsed.brief.times, ("09:15",))

    def test_secret_expansion(self):
        parsed = parse_config(
            {
                "secrets": {"TOKEN": "abc"},
                "sources": [
                    {
                        "id": "nas",
                        "type": "http.json",
                        "url": "http://192.168.1.2/api",
                        "headers": {"Authorization": "Bearer ${secret:TOKEN}"},
                        "mode": "single",
                        "text": "{ok}",
                    }
                ],
            }
        )
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.sources[0].headers["Authorization"], "Bearer abc")

    def test_missing_secret_is_error(self):
        parsed = parse_config(
            {
                "sources": [
                    {
                        "id": "nas",
                        "type": "http.json",
                        "url": "http://x.test/a",
                        "headers": {"Authorization": "Bearer ${secret:NOPE}"},
                        "mode": "single",
                        "text": "{ok}",
                    }
                ]
            }
        )
        self.assertEqual(parsed.sources, [])
        self.assertTrue(any("missing secret" in e["message"] for e in parsed.errors))

    def test_bad_type_and_url(self):
        parsed = parse_config(
            {
                "sources": [
                    {"id": "x", "type": "crawler", "url": "https://x.test"},
                    {"id": "y", "type": "rss", "url": "ftp://x.test"},
                ]
            }
        )
        self.assertEqual(parsed.sources, [])
        messages = " ".join(entry["message"] for entry in parsed.errors)
        self.assertIn("unknown type", messages)
        self.assertIn("url must be http(s)", messages)

    def test_duplicate_ids(self):
        parsed = parse_config(
            {
                "sources": [
                    {"id": "dup", "type": "rss", "url": "https://a.test"},
                    {"id": "dup", "type": "rss", "url": "https://b.test"},
                ]
            }
        )
        self.assertEqual(len(parsed.sources), 1)
        self.assertTrue(any("duplicate" in e["message"] for e in parsed.errors))

    def test_list_mode_requires_items_path(self):
        parsed = parse_config(
            {
                "sources": [
                    {"id": "x", "type": "http.json", "url": "https://x.test", "mode": "list"}
                ]
            }
        )
        self.assertEqual(parsed.sources, [])

    def test_list_mode_auto_detected(self):
        parsed = parse_config(
            {
                "sources": [
                    {
                        "id": "x",
                        "type": "http.json",
                        "url": "https://x.test",
                        "items_path": "$.items",
                        "map": {"title": "$.t"},
                    }
                ]
            }
        )
        self.assertEqual(parsed.sources[0].mode, "list")

    def test_weight_clamped(self):
        parsed = parse_config(
            {
                "sources": [
                    {"id": "x", "type": "rss", "url": "https://x.test", "weight": 99}
                ]
            }
        )
        self.assertEqual(parsed.sources[0].weight, 10)

    def test_no_sources_is_reported_but_not_fatal(self):
        parsed = parse_config({"brief": {}})
        self.assertEqual(parsed.sources, [])
        self.assertTrue(parsed.errors)

    def test_unknown_timezone_falls_back(self):
        parsed = parse_config(
            {"brief": {"timezone": "Mars/Olympus"}, "sources": BASE["sources"]}
        )
        self.assertIsNone(parsed.tz)
        self.assertTrue(any("timezone" in e["message"] for e in parsed.errors))

    def test_known_timezone(self):
        parsed = parse_config(
            {"brief": {"timezone": "Asia/Shanghai"}, "sources": BASE["sources"]}
        )
        self.assertIsNotNone(parsed.tz)

    def test_bad_mode_falls_back(self):
        parsed = parse_config(
            {"brief": {"mode": "telepathy"}, "sources": BASE["sources"]}
        )
        self.assertEqual(parsed.brief.mode, "hybrid")

    def test_non_dict_config(self):
        parsed = parse_config(None)
        self.assertEqual(parsed.sources, [])
        self.assertTrue(parsed.errors)

    def test_dedupe_defaults_to_on(self):
        parsed = parse_config(
            {
                "sources": [
                    {"id": "weather", "type": "builtin.weather", "params": {"city": "杭州"}},
                    {"id": "rss", "type": "rss", "url": "https://x.test/feed"},
                ]
            }
        )
        self.assertEqual(parsed.errors, [])
        self.assertTrue(all(source.dedupe for source in parsed.sources))

    def test_dedupe_can_be_disabled(self):
        parsed = parse_config(
            {
                "sources": [
                    {"id": "rss", "type": "rss", "url": "https://x.test/feed", "dedupe": False}
                ]
            }
        )
        self.assertFalse(parsed.sources[0].dedupe)

    def test_url_only_source_defaults_to_auto(self):
        parsed = parse_config(
            {"sources": [{"url": "https://news.test/feed", "label": "新闻"}]}
        )
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.sources[0].type, "auto")
        self.assertTrue(parsed.sources[0].enabled)

    def test_missing_id_is_derived_from_host(self):
        parsed = parse_config({"sources": [{"url": "https://News.Test/feed"}]})
        self.assertEqual(parsed.errors, [])
        self.assertTrue(parsed.sources[0].id.startswith("news_test_"))

    def test_missing_label_uses_host(self):
        parsed = parse_config({"sources": [{"url": "https://news.test/feed"}]})
        self.assertEqual(parsed.sources[0].label, "news.test")

    def test_weather_city_at_top_level(self):
        parsed = parse_config(
            {"sources": [{"id": "weather", "type": "builtin.weather", "city": "上海"}]}
        )
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.sources[0].params["city"], "上海")

    def test_weather_city_legacy_params(self):
        parsed = parse_config(
            {
                "sources": [
                    {"id": "weather", "type": "builtin.weather", "params": {"city": "北京"}}
                ]
            }
        )
        self.assertEqual(parsed.sources[0].params["city"], "北京")

    def test_links_list(self):
        parsed = parse_config(
            {
                "brief": {
                    "links": [
                        "weather:上海",
                        "https://news.test/feed | 新闻",
                        "# https://disabled.test/",
                        "http://127.0.0.1:8080/api",
                    ]
                }
            }
        )
        self.assertEqual(parsed.errors, [])
        self.assertEqual(len(parsed.sources), 3)
        self.assertEqual(parsed.sources[0].type, "builtin.weather")
        self.assertEqual(parsed.sources[0].params["city"], "上海")
        self.assertEqual(parsed.sources[1].label, "新闻")
        self.assertEqual(parsed.sources[1].type, "auto")
        self.assertEqual(parsed.sources[2].url, "http://127.0.0.1:8080/api")

    def test_links_multiline_string(self):
        parsed = parse_config(
            {
                "brief": {
                    "links": "https://a.test/feed\n# https://b.test/\nweather:杭州"
                }
            }
        )
        self.assertEqual(parsed.errors, [])
        self.assertEqual([source.type for source in parsed.sources], ["auto", "builtin.weather"])

    def test_links_invalid_entry_reports(self):
        parsed = parse_config({"brief": {"links": ["not a link"]}})
        self.assertTrue(any("not a link" in error["message"] for error in parsed.errors))

    def test_instructions_skip_disabled_lines(self):
        parsed = parse_config(
            {
                "links": "\n".join(
                    [
                        "通过链接 https://a.test/ 获取新闻",
                        "# 通过链接 https://b.test/ 获取币价",
                        "",
                    ]
                )
            }
        )
        self.assertEqual(parsed.brief.instructions, "通过链接 https://a.test/ 获取新闻")

    def test_root_keys_are_tasks(self):
        parsed = parse_config(
            {
                "time": "08:00",
                "weather": "https://api.test/w 获取杭州天气",
                "hn": "# https://hnrss.org/frontpage 获取新闻",
                "crypto": "https://api.test/p cookie=abc 获取币价",
            }
        )
        self.assertEqual(parsed.errors, [])
        tasks = dict(parsed.brief.tasks)
        self.assertEqual(tasks["weather"], "https://api.test/w 获取杭州天气")
        self.assertNotIn("hn", tasks)
        self.assertIn("crypto", tasks)
        self.assertIn("weather：", parsed.brief.instructions)

    def test_reserved_keys_are_not_tasks(self):
        parsed = parse_config(
            {
                "time": "08:00",
                "proxy": "none",
                "spoken_char_limit": 200,
                "weather": "https://api.test/w",
            }
        )
        self.assertEqual([name for name, _ in parsed.brief.tasks], ["weather"])

    def test_links_and_sources_merge_without_duplicates(self):
        parsed = parse_config(
            {
                "brief": {"links": ["https://a.test/feed"]},
                "sources": [{"url": "https://a.test/feed"}, {"url": "https://b.test/feed"}],
            }
        )
        urls = [source.url for source in parsed.sources if source.url]
        self.assertEqual(urls, ["https://a.test/feed", "https://b.test/feed"])

if __name__ == "__main__":
    unittest.main()
