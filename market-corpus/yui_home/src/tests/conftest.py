import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def home_module(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("yui_home_market_test", root / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The runtime is Windows-only; portable tests use fixtures and never run an exe.
    monkeypatch.setattr(module, "_supports_runtime", lambda: True)
    return module


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-data"))
    root = tmp_path / "portable-home"
    root.mkdir()
    (root / "desktop.cjs").write_text("// test fixture", encoding="utf-8")
    (root / "preload.cjs").write_text("// test fixture", encoding="utf-8")
    exe = tmp_path / "N.E.K.O.exe"
    exe.write_bytes(b"fixture, never executed")
    record = tmp_path / "local-data/YuiHome/installation.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"root": str(root), "exe": str(exe)}), encoding="utf-8")
    (root / "runtime").mkdir()
    (root / "runtime/session.json").write_text(json.dumps({"token": "test-local-token"}), encoding="utf-8")
    return root, exe, record


@pytest.fixture
def local_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"body": body, "token": self.headers.get("x-nekocraft-token"), "path": self.path})
            if self.path == "/redirect":
                self.send_response(307)
                self.send_header("Location", "/unexpected")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"status":"test"}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
