"""Offline tests for the host status read. No socket is ever opened: ``_net.request`` is faked.

The shape asserted here is the one read out of the host source:

* ``GET /plugin/status?plugin_id=x`` -> ``{"plugin_id", "status": {"status": "running|stopped|crashed"},
  "updated_at", "source", "time"}`` (``plugin/core/status.py`` + ``query_service.py:574``).

There is deliberately nothing about ``POST /plugin/x/start|stop`` in this file: a
user plugin pressing that switch reported changes that had landed as failures, so
the write path was removed (see ``test_the_client_has_no_write_route``).
"""

from __future__ import annotations

import builtins
import json
import socket
import sys
import types
import urllib.request
from pathlib import Path

import conftest
import pytest

host = conftest.load("_host")
net = conftest.load("_net")

PID = "web_search"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_real_socket(monkeypatch):
    """Prove this module is offline: any outbound transport attempt fails the test."""

    def refused(*args: object, **kwargs: object) -> object:
        raise AssertionError("test_host.py must never open a real connection")

    monkeypatch.setattr(socket, "socket", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", refused)
    yield


class FakeResponse:
    """Stands in for ``_net.Response`` (same attribute/method surface)."""

    def __init__(self, body: bytes = b"", status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.url = "http://127.0.0.1:48916/plugin/status"
        self.headers = dict(headers or {})
        self.body = body

    def header(self, name: str) -> str:
        want = name.casefold()
        for key, value in self.headers.items():
            if key.casefold() == want:
                return value
        return ""


def json_response(payload: object, status: int = 200, headers: dict[str, str] | None = None) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"), status, headers)


class Recorder:
    """Collects every call and answers from a queue of results."""

    def __init__(self, *results: object) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.results = list(results)

    def __call__(self, method: str, url: str, **kwargs: object) -> object:
        self.calls.append((method, url, kwargs))
        if self.results:
            outcome = self.results.pop(0)
        else:  # a queue that ran dry is a test bug, not an empty success
            raise AssertionError(f"unexpected extra request: {method} {url}")
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def count(self) -> int:
        return len(self.calls)


def control(monkeypatch, *results: object):
    """Return ``(control, recorder)`` with ``_net.request`` replaced by the recorder."""
    rec = Recorder(*results)
    monkeypatch.setattr(host._net, "request", rec)
    return host.HostPluginControl("http://127.0.0.1:48916"), rec


def http_error(status: int, code: str = "") -> Exception:
    message = f"HTTP {status}" if not code else f"HTTP {status} {code}"
    error = net.HttpStatusCodeError(message, status)
    if code:
        error.code = code  # type: ignore[attr-defined]
    return error


# --- whitelist ------------------------------------------------------------


@pytest.mark.parametrize("pid", ["web_fetch", "WEB_SEARCH", "web_search ", "", " lifekit",
                                 "web_search/../memo", "game_agent_minecraft", None, 0, ["web_search"]])
def test_only_the_whitelisted_plugin_id_is_accepted(monkeypatch, pid: object) -> None:
    control_obj, rec = control(monkeypatch)
    with pytest.raises(ValueError):
        control_obj.status(str(pid) if pid is not None else "")
    assert rec.count == 0
    assert host.ALLOWED_PLUGIN_IDS == frozenset({"web_search"})


def test_the_client_has_no_write_route() -> None:
    """No start/stop is reachable from this module -- the user does that in 插件中心.

    Guarded at source level on purpose: ``set_enabled`` was the single call that
    made this plugin report a landed change as "未能连接宿主管理接口" (19 logged
    failures on a real install, v0.2.0 through v0.9.6, none of them real), and a
    half-restored write path would keep every test here green.
    """
    source = (ROOT / "_host.py").read_text(encoding="utf-8")
    assert "def set_enabled" not in source
    assert '_call("POST' not in source
    assert not hasattr(host.HostPluginControl, "set_enabled")


def test_every_request_uses_policy_none_and_no_proxy_url(monkeypatch) -> None:
    control_obj, rec = control(
        monkeypatch,
        json_response({"plugin_id": PID, "status": {"status": "running"}, "source": "main_process_synthetic"}),
    )

    control_obj.status(PID)

    assert rec.count == 1
    for method, url, kwargs in rec.calls:
        assert kwargs["policy"] == "none"
        assert kwargs["policy"] == net.POLICY_NONE
        assert kwargs["proxy_url"] == ""
        assert url.startswith("http://127.0.0.1:48916")
    assert rec.calls[0][0] == "GET"
    assert rec.calls[0][1].endswith("/plugin/status")
    assert rec.calls[0][2]["params"] == {"plugin_id": PID}


def test_the_whitelist_is_immutable_at_runtime() -> None:
    assert isinstance(host.ALLOWED_PLUGIN_IDS, frozenset)
    with pytest.raises((AttributeError, TypeError)):
        host.ALLOWED_PLUGIN_IDS.add("lifekit")  # type: ignore[attr-defined]


# --- status reading -------------------------------------------------------


def test_status_reads_the_host_synthetic_record(monkeypatch) -> None:
    running = {"plugin_id": PID, "status": {"status": "running"}, "updated_at": "x", "source": "main_process_synthetic"}
    stopped = {"plugin_id": PID, "status": {"status": "stopped"}, "updated_at": "x", "source": "main_process_synthetic"}
    control_obj, _ = control(monkeypatch, json_response(running), json_response(stopped))

    state = control_obj.status(PID)
    assert (state.plugin_id, state.exists, state.running) == (PID, True, True)
    assert state.error == ""

    state = control_obj.status(PID)
    assert (state.exists, state.running) == (True, False)
    assert state.raw["status"]["status"] == "stopped"
    assert state.as_context_dict() == {"exists": True, "running": False, "error": ""}


def test_status_accepts_a_reported_status_payload(monkeypatch) -> None:
    payload = {"plugin_id": PID, "status": {"running": True, "note": "内部状态"}, "source": "main_process_direct"}
    control_obj, _ = control(monkeypatch, json_response(payload))

    state = control_obj.status(PID)
    assert state.running is True
    assert state.exists is True


@pytest.mark.parametrize("body", [
    b"",
    b"not json at all",
    b"{",
    json.dumps(None).encode("utf-8"),
    json.dumps([]).encode("utf-8"),
    json.dumps([{"plugin_id": PID}]).encode("utf-8"),
    json.dumps("text").encode("utf-8"),
    json.dumps({}).encode("utf-8"),
    json.dumps({"detail": "Not Found"}).encode("utf-8"),
    json.dumps({"detail": {"code": "PLUGIN_STATUS_QUERY_FAILED", "message": "Failed"}}).encode("utf-8"),
    json.dumps({"plugin_id": "another", "status": {"status": "running"}}).encode("utf-8"),
    json.dumps({"status": "running"}).encode("utf-8"),
    json.dumps({"unexpected": 1}).encode("utf-8"),
])
def test_status_survives_malformed_bodies(monkeypatch, body: bytes) -> None:
    control_obj, _ = control(monkeypatch, FakeResponse(body))

    state = control_obj.status(PID)
    assert isinstance(state, host.HostPluginState)
    assert isinstance(state.raw, dict)
    assert state.running in (True, False)
    assert state.exists in (True, False)


def test_status_survives_a_non_json_body(monkeypatch) -> None:
    control_obj, _ = control(monkeypatch, FakeResponse(b"<html>502 Bad Gateway</html>"))

    state = control_obj.status(PID)
    assert state.exists is False
    assert state.running is False
    assert state.error == host.MESSAGE_BAD_RESPONSE
    assert "502" not in state.error


def test_status_of_an_empty_but_wellformed_record_is_not_running(monkeypatch) -> None:
    control_obj, _ = control(monkeypatch, json_response({"plugin_id": PID}))

    state = control_obj.status(PID)
    assert (state.exists, state.running) == (True, False)
    assert state.error == ""


# --- failure normalisation ------------------------------------------------


def test_network_failure_is_chinese_and_leaks_nothing(monkeypatch) -> None:
    raw = "网络不可达: [Errno 111] Connection refused http://127.0.0.1:48916/plugin/status Traceback"
    control_obj, _ = control(monkeypatch, net.NetworkError(raw))
    state = control_obj.status(PID)
    assert (state.exists, state.running) == (False, False)
    assert state.error == host.MESSAGE_UNREACHABLE
    for forbidden in ("http", "127.0.0.1", "Traceback", "Errno", "48916"):
        assert forbidden not in state.error


def test_a_status_read_that_times_out_says_slow_not_unreachable(monkeypatch) -> None:
    """A host that is mid-reload answers late; "slow" and "absent" are different advice."""
    control_obj, _ = control(monkeypatch, net.NetworkError("请求超时"))
    state = control_obj.status(PID)
    assert state.error == host.MESSAGE_SLOW
    assert "稍后再点一次" in state.error
    for forbidden in ("http", "127.0.0.1", "Traceback", "Errno"):
        assert forbidden not in state.error


def test_unexpected_exception_never_escapes(monkeypatch) -> None:
    class Boom(RuntimeError):
        pass

    control_obj, _ = control(monkeypatch, Boom("secret /internal/path http://127.0.0.1:1"))
    state = control_obj.status(PID)
    assert state.exists is False and state.running is False
    assert "secret" not in state.error


def test_http_error_on_status_is_normalised(monkeypatch) -> None:
    control_obj, _ = control(monkeypatch, http_error(500, "PLUGIN_STATUS_QUERY_FAILED"))

    state = control_obj.status(PID)
    assert (state.exists, state.running) == (False, False)
    assert state.error == f"{host.MESSAGE_STATUS_FAILED}（PLUGIN_STATUS_QUERY_FAILED）"
    assert "http" not in state.error


# --- base url resolution --------------------------------------------------


def _block_config_import(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0, *args, **kwargs):  # noqa: ANN001
        if name == "config" or name.startswith("config."):
            raise ImportError(f"blocked for test: {name}")
        return real_import(name, globals, locals, fromlist, level, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    for key in ("NEKO_USER_PLUGIN_SERVER_PORT", "USER_PLUGIN_SERVER_PORT"):
        monkeypatch.delenv(key, raising=False)


def _install_fake_config(monkeypatch, module: types.ModuleType, network: types.ModuleType | None = None) -> None:
    """Put a fake ``config`` package in front of the import system (no blocking).

    ``config.network`` is replaced too: the host project is on ``sys.path`` in this
    venv and other tests already imported the real one, which would otherwise
    answer tier 1b with the live ``48916`` and make these tests order-dependent.
    """
    for key in ("NEKO_USER_PLUGIN_SERVER_PORT", "USER_PLUGIN_SERVER_PORT"):
        monkeypatch.delenv(key, raising=False)
    if getattr(module, "__path__", None) is None:
        module.__path__ = []  # type: ignore[attr-defined]
    if network is None:
        network = types.ModuleType("config.network")
    network.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "config", module)
    monkeypatch.setitem(sys.modules, "config.network", network)


def test_explicit_base_url_wins_and_is_normalised(monkeypatch) -> None:
    _block_config_import(monkeypatch)
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "60001")

    assert host.HostPluginControl("http://127.0.0.1:40000/").base_url == "http://127.0.0.1:40000"
    assert host.HostPluginControl("127.0.0.1:40001").base_url == "http://127.0.0.1:40001"


def test_a_non_loopback_base_url_is_refused(monkeypatch) -> None:
    _block_config_import(monkeypatch)

    control_obj = host.HostPluginControl("http://attacker.example.com:48916")
    assert control_obj.base_url == host.FALLBACK_BASE_URL


def test_tier1_resolver_from_config_package_is_preferred(monkeypatch) -> None:
    calls: list[str] = []
    module = types.ModuleType("config")
    module.USER_PLUGIN_BASE = "http://127.0.0.1:50001"  # type: ignore[attr-defined]

    def resolve() -> str:
        calls.append("resolve")
        return "http://127.0.0.1:50002/"

    module.resolve_user_plugin_base = resolve  # type: ignore[attr-defined]
    _install_fake_config(monkeypatch, module)
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "50003")

    assert host.resolve_base_url() == "http://127.0.0.1:50002"
    assert calls == ["resolve"]


