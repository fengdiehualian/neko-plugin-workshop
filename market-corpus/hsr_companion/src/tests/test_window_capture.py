from __future__ import annotations

import asyncio
import base64
import importlib
import sys
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from PIL import Image
from plugin.plugins.hsr_companion.assistant import VisionAssistantClient
from plugin.plugins.hsr_companion.assistant_runtime.window_capture import (
    WindowCaptureError,
    WindowCaptureSession,
    assert_capturable_window,
)
from plugin.plugins.hsr_companion.tests.test_current_observation import plugin_fixture


def fake_frame(color="red"):
    image = Image.new("RGB", (64, 48), color)
    return SimpleNamespace(width=64, height=48, frame_buffer=SimpleNamespace(tobytes=lambda: image.tobytes("raw", "BGRX")))


class FakeCapture:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stopped = False
        self.events = {}
        self.instances.append(self)

    def event(self, callback):
        self.events[callback.__name__] = callback
        return callback

    def emit(self, color="red"):
        self.events["on_frame_arrived"](fake_frame(color), None)

    def start_free_threaded(self):
        self.emit()
        return SimpleNamespace(is_finished=lambda: False, stop=lambda: setattr(self, "stopped", True))


def session():
    return WindowCaptureSession(factory=FakeCapture, validate=lambda w: None, crop=lambda im, w: im)


def window(hwnd=1, pid=1):
    return SimpleNamespace(hwnd=hwnd, pid=pid, width=64, height=48)


def test_exact_window_target_excludes_monitor_and_secondary_windows():
    capture = session()
    image, stamp = capture.read(window())
    native = FakeCapture.instances[-1]
    assert native.kwargs["window_hwnd"] == 1
    assert native.kwargs["monitor_index"] is None
    assert native.kwargs["window_name"] is None
    assert native.kwargs["secondary_window"] is False
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert 0 <= time.time() - stamp < 2
    capture.close()
    assert native.stopped


def test_repeated_read_requires_new_native_frame_even_for_static_pixels():
    capture = session()
    capture.read(window())
    with pytest.raises(WindowCaptureError, match="no_new_frame"):
        capture.read(window(), timeout=.001)
    FakeCapture.instances[-1].emit()
    assert capture.read(window())[0].size == (64, 48)
    capture.close()


def test_black_frame_and_closed_item_do_not_replay_previous_image():
    capture = session()
    capture.read(window())
    native = FakeCapture.instances[-1]
    native.emit("black")
    with pytest.raises(WindowCaptureError, match="black_frame"):
        capture.read(window())
    native.events["on_closed"]()
    with pytest.raises(WindowCaptureError, match="closed"):
        capture.read(window())
    capture.close()


def test_process_change_replaces_session_and_rejects_late_old_callback():
    capture = session()
    capture.read(window())
    old = FakeCapture.instances[-1]
    capture.read(window(pid=2))
    assert old.stopped
    old.emit("blue")
    assert capture._image.getpixel((0, 0)) == (255, 0, 0)
    capture.close()


def test_stale_native_frame_is_rejected():
    capture = session()
    capture.read(window())
    FakeCapture.instances[-1].emit()
    capture._captured_at = time.time() - 6
    with pytest.raises(WindowCaptureError, match="stale_frame"):
        capture.read(window())
    capture.close()


def test_window_availability_is_checked_again_before_returning_image():
    capture = session()
    calls = []

    def validate(w):
        calls.append(True)
        if len(calls) == 3:
            raise WindowCaptureError("game_minimized")

    capture._validate = validate
    with pytest.raises(WindowCaptureError, match="game_minimized"):
        capture.read(window())
    capture.close()


@pytest.mark.parametrize("exists,minimized,visible,pid,reason", [
    (True, False, True, 1, ""),
    (False, False, True, 1, "game_window_closed"),
    (True, True, True, 1, "game_minimized"),
    (True, False, False, 1, "game_window_hidden"),
    (True, False, True, 2, "game_window_changed"),
])
def test_window_validation_never_requires_chat_focus(monkeypatch, exists, minimized, visible, pid, reason):
    # Deliberately no GetForegroundWindow API: focus is not capture authority.
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        IsWindow=lambda hwnd: exists, IsIconic=lambda hwnd: minimized,
        IsWindowVisible=lambda hwnd: visible,
    ))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (0, pid)))
    if reason:
        with pytest.raises(WindowCaptureError, match=reason):
            assert_capturable_window(window())
    else:
        assert_capturable_window(window())


