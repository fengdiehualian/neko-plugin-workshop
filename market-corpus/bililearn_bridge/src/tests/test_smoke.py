"""Tests for the BiliLearn bridge N.E.K.O plugin.

Runs green under both `unittest` and `pytest`, with or without the N.E.K.O SDK
present: SDK-dependent checks skip instead of failing, and every other check
is offline (the auth handshake runs against a local stub panel).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGIN_TOML = PLUGIN_DIR / "plugin.toml"
ENTRY_MODULE = PLUGIN_DIR / "__init__.py"


def _load(name: str, filename: str):
    """Load a plugin submodule by path, without importing the package root."""
    spec = importlib.util.spec_from_file_location(name, PLUGIN_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestPluginManifest(unittest.TestCase):
    def test_plugin_toml_present(self):
        self.assertTrue(PLUGIN_TOML.is_file(), "plugin.toml must exist at the repo root")

    def test_plugin_identity(self):
        import tomllib

        with PLUGIN_TOML.open("rb") as fh:
            data = tomllib.load(fh)
        plugin = data["plugin"]
        self.assertEqual(plugin["id"], "bililearn_bridge")
        self.assertEqual(plugin["entry"], "plugin.plugins.bililearn_bridge:BiliLearnBridgePlugin")
        self.assertEqual(plugin["type"], "plugin")
        self.assertRegex(plugin["version"], r"^\d+\.\d+\.\d+")
        self.assertIn("sdk", plugin)

    def test_entry_module_defines_class(self):
        self.assertTrue(ENTRY_MODULE.is_file(), "__init__.py must exist at the repo root")
        tree = ast.parse(ENTRY_MODULE.read_text(encoding="utf-8"))
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        self.assertIn("BiliLearnBridgePlugin", classes)
        decorators = {
            node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
            for node in classes["BiliLearnBridgePlugin"].decorator_list
        }
        self.assertIn("neko_plugin", decorators)


class TestEntryBudget(unittest.TestCase):
    """Keep the host-facing entry list small (full coverage lives in api_call)."""

    MAX_ENTRIES = 50

    def _entry_ids(self):
        ids = []
        for filename in ("__init__.py", "director.py"):
            tree = ast.parse((PLUGIN_DIR / filename).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    func = decorator.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name != "plugin_entry":
                        continue
                    entry_id = node.name
                    for keyword in decorator.keywords:
                        if keyword.arg == "id" and isinstance(keyword.value, ast.Constant):
                            entry_id = str(keyword.value.value)
                    ids.append(entry_id)
        return ids

    def test_entry_count_within_budget(self):
        ids = self._entry_ids()
        self.assertGreater(len(ids), 0)
        self.assertLessEqual(
            len(ids),
            self.MAX_ENTRIES,
            f"entry count {len(ids)} exceeds budget {self.MAX_ENTRIES}: {sorted(ids)}",
        )

    def test_entry_ids_unique(self):
        ids = self._entry_ids()
        self.assertEqual(len(ids), len(set(ids)), f"duplicate entry ids: {sorted(ids)}")

    def test_generic_passthrough_still_reaches_everything(self):
        ids = set(self._entry_ids())
        for required in ("api_call", "api_batch", "api_groups", "api_search", "api_describe"):
            self.assertIn(required, ids, "generic passthrough entries must stay available")


class TestCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = _load("bridge_catalog_under_test", "catalog.py")

    def test_route_counts(self):
        # BiliLearn 3.1.6 exposes 308 route handlers across 90 groups. A few
        # paths are registered by several handlers (e.g. PUT + DELETE on
        # /api/diary/entry/<id>), so ROUTES holds the deduplicated path set.
        registered = sum(len(items) for items in self.catalog.GROUPS.values())
        self.assertEqual(registered, 308)
        self.assertEqual(len(self.catalog.GROUPS), 90)
        self.assertLessEqual(len(self.catalog.ROUTES), registered)
        for items in self.catalog.GROUPS.values():
            for item in items:
                self.assertIn(item["p"], self.catalog.ROUTES)

    def test_versions_and_key_routes(self):
        self.assertEqual(self.catalog.BILILEARN_VERSION, "3.1.6")
        for path in ("/api/bot/start", "/api/info", "/api/kb/tutor-chat", "/api/agent/start"):
            self.assertIn(path, self.catalog.ROUTES)

    def test_find_is_case_insensitive(self):
        hits = self.catalog.find("BOT")
        paths = {item["p"] for item in hits}
        self.assertIn("/api/bot/start", paths)

    def test_routes_in_unknown_group_is_empty(self):
        self.assertEqual(self.catalog.routes_in("nope"), [])

    def test_parameter_documentation(self):
        bot = {item["p"]: item for item in self.catalog.routes_in("bot")}
        self.assertIn("mode", bot["/api/bot/start"]["q"])


class _StubPanel(BaseHTTPRequestHandler):
    """Minimal stand-in for the BiliLearn panel's auth-gated surface."""

    users = {"yaya": "yaya"}
    seen_requests: list[tuple[str, str]] = []

    def log_message(self, *args):  # silence test output
        pass

    def _send(self, status: int, payload: dict, *, cookie: str = ""):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        return "bililearn_session=ok" in (self.headers.get("Cookie") or "")

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}

    def do_GET(self):  # noqa: N802 - http.server contract
        path = urlparse(self.path).path
        type(self).seen_requests.append(("GET", path))
        if path == "/api/health":
            self._send(200, {"ok": True, "service": "bilibili-learning-bot-web"})
        elif path == "/api/auth/status":
            self._send(200, {"authenticated": self._authed()})
        elif path == "/api/info":
            if self._authed():
                self._send(200, {"ok": True, "version": "3.1.6", "bot_running": True})
            else:
                self._send(401, {"ok": False, "message": "登录状态已失效，请重新登录", "auth_required": True})
        else:
            self._send(404, {"ok": False, "message": "not found"})

    def do_POST(self):  # noqa: N802 - http.server contract
        path = urlparse(self.path).path
        type(self).seen_requests.append(("POST", path))
        body = self._read_body()
        if path == "/api/disclaimer/confirm":
            self._send(200, {"ok": True})
        elif path == "/api/auth/login":
            user, password = body.get("username"), body.get("password")
            if type(self).users.get(user) == password:
                self._send(200, {"ok": True, "message": "登录成功"}, cookie="bililearn_session=ok; Path=/")
            else:
                self._send(200, {"ok": False, "message": "用户名或密码错误"})
        elif path == "/api/auth/setup":
            type(self).users[body.get("username", "")] = body.get("password", "")
            self._send(200, {"ok": True, "message": "设置成功"}, cookie="bililearn_session=ok; Path=/")
        else:
            self._send(404, {"ok": False, "message": "not found"})