def test_tier1b_falls_to_config_network_module(monkeypatch) -> None:
    package = types.ModuleType("config")
    package.__path__ = []  # type: ignore[attr-defined]
    network = types.ModuleType("config.network")
    network.resolve_user_plugin_base = lambda: "http://127.0.0.1:50004"  # type: ignore[attr-defined]
    package.network = network  # type: ignore[attr-defined]
    _install_fake_config(monkeypatch, package, network)
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "50003")

    assert host.resolve_base_url() == "http://127.0.0.1:50004"


def test_tier2_constant_when_the_resolver_is_not_exported(monkeypatch) -> None:
    module = types.ModuleType("config")
    module.USER_PLUGIN_BASE = "http://localhost:50005"  # type: ignore[attr-defined]
    _install_fake_config(monkeypatch, module)

    assert host.resolve_base_url() == "http://localhost:50005"


def test_tier3_environment_port_when_config_is_unimportable(monkeypatch) -> None:
    _block_config_import(monkeypatch)
    monkeypatch.setenv("USER_PLUGIN_SERVER_PORT", "50006")

    assert host.resolve_base_url() == "http://127.0.0.1:50006"


def test_tier3_environment_overrides_a_bogus_constant(monkeypatch) -> None:
    _block_config_import(monkeypatch)
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "50007")
    monkeypatch.setenv("USER_PLUGIN_SERVER_PORT", "not-a-port")

    assert host.resolve_base_url() == "http://127.0.0.1:50007"


