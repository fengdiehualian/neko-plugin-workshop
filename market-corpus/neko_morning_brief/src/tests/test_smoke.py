"""Contract test: import the real entry module and drive it with a fake host.

This is not a substitute for loading the plugin in N.E.K.O - it is a fast check
that decorators, config plumbing, entries, tools and one offline run fit
together. It uses a documented-API stub (``_sdk_stub``) so it works in CI
without the N.E.K.O source tree.

Run against the real SDK by placing the plugin in ``plugin/plugins/`` and using
``neko-plugin check``; that path is not exercised here.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
import unittest
from pathlib import Path

import _loader
import _sdk_stub

_sdk_stub.install()

ROOT = Path(__file__).resolve().parents[1]

_loader.setup()

def load_entry_module():
    name = "nmb.entrypoint"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "nmb"  # so `from .config import ...` resolves
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

BASE_CONFIG = {
    "time": "03:00",
    # No catch-up window: the tick test must not depend on when CI runs.
    "catch_up_minutes": 0,
    "feed": "https://example.test/feed 获取科技新闻",
}

class PluginSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        module = load_entry_module()
        self.module = module
        self.root = _loader.scratch_dir()
        for stale in (self.root / "data").glob("brief.db*"):
            try:
                stale.unlink()
            except OSError:
                pass
        self.ctx = _sdk_stub.FakeContext(BASE_CONFIG, self.root)
        self.plugin = module.NekoMorningBriefPlugin(self.ctx)
        result = await self.plugin.on_startup()
        self.assertIsInstance(result, _sdk_stub.Ok)

    async def asyncTearDown(self):
        await self.plugin.on_shutdown()
        _loader.cleanup_db(self.root / "data" / "brief.db")

    def test_registered_surfaces(self):
        self.assertIn("brief_now", self.plugin.entries)
        self.assertIn("brief_test", self.plugin.entries)
        self.assertIn("brief_doctor", self.plugin.entries)
        self.assertIn("list_sources", self.plugin.llm_tools)
        self.assertIn("fetch_url", self.plugin.llm_tools)
        self.assertIn("brief_tick", self.plugin.timers)

    async def test_doctor(self):
        result = await self.plugin.brief_doctor()
        assert isinstance(result, _sdk_stub.Ok)
        self.assertTrue(result.value["ready"])
        self.assertEqual(result.value["times"], ["03:00"])
        self.assertIn("获取科技新闻", result.value["instructions"])

    async def test_list_sources(self):
        catalog = await self.plugin.list_sources()
        self.assertEqual(catalog["brief_times"], ["03:00"])
        self.assertIn("https://example.test/feed", catalog["instructions"])

    async def test_fetch_url_without_url_is_error(self):
        result = await self.plugin.fetch_url(url="")
        self.assertTrue(result["is_error"])
        self.assertEqual(result["error"], "MISSING_URL")

    async def test_brief_test_without_url_is_error(self):
        result = await self.plugin.brief_test(url="")
        self.assertIsInstance(result, _sdk_stub.Err)

    async def test_tick_is_skipped_when_not_due(self):
        result = await self.plugin.brief_tick()
        assert isinstance(result, _sdk_stub.Ok)
        self.assertTrue(result.value.get("skipped"))

    async def test_run_hands_tasks_to_model(self):
        result = await self.plugin.brief_now()
        assert isinstance(result, _sdk_stub.Ok), result
        self.assertTrue(result.value["ok"])
        self.assertTrue(result.value["pushed"])
        self.assertEqual(result.value["tasks"], 1)
        self.assertEqual(len(self.ctx.cards), 0)
        self.assertEqual(len(self.ctx.pushed), 1)
        pushed = self.ctx.pushed[0]
        self.assertEqual(pushed["ai_behavior"], "respond")
        self.assertEqual(pushed["visibility"], [])
        text = pushed["parts"][0]["text"]
        self.assertIn("https://example.test/feed", text)
        self.assertIn("fetch_url", text)
        self.assertTrue(self.ctx.status)

    async def test_run_is_recorded_and_not_repeated(self):
        await self.plugin.brief_now()
        last = self.plugin._store.last_run()
        assert last is not None
        self.assertEqual(last["slot"], "manual")

    async def test_config_reload(self):
        self.ctx.config._data = dict(BASE_CONFIG, time="22:00")
        result = await self.plugin.on_config_change()
        assert isinstance(result, _sdk_stub.Ok)
        self.assertEqual(result.value["times"], ["22:00"])

class ManifestTests(unittest.TestCase):
    def test_plugin_toml(self):
        data = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
        plugin = data["plugin"]
        self.assertEqual(plugin["id"], "neko_morning_brief")
        self.assertEqual(
            plugin["entry"], "plugin.plugins.neko_morning_brief:NekoMorningBriefPlugin"
        )
        self.assertTrue(data["plugin_runtime"]["auto_start"])
        self.assertNotIn("brief", data)  # defaults live in config.example.toml

    def test_example_config_is_valid(self):
        from nmb.config import parse_config

        raw = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
        parsed = parse_config(raw)
        self.assertEqual(parsed.errors, [])
        self.assertEqual(len(parsed.brief.tasks), 1)
        name, text = parsed.brief.tasks[0]
        self.assertEqual(name, "weather")
        self.assertIn("获取杭州今天的天气", text)
        self.assertIn("weather：", parsed.brief.instructions)

    def test_disabled_source_may_lack_secret(self):
        from nmb.config import parse_config

        parsed = parse_config(
            {
                "sources": [
                    {
                        "id": "nas",
                        "type": "http.json",
                        "enabled": False,
                        "url": "http://192.168.1.2/x",
                        "headers": {"Authorization": "Bearer ${secret:NOT_SET}"},
                        "mode": "single",
                        "text": "{ok}",
                    }
                ]
            }
        )
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.sources[0].headers["Authorization"], "Bearer ")

    def test_schema_json_parses(self):
        import json

        data = json.loads((ROOT / "config.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(data["type"], "object")
        self.assertIn("time", data["properties"])

if __name__ == "__main__":
    unittest.main()