class TestClientHandshake(unittest.TestCase):
    """End-to-end auth: disclaimer -> login -> gated call, against a stub panel."""

    @classmethod
    def setUpClass(cls):
        cls.client_mod = _load("bridge_client_under_test", "client.py")
        _StubPanel.seen_requests = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _StubPanel)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _client(self, **kwargs):
        return self.client_mod.BiliLearnClient(port=self.port, timeout=5.0, **kwargs)

    def test_health_identifies_panel(self):
        import asyncio

        client = self._client()
        self.assertTrue(asyncio.run(client.health()))

    def test_seeded_default_login_then_gated_call(self):
        import asyncio

        client = self._client()
        auth = asyncio.run(client.ensure_auth())
        self.assertTrue(auth["authenticated"], auth)
        self.assertEqual(auth["mode"], "seeded-default")

        res = asyncio.run(client.call("GET", "/api/info"))
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["data"]["version"], "3.1.6")

        # The handshake really walked disclaimer + login, in that order.
        seen = _StubPanel.seen_requests
        self.assertIn(("POST", "/api/disclaimer/confirm"), seen)
        self.assertIn(("POST", "/api/auth/login"), seen)

    def test_configured_credentials_win_and_are_used(self):
        import asyncio

        client = self._client(username="yaya", password="yaya")
        auth = asyncio.run(client.ensure_auth())
        self.assertTrue(auth["authenticated"], auth)
        self.assertEqual(auth["mode"], "config")

    def test_bad_credentials_fail_without_setup_overwrite(self):
        import asyncio

        client = self._client(username="yaya", password="wrong", auto_setup=False)
        auth = asyncio.run(client.ensure_auth())
        self.assertFalse(auth["authenticated"])
        self.assertEqual(auth["mode"], "failed")

    def test_expired_session_is_recovered_once(self):
        import asyncio

        client = self._client()
        self.assertTrue(asyncio.run(client.ensure_auth())["authenticated"])
        # Drop the session cookie behind the client's back, then call a gated route.
        client._jar.clear()  # noqa: SLF001 - exercising the 401-retry path
        res = asyncio.run(client.call("GET", "/api/info"))
        self.assertTrue(res["ok"], res)

    def test_rejects_unsafe_paths(self):
        import asyncio

        client = self._client()
        with self.assertRaises(self.client_mod.BiliLearnClientError):
            asyncio.run(client.request("GET", "http://evil.example/api/x"))
        with self.assertRaises(self.client_mod.BiliLearnClientError):
            asyncio.run(client.request("GET", "//evil.example/api/x"))

    def test_rejects_unsupported_methods(self):
        import asyncio

        client = self._client()
        with self.assertRaises(self.client_mod.BiliLearnClientError):
            asyncio.run(client.request("TRACE", "/api/info"))


