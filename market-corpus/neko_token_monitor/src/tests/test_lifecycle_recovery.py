import json
import os
import sys
import threading
import time
from unittest.mock import patch
from urllib.request import Request, urlopen

import pytest
from test_providers import load

desktop, app, recording, takeover, server = [load(name) for name in
                                           ("desktop_quota", "app_topup", "recording", "takeover", "server")]


def test_extracted_helpers_run_after_installer_removes_execute_bit(tmp_path):
    if os.name == "nt":
        return
    folder = tmp_path / "bin"
    folder.mkdir()
    cases = {
        "catfood-quota-reader": {"ok": True, "panels": [["总可用积分", "129.5"]]},
        "catfood-open-topup": {"ok": True, "stage": "credits"},
    }
    for name, payload in cases.items():
        helper = folder / name
        helper.write_text(f"#!{sys.executable}\nimport json\nprint({json.dumps(json.dumps(payload))})\n")
        helper.chmod(0o644)
    with patch.object(desktop, "__file__", str(tmp_path / "desktop_quota.py")), \
            patch.object(app, "__file__", str(tmp_path / "app_topup.py")), \
            patch.object(desktop.sys, "platform", "darwin"):
        assert desktop.read_quota("trae")["remaining"] == 129.5
        assert app._run("trae") == {"ok": True, "stage": "credits"}
    assert all(os.access(folder / name, os.X_OK) for name in cases)


def marker(record, identifier):
    path = record.path.parent / ("closed-" + identifier + ".json")
    path.write_text(json.dumps({"session_id": identifier}))
    return path


def test_native_end_survives_restart_and_old_end_cannot_close_a_new_session(tmp_path):
    record = recording.Recording(tmp_path, importer=lambda row: False)
    assert record.action("end", session_id="absent-session")["ended_session_id"] == "absent-session"
    first = record.action("start")["id"]
    record.action("pet", "first-pet", first)
    path = marker(record, first)
    restarted = recording.Recording(tmp_path, importer=lambda row: False)
    assert not restarted.snapshot()["active"]
    assert not path.exists()
    second = restarted.action("start")["id"]
    ended = restarted.action("end", session_id=first)
    assert ended["ended_session_id"] == first and ended["id"] == second and ended["active"]
    marker(restarted, first)
    assert restarted.snapshot()["active"]
    # Late clicks still update the completed session exactly once.
    restarted.action("pet", "late-pet", first)
    restarted.action("pet", "late-pet", first)
    assert restarted.snapshot()["history"][1]["pet"] == 2
    assert restarted.snapshot()["pet"] == 0


def test_native_end_watcher_does_not_wait_for_slow_memory_import(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def importer(row):
        entered.set()
        release.wait(4)
        return False
    record = recording.Recording(tmp_path, importer=importer)
    record.action("start")
    record.action("end")
    record.start()
    try:
        assert entered.wait(2)
        current = record.action("start")["id"]
        marker(record, current)
        deadline = time.monotonic() + 2
        # Inspect durable storage directly, without a snapshot that would reconcile it.
        while time.monotonic() < deadline:
            if not json.loads(record.path.read_text())["sessions"][-1]["active"]:
                break
            time.sleep(0.05)
        assert not json.loads(record.path.read_text())["sessions"][-1]["active"]
    finally:
        release.set()
        record.close()


def test_end_marker_is_retained_until_disk_commit_succeeds(tmp_path):
    record = recording.Recording(tmp_path)
    identifier = record.action("start")["id"]
    path = marker(record, identifier)
    with patch.object(record, "save", side_effect=OSError("temporary disk failure")):
        record.reconcile_ends()
    assert path.exists()
    record.reconcile_ends()
    assert not path.exists()
    assert not recording.Recording(tmp_path).snapshot()["active"]


def test_first_http_start_and_restarted_active_session_accept_native_ack(tmp_path):
    record = recording.Recording(tmp_path)
    state = takeover.TakeoverState()
    class Monitor:
        def snapshot(self):
            return {}
    httpd, thread = server.start_server(Monitor(), port=0, recording=record, takeover=state)
    base = f"http://127.0.0.1:{httpd.server_port}"
    def post(route, body):
        request = Request(base + route, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Neko-CSRF": csrf})
        with urlopen(request, timeout=3) as response:
            return json.load(response)
    try:
        with urlopen(base + "/api/status", timeout=3) as response:
            csrf = json.load(response)["csrf"]
        first = post("/api/recording", {"action": "start"})["id"]
        assert post("/api/takeover", {"session_id": first, "ok": True})["accepted"]
        assert state.wait(first, 0)["ok"]
        # Simulate restored Recording with an empty transient takeover state.
        state.session_id, state.result = None, None
        state.event.clear()
        assert post("/api/takeover", {"session_id": first, "ok": True})["accepted"]
        post("/api/recording", {"action": "end", "session_id": first})
        assert not post("/api/takeover", {"session_id": first, "ok": True})["accepted"]
        second = post("/api/recording", {"action": "start"})["id"]
        post("/api/recording", {"action": "end", "session_id": first})
        assert record.snapshot()["id"] == second and record.snapshot()["active"]
        assert not post("/api/takeover", {"session_id": first, "ok": True})["accepted"]
        assert post("/api/takeover", {"session_id": second, "ok": True})["accepted"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


@pytest.mark.parametrize("end_source", ["native", "http"])
def test_failure_ack_after_end_wakes_waiter_without_resuming_session(tmp_path, end_source):
    record = recording.Recording(tmp_path, importer=lambda row: False)
    state = takeover.TakeoverState()

    class Monitor:
        def snapshot(self):
            return {}

    httpd, thread = server.start_server(Monitor(), port=0, recording=record, takeover=state)
    base = f"http://127.0.0.1:{httpd.server_port}"
    with urlopen(base + "/api/status", timeout=3) as response:
        csrf = json.load(response)["csrf"]

    def post(route, body):
        request = Request(base + route, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Neko-CSRF": csrf})
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    try:
        first = post("/api/recording", {"action": "start"})["id"]
        completed = threading.Event()
        outcome = []

        def wait():
            outcome.append(state.wait(first, 2))
            completed.set()

        waiter = threading.Thread(target=wait)
        waiter.start()
        if end_source == "native":
            marker(record, first)
        else:
            post("/api/recording", {"action": "end", "session_id": first})
        assert not post("/api/takeover", {"session_id": first, "ok": True})["accepted"]
        assert post("/api/takeover", {
            "session_id": first, "ok": False, "reason": "pause_unconfirmed"})["accepted"]
        assert completed.wait(.5), "failure must wake the original waiter before its timeout"
        waiter.join(2)
        assert outcome == [{"ok": False, "reason": "pause_unconfirmed"}]
        assert not record.snapshot()["active"]
        assert not post("/api/takeover", {
            "session_id": first, "ok": False, "reason": "timeout"})["accepted"]
        second = post("/api/recording", {"action": "start"})["id"]
        assert not post("/api/takeover", {
            "session_id": first, "ok": False, "reason": "pause_unconfirmed"})["accepted"]
        assert state.wait(second, 0) is None
        assert record.snapshot()["active"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)
