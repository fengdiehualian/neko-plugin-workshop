"""Exercise the real host's dependency graph without starting its server."""

import socket
import subprocess
from pathlib import Path


def test_panel_dependency_graph_is_supported_by_host(monkeypatch):
    def forbidden_io(*args, **kwargs):
        raise AssertionError("Hosted UI resolution must remain offline.")

    monkeypatch.setattr(socket, "create_connection", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect", forbidden_io)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_io)
    monkeypatch.setattr(subprocess, "Popen", forbidden_io)

    # conftest selects the actual NEKO_ROOT checkout, never a copied resolver.
    from plugin.server.application.plugins.ui_query_service import (
        _load_hosted_tsx_dependencies_sync,
    )

    root = Path(__file__).resolve().parents[1]
    dependencies = _load_hosted_tsx_dependencies_sync(
        {"config_path": str(root / "plugin.toml")}, root / "ui/panel.tsx"
    )
    assert dependencies == [
        {
            "path": "ui/installation-guidance.js",
            "source": (root / "ui/installation-guidance.js").read_text(encoding="utf-8"),
        },
        {
            "path": "ui/connection-presets.js",
            "source": (root / "ui/connection-presets.js").read_text(encoding="utf-8"),
        },
        {
            "path": "ui/panel-refresh.js",
            "source": (root / "ui/panel-refresh.js").read_text(encoding="utf-8"),
        },
    ]
