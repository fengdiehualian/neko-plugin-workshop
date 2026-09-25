"""
猫娘小游戏屋 - 冒烟测试

校验插件结构、plugin.toml 入口、核心游戏逻辑（抽卡 / 保底 / 运势 / 骰子）。
"""

from __future__ import annotations

import importlib.util
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PLUGIN_ID = "mini_games"


def load_core():
    core_path = os.path.join(ROOT, "game_core.py")
    spec = importlib.util.spec_from_file_location("game_core_test", core_path)
    mod = importlib.util.module_from_spec(spec)
    # 必须在 exec_module 前注册到 sys.modules，否则 dataclass 在解析类型时会
    # 因 sys.modules 中找不到本模块而报 AttributeError（真实运行时由 import 系统自动注册，无此问题）
    sys.modules["game_core_test"] = mod
    spec.loader.exec_module(mod)
    return mod


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
            f"plugins.{PLUGIN_ID}:MiniGamesPlugin",
            f"plugin.toml entry 应为 plugins.{PLUGIN_ID}:MiniGamesPlugin",
        )

    def test_i18n_files_exist(self):
        for loc in ("zh-CN.json", "en.json"):
            p = os.path.join(ROOT, "i18n", loc)
            self.assertTrue(os.path.isfile(p), f"i18n 文件缺失: {p}")


class TestGameLogic(unittest.TestCase):
    def setUp(self):
        self.core = load_core()

    def test_pull_one_valid(self):
        eng = self.core.GachaEngine(rng=random.Random(1))
        sess = self.core.Session()
        r = eng.pull_one(sess)
        self.assertIn(r["rarity"], self.core.RARITIES)
        self.assertIn(r["item"], self.core.POOL[r["rarity"]])
        self.assertEqual(sess.total_pulls, 1)

    def test_ten_pull_count_and_floor(self):
        eng = self.core.GachaEngine(rng=random.Random(2))
        sess = self.core.Session()
        res = eng.pull_ten(sess)
        self.assertEqual(len(res["results"]), 10)
        # 十连保底：至少一枚 >= R
        self.assertTrue(
            any(
                self.core.RARITY_ORDER[x["rarity"]] >= self.core.RARITY_ORDER["R"]
                for x in res["results"]
            )
        )

    def test_pity_forces_ssr(self):
        eng = self.core.GachaEngine(pity_ssr=50, rng=random.Random(3))
        sess = self.core.Session()
        sess.pity = 49  # 下一抽应触发保底
        r = eng.pull_one(sess)
        self.assertIn(r["rarity"], ("SSR", "UR"))
        self.assertEqual(sess.pity, 0)  # 出 SSR/UR 后保底归零

    def test_fortune_deterministic(self):
        f1 = self.core.daily_fortune("alice", "2026-08-20")
        f2 = self.core.daily_fortune("alice", "2026-08-20")
        self.assertEqual(f1["level"], f2["level"])
        self.assertEqual(f1["lucky_number"], f2["lucky_number"])
        self.assertEqual(f1["lucky_color"], f2["lucky_color"])
        # 字段合法性
        self.assertIn(f1["level"], [lvl for lvl, _ in self.core._FORTUNE_LEVELS])
        self.assertIn(f1["lucky_color"], self.core._FORTUNE_COLORS)

    def test_dice(self):
        d = self.core.roll_dice(count=3, sides=6, seed=42)
        self.assertEqual(len(d["results"]), 3)
        self.assertEqual(d["sum"], sum(d["results"]))
        self.assertTrue(all(1 <= x <= 6 for x in d["results"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
