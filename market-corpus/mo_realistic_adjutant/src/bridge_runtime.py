from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlparse

from .bridge_client import BridgeClient, BridgeError

try:
    from .real_bridge.server import create_server
except ImportError:
    # The development tree keeps real_bridge at repository root. The portable
    # plugin bundles it as a child package so N.E.K.O remains self-contained.
    from real_bridge.server import create_server


class EmbeddedBridgeError(RuntimeError):
    pass


class EmbeddedBridgeRuntime:
    def __init__(self) -> None:
        self._server: Any | None = None
        self._thread: threading.Thread | None = None

    @property
    def owns_server(self) -> bool:
        return self._server is not None

    def ensure_started(self, base_url: str, timeout_seconds: float = 5.0) -> bool:
        client = BridgeClient(base_url, min(timeout_seconds, 1.0))
        try:
            client.health()
            return False
        except BridgeError:
            pass

        parsed = urlparse(base_url)
        if parsed.path not in {"", "/"}:
            raise EmbeddedBridgeError("automatic bridge URL must not contain a path")
        host = parsed.hostname or "127.0.0.1"
        if host == "localhost":
            host = "127.0.0.1"
        if host == "::1":
            raise EmbeddedBridgeError("automatic bridge currently requires IPv4 localhost")
        port = parsed.port or 80

        try:
            server = create_server(host, port)
        except OSError as exc:
            raise EmbeddedBridgeError(f"cannot start local bridge on {host}:{port}: {exc}") from exc

        thread = threading.Thread(
            target=server.serve_forever,
            name="mo-companion-local-bridge",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        thread.start()

        deadline = time.monotonic() + max(0.5, timeout_seconds)
        while time.monotonic() < deadline:
            try:
                client.health()
                return True
            except BridgeError:
                time.sleep(0.05)

        self.stop()
        raise EmbeddedBridgeError("local bridge did not become ready")

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        if thread is not None:
            thread.join(3.0)
        server.bridge_state.native.close()
        server.server_close()
