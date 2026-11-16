"""在 SDK 桩上把插件类真跑一遍：导入、启动、四个工具的返回形态。

这是一条**结构**测试：证明入口签名、装饰器参数、返回值形态不会当场炸。
真机行为（权限、UI、10 秒就绪）仍需在 N.E.K.O 里验。

返回值的判断按真 SDK 的 `Ok` / `Err` 形态写（`Ok.is_ok()`、`Err.error`），
这样本地桩与市场 CI 里的真 SDK 走同一套断言。
"""

import asyncio
import importlib.util
import os
import sys
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUB = os.path.join(ROOT, "tests", "stub")
for p in (STUB, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)


class _Config:
    def __init__(self, settings):
        self._settings = settings

    async def dump(self, *, timeout=5.0):
        return {"settings": dict(self._settings)}


class _Ctx:
    """同时满足两种运行环境：

    - 本地桩：直接读 `ctx.config.dump()`；
    - 市场 CI 的真 SDK：`PluginConfig.dump()` 会把 ctx 包成 SdkContext 再委托
      `ctx.get_own_config()`，所以这里也实现那个 getter，并返回 `{"config": ...}` 包体。
    """

    def __init__(self, settings=None):
        self._settings = dict(settings or {})
        self.config = _Config(self._settings)

    async def get_own_config(self, timeout=5.0):
        return {"config": {"settings": dict(self._settings)}}


