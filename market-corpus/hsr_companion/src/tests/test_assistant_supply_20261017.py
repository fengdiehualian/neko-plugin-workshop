"""Frame supply contracts, independently of OCR and host speech."""
from __future__ import annotations

import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from plugin.plugins.hsr_companion.assistant_runtime import server
from plugin.plugins.hsr_companion.assistant_runtime import window_capture
from plugin.plugins.hsr_companion.tests.test_window_capture import FakeCapture, session, window


def ready_state():
    state = server.CaptureState()
    state.frame_seq, state.timestamp = 1, time.time()
    state.image, state.signature = b"first", "first-signature"
    state.game = {"running": True, "minimized": False}
    state.capture = {"state": "capturing", "isolated_window": True}
    return state


def test_slow_reader_cannot_hold_publisher_lock_and_metadata_stays_atomic(monkeypatch):
    state = ready_state()
    entered, release = threading.Event(), threading.Event()
    original = base64.b64encode

    def slow_encode(data):
        entered.set()
        assert release.wait(2)
        return original(data)

    monkeypatch.setattr(server.base64, "b64encode", slow_encode)
    with ThreadPoolExecutor(max_workers=1) as workers:
        reader = workers.submit(state.snapshot, include_image=True)
        assert entered.wait(1)
        acquired = state.lock.acquire(timeout=.2)
        try:
            if acquired:
                state.frame_seq, state.image, state.signature = 2, b"second", "second-signature"
        finally:
            if acquired:
                state.lock.release()
            release.set()
        payload = reader.result(timeout=2)
    assert acquired, "base64/slow reader blocks frame publication and health"
    assert payload["frame_seq"] == 1
    assert payload["signature"] == "first-signature"
    assert base64.b64decode(payload["image_base64"]) == b"first"


def test_native_copy_does_not_block_new_callback(monkeypatch):
    capture = session()
    capture.read(window())
    FakeCapture.instances[-1].emit("red")
    old = capture._image
    original_copy = old.copy
    entered, release = threading.Event(), threading.Event()

    def slow_copy():
        entered.set()
        assert release.wait(2)
        return original_copy()

    monkeypatch.setattr(capture._image, "copy", slow_copy)
    completed = threading.Event()
    with ThreadPoolExecutor(max_workers=2) as workers:
        reader = workers.submit(capture.read, window())
        assert entered.wait(1)
        def emit():
            FakeCapture.instances[-1].emit("blue")
            completed.set()
        emitter = workers.submit(emit)
        not_blocked = completed.wait(.2)
        release.set()
        image, _ = reader.result(timeout=2)
        emitter.result(timeout=2)
    assert not_blocked, "copy under native condition stalls newest-frame callback"
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert capture.read(window())[0].getpixel((0, 0)) == (0, 0, 255)
    capture.close()


def test_timestamp_includes_callback_conversion_time(monkeypatch):
    capture = session()
    clock = [100.0]
    monkeypatch.setattr(window_capture.time, "time", lambda: clock[0])
    def slow_crop(image, selected):
        clock[0] += 1.25
        return image
    capture._crop = slow_crop
    _, captured_at = capture.read(window())
    assert captured_at == 100.0, "conversion must not renew frame capture time"
    capture.close()


@pytest.mark.parametrize("age", [-1, 16])
def test_no_fake_freshness_or_old_frame_replay(age):
    state = ready_state()
    state.timestamp = time.time() - age
    assert state.snapshot(include_image=True)["image_base64"] == ""


def test_same_sequence_not_returned_as_a_new_capture():
    state = ready_state()
    assert state.snapshot(include_image=True, since_frame=1)["image_base64"] == ""


def test_work_time_is_not_followed_by_another_full_capture_interval(monkeypatch):
    from types import SimpleNamespace
    state = server.CaptureState()
    clock = [10.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(server, "_find_window", lambda: SimpleNamespace(minimized=False, public=lambda: {}))
    def capture(selected, **kwargs):
        clock[0] += .6
        return b"frame", "image/png", time.time()
    monkeypatch.setattr(server, "_capture", capture)
    waits = []
    def wait(seconds):
        waits.append(seconds)
        state.stop.set()
    state.stop.wait = wait
    state.update()
    assert waits == [pytest.approx(.15)]
    assert state.supply["cycle_work_seconds"] == .6


def test_close_during_copy_never_delivers_the_old_session(monkeypatch):
    capture = session()
    capture.read(window())
    FakeCapture.instances[-1].emit()
    original_copy = capture._image.copy
    def close_then_copy():
        capture.close()
        return original_copy()
    monkeypatch.setattr(capture._image, "copy", close_then_copy)
    with pytest.raises(window_capture.WindowCaptureError, match="closed"):
        capture.read(window())


def test_loopback_legacy_http_contract_preserves_same_bundle():
    import json
    import urllib.request
    state = ready_state()
    http = server.ThreadingHTTPServer(("127.0.0.1", 0), server._handler(state))
    worker = threading.Thread(target=http.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{http.server_port}"
        with urllib.request.urlopen(base + "/health", timeout=2) as response:
            health = json.load(response)
        with urllib.request.urlopen(base + "/api/frame?since_frame=0", timeout=2) as response:
            payload = json.load(response)
        assert payload["timestamp"] == health["timestamp"] == state.timestamp
        assert payload["frame_seq"] == health["frame_seq"] == 1
        assert base64.b64decode(payload["image_base64"]) == b"first"
        assert health["image_base64"] == ""
    finally:
        http.shutdown()
        http.server_close()
        worker.join(2)
