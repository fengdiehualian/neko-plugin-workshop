"""Optional recent-message reads must not abort a strategic review timer."""
import importlib
from types import SimpleNamespace


def test_status_read_denied_after_ownership_probe_is_optional(plugin_instance, monkeypatch, tmp_path):
    recent = importlib.import_module(plugin_instance.__class__.__module__ + ".neko_recent_context")
    monkeypatch.setattr(recent, "owned_runtime", lambda *_: {"identity": "confirmed"})

    def unavailable(_):
        raise PermissionError("temporary snapshot replacement contention")

    monkeypatch.setattr(recent, "observation", unavailable)
    assert recent.current_request(plugin_instance, None, "Role", SimpleNamespace(directory=tmp_path), 1) is None
