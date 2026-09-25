"""Smoke tests for the qqbot_bridge plugin.

These run under `python -m unittest` in CI (the plugin-market-verify workflow).
They avoid importing the plugin entry module (which requires the N.E.K.O SDK
`plugin.sdk.*` at import time). The pure-stdlib helpers (qq_ws, qq_core) are
loaded under a package namespace so their relative imports resolve.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "qqbot_bridge"


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(f"{PKG}.{name}", os.path.join(ROOT, filename))
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = PKG
    sys.modules[f"{PKG}.{name}"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# register the package so `from .qq_ws import ...` resolves
_pkg = types.ModuleType(PKG)
_pkg.__path__ = [ROOT]
_pkg.__package__ = PKG
sys.modules[PKG] = _pkg

QQ_WS = _load_module("qq_ws", "qq_ws.py")
QQ_CORE = _load_module("qq_core", "qq_core.py")


class TestStructure(unittest.TestCase):
    def test_plugin_toml_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "plugin.toml")))

    def test_entry_module_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "__init__.py")))

    def test_tsx_panel_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "ui", "panel.tsx")))

    def test_i18n_files_exist(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "i18n", "zh-CN.json")))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "i18n", "en.json")))

    def test_plugin_toml_contract(self):
        with open(os.path.join(ROOT, "plugin.toml"), "rb") as fh:
            cfg = tomllib.load(fh)
        self.assertEqual(cfg["plugin"]["id"], "qqbot_bridge")
        self.assertEqual(cfg["plugin"]["entry"], "plugins.qqbot_bridge:QqbotBridgePlugin")
        self.assertIn("version", cfg["plugin"])
        panels = cfg["plugin"].get("ui", {}).get("panel", [])
        if isinstance(panels, dict):
            panels = [panels]
        self.assertTrue(any(p.get("entry") == "ui/panel.tsx" for p in panels))


class TestPythonSyntax(unittest.TestCase):
    def test_py_compile_all(self):
        import py_compile

        for name in ("__init__.py", "qq_core.py", "qq_ws.py"):
            path = os.path.join(ROOT, name)
            self.assertTrue(os.path.isfile(path), f"missing {name}")
            py_compile.compile(path, doraise=True)


class TestWebSocketFrames(unittest.TestCase):
    def test_make_frame_text(self):
        frame = QQ_WS.make_frame(0x1, b"hello")
        self.assertEqual(frame[0], 0x81)
        self.assertTrue(frame[1] & 0x80)
        self.assertEqual(frame[1] & 0x7F, 5)
        fin, opcode, masked, length, off = QQ_WS.parse_frame_header(frame[:2])
        self.assertTrue(fin)
        self.assertEqual(opcode, 0x1)
        self.assertEqual(length, 5)
        self.assertEqual(off, 2)

    def test_make_frame_long(self):
        frame = QQ_WS.make_frame(0x1, b"x" * 200)
        fin, opcode, masked, length, off = QQ_WS.parse_frame_header(frame[:4])
        self.assertEqual(length, 200)
        self.assertEqual(opcode, 0x1)


class TestQqCorePure(unittest.TestCase):
    def test_strip_mention(self):
        self.assertEqual(QQ_CORE.strip_mention("<@!12345> 你好"), "你好")
        self.assertEqual(QQ_CORE.strip_mention("<@12345> 在吗"), "在吗")
        self.assertEqual(QQ_CORE.strip_mention("普通消息"), "普通消息")

    def test_normalize_group_at(self):
        ev = QQ_CORE.normalize_qq_event(
            "GROUP_AT_MESSAGE_CREATE",
            {"group_openid": "g_1", "id": "m_1", "content": "<@!bot> 你好", "author": {"user_openid": "u_9"}},
        )
        self.assertEqual(ev["channel"], "group")
        self.assertEqual(ev["target_id"], "g_1")
        self.assertEqual(ev["msg_id"], "m_1")
        self.assertEqual(ev["text"], "你好")
        self.assertEqual(ev["author"], "u_9")

    def test_normalize_c2c(self):
        ev = QQ_CORE.normalize_qq_event(
            "C2C_MESSAGE_CREATE",
            {"user_openid": "u_2", "id": "m_2", "content": "私聊内容"},
        )
        self.assertEqual(ev["channel"], "c2c")
        self.assertEqual(ev["target_id"], "u_2")
        self.assertEqual(ev["text"], "私聊内容")

    def test_intents_default(self):
        self.assertEqual(QQ_CORE.DEFAULT_INTENTS, (1 << 25) | (1 << 27))

    def test_qq_core_loaded(self):
        self.assertTrue(hasattr(QQ_CORE, "QqBotClient"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