@pytest.mark.parametrize("border", [False, True])
def test_high_dpi_client_crop_uses_physical_pixels_not_old_virtualized_size(monkeypatch, border):
    import ctypes

    module = importlib.import_module(WindowCaptureSession.__module__)
    monkeypatch.setattr(module, "physical_window_coordinates", nullcontext)
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        GetClientRect=lambda hwnd: (0, 0, 2560, 1600),
        ClientToScreen=lambda hwnd, point: (0, 0),
        GetWindowRect=lambda hwnd: (-8, -31, 2568, 1608) if border else (0, 0, 2560, 1600),
    ))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(dwmapi=SimpleNamespace(DwmGetWindowAttribute=lambda *args: 1)))
    image = Image.new("RGB", (2576, 1639) if border else (2560, 1600), "red")
    stale_virtualized = SimpleNamespace(hwnd=1, width=1706, height=1066, left=0, top=0)
    assert module._client_image(image, stale_virtualized).size == (2560, 1600)


def test_client_geometry_mismatch_is_not_silently_cropped(monkeypatch):
    import ctypes

    module = importlib.import_module(WindowCaptureSession.__module__)
    monkeypatch.setattr(module, "physical_window_coordinates", nullcontext)
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        GetClientRect=lambda hwnd: (0, 0, 2560, 1600), ClientToScreen=lambda hwnd, p: (0, 0),
        GetWindowRect=lambda hwnd: (0, 0, 2560, 1600),
    ))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(dwmapi=SimpleNamespace(DwmGetWindowAttribute=lambda *args: 1)))
    with pytest.raises(WindowCaptureError, match="geometry_mismatch"):
        module._client_image(Image.new("RGB", (1280, 800)), window())


def test_client_rejects_old_desktop_capture_with_update_explanation():
    client = VisionAssistantClient()
    client._get_json = lambda path: {
        "frame_seq": 1, "timestamp": time.time(), "image_base64": base64.b64encode(b"desktop").decode(),
        "capture": {"state": "capturing"}, "game": {"running": True, "foreground": True},
    }
    with pytest.raises(RuntimeError, match="更新独立视觉助手"):
        client.fetch_frame()


def test_observer_never_uses_legacy_desktop_helper_frame(tmp_path, monkeypatch):
    plugin, values, _ = plugin_fixture(tmp_path)
    module = importlib.import_module(type(plugin).__module__)
    plugin._assistant_client = SimpleNamespace(health=lambda: {
        "connected": True, "capture": {"state": "capturing"}, "game": {"running": True, "foreground": True},
    })
    plugin._assistant_frame_seq = 0
    plugin._assistant_process = SimpleNamespace(snapshot=lambda: {})

    async def stop(delay):
        raise asyncio.CancelledError()

    monkeypatch.setattr(module, "asyncio", SimpleNamespace(to_thread=asyncio.to_thread, sleep=stop, CancelledError=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(plugin._observer_loop())
    assert not values.get("last_local_vision")
    assert plugin._observer_status["capture_backend"] == "legacy_desktop_rejected"
    assert "更新" in plugin._observer_status["message"]


def test_capture_code_has_no_desktop_fallback():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in (root / "observer.py", root / "assistant_runtime/server.py", root / "assistant_runtime/window_capture.py"):
        source = path.read_text(encoding="utf-8")
        assert "screen.grab(" not in source
        assert "ImageGrab.grab(" not in source


@pytest.mark.parametrize("unavailable", ["minimized", "closed", "error"])
def test_helper_unavailable_state_clears_in_memory_frame(monkeypatch, unavailable):
    module = importlib.import_module("plugin.plugins.hsr_companion.assistant_runtime.server")
    state = module.CaptureState()
    state.image = b"old-game-frame"
    state.timestamp = time.time()
    state.frame_seq = 5

    def find():
        state.stop.set()
        if unavailable == "error":
            raise RuntimeError("lost_window")
        if unavailable == "closed":
            return None
        return SimpleNamespace(minimized=True, public=lambda: {"minimized": True})

    monkeypatch.setattr(module, "_find_window", find)
    # Run exactly one iteration while preserving the stop event's wait method.
    state.stop.is_set = lambda: False if state.image else True
    state.update()
    assert state.image == b""
    assert state.snapshot(include_image=True)["image_base64"] == ""
