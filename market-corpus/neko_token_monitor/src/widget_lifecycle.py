"""Allow the native macOS host restoration to finish before forcing a stop."""

import subprocess


def stop_widget(process, platform, stop_file=None):
    if process.poll() is not None:
        return
    if platform == "win32":
        stop_file.touch()
    else:
        process.terminate()
    try:
        # Native macOS restoration has a 24s deadline plus a 2s signal timer.
        # A 3s kill used to interrupt it while Electron was still returning YUI.
        process.wait(timeout=28 if platform == "darwin" else 3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
