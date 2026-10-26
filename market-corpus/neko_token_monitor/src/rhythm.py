"""Own a local audio-only helper. No model calls, audio files or network client."""
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


class Rhythm:
    def __init__(self, directory, executable=None, *, popen=subprocess.Popen, lease=8):
        self.directory = Path(directory)
        self.executable = Path(executable) if executable else (
            Path(__file__).parent / "bin/CatFoodBPM.app/Contents/MacOS/CatFoodBPM"
        )
        self.popen, self.lease = popen, lease
        self.lock = threading.RLock()
        self.process = None
        self.session = None
        self.runtime = None
        self.pending = None
        self.strength = 0.65
        self.last_poll = 0
        self.error = ""
        self.closed = threading.Event()
        self.watchdog = threading.Thread(target=self._watch, daemon=True)
        self.watchdog.start()

    def supported(self):
        return sys.platform == "darwin" and int(platform.mac_ver()[0].split(".")[0] or 0) >= 13

    def _watch(self):
        while not self.closed.wait(1):
            with self.lock:
                if self.process and time.monotonic() - self.last_poll > self.lease:
                    self._stop()

    def _write(self, action, **values):
        self.pending = str(uuid.uuid4())
        path = self.runtime / "control.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"generation": self.pending, "action": action, **values}))
        os.chmod(temporary, 0o600)
        temporary.replace(path)

    @staticmethod
    def _number(value, low, high):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError("节拍设置超出范围")
        return float(value)

    def action(self, body):
        action = body.get("action")
        if action not in ("start", "stop", "manual", "auto", "strength"):
            raise ValueError("未知节拍操作")
        values = {}
        if action == "manual":
            values["bpm"] = self._number(body.get("bpm"), 40, 200)
        if action == "strength":
            values["strength"] = self._number(body.get("strength"), 0, 1)
        with self.lock:
            if self.closed.is_set():
                return {"enabled": False, "active": False, "capture_status": "stopped"}
            self.last_poll = time.monotonic()
            if action == "stop":
                self._stop()
            elif not self.supported():
                self.error = "自动跟拍目前支持 macOS 13 及以上；Windows 尚未接入。"
            elif action == "strength":
                self.strength = values["strength"]
                if self.process and self.process.poll() is None:
                    self._write(action, **values)
            else:
                running = self.process and self.process.poll() is None
                if not running:
                    self._stop()
                    if not self.executable.is_file():
                        self.error = "本地 BPM 模块未安装"
                        return self.snapshot()
                    self.error = ""
                    self.session = str(uuid.uuid4())
                    self.runtime = self.directory / self.session
                    self.runtime.mkdir(parents=True, mode=0o700)
                    os.chmod(self.runtime, 0o700)
                    arguments = [str(self.executable), "--state", str(self.runtime / "state.json"),
                                 "--control", str(self.runtime / "control.json"),
                                 "--session", self.session, "--parent", str(os.getpid()),
                                 "--strength", str(self.strength)]
                    if action == "manual":
                        arguments += ["--bpm", str(values["bpm"])]
                    try:
                        # Plugin Center extracts runtime files with 0644 mode.
                        # Restore execute permission only on our bundled helper.
                        self.executable.chmod(self.executable.stat().st_mode | 0o100)
                        self.process = self.popen(arguments, stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                  # Avoid fork in the threaded frozen Mac worker:
                                                  # Cocoa/dispatch at-fork handlers can abort it.
                                                  shell=False, close_fds=False)
                    except OSError:
                        self.error = "本地 BPM 模块启动失败"
                        self.process = None
                elif action != "start":
                    self._write(action, **values)
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            self.last_poll = time.monotonic()
            running = bool(self.process and self.process.poll() is None)
            result = {"supported": self.supported(), "enabled": running, "active": False,
                      "bpm": None, "phase": 0, "confidence": 0, "amplitude": 0,
                      "source": "system", "strength": self.strength,
                      "capture_status": "starting" if running else "stopped", "error": self.error}
            if not running:
                if self.process:
                    result.update(capture_status="error", error="本地 BPM 模块已退出，请重新开启")
                return result
            try:
                data = json.loads((self.runtime / "state.json").read_text())
                if data.get("session") != self.session or data.get("pid") != self.process.pid:
                    return result
                age = time.time() - data["updated_at"]
                if not math.isfinite(age) or not -1 <= age <= 2:
                    result.update(capture_status="stale", error="节拍数据已中断")
                    return result
                for name in ("phase", "confidence", "amplitude", "strength"):
                    result[name] = self._number(data[name], 0, 1)
                result["capture_status"] = data.get("capture_status", "error")
                result["error"] = str(data.get("error", ""))[:200]
                result["source"] = "manual" if data.get("source") == "manual" else "system"
                result["active"] = data.get("active") is True and (self.pending is None or data.get("generation") == self.pending)
                if result["active"]:
                    result["bpm"] = self._number(data["bpm"], 40, 200)
                # Advance the native beat phase to response time. The browser
                # continues this clock between polls rather than animating at 3 Hz.
                if result["active"]:
                    result["phase"] = (result["phase"] + max(0, age) * result["bpm"] / 60) % 1
            except (OSError, ValueError, KeyError, TypeError):
                result.update(active=False, bpm=None)
            return result

    def _stop(self):
        process = self.process
        if process and process.poll() is None:
            try:
                self._write("quit")
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        self.process = None
        self.pending = None

    def close(self):
        self.closed.set()
        with self.lock:
            self._stop()
        self.watchdog.join(timeout=2)
