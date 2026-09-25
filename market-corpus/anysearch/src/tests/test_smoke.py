"""AnySearch 插件冒烟测试：结构契约 + 搜索核心解析（不导入 N.E.K.O SDK）。"""

import importlib
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestStructure(unittest.TestCase):
    def test_plugin_toml_present(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "plugin.toml")))

    def test_entry_declared(self):
        with open(os.path.join(ROOT, "plugin.toml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('id = "anysearch"', text)
        self.assertIn('entry = "plugins.anysearch:AnySearchPlugin"', text)
        self.assertIn("[[plugin.ui.panel]]", text)
        self.assertIn('entry = "ui/panel.tsx"', text)
        self.assertIn('context = "main"', text)

    def test_sources_present(self):
        for f in ("anysearch_core.py", "ui/panel.tsx", "__init__.py", "i18n/zh-CN.json"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, f)), f"{f} 缺失")

    def test_vscode_present(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".vscode", "settings.json")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".vscode", "tasks.json")))


class TestAnysearchCore(unittest.TestCase):
    def _load(self):
        spec = importlib.util.spec_from_file_location(
            "anysearch_core_live", os.path.join(ROOT, "anysearch_core.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["anysearch_core_live"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_parse_payload(self):
        mod = self._load()
        raw = {
            "results": [
                {
                    "title": "量子计算",
                    "url": "https://example.com/q",
                    "description": "简介",
                    "content": "正文",
                    "source": "web",
                    "score": 0.9,
                    "quality_score": 0.8,
                    "published_at": "2024-01-01T00:00:00Z",
                },
                {"title": "第二条", "url": "https://example.com/2"},
            ],
            "metadata": {"total_results": 2, "search_time_ms": 300, "request_id": "req_x"},
        }
        resp = mod.parse_payload(raw)
        self.assertEqual(len(resp.results), 2)
        self.assertEqual(resp.results[0].title, "量子计算")
        self.assertEqual(resp.results[1].url, "https://example.com/2")
        self.assertEqual(resp.total_results, 2)
        self.assertEqual(resp.search_time_ms, 300)
        self.assertEqual(resp.request_id, "req_x")
        # to_list 输出字段对齐
        card = resp.to_list()[0]
        self.assertEqual(card["title"], "量子计算")
        self.assertEqual(card["quality_score"], 0.8)

    def test_format_results(self):
        mod = self._load()
        resp = mod.parse_payload(
            {
                "results": [{"title": "T", "url": "https://x", "description": "D"}],
                "metadata": {"total_results": 1, "search_time_ms": 10},
            }
        )
        text = mod.format_results(resp, "测试")
        self.assertIn("测试", text)
        self.assertIn("T", text)
        self.assertIn("https://x", text)

    def test_format_empty(self):
        mod = self._load()
        resp = mod.parse_payload({"results": [], "metadata": {"total_results": 0}})
        self.assertIn("没有找到", mod.format_results(resp, "空"))

    def test_empty_query_raises(self):
        mod = self._load()
        client = mod.AnySearchClient()
        with self.assertRaises(mod.AnySearchError):
            client.search("")

    def test_client_defaults(self):
        mod = self._load()
        client = mod.AnySearchClient(api_key="k", default_zone="intl", default_max_results=3)
        self.assertEqual(client.default_zone, "intl")
        self.assertEqual(client.default_max_results, 3)
        self.assertEqual(client._headers().get("Authorization"), "Bearer k")
        # 匿名模式无 Authorization
        anon = mod.AnySearchClient()
        self.assertNotIn("Authorization", anon._headers())


if __name__ == "__main__":
    unittest.main(verbosity=2)
