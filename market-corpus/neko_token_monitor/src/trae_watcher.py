"""Refresh on TRAE launch/activation without polling or opening a closed app."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path


class TraeWatcher:
    def __init__(self, callback):
        self.callback = callback
        self.stop = threading.Event()
        self.thread = None
        self.process = None
        self.last_event = float('-inf')
        self.last_native_event = None
        self.last_pid = None

    def event(self, value):
        if (not isinstance(value, dict) or value.get('event') not in ('running', 'launch', 'activate')
                or type(value.get('pid')) is not int or value['pid'] <= 0 or self.stop.is_set()):
            return
        now = time.monotonic()
        if value['event'] == 'running':
            # Monitor.start already does an initial refresh.
            self.last_event = now
            return
        if now - self.last_event < 15 and (value['event'] != 'launch' or value['pid'] == self.last_pid):
            return
        self.last_event = now
        self.last_pid = value['pid']
        self.last_native_event = value['event']
        self.callback()

    def start(self):
        if sys.platform != 'darwin' or self.thread and self.thread.is_alive():
            return

        def worker():
            helper = Path(__file__).parent / 'bin/catfood-quota-reader'
            while not self.stop.is_set():
                try:
                    helper.chmod(helper.stat().st_mode | 0o100)
                    process = subprocess.Popen([str(helper), 'trae-watch'], stdin=subprocess.DEVNULL,
                                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    self.process = process
                    if self.stop.is_set():
                        process.terminate()
                    while not self.stop.is_set():
                        raw = process.stdout.readline(1025)
                        if not raw or len(raw) > 1024:
                            break
                        try:
                            self.event(json.loads(raw))
                        except (ValueError, TypeError):
                            continue
                except (OSError, ValueError):
                    pass
                finally:
                    process = self.process
                    if process:
                        if process.poll() is None:
                            process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                        if process.stdout:
                            process.stdout.close()
                    self.process = None
                self.stop.wait(5)

        self.thread = threading.Thread(target=worker, daemon=True, name='catfood-trae-open')
        self.thread.start()

    def close(self):
        self.stop.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()
        if self.thread:
            self.thread.join(12)
