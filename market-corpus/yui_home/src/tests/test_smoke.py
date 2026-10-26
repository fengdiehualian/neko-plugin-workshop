import asyncio
import json
from pathlib import Path

import pytest


def test_plugin_manifest_exists():
    text = (Path(__file__).resolve().parents[1] / "plugin.toml").read_text(encoding="utf-8")
    assert 'id = "yui_home"' in text
    assert 'entry = "plugin.plugins.yui_home:YuiHomePlugin"' in text


def test_missing_runtime_is_explained_without_launch(home_module, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    launched = []
    monkeypatch.setattr(home_module.subprocess, "Popen", lambda *args, **kwargs: launched.append(args))
    result = asyncio.run(home_module.YuiHomePlugin.startup(None))
    assert result.unwrap()["status"] == "setup_required"
    assert "完整项目包" in result.unwrap()["message"]
    assert not launched


def test_registered_paths_are_relocatable(home_module, installation):
    root, exe, _ = installation
    assert home_module._installation()["root"] == str(root)
    assert home_module._installation()["exe"] == str(exe)


def test_invalid_registration_is_rejected(home_module, installation):
    _, _, record = installation
    record.write_text(json.dumps({"root": "relative", "exe": "N.E.K.O.exe"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="绝对路径"):
        home_module._installation()


def test_moved_project_is_rejected(home_module, installation):
    root, _, _ = installation
    (root / "desktop.cjs").unlink()
    with pytest.raises(RuntimeError, match="已移动"):
        home_module._installation()


def test_startup_uses_registered_native_executable(home_module, installation, monkeypatch):
    _, exe, _ = installation
    launches = []
    monkeypatch.setattr(home_module.subprocess, "Popen", lambda *args, **kwargs: launches.append((args, kwargs)))
    result = asyncio.run(home_module.YuiHomePlugin.startup(None))
    assert result.unwrap()["status"] == "started"
    assert launches[0][0][0] == [str(exe), "--nekocraft-desktop"]


def test_command_authenticates_only_to_loopback(home_module, installation, local_server, monkeypatch):
    url, requests = local_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr(home_module, "_COMMAND_URL", url + "/command")
    result = home_module._request("status")
    assert result == {"ok": True, "status": "test"}
    assert requests == [{"body": {"action": "status"}, "token": "test-local-token", "path": "/command"}]
    assert "token" not in result


def test_redirect_never_reuses_session_token(home_module, installation, local_server, monkeypatch):
    url, requests = local_server
    monkeypatch.setattr(home_module, "_COMMAND_URL", url + "/redirect")
    with pytest.raises(RuntimeError, match="无法连接"):
        home_module._request("status")
    assert len(requests) == 1
    assert requests[0]["path"] == "/redirect"


def test_missing_session_is_explained(home_module, installation):
    root, _, _ = installation
    (root / "runtime/session.json").unlink()
    with pytest.raises(RuntimeError, match="尚未启动"):
        home_module._request("status")