class TestExeLookup(unittest.TestCase):
    def test_scan_finds_nested_exe(self):
        # Exercised through the plugin class when the SDK is importable.
        try:
            sys.path.insert(0, str(PLUGIN_DIR.parent))
            spec = importlib.util.spec_from_file_location("bridge_pkg_under_test", ENTRY_MODULE)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules["bridge_pkg_under_test"] = module
            spec.loader.exec_module(module)
        except ImportError as exc:
            self.skipTest(f"N.E.K.O SDK unavailable: {exc}")

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "BiliLearn Web"
            root.mkdir()
            exe = root / "BiliLearn Web.exe"
            exe.write_bytes(b"stub")
            found = module.BiliLearnBridgePlugin._find_exe_under(Path(tmp), 3)
            self.assertIsNotNone(found)
            self.assertEqual(Path(found).name, "BiliLearn Web.exe")


class TestInstaller(unittest.TestCase):
    """Bundled / cached installer discovery and safe extraction."""

    @classmethod
    def setUpClass(cls):
        cls.installer = _load("bridge_installer_under_test", "installer.py")

    def test_finds_bundled_installer(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            assets = plugin_dir / "assets"
            assets.mkdir()
            (assets / "BiliLearn-Web-3.1.6-Windows-x64.zip").write_bytes(b"stub")
            found = self.installer.find_bundled_installer(plugin_dir)
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "BiliLearn-Web-3.1.6-Windows-x64.zip")

    def test_no_bundled_installer_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self.installer.find_bundled_installer(Path(tmp)))

    def test_extracts_and_is_idempotent(self):
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            assets = plugin_dir / "assets"
            assets.mkdir()
            zip_path = assets / "BiliLearn-Web-3.1.6-Windows-x64.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("BiliLearn Web/BiliLearn Web.exe", "stub-exe")
                archive.writestr("BiliLearn Web/_internal/keep.txt", "data")

            state_dir = Path(tmp) / "state"
            exe = self.installer.ensure_runtime_exe(zip_path, state_dir)
            self.assertIsNotNone(exe)
            self.assertEqual(Path(exe).name, "BiliLearn Web.exe")
            self.assertTrue((state_dir / "runtime" / ".installed-version").is_file())
            # Second call must reuse the extraction, not redo it.
            again = self.installer.ensure_runtime_exe(zip_path, state_dir)
            self.assertEqual(exe, again)
            # And find_runtime_exe sees it without touching the zip.
            self.assertEqual(Path(exe), self.installer.find_runtime_exe(state_dir))

    def test_rejects_zip_slip_entries(self):
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "BiliLearn-Web-3.1.6-Windows-x64.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("../evil.txt", "escaped")
            state_dir = Path(tmp) / "state"
            exe = self.installer.ensure_runtime_exe(zip_path, state_dir)
            self.assertIsNone(exe, "zip-slip entries must abort extraction")
            self.assertFalse((Path(tmp) / "evil.txt").exists())

    def test_cleanup_runtime(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            zip_path = state_dir.parent / "BiliLearn-Web-3.1.6-Windows-x64.zip"
            import zipfile as _zip

            with _zip.ZipFile(zip_path, "w") as archive:
                archive.writestr("BiliLearn Web/BiliLearn Web.exe", "stub-exe")
            self.assertIsNotNone(self.installer.ensure_runtime_exe(zip_path, state_dir))
            self.assertTrue((state_dir / "runtime").is_dir())
            self.installer.cleanup_runtime(state_dir)
            self.assertFalse((state_dir / "runtime").exists())

    def test_version_from_name(self):
        self.assertEqual(
            self.installer._version_from_name("BiliLearn-Web-3.1.6-Windows-x64.zip"),  # noqa: SLF001
            "3.1.6",
        )

    def test_reassembles_split_parts_and_verifies(self):
        import hashlib
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            assets = plugin_dir / "assets"
            assets.mkdir(parents=True)
            name = "BiliLearn-Web-3.1.6-Windows-x64.zip"
            payload = b"stub-zip-bytes" * 4096
            (assets / name).write_bytes(payload)
            # Split by hand into 3 uneven parts, mimicking tools/split_installer.py.
            parts = [payload[:20000], payload[20000:50000], payload[50000:]]
            names = []
            for index, blob in enumerate(parts, start=1):
                part_name = f"{name}.part{index:03d}"
                (assets / part_name).write_bytes(blob)
                names.append(part_name)
            (assets / name).unlink()

            (assets / "installer-manifest.json").write_text(json.dumps({
                "name": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "part_bytes": 20000,
                "parts": names,
            }), encoding="utf-8")

            state_dir = Path(tmp) / "state"
            rebuilt = self.installer.reassemble_bundled(plugin_dir, state_dir)
            self.assertIsNotNone(rebuilt)
            self.assertEqual(rebuilt.name, name)
            self.assertEqual(rebuilt.read_bytes(), payload)

            # Second call must hit the cache, not re-read the parts.
            cached = self.installer.reassemble_bundled(plugin_dir, state_dir)
            self.assertEqual(rebuilt, cached)

            # A tampered part must be rejected.
            (assets / names[0]).write_bytes(b"corrupted")
            other_state = Path(tmp) / "state2"
            bad = self.installer.reassemble_bundled(plugin_dir, other_state)
            self.assertIsNone(bad, "hash mismatch must abort reassembly")

    def test_reassemble_without_manifest_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            (plugin_dir / "assets").mkdir(parents=True)
            self.assertIsNone(self.installer.reassemble_bundled(plugin_dir, Path(tmp) / "s"))

    def test_mirror_urls_start_with_fastest(self):
        urls = self.installer._mirror_urls("https://github.com/o/r/releases/download/v1/f.zip")  # noqa: SLF001
        names = [name for name, _ in urls]
        # Fastest measured mirror first; official stays in the list as fallback.
        self.assertEqual(names[0], "gh-proxy.com")
        self.assertIn("official", names)
        # The official entry must be the raw URL, unwrapped.
        for name, candidate in urls:
            if name == "official":
                self.assertEqual(candidate, "https://github.com/o/r/releases/download/v1/f.zip")
            else:
                self.assertIn("https://github.com/o/r/releases/download/v1/f.zip", candidate, name)
                self.assertTrue(candidate.startswith("https://"), name)

    def test_mirror_fallback_uses_next_source(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            target = state / "payload.bin"
            seen: list[str] = []

            def fake_download(url: str, dest: Path, timeout: float, **kwargs) -> None:
                # Only the raw official URL (no mirror prefix) succeeds.
                if url.startswith("https://github.com/"):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b"ok")
                    return
                raise OSError("connection reset")

            original = self.installer._download_one  # noqa: SLF001 - tooling check
            self.installer._download_one = fake_download  # type: ignore[assignment]
            try:
                ok, note = self.installer._download_via_mirrors(  # noqa: SLF001
                    "https://github.com/o/r/releases/download/v1/f.zip",
                    target,
                    5.0,
                    on_source=seen.append,
                )
            finally:
                self.installer._download_one = original  # type: ignore[assignment]

            self.assertTrue(ok)
            self.assertEqual(note, "official")
            # Sources are tried in order, each retried before moving on.
            self.assertEqual(seen[0], "gh-proxy.com")
            self.assertEqual(seen[-1], "official")
            self.assertLess(seen.index("ghfast.top"), seen.index("official"))
            self.assertEqual(target.read_bytes(), b"ok")
            # No half-finished .part may survive a failed source.
            self.assertFalse(target.with_suffix(".bin.part").exists())

    def test_download_gives_up_after_all_sources(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            original = self.installer._download_one  # noqa: SLF001 - tooling check

            def always_fail(url: str, dest: Path, timeout: float, **kwargs) -> None:
                raise OSError("timed out")

            self.installer._download_one = always_fail  # type: ignore[assignment]
            try:
                ok, note = self.installer._download_via_mirrors(  # noqa: SLF001
                    "https://github.com/o/r/releases/download/v1/f.zip",
                    state / "payload.bin",
                    5.0,
                )
            finally:
                self.installer._download_one = original  # type: ignore[assignment]

            self.assertFalse(ok)
            for name, _ in self.installer._DOWNLOAD_SOURCES:  # noqa: SLF001
                self.assertIn(name, note)


if __name__ == "__main__":
    unittest.main()