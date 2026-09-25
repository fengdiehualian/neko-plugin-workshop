"""Smoke test for the BiliLearn bridge N.E.K.O plugin.

Tests plugin.toml schema without importing the plugin runtime, so it stays
green under both `unittest` and `pytest` even before the N.E.K.O SDK is present.
"""

import tomllib
import unittest
from pathlib import Path

PLUGIN_TOML = Path(__file__).resolve().parent.parent / "plugin.toml"
# 运行时按 ``plugins.<id>`` 命名空间包导入（见 N.E.K.O host.py / entry_points.py），
# 入口类由插件包 ``__init__.py`` re-export，因此入口模块文件是 ``__init__.py``。
ENTRY_MODULE = Path(__file__).resolve().parent.parent / "__init__.py"


class TestPluginSmoke(unittest.TestCase):
    def test_plugin_toml_present(self):
        self.assertTrue(PLUGIN_TOML.is_file(), "plugin.toml must exist at the repo root")

    def test_plugin_id_and_entry(self):
        with PLUGIN_TOML.open("rb") as fh:
            data = tomllib.load(fh)
        plugin = data["plugin"]
        self.assertEqual(plugin["id"], "bililearn_bridge")
        self.assertEqual(plugin["entry"], "plugin.plugins.bililearn_bridge:BiliLearnBridgePlugin")
        self.assertEqual(plugin["type"], "plugin")

    def test_entry_module_exists(self):
        self.assertTrue(
            ENTRY_MODULE.is_file(),
            "bililearn_bridge.py (entry module) must exist at the repo root",
        )


if __name__ == "__main__":
    unittest.main()