def _load_plugin_module():
    spec = importlib.util.spec_from_file_location(
        "rbb_plugin_under_test", os.path.join(ROOT, "__init__.py"),
        submodule_search_locations=[ROOT],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _err_text(res):
    """Ok/Err 两种形态都能安全转成字符串（断言失败时用来打诊断信息）。"""
    return str(getattr(res, "error", res))


class TestPluginRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_plugin_module()

    async def _lifecycle(self, plugin, settings):
        plugin = plugin(_Ctx(settings))
        res = await plugin.on_startup()
        await asyncio.sleep(0.6)  # 让后台哈希探测跑完，避免 loop 关闭时被取消
        return plugin, res

    def test_startup_returns_ok(self):
        plugin, res = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        self.assertTrue(res.is_ok(), _err_text(res))
        self.assertEqual(res.value["status"], "ready")

    def test_settings_are_read(self):
        plugin, _ = asyncio.run(self._lifecycle(
            self.mod.ReuseBeforeBuildPlugin,
            {"enable_github_scan": False, "default_stars": 120, "default_limit": 3, "updated_days": 90},
        ))
        self.assertFalse(plugin._enable_scan)
        self.assertEqual(plugin._stars, 120)
        self.assertEqual(plugin._limit, 3)
        self.assertEqual(plugin._updated_days, 90)

    def test_settings_fall_back_to_manifest_defaults(self):
        """宿主读不到配置时不该启动失败，也不该读出脏值。"""
        plugin, res = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        self.assertTrue(res.is_ok(), _err_text(res))
        self.assertTrue(plugin._enable_scan)
        self.assertEqual(plugin._gh_binary, "gh")
        self.assertEqual(plugin._limit, 6)

    def test_rules_tool_returns_five_rules(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        res = asyncio.run(plugin.rbb_rules())
        self.assertTrue(res.is_ok(), _err_text(res))
        rules = res.value["rules"]
        numbered = [line for line in rules.splitlines() if line[:2] in ("1.", "2.", "3.", "4.", "5.")]
        self.assertEqual(len(numbered), 5)
        self.assertEqual(res.value["verdict_order"], ["Join", "Extend", "Fork", "Build"])
        for needle in ("Modifiability gate", "Branch first", "Untrusted repository content"):
            self.assertIn(needle, res.value["content"])
        flow = asyncio.run(plugin.rbb_rules(section="flow"))
        self.assertTrue(flow.is_ok(), _err_text(flow))
        self.assertEqual(len(flow.value["steps"]), 4)
        self.assertIn("Modifiability gate", flow.value["steps"][1]["content"])
        bad = asyncio.run(plugin.rbb_rules(section="nope"))
        self.assertFalse(bad.is_ok())
        self.assertIn("未知 section", _err_text(bad))
        self.assertFalse(hasattr(plugin, "_python"))

    def test_scan_respects_disabled_switch(self):
        plugin, _ = asyncio.run(self._lifecycle(
            self.mod.ReuseBeforeBuildPlugin, {"enable_github_scan": False}))
        res = asyncio.run(plugin.rbb_scan(keywords=["anything"]))
        self.assertFalse(res.is_ok())
        self.assertIn("enable_github_scan", _err_text(res))

    def test_scan_reports_unavailable_not_empty(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        with unittest.mock.patch("shutil.which", return_value=None):
            res = asyncio.run(plugin.rbb_scan(keywords=["video clip"]))
        self.assertFalse(res.is_ok())
        msg = _err_text(res)
        self.assertIn("code=2", msg)
        self.assertIn("不等于没有同类项目", msg)

    def test_scan_explicit_zero_stars_and_timeout_are_forwarded(self):
        """stars=0 是合法值（不限），不能被 `or` 当成没传而回默认 50。"""
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        seen = {}

        def fake_candidates(kws, stars, limit, since, gh_binary, timeout, updated_days):
            seen.update(stars=stars, limit=limit, timeout=timeout, updated_days=updated_days)
            return {
                "code": 0, "ok": True, "keywords": kws, "rows": [], "table": "",
                "note": "", "stars": stars, "updated_since": since, "limit": limit,
                "failed": [], "skipped": [], "reason": "", "hint": "",
            }

        with unittest.mock.patch.object(
            self.mod.rbb_core, "search_candidates", side_effect=fake_candidates
        ):
            res = asyncio.run(plugin.rbb_scan(
                keywords=["video clip"], stars=0, limit=0, timeout=7
            ))
        self.assertTrue(res.is_ok(), _err_text(res))
        self.assertEqual(seen["stars"], 0)
        self.assertEqual(seen["limit"], 1)
        self.assertEqual(seen["timeout"], 7)
        self.assertEqual(seen["updated_days"], 365)

    def test_invalid_gh_binary_falls_back_with_warning(self):
        """配置里写任意程序名不能再变成执行入口：回退 gh，并在 status 里给出 warning。"""
        plugin, _ = asyncio.run(self._lifecycle(
            self.mod.ReuseBeforeBuildPlugin, {"gh_binary": "C:/tmp/evil.exe"}))
        res = asyncio.run(plugin.rbb_status())
        self.assertTrue(res.is_ok(), _err_text(res))
        self.assertEqual(res.value["gh"]["binary"], "gh")
        self.assertIn("gh_binary", res.value["settings"]["warning"])

    def test_check_tool_flags_missing_parts(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        res = asyncio.run(plugin.rbb_check(deliverable="## 找到了什么\n没有别的段落"))
        self.assertTrue(res.is_ok(), _err_text(res))
        self.assertFalse(res.value["ok"])
        self.assertIn("section:why", res.value["missing"])

    def test_verify_tool_passes_bundled_copy(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        res = asyncio.run(plugin.rbb_verify(path=os.path.join(
            ROOT, "skills", "reuse-before-build", "SKILL.md")))
        self.assertTrue(res.is_ok(), _err_text(res))
        self.assertEqual(res.value["exit_code"], 0)
        self.assertIn("verbatim", res.value["output"])

    def test_status_tool_has_hash(self):
        plugin, _ = asyncio.run(self._lifecycle(self.mod.ReuseBeforeBuildPlugin, {}))
        res = asyncio.run(plugin.rbb_status())
        self.assertTrue(res.is_ok(), _err_text(res))
        self.assertEqual(len(res.value["rule_sha256"]), 64)
        self.assertEqual(res.value["bundled_skill_verify"]["exit_code"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
