import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_providers import load

topup, fleet, server = [load(x) for x in ("topup", "fleet", "server")]
app = topup.app_topup


def test_application_platform_guard_and_no_guessed_bundles():
    with patch.object(app.sys, "platform", "darwin"):
        assert topup.info("trae")["application"]["id"] == "application"
        for key, *_ in fleet.CATALOG:
            if key not in ("trae", "deepseek_harness", "workbuddy"):
                assert topup.info(key)["application"] is None
                try:
                    app.open_application(key, lambda _: (_ for _ in ()).throw(AssertionError()))
                except ValueError:
                    pass
                else:
                    raise AssertionError("unsupported application allowed")
        assert topup.info("deepseek_harness")["application"]["id"] == "application"
    for platform in ("win32", "linux"):
        with patch.object(app.sys, "platform", platform):
            assert topup.info("trae")["application"] is None
            assert topup.info("trae")["entries"]
            try:
                app.open_application("trae", lambda _: (_ for _ in ()).throw(AssertionError()))
            except ValueError:
                pass
            else:
                raise AssertionError("unsupported OS allowed")


def test_only_confirmed_screen_counts_and_never_payment_or_raw_ui():
    with patch.object(app.sys, "platform", "darwin"):
        calls = []
        for payload in (
            {"ok": True},
            {"ok": True, "stage": "billing"},
            {"ok": False, "stage": "credits"},
            {"ok": "true", "stage": "credits"},
            {"error": "private error"},
            None,
        ):
            result = app.open_application("trae", lambda profile: calls.append(profile) or payload)
            assert not result["requested"] and not result["page_confirmed"] and not result["payment_confirmed"]
        result = topup.open_official(
            "trae",
            "application",
            lambda _: (_ for _ in ()).throw(AssertionError("no web fallback")),
            lambda _: {
                "ok": True,
                "stage": "credits",
                "payment_confirmed": True,
                "ui": "private account label",
                "steps": ["avatar", "quota", "credits", "pay"],
                "diagnostic": "private error",
            },
        )
        assert result["requested"] and result["page_confirmed"] and result["payment_confirmed"] is False
        assert result["navigation_steps"] == ["avatar", "quota", "credits"]
        assert "private" not in json.dumps(result) and 'pay"' not in json.dumps(result)
        assert calls == ["trae"] * 6
        for reason in ("permission", "not_installed", "interrupted", "not_confirmed"):
            result = app.open_application("trae", lambda _: {"ok": False, "error": reason})
            assert result["error"] == reason and not result["requested"]


def test_concurrent_navigation_does_not_repeat_clicks_and_helper_is_bounded():
    with patch.object(app.sys, "platform", "darwin"):
        app._lock.acquire()
        try:
            result = app.open_application(
                "trae", lambda _: (_ for _ in ()).throw(AssertionError("must not start second navigator"))
            )
            assert result["error"] == "busy"
        finally:
            app._lock.release()
        with patch.object(app.Path, "is_file", return_value=True):
            for output in (b"not json", b"[]", b"x" * 4097):
                with patch.object(app.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=output)):
                    assert not app._run("trae")["ok"]
            with patch.object(app.subprocess, "run", side_effect=subprocess.TimeoutExpired("helper", 16)):
                assert not app._run("trae")["ok"]
            with patch.object(
                app.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=b'{"ok":true,"stage":"credits"}'),
            ) as run:
                assert app._run("trae")["ok"]
                assert run.call_args.args[0] == [str(Path(app.__file__).parent / "bin/catfood-open-topup"), "trae"]
                assert run.call_args.kwargs["timeout"] == 16 and "shell" not in run.call_args.kwargs


def test_application_http_requires_csrf_and_cannot_inject_actions_or_change_balance(tmp_path):
    f = fleet.Fleet(tmp_path)
    f.configure(
        {"profile": "trae", "quota_mode": "manual", "quota_remaining": 9, "quota_total": 100, "quota_unit": "积分"}
    )
    before = f.snapshot()
    app_calls, web_calls = [], []
    with patch.object(app.sys, "platform", "darwin"):
        httpd, thread = server.start_server(
            f,
            port=0,
            official_opener=lambda url: web_calls.append(url) or True,
            application_opener=lambda key: app_calls.append(key) or {"ok": True, "stage": "credits"},
        )
        base = f"http://127.0.0.1:{httpd.server_port}"

        def request(body, headers):
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(base + "/api/topup", data=json.dumps(body).encode(), headers=headers),
                    timeout=3,
                ) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                return exc.code, json.load(exc)

        try:
            with urllib.request.urlopen(base + "/api/status", timeout=3) as response:
                csrf = json.load(response)["csrf"]
            headers = {"Content-Type": "application/json", "X-Neko-CSRF": csrf}
            body = {"profile": "trae", "target": "application"}
            assert request(body, {})[0] == 403
            assert request(body, dict(headers, Origin="https://evil.invalid"))[0] == 403
            for extra in ({"bundle": "com.other.app"}, {"action": "同意协议"}, {"url": "https://evil.invalid"}):
                assert request(dict(body, **extra), headers)[0] == 400
            assert request(dict(body, profile="codex"), headers)[0] == 400
            assert not app_calls and not web_calls
            code, result = request(body, headers)
            assert code == 200 and result["page_confirmed"] and not result["payment_confirmed"]
            assert app_calls == ["trae"] and not web_calls
            after = f.snapshot()
            assert after["config"] == before["config"] and after["account_quota"] == before["account_quota"]
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)
