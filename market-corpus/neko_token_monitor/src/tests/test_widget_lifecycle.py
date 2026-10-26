import signal
import subprocess
import sys
import time

import pytest
from test_providers import load

lifecycle = load("widget_lifecycle")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX cooperative termination")
def test_mac_restore_taking_more_than_three_seconds_is_not_killed(tmp_path):
    ready, restored = tmp_path / "ready", tmp_path / "restored"
    script = """
import signal,sys,time
from pathlib import Path
def stop(*_):
    time.sleep(3.2)
    Path(sys.argv[2]).write_text('restored')
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
Path(sys.argv[1]).touch()
while True:time.sleep(.1)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(ready), str(restored)])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists()
        lifecycle.stop_widget(process, "darwin")
        assert process.returncode == 0
        assert restored.read_text() == "restored"
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGKILL)
            process.wait()
