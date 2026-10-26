"""Real Win32 hide/re-hide/restore test using two harmless NEKO-named fixture windows."""

import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

user = ctypes.windll.user32
user.IsWindowVisible.argtypes = [ctypes.c_void_p]
class Rect(ctypes.Structure):
    _fields_ = [(name, ctypes.c_long) for name in ("left", "top", "right", "bottom")]

user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(Rect)]
user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user.EnumWindows.argtypes = [callback_type, ctypes.c_void_p]
phase = "start"
session = "2754c912-aaed-40b2-bcbc-a2b55efb9f05"
reports = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/phase":
            body = phase.encode()
        elif self.path.startswith("/report?"):
            reports.append(self.path)
            body = b"ok"
        else:
            body = b"""<!doctype html><script>
            let previous='';window.catfoodNativeState=d=>fetch('/report?ok='+d.ok+'&session='+d.session_id);
            setInterval(async()=>{const p=await (await fetch('/phase')).text();if(p!==previous){previous=p;chrome.webview.postMessage({action:p==='start'?'recordStart':'recordEnd',session_id:p==='stale'?'2754c912-aaed-40b2-bcbc-a2b55efb9f00':'2754c912-aaed-40b2-bcbc-a2b55efb9f05'});}},200);
            </script>"""
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def windows(pid):
    result = []

    @callback_type
    def collect(handle, unused):
        owner = ctypes.c_ulong()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid:
            result.append(handle)
        return True

    user.EnumWindows(collect, None)
    return result


def rectangle(window):
    r = Rect()
    assert user.GetWindowRect(window, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def wait_for(check, message, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.2)
    raise AssertionError(message)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
fixture = widget = None
try:
    executable = Path("out/CatFood.exe").resolve()
    fixture_exe = executable.with_name("NEKO.exe")
    shutil.copy2(executable, fixture_exe)
    fixture = subprocess.Popen([str(fixture_exe), "--fixture"])
    wait_for(lambda: len([w for w in windows(fixture.pid) if user.IsWindowVisible(w)]) >= 2, "fixture not visible")
    targets = [w for w in windows(fixture.pid) if user.IsWindowVisible(w)]
    original = {w: rectangle(w) for w in targets}
    with tempfile.TemporaryDirectory() as directory:
        widget = subprocess.Popen(
            [str(executable), str(os.getpid()), f"http://127.0.0.1:{server.server_port}", str(Path(directory) / "stop")]
        )
        wait_for(lambda: reports and all(not user.IsWindowVisible(w) for w in targets), "host windows not hidden")
        for w in targets:
            user.ShowWindow(w, 8)
        wait_for(lambda: all(not user.IsWindowVisible(w) for w in targets), "reopened host windows not hidden")
        phase = "stale"
        time.sleep(1)
        assert all(not user.IsWindowVisible(w) for w in targets), "stale session restored current windows"
        phase = "end"
        wait_for(lambda: all(user.IsWindowVisible(w) for w in targets), "host windows not restored")
        wait_for(lambda: (Path(directory) / ("closed-" + session + ".json")).exists(), "no durable end marker")
        assert session in " ".join(reports), "native callback lost session id"
        # Re-enter and stop the native app through the plugin's cooperative stop file.
        assert {w: rectangle(w) for w in targets} == original, "window positions changed on restore"
        phase = "start"
        wait_for(lambda: all(not user.IsWindowVisible(w) for w in targets), "second takeover failed")
        (Path(directory) / "stop").touch()
        widget.wait(timeout=15)
        assert all(user.IsWindowVisible(w) for w in targets), "stop did not restore host"
        assert not json.loads((Path(directory) / "windows-hidden.json").read_text(encoding="utf-8"))
        # A killed renderer/application cannot send recordEnd. Restart must recover
        # only the journaled, still-owned windows before initializing WebView2.
        (Path(directory) / "stop").unlink()
        widget = subprocess.Popen([str(executable), str(os.getpid()), f"http://127.0.0.1:{server.server_port}", str(Path(directory) / "stop")])
        wait_for(lambda: all(not user.IsWindowVisible(w) for w in targets), "third takeover failed")
        widget.kill()
        widget.wait(timeout=10)
        phase = "end"
        widget = subprocess.Popen([str(executable), str(os.getpid()), f"http://127.0.0.1:{server.server_port}", str(Path(directory) / "stop"), "--launcher-only"])
        wait_for(lambda: all(user.IsWindowVisible(w) for w in targets), "crash recovery did not restore host")
        (Path(directory) / "stop").touch()
        widget.wait(timeout=15)
        assert widget.returncode == 0
        print(json.dumps({"windows": len(targets), "hide": "PASS", "rehide": "PASS", "restore": "PASS", "session_isolation": "PASS", "cooperative_exit": "PASS", "crash_recovery": "PASS"}))
finally:
    for proc in (widget, fixture):
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
    if fixture_exe.exists():
        fixture_exe.unlink()
    server.shutdown()
    server.server_close()