def test_tier4_hardcoded_loopback_is_the_last_resort(monkeypatch) -> None:
    _block_config_import(monkeypatch)

    assert host.resolve_base_url() == "http://127.0.0.1:48916"
    assert host.HostPluginControl().base_url == "http://127.0.0.1:48916"


def test_a_config_that_raises_does_not_break_resolution(monkeypatch) -> None:
    module = types.ModuleType("config")

    def explode() -> str:
        raise RuntimeError("host config is on fire http://internal")

    module.resolve_user_plugin_base = explode  # type: ignore[attr-defined]
    module.USER_PLUGIN_BASE = "http://127.0.0.1:50008"  # type: ignore[attr-defined]
    _install_fake_config(monkeypatch, module)

    assert host.resolve_base_url() == "http://127.0.0.1:50008"


def test_timeout_defaults_and_guards(monkeypatch) -> None:
    _block_config_import(monkeypatch)

    assert host.HostPluginControl("http://127.0.0.1:48916").timeout == 4.0
    assert host.HostPluginControl("http://127.0.0.1:48916", timeout=0).timeout == 4.0
    assert host.HostPluginControl("http://127.0.0.1:48916", timeout="bad").timeout == 4.0  # type: ignore[arg-type]
    assert host.HostPluginControl("http://127.0.0.1:48916", timeout=1.5).timeout == 1.5


def test_no_global_mutable_state_between_instances(monkeypatch) -> None:
    control_obj, rec = control(
        monkeypatch, json_response({"plugin_id": PID, "status": {"status": "running"}, "source": "x"}))
    other = host.HostPluginControl("http://127.0.0.1:48917")

    control_obj.status(PID)
    assert [call[1] for call in rec.calls] == ["http://127.0.0.1:48916/plugin/status"]
    assert other.base_url == "http://127.0.0.1:48917"


def test_state_dataclass_keeps_the_contract_field_order() -> None:
    state = host.HostPluginState(PID, True, False, {"k": "v"})
    assert (state.plugin_id, state.exists, state.running, state.raw) == (PID, True, False, {"k": "v"})
