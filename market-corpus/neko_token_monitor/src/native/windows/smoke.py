"""Exercise WebView2 initialization on Windows without NEKO, keys or network APIs."""

import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<!doctype html><title>Cat Food smoke</title><p>Cat Food ready</p>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    with tempfile.TemporaryDirectory() as directory:
        stop = Path(directory) / "stop"
        subprocess.run(
            [
                str(Path("out/CatFood.exe").resolve()),
                str(os.getpid()),
                f"http://127.0.0.1:{server.server_port}",
                str(stop),
                "--smoke",
            ],
            timeout=40,
            check=True,
        )
        assert Path(str(stop) + ".ready").read_text() == "ready"
finally:
    server.shutdown()
    server.server_close()
print("Windows WebView2 native window initialized and loaded local HTML successfully")
