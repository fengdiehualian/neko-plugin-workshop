import asyncio
import threading
from types import SimpleNamespace

import pytest


def test_non_windows_never_starts_executable(home_module, monkeypatch):
    monkeypatch.setattr(home_module, "_supports_runtime", lambda: False)
    monkeypatch.setattr(home_module.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not launch"))
    result = asyncio.run(home_module.YuiHomePlugin.startup(None))
    assert result.unwrap()["status"] == "unsupported_platform"
    assert "Windows" in result.unwrap()["message"]


def test_startup_launch_failure_returns_sdk_error(home_module, installation, monkeypatch):
    def fail(*_a, **_k):
        raise PermissionError("fixture: OS rejected executable")

    monkeypatch.setattr(home_module.subprocess, "Popen", fail)
    result = asyncio.run(home_module.YuiHomePlugin.startup(None))
    assert result.is_err()
    assert result.err().code == "home_launch_failed"


def test_launch_runs_outside_event_loop(home_module, installation, monkeypatch):
    main_thread = threading.get_ident()
    threads = []
    monkeypatch.setattr(home_module.subprocess, "Popen", lambda *_a, **_k: threads.append(threading.get_ident()))
    result = asyncio.run(home_module.YuiHomePlugin.startup(None))
    assert threads and threads[0] != main_thread
    assert result.unwrap()["readiness"] == "launch_requested"
    assert "connected" not in result.unwrap()


@pytest.mark.parametrize(
    "method,kwargs,action,args",
    [
        ("open_home", {}, "show", {}),
        ("build_home", {"style": "cherry"}, "build", {"style": "cherry", "_actor": "YUI"}),
        ("save_home", {}, "export", {}),
        ("home_status", {}, "status", {}),
        ("journal", {}, "journal", {}),
        ("ask_yui", {}, "open_chat", {}),
        ("collaborate", {}, "collaborate", {}),
        ("farm", {"task": "cancel"}, "farm", {"task": "cancel", "_actor": "YUI"}),
    ],
)
def test_entries_use_authenticated_http(
    home_module, installation, local_server, monkeypatch, method, kwargs, action, args
):
    url, requests = local_server
    monkeypatch.setattr(home_module, "_COMMAND_URL", url + "/command")
    result = asyncio.run(getattr(home_module.YuiHomePlugin, method)(None, **kwargs))
    assert result.is_ok()
    assert requests[0]["body"] == {"action": action, **args}
    assert requests[0]["token"] == "test-local-token"
    assert "test-local-token" not in str(result.unwrap())


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("build_home", {"style": "unsafe"}),
        ("build_home", {"style": []}),
        ("farm", {"task": "arbitrary"}),
        ("farm", {"task": None}),
    ],
)
def test_invalid_inputs_never_reach_runtime(home_module, monkeypatch, method, kwargs):
    monkeypatch.setattr(home_module, "_request", lambda *_a, **_k: pytest.fail("invalid input reached runtime"))
    result = asyncio.run(getattr(home_module.YuiHomePlugin, method)(None, **kwargs))
    assert result.is_err() and result.err().code == "invalid_argument"


def test_missing_runtime_entry_is_actionable(home_module, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    result = asyncio.run(home_module.YuiHomePlugin.home_status(None))
    assert result.is_err()
    assert result.err().code == "home_runtime_unavailable"
    assert "完整项目包" in str(result.err())


@pytest.mark.parametrize("submitted", [True, False])
def test_scene_receipt_preserves_acceptance_without_inventing_speech(home_module, monkeypatch, submitted):
    messages, callbacks = [], []

    def push(**message):
        messages.append(message)
        return {"submitted": submitted, "message_id": "fixture-message", "reason": None if submitted else "offline"}

    monkeypatch.setattr(
        home_module, "_request", lambda action, **args: callbacks.append((action, args)) or {"ok": True}
    )
    actor = SimpleNamespace(ctx=SimpleNamespace(push_message=push))
    result = asyncio.run(
        home_module.YuiHomePlugin.notify_home(
            actor, {"question": "加个花园", "privateToken": "excluded"}, "fixture-event"
        )
    )
    value = result.unwrap()
    assert value["ok"] is submitted
    assert value["generated_by_plugin"] is False
    assert "speech" not in value and "completed" not in value
    assert messages[0]["target_lanlan"] == "YUI"
    assert "excluded" not in messages[0]["parts"][0]["text"]
    assert callbacks[0][0] == "native_receipt"
    assert callbacks[0][1]["request_id"] == "fixture-event"
    assert callbacks[0][1]["receipt"]["submitted"] is submitted


def test_receipt_failure_does_not_claim_window_received_it(home_module, monkeypatch):
    def fail(*_a, **_k):
        raise RuntimeError("fixture: window offline")

    monkeypatch.setattr(home_module, "_request", fail)
    actor = SimpleNamespace(ctx=SimpleNamespace(push_message=lambda **_: {"submitted": True, "message_id": "fixture"}))
    result = asyncio.run(home_module.YuiHomePlugin.notify_home(actor, {}, "fixture-event"))
    assert result.is_err() and result.err().code == "home_receipt_failed"
    assert result.err().details["submitted"] is True


@pytest.mark.parametrize(
    "context,event,behavior",
    [
        ([], "fixture-event", "respond"),
        ({}, "invalid\nevent", "respond"),
        ({}, "fixture-event", "invalid"),
        ({"question": "x" * 32769}, "fixture-event", "read"),
        ({"progress": float("nan")}, "fixture-event", "respond"),
    ],
)
def test_invalid_scene_does_not_send_message(home_module, context, event, behavior):
    actor = SimpleNamespace(ctx=SimpleNamespace(push_message=lambda **_: pytest.fail("invalid scene submitted")))
    result = asyncio.run(home_module.YuiHomePlugin.notify_home(actor, context, event, behavior))
    assert result.is_err() and result.err().code == "invalid_argument"
