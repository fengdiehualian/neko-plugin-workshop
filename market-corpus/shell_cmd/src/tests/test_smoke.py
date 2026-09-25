"""
猫娘命令操作器 - 冒烟测试

校验插件结构、plugin.toml 入口、核心执行器逻辑。
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PLUGIN_ID = "shell_cmd"


class TestPluginStructure(unittest.TestCase):
    def test_plugin_toml_exists(self):
        self.assertTrue(
            os.path.isfile(os.path.join(ROOT, "plugin.toml")),
            "根目录 plugin.toml 缺失",
        )

    def test_entry_module_exists(self):
        entry = os.path.join(ROOT, "__init__.py")
        self.assertTrue(os.path.isfile(entry), f"入口模块缺失: {entry}")

    def test_plugin_toml_entry(self):
        import tomllib

        with open(os.path.join(ROOT, "plugin.toml"), "rb") as f:
            data = tomllib.load(f)
        entry = data["plugin"]["entry"]
        self.assertEqual(
            entry,
            f"plugins.{PLUGIN_ID}:ShellCmdPlugin",
            f"plugin.toml entry 应为 plugins.{PLUGIN_ID}:ShellCmdPlugin",
        )

    def test_core_executor(self):
        # 用 importlib 直接加载 shell_core.py，避免触发 __init__.py 对 plugin.sdk 的依赖
        import importlib.util

        core_path = os.path.join(ROOT, "shell_core.py")
        spec = importlib.util.spec_from_file_location("shell_core_test", core_path)
        core_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core_mod)
        ShellExecutor = core_mod.ShellExecutor

        ex = ShellExecutor(timeout=10, blacklist=["rm -rf /"])
        # 安全命令应通过
        r = ex.run("echo hello")
        self.assertTrue(r["ok"], r)
        self.assertIn("hello", r["stdout"])
        # 危险命令应被拦截
        bad = ex.run("rm -rf /")
        self.assertFalse(bad["ok"])
        self.assertIn("拦截", bad["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
