"""Unit tests for capture evidence, health contracts and fail-closed fallbacks.

These tests never touch a real game window, dxcam device or host service.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import time
import types
from dataclasses import FrozenInstanceError
from typing import Any

import numpy as np
import pytest
from kards_companion.perception import capture as capture_mod
from kards_companion.perception.capture import (
    ScreenCapture,
    WindowInfo,
    dhash,
    find_kards_window,
    hamming,
    is_kards_process,
)
from kards_companion.runtime.health import (
    CAPTURE_HEALTH_VALUES,
    CaptureHealth,
    CaptureHealthSnapshot,
    evaluate_capture_health,
)
from PIL import Image

PNG_SIZE = (1280, 720)


def make_window(**overrides: Any) -> WindowInfo:
    values: dict[str, Any] = {
        "hwnd": 100,
        "left": 10,
        "top": 20,
        "right": 1290,
        "bottom": 740,
        "pid": 4242,
        "process_name": "kards.exe",
        "title": "KARDS",
        "minimized": False,
        "visible": True,
    }
    values.update(overrides)
    return WindowInfo(**values)


def make_capture(
    *,
    window: WindowInfo | None = None,
    processes: list[tuple[int, str]] | None = None,
    foreground: bool = True,
    foreground_probe: Any = None,
    occlusion: bool | None = False,
    occlusion_probe: Any = None,
    grabber: Any = None,
    **kwargs: Any,
) -> ScreenCapture:
    if window is None and processes is None:
        window = make_window()
    if grabber is None:
        image = Image.new("RGB", PNG_SIZE, (5, 6, 7))

        def grabber(**_kwargs: Any) -> Any:
            return image
    probe = occlusion_probe or (lambda _window, _bbox: occlusion)
    capture = ScreenCapture(
        window_finder=lambda: window,
        process_finder=lambda: list(processes or []),
        foreground_probe=foreground_probe or (lambda _window: foreground),
        occlusion_probe=probe,
        image_grabber=grabber,
        **kwargs,
    )
    # health_snapshot() reports "stopped" unless the capture is running; these
    # tests call capture_frame() directly instead of starting the thread.
    capture._running = True
    capture._ever_started = True
    return capture


@pytest.fixture
def fake_bbox(monkeypatch: pytest.MonkeyPatch) -> tuple[int, int, int, int]:
    bbox = (0, 0, *PNG_SIZE)
    monkeypatch.setattr(capture_mod, "client_bbox", lambda _window: bbox)
    return bbox


def png_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


# ---------------------------------------------------------------- window info

def test_is_kards_process_variants() -> None:
    assert is_kards_process("KARDS.exe")
    assert is_kards_process("kards-win64-shipping.exe")
    assert is_kards_process("kards-win64-shipping-eac.exe")
    assert is_kards_process("kards_beta.exe")
    assert not is_kards_process("notkards.exe")
    assert not is_kards_process("")


def test_window_info_geometry_and_metadata() -> None:
    window = make_window()
    assert window.width == 1280
    assert window.height == 720
    assert window.bbox == (10, 20, 1290, 740)
    assert window.pid == 4242
    assert window.process_name == "kards.exe"
    assert window.title == "KARDS"
    assert window.minimized is False
    assert window.visible is True


def test_find_kards_window_prefers_largest_kards_window() -> None:
    other = make_window(hwnd=1, process_name="explorer.exe", title="Desktop")
    small = make_window(hwnd=2, right=900, bottom=500, pid=10)
    large = make_window(hwnd=3, right=2000, bottom=1200, pid=11)
    assert find_kards_window(windows=[other, small, large]) is large
    assert find_kards_window(windows=[other]) is None


def test_find_kards_window_prefers_visible_over_larger_hidden() -> None:
    hidden_large = make_window(hwnd=4, right=3000, bottom=2000, visible=False)
    visible = make_window(hwnd=5, right=1280, bottom=720, visible=True)
    assert find_kards_window(windows=[hidden_large, visible]) is visible


def test_dhash_and_hamming_identity() -> None:
    image = Image.new("RGB", (32, 32), (1, 2, 3))
    value = dhash(image)
    assert hamming(value, value) == 0
    assert hamming(value, value ^ 0b11) == 2


# ------------------------------------------------------------ capture ownership

def test_start_defaults_to_single_call_driven_producer(fake_bbox) -> None:
    calls: list[dict[str, Any]] = []

    def grabber(**kwargs: Any) -> Image.Image:
        calls.append(kwargs)
        return Image.new("RGB", PNG_SIZE, (1, 2, 3))

    capture = ScreenCapture(
        window_finder=lambda: make_window(),
        process_finder=lambda: [(4242, "kards.exe")],
        foreground_probe=lambda _window: True,
        occlusion_probe=lambda _window, _bbox: False,
        image_grabber=grabber,
    )
    capture._dxcam_failed = True
    capture.start()
    try:
        assert capture.capture_thread_enabled is False
        assert capture._thread is None
        assert capture._running is True
        result = capture.capture_frame()
        assert result is not None
        assert calls == [{"bbox": fake_bbox, "all_screens": True}]
        assert capture.stats["frames"] == 1
    finally:
        capture.stop()
    assert capture._running is False


def test_start_with_capture_thread_enabled_spawns_worker() -> None:
    capture = ScreenCapture(
        capture_thread_enabled=True,
        window_finder=lambda: None,
        process_finder=lambda: [],
        foreground_probe=lambda _window: False,
        image_grabber=lambda **_kwargs: None,
    )
    capture.start()
    try:
        assert capture.capture_thread_enabled is True
        assert capture._thread is not None
        assert capture._thread.is_alive()
        assert capture._running is True
    finally:
        capture.stop()
    assert capture._thread is None
    assert capture._running is False


def test_sync_capture_defers_to_live_background_worker(fake_bbox) -> None:
    class _LiveWorker:
        def is_alive(self) -> bool:
            return True

    capture = make_capture(foreground=True)
    capture._dxcam_failed = True
    result = capture.capture_frame()
    assert result is not None
    assert capture.stats["frames"] == 1

    capture._thread = _LiveWorker()  # type: ignore[assignment]
    assert capture.capture_frame() is result
    # No second grab happened: the worker remains the only capture owner.
    assert capture.stats["frames"] == 1


def test_capture_result_keeps_legacy_positional_shape() -> None:
    image = Image.new("RGB", (10, 10))
    legacy = capture_mod.CaptureResult(
        7, 123.0, 10, 10, "dxcam", image, None, True, "dxcam"
    )
    assert legacy.frame_id == 7
    assert legacy.captured_at == 123.0
    assert legacy.source == "dxcam"
    # New evidence fields are additive and default safely.
    assert legacy.fallback_reason == ""
    assert legacy.trusted is False


# ---------------------------------------------------------------- health rule

def test_evaluate_capture_health_precedence() -> None:
    base = dict(
        process_found=True,
        window_found=True,
        visible=True,
        minimized=False,
        frame_available=True,
        frame_age_ms=10.0,
        max_age_ms=500.0,
        backend="dxcam",
        degraded=False,
    )
    assert evaluate_capture_health(**{**base, "process_found": False}) == "process_not_found"
    assert evaluate_capture_health(**{**base, "window_found": False}) == "window_not_found"
    assert evaluate_capture_health(**{**base, "minimized": True}) == "minimized"
    assert evaluate_capture_health(**{**base, "visible": False}) == "frame_unavailable"
    assert evaluate_capture_health(**{**base, "frame_available": False}) == "frame_unavailable"
    assert evaluate_capture_health(**{**base, "frame_age_ms": 900.0}) == "stale_frame"
    assert evaluate_capture_health(**{**base, "frame_age_ms": None}) == "stale_frame"
    assert evaluate_capture_health(**base) == "capturing"
    assert evaluate_capture_health(**{**base, "backend": "imagegrab"}) == "degraded"
    assert evaluate_capture_health(**{**base, "degraded": True}) == "degraded"


def test_health_snapshot_contains_window_evidence_and_no_occlusion_claims(
    fake_bbox,
) -> None:
    image = Image.new("RGB", PNG_SIZE, (9, 9, 9))
    capture = make_capture(foreground=True, grabber=lambda **_kwargs: image)
    capture._dxcam_failed = True
    result = capture.capture_frame()
    assert result is not None
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.backend == "imagegrab"
    assert health.reading is True
    assert health.frame_id == result.frame_id == 1
    assert health.width == PNG_SIZE[0]
    assert health.height == PNG_SIZE[1]
    assert health.hwnd == 100
    assert health.pid == 4242
    assert health.process_name == "kards.exe"
    assert health.window_title == "KARDS"
    assert health.visible is True
    assert health.minimized is False
    assert health.foreground is True
    assert "graphics_capture_unavailable" in health.reason
    assert health.occluded is False
    assert health.to_dict()["occluded"] is False


def test_health_snapshot_stopped_before_start() -> None:
    capture = ScreenCapture(window_finder=lambda: None, process_finder=lambda: [])
    health = capture.health_snapshot()
    assert health.status == "stopped"
    assert health.reading is False
    assert health.reason == "capture_not_started"
    assert health.hwnd is None and health.pid is None


def test_health_snapshot_process_not_found() -> None:
    capture = make_capture(window=None, processes=[])
    assert capture.capture_frame() is None
    health = capture.health_snapshot()
    assert health.status == "process_not_found"
    assert capture.process_found is False
    assert health.pid is None


def test_health_snapshot_window_not_found() -> None:
    capture = make_capture(window=None, processes=[(77, "kards.exe")])
    assert capture.capture_frame() is None
    health = capture.health_snapshot()
    assert health.status == "window_not_found"
    assert capture.process_found is True
    assert health.pid is None


def test_health_snapshot_minimized_reports_metadata() -> None:
    window = make_window(minimized=True)
    capture = make_capture(window=window)
    assert capture.capture_frame() is None
    health = capture.health_snapshot()
    assert health.status == "minimized"
    assert health.minimized is True
    assert health.hwnd == 100
    assert health.pid == 4242
    assert health.window_title == "KARDS"
    assert health.reading is False


def test_health_snapshot_invisible_window_fails_closed(monkeypatch, fake_bbox) -> None:
    calls: list[dict[str, Any]] = []

    def grabber(**kwargs: Any) -> Image.Image:
        calls.append(kwargs)
        return Image.new("RGB", PNG_SIZE)

    window = make_window(visible=False)
    capture = make_capture(window=window, foreground=True, grabber=grabber)
    assert capture.capture_frame() is None
    assert calls == []
    health = capture.health_snapshot()
    assert health.status == "frame_unavailable"
    assert health.visible is False
    assert health.reason == "window_not_visible"


def test_health_snapshot_host_degraded_reason_is_not_graphics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in capture_mod.HOST_CAPTURE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    capture = make_capture(host_capture_base="http://host.test")
    # Probe the window without capturing: the host bridge is configured but
    # has not been confirmed, and the backend placeholder is still imagegrab.
    capture._probe_window()
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.backend == "imagegrab"
    assert health.reading is False
    assert health.reason == "host_capture_unavailable"
    assert "graphics_capture_unavailable" not in health.reason


def test_health_snapshot_recovered_window_drops_stale_window_reason() -> None:
    capture = make_capture(foreground=True)
    capture._dxcam_failed = True
    capture.capture_reason = "window_minimized"
    capture._probe_window()
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert "window_minimized" not in health.reason
    assert health.reason == "graphics_capture_unavailable"


def test_health_snapshot_stale_fallback_keeps_stale_reason(fake_bbox) -> None:
    capture = make_capture(foreground=True)
    capture._dxcam_failed = True
    result = capture.capture_frame()
    assert result is not None
    health = capture.health_snapshot(now=result.captured_at + 2.0, max_age_ms=500.0)
    # Staleness must not be hidden behind the degraded fallback backend.
    assert health.status == "stale_frame"
    assert health.reading is False
    assert "stale_frame" in health.reason
    assert "graphics_capture_unavailable" in health.reason


def test_fps_update_syncs_frame_age_budget(fake_bbox) -> None:
    capture = make_capture(fps=8.0)
    assert capture.max_frame_age_ms == 500.0

    capture.fps = 2.0
    assert capture.fps == 2.0
    assert capture.max_frame_age_ms == 1500.0

    capture.fps = 0.01  # clamped to the supported minimum
    assert capture.fps == 0.5
    assert capture.max_frame_age_ms == 6000.0


def test_updated_fps_widens_the_health_staleness_budget(fake_bbox) -> None:
    capture = make_capture(foreground=True, fps=8.0)
    capture._dxcam_failed = True
    result = capture.capture_frame()
    assert result is not None

    assert capture.health_snapshot(
        now=result.captured_at + 1.0).status == "stale_frame"

    capture.fps = 2.0
    health = capture.health_snapshot(now=result.captured_at + 1.0)
    assert health.status == "degraded"
    assert health.reading is True


# ------------------------------------------------------- ImageGrab fallback

def test_imagegrab_fallback_requires_foreground(fake_bbox) -> None:
    calls: list[dict[str, Any]] = []

    def grabber(**kwargs: Any) -> Image.Image:
        calls.append(kwargs)
        return Image.new("RGB", PNG_SIZE)

    capture = make_capture(foreground=False, grabber=grabber)
    capture._dxcam_failed = True
    assert capture.capture_frame() is None
    assert calls == []
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.backend == "imagegrab"
    assert health.reading is False
    assert "desktop_requires_foreground" in health.reason


def test_imagegrab_fallback_accepts_foreground_frame(fake_bbox) -> None:
    image = Image.new("RGB", PNG_SIZE, (11, 12, 13))
    calls: list[dict[str, Any]] = []

    def grabber(**kwargs: Any) -> Image.Image:
        calls.append(kwargs)
        return image

    capture = make_capture(foreground=True, grabber=grabber)
    capture._dxcam_failed = True
    result = capture.capture_frame()
    assert result is not None
    assert result.backend == "imagegrab"
    assert result.foreground is True
    assert result.window is not None and result.window.pid == 4242
    assert result.width == PNG_SIZE[0] and result.height == PNG_SIZE[1]
    assert calls == [{"bbox": fake_bbox, "all_screens": True}]
    assert capture.latest_frame() is not None
    assert capture.latest_capture() is result
    assert capture.stats["frames"] == 1
    meta = result.to_frame_meta(reason="foreground_fallback")
    assert meta.valid is True
    assert meta.backend == "imagegrab"
    assert meta.frame_id == result.frame_id
    assert meta.width == PNG_SIZE[0] and meta.height == PNG_SIZE[1]


def test_health_snapshot_fallback_reading_requires_foreground(fake_bbox) -> None:
    capture = make_capture(foreground=True)
    capture._dxcam_failed = True
    result = capture.capture_frame()
    assert result is not None

    fresh = capture.health_snapshot()
    assert fresh.status == "degraded"
    assert fresh.foreground is True
    assert fresh.reading is True

    # The frame is still young, but ImageGrab cannot be trusted once the game
    # is no longer foreground.
    capture.foreground = False
    background = capture.health_snapshot()
    assert background.status == "degraded"
    assert background.foreground is False
    assert background.reading is False


def test_health_snapshot_imagegrab_focus_loss_is_not_reading(fake_bbox) -> None:
    focused = {"value": True}

    def probe(_window: WindowInfo) -> bool:
        return focused["value"]

    capture = make_capture(foreground=True, foreground_probe=probe)
    capture._dxcam_failed = True
    result = capture.capture_frame()
    assert result is not None
    assert capture.health_snapshot(now=result.captured_at + 0.01).reading is True

    # Focus moved to another window after the last capture.  A health poll
    # before the next capture tick must not keep claiming the frame is readable.
    focused["value"] = False
    health = capture.health_snapshot(now=result.captured_at + 0.02)
    assert health.status == "degraded"
    assert health.foreground is False
    assert health.reading is False
    assert capture.foreground is False


# -------------------------------------------------------------------- dxcam

def test_dxcam_uses_correct_create_signature(monkeypatch, fake_bbox) -> None:
    calls: dict[str, Any] = {}

    class FakeCamera:
        def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
            calls["grab_region"] = region
            return np.zeros((PNG_SIZE[1], PNG_SIZE[0], 3), dtype=np.uint8)

        def stop(self) -> None:
            calls["stopped"] = True

        def release(self) -> None:
            calls["released"] = True

    def create(**kwargs: Any) -> FakeCamera:
        calls["create_kwargs"] = kwargs
        return FakeCamera()

    monkeypatch.setitem(sys.modules, "dxcam", types.SimpleNamespace(create=create))
    capture = make_capture(foreground=True)
    result = capture.capture_frame()
    assert result is not None
    assert result.backend == "dxcam"
    assert calls["create_kwargs"] == {"output_color": "RGB"}
    assert "region" not in calls["create_kwargs"]
    assert "target_fps" not in calls["create_kwargs"]
    assert calls["grab_region"] == fake_bbox
    health = capture.health_snapshot()
    assert health.status == "capturing"
    assert health.reading is True
    assert health.reason == ""
    assert health.occluded is False
    assert health.frame_id == result.frame_id


def test_dxcam_stale_frame_is_not_reading(monkeypatch, fake_bbox) -> None:
    class FakeCamera:
        def grab(self, _region: tuple[int, int, int, int]) -> np.ndarray:
            return np.zeros((PNG_SIZE[1], PNG_SIZE[0], 3), dtype=np.uint8)

    monkeypatch.setitem(
        sys.modules, "dxcam", types.SimpleNamespace(create=lambda **_kwargs: FakeCamera())
    )
    capture = make_capture()
    result = capture.capture_frame()
    assert result is not None
    health = capture.health_snapshot(now=result.captured_at + 2.0, max_age_ms=500.0)
    assert health.status == "stale_frame"
    assert health.reading is False
    assert "stale_frame" in health.reason
    assert health.frame_age_ms is not None and health.frame_age_ms >= 1900.0


def test_dxcam_unavailable_degrades_to_imagegrab(monkeypatch, fake_bbox) -> None:
    def create(**_kwargs: Any) -> Any:
        raise RuntimeError("no desktop duplication")

    monkeypatch.setitem(sys.modules, "dxcam", types.SimpleNamespace(create=create))
    capture = make_capture(foreground=True)
    result = capture.capture_frame()
    assert result is not None
    assert result.backend == "imagegrab"
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.reading is True
    assert "graphics_capture_unavailable" in health.reason


# ------------------------------------------------- desktop visibility gate

def test_desktop_requires_foreground_for_dxcam(
    monkeypatch: pytest.MonkeyPatch, fake_bbox
) -> None:
    created: list[Any] = []
    monkeypatch.setitem(
        sys.modules,
        "dxcam",
        types.SimpleNamespace(create=lambda **kwargs: created.append(kwargs)),
    )
    capture = make_capture(foreground=False)
    assert capture.capture_frame() is None
    assert created == []
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.reading is False
    assert "desktop_requires_foreground" in health.reason
    assert capture.occlusion_state == "foreground_required"


@pytest.mark.parametrize(
    ("occlusion", "expected_reason", "state"),
    [
        (True, "desktop_client_occluded", "occluded"),
        (None, "desktop_occlusion_unverified", "unverified"),
    ],
)
def test_desktop_occlusion_blocks_all_desktop_backends(
    occlusion: bool | None,
    expected_reason: str,
    state: str,
    fake_bbox,
) -> None:
    dxcam_calls: list[Any] = []
    grabber_calls: list[Any] = []
    capture = make_capture(
        foreground=True,
        occlusion=occlusion,
        grabber=lambda **kwargs: grabber_calls.append(kwargs)
        or Image.new("RGB", PNG_SIZE),
    )
    assert capture.capture_frame() is None
    assert dxcam_calls == []
    assert grabber_calls == []
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.reading is False
    assert expected_reason in health.reason
    assert capture.occlusion_state == state
    assert health.occluded is False


def test_occlusion_after_success_stops_reading_the_cached_frame(fake_bbox) -> None:
    state = {"occluded": False}
    capture = make_capture(
        foreground=True,
        occlusion_probe=lambda _window, _bbox: state["occluded"],
    )
    capture._dxcam_failed = True
    first = capture.capture_frame()
    assert first is not None
    assert capture.health_snapshot().reading is True

    state["occluded"] = True
    assert capture.capture_frame() is None
    health = capture.health_snapshot(now=first.captured_at + 0.05)
    # The old frame is still young, but a newly discovered overlay means it is
    # no longer a trustworthy reading.
    assert health.status == "degraded"
    assert health.reading is False
    assert "desktop_client_occluded" in health.reason
    assert capture.occlusion_state == "occluded"


def test_dxcam_missing_new_frame_falls_through_without_stale_timestamp(
    monkeypatch: pytest.MonkeyPatch, fake_bbox
) -> None:
    class NoFrameCamera:
        def grab(self, _region: tuple[int, int, int, int]) -> None:
            return None

    monkeypatch.setitem(
        sys.modules, "dxcam", types.SimpleNamespace(create=lambda **_kwargs: NoFrameCamera())
    )
    grabber_calls: list[dict[str, Any]] = []
    image = Image.new("RGB", PNG_SIZE, (61, 62, 63))
    capture = make_capture(
        foreground=True,
        grabber=lambda **kwargs: grabber_calls.append(kwargs) or image,
    )
    started = time.time()
    result = capture.capture_frame()
    assert result is not None
    assert result.source == "imagegrab"
    assert result.backend == "imagegrab"
    assert result.trusted is True
    assert "dxcam_no_frame" in result.fallback_reason
    assert result.captured_at >= started
    assert grabber_calls == [{"bbox": fake_bbox, "all_screens": True}]
    assert result.frame_id == 1
    assert capture.latest_capture() is result
    assert capture.health_snapshot().reading is True


# ---------------------------------------------------- producer worker（无回调）

def test_worker_produces_frames_without_any_callback(fake_bbox) -> None:
    # 录像帧回调链已删除：生产者不再暴露 on_frame，也不再有 JPEG 环形缓冲。
    capture = make_capture(capture_thread_enabled=True)
    capture._dxcam_failed = True
    capture.fps = 20.0
    assert not hasattr(capture, "ring")
    assert not hasattr(capture, "_on_frame")
    capture.start()
    try:
        deadline = time.monotonic() + 2.0
        while capture.stats["frames"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert capture.stats["frames"] >= 2
    finally:
        capture.stop()
    assert "callback_errors" not in capture.stats


def test_worker_stop_retains_latest_capture(fake_bbox) -> None:
    capture = make_capture(capture_thread_enabled=True)
    capture._dxcam_failed = True
    capture.fps = 20.0
    capture.start()
    try:
        deadline = time.monotonic() + 2.0
        while capture.stats["frames"] < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        latest = capture.latest_capture()
        assert latest is not None
        time.sleep(0.08)
    finally:
        capture.stop()
    assert capture._thread is None
    assert capture._running is False
    # Stop must not delete the last frame; perception consumers may still
    # inspect it.
    assert capture.latest_capture() is not None


def test_frame_ids_advance_monotonically_without_callback(fake_bbox) -> None:
    capture = make_capture()
    capture._dxcam_failed = True
    first = capture.capture_frame()
    second = capture.capture_frame()
    assert first is not None and second is not None
    assert first.frame_id == 1 and second.frame_id == 2
    assert capture.stats["frames"] == 2
    assert capture.latest_capture() is second


# -------------------------------------------------------------- host bridge

def test_host_bridge_is_opt_in_via_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in capture_mod.HOST_CAPTURE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert ScreenCapture().host_bridge_enabled is False

    monkeypatch.setenv("NEKO_CAPTURE_API_BASE", "http://host.test")
    capture = ScreenCapture()
    assert capture.host_bridge_enabled is True
    assert capture._host_base == "http://host.test"

    explicit = ScreenCapture(host_capture_base="https://explicit.test/")
    assert explicit._host_base == "https://explicit.test"


def test_http_json_rejects_non_loopback_targets() -> None:
    for url in (
        "http://example.com/api/capture/health",
        "http://192.168.1.10:9999/api/capture/health",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            capture_mod._http_json(url)
    assert capture_mod._loopback_host("127.0.0.1")
    assert capture_mod._loopback_host("localhost")
    assert capture_mod._loopback_host("::1")
    assert not capture_mod._loopback_host("10.0.0.1")
    assert not capture_mod._loopback_host("example.com")


def test_host_bridge_confirmed_frame(fake_bbox, monkeypatch: pytest.MonkeyPatch) -> None:
    image = Image.new("RGB", (640, 360), (21, 22, 23))
    calls: list[tuple[str, dict[str, Any] | None]] = []
    # source_id is opaque and deliberately not equal to the HWND (100).
    source_id = "wgc:session-opaque-7"

    def fake_http(
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 0.25,
    ) -> dict[str, Any]:
        calls.append((url, payload))
        if url.endswith("/api/capture/health"):
            return {"ok": True, "source_id": source_id}
        return {
            "ok": True,
            "source_id": source_id,
            "width": 640,
            "height": 360,
            "image": png_data_url(image),
        }

    monkeypatch.setattr(capture_mod, "_http_json", fake_http)
    capture = make_capture(
        foreground=False, host_capture_base="http://host.test"
    )
    result = capture.capture_frame()
    assert result is not None
    assert result.backend == "host_wgc"
    assert result.width == 640 and result.height == 360
    assert result.source == "host_wgc"
    assert result.trusted is True
    assert result.fallback_reason == ""
    assert capture.occlusion_state == "not_required"
    assert capture._host_source_id == source_id
    assert calls[0][0].endswith("/api/capture/health")
    assert calls[1] == ("http://host.test/api/capture/screenshot", {"target_id": 100})
    health = capture.health_snapshot()
    assert health.status == "capturing"
    assert health.reading is True
    assert health.backend == "host_wgc"
    assert health.occluded is False


def test_host_bridge_failure_safely_falls_through_to_dxcam(
    fake_bbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_http(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("connection refused")

    dxcam_calls: list[Any] = []

    class FakeCamera:
        def grab(self, region: tuple[int, int, int, int]) -> np.ndarray:
            dxcam_calls.append(region)
            return np.zeros((PNG_SIZE[1], PNG_SIZE[0], 3), dtype=np.uint8)

    monkeypatch.setattr(capture_mod, "_http_json", fake_http)
    monkeypatch.setitem(
        sys.modules,
        "dxcam",
        types.SimpleNamespace(create=lambda **kwargs: FakeCamera()),
    )
    grabber_calls: list[dict[str, Any]] = []

    def grabber(**kwargs: Any) -> Image.Image:
        grabber_calls.append(kwargs)
        return Image.new("RGB", PNG_SIZE)

    capture = make_capture(
        foreground=True, grabber=grabber, host_capture_base="http://host.test"
    )
    result = capture.capture_frame()
    assert result is not None
    assert result.source == "dxcam"
    assert result.backend == "dxcam"
    assert result.trusted is True
    assert "host_capture_unavailable" in result.fallback_reason
    assert dxcam_calls == [fake_bbox]
    assert grabber_calls == []
    assert capture.diagnostics()["fallback_reason"].startswith(
        "host_capture_unavailable"
    )
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.reading is True
    assert "host_capture_unavailable" in health.reason


def test_host_bridge_failure_without_foreground_pauses_desktop(
    fake_bbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_http(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(capture_mod, "_http_json", fake_http)
    capture = make_capture(
        foreground=False,
        host_capture_base="http://host.test",
    )
    assert capture.capture_frame() is None
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.reading is False
    assert "host_capture_unavailable" in health.reason
    assert "desktop_requires_foreground" in health.reason


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("not_ok", "host_capture_invalid_response"),
        ("source_mismatch", "host_capture_source_mismatch"),
        ("size_mismatch", "host_capture_size_mismatch"),
        ("bad_image", "host_capture_invalid_image"),
        ("no_size", "host_capture_invalid_size"),
    ],
)
def test_host_bridge_invalid_screenshot_falls_back_degraded(
    case: str,
    expected_reason: str,
    fake_bbox: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (640, 360), (31, 32, 33))
    response: dict[str, Any] = {
        "ok": True,
        "source_id": 100,
        "width": 640,
        "height": 360,
        "image": png_data_url(image),
    }
    if case == "not_ok":
        response["ok"] = False
    elif case == "source_mismatch":
        response["source_id"] = 999
    elif case == "size_mismatch":
        response["width"] = 641
    elif case == "bad_image":
        response["image"] = "data:image/png;base64,not-valid-base64!!!"
    elif case == "no_size":
        response.pop("width")
        response.pop("height")

    def fake_http(
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 0.25,
    ) -> dict[str, Any]:
        if url.endswith("/api/capture/health"):
            return {"ok": True, "source_id": 100}
        return response

    monkeypatch.setattr(capture_mod, "_http_json", fake_http)
    grabber_calls: list[dict[str, Any]] = []

    def grabber(**kwargs: Any) -> Image.Image:
        grabber_calls.append(kwargs)
        return Image.new("RGB", PNG_SIZE)

    capture = make_capture(
        foreground=True, grabber=grabber, host_capture_base="http://host.test"
    )
    capture._dxcam_failed = True
    result = capture.capture_frame()
    assert result is not None
    assert result.source == "imagegrab"
    assert result.trusted is True
    assert expected_reason in result.fallback_reason
    assert grabber_calls == [{"bbox": fake_bbox, "all_screens": True}]
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.reading is True
    assert health.backend == "imagegrab"
    assert expected_reason in health.reason


def test_host_bridge_source_id_is_opaque_but_must_match_health(
    fake_bbox: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = Image.new("RGB", (640, 360), (41, 42, 43))

    def fake_http(
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 0.25,
    ) -> dict[str, Any]:
        if url.endswith("/api/capture/health"):
            return {"ok": True, "source_id": "session-alpha"}
        return {
            "ok": True,
            "source_id": "session-beta",  # different opaque source
            "width": 640,
            "height": 360,
            "image": png_data_url(image),
        }

    monkeypatch.setattr(capture_mod, "_http_json", fake_http)
    capture = make_capture(
        foreground=True, host_capture_base="http://host.test")
    capture._dxcam_failed = True

    result = capture.capture_frame()
    assert result is not None
    assert result.source == "imagegrab"
    assert "host_capture_source_mismatch" in result.fallback_reason
    assert capture._host_source_id == ""
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.reading is True
    assert "host_capture_source_mismatch" in health.reason


def test_host_bridge_health_without_source_id_degrades(
    fake_bbox: tuple[int, int, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_http(
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 0.25,
    ) -> dict[str, Any]:
        assert url.endswith("/api/capture/health")
        return {"ok": True}  # no source_id: association cannot be proven

    monkeypatch.setattr(capture_mod, "_http_json", fake_http)
    capture = make_capture(
        foreground=True, host_capture_base="http://host.test")
    capture._dxcam_failed = True

    result = capture.capture_frame()
    assert result is not None
    assert "host_capture_invalid_response" in result.fallback_reason
    assert capture.host_bridge_confirmed is False
    assert capture._host_source_id == ""
    health = capture.health_snapshot()
    assert health.status == "degraded"
    assert health.reading is True
    assert "host_capture_invalid_response" in health.reason


def test_health_snapshot_public_payload_is_plain() -> None:
    snapshot = CaptureHealthSnapshot(
        status="degraded",
        reason="graphics_capture_unavailable",
        reading=True,
        backend="imagegrab",
        frame_id=3,
        frame_age_ms=12.5,
        width=1280,
        height=720,
        hwnd=100,
        pid=4242,
        process_name="kards.exe",
        window_title="KARDS",
        foreground=True,
        visible=True,
        minimized=False,
        occluded=False,
    )
    payload = snapshot.to_dict()
    assert payload["status"] == "degraded"
    assert payload["hwnd"] == 100
    assert payload["pid"] == 4242
    assert payload["process_name"] == "kards.exe"
    assert payload["window_title"] == "KARDS"
    assert payload["visible"] is True
    assert payload["minimized"] is False
    assert payload["occluded"] is False


# ------------------------------------------------- health state contract

HEALTH_BASE: dict[str, Any] = {
    "process_found": True,
    "window_found": True,
    "visible": True,
    "minimized": False,
    "frame_available": True,
    "frame_age_ms": 10.0,
    "max_age_ms": 500.0,
    "backend": "dxcam",
    "degraded": False,
    "reason": "",
}


def health_call(**overrides: Any) -> str:
    return evaluate_capture_health(**{**HEALTH_BASE, **overrides})


def test_capture_health_status_values_are_a_closed_contract() -> None:
    assert CaptureHealth is str
    assert isinstance(CAPTURE_HEALTH_VALUES, frozenset)
    assert CAPTURE_HEALTH_VALUES == {
        "disabled",
        "process_not_found",
        "window_not_found",
        "minimized",
        "frame_unavailable",
        "stale_frame",
        "capturing",
        "degraded",
        "stopped",
    }


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # terminal lifecycle states are owned by ScreenCapture, not by the
        # evaluator; every evaluator result must be declared.
        ({}, "capturing"),
        ({"process_found": False}, "process_not_found"),
        ({"process_found": False, "window_found": False}, "process_not_found"),
        ({"window_found": False}, "window_not_found"),
        ({"minimized": True}, "minimized"),
        (
            {"minimized": True, "visible": False, "frame_available": False},
            "minimized",
        ),
        ({"visible": False}, "frame_unavailable"),
        ({"frame_available": False}, "frame_unavailable"),
        (
            {"visible": False, "frame_available": False, "degraded": True},
            "frame_unavailable",
        ),
        ({"frame_age_ms": None}, "stale_frame"),
        ({"frame_age_ms": 500.001}, "stale_frame"),
        # boundary: exactly max_age is still fresh (contract uses strictly >).
        ({"frame_age_ms": 500.0}, "capturing"),
        # negative budgets clamp to zero rather than making every frame stale.
        ({"max_age_ms": -1.0, "frame_age_ms": 0.0}, "capturing"),
        ({"max_age_ms": -1.0, "frame_age_ms": 0.001}, "stale_frame"),
        ({"degraded": True}, "degraded"),
        ({"backend": "imagegrab"}, "degraded"),
        ({"backend": "fallback"}, "degraded"),
        ({"backend": "host_wgc"}, "capturing"),
        ({"backend": ""}, "capturing"),
        # staleness beats degradation: an old fallback frame is not readable.
        ({"frame_age_ms": None, "backend": "imagegrab"}, "stale_frame"),
    ],
)
def test_evaluate_capture_health_state_mapping(
    overrides: dict[str, Any], expected: str
) -> None:
    status = health_call(**overrides)
    assert status == expected
    assert status in CAPTURE_HEALTH_VALUES


def test_evaluate_capture_health_never_claims_lifecycle_states() -> None:
    # ``disabled``/``stopped`` come from lifecycle, not the frame evaluator.
    assert health_call() not in {"disabled", "stopped"}
    assert health_call(process_found=False) not in {"disabled", "stopped"}
    assert health_call(frame_age_ms=None) not in {"disabled", "stopped"}


def test_default_health_snapshot_is_stopped_and_json_serializable() -> None:
    snapshot = CaptureHealthSnapshot()
    assert snapshot.status == "stopped"
    assert snapshot.reading is False
    assert snapshot.frame_age_ms is None
    assert snapshot.hwnd is None and snapshot.pid is None

    payload = snapshot.to_dict()
    assert set(payload) == {
        "status",
        "reason",
        "reading",
        "backend",
        "frame_id",
        "frame_age_ms",
        "width",
        "height",
        "hwnd",
        "pid",
        "process_name",
        "window_title",
        "foreground",
        "visible",
        "minimized",
        "occluded",
    }
    assert json.loads(json.dumps(payload)) == payload
    with pytest.raises(FrozenInstanceError):
        snapshot.status = "capturing"  # type: ignore[misc]


def test_health_snapshot_after_stop_reports_stopped_reason(fake_bbox) -> None:
    capture = make_capture()
    assert capture._ever_started is True
    assert capture.capture_frame() is not None
    capture._running = False

    health = capture.health_snapshot()
    assert health.status == "stopped"
    assert health.status in CAPTURE_HEALTH_VALUES
    assert health.reading is False
    assert health.reason == "capture_stopped"
    # Identity evidence stays available for diagnostics after the thread stops.
    assert health.hwnd == 100
    assert health.pid == 4242


def test_health_snapshot_status_never_claims_reading_without_fresh_frame(
    fake_bbox,
) -> None:
    image = Image.new("RGB", PNG_SIZE, (4, 4, 4))
    capture = make_capture(foreground=True, grabber=lambda **_kwargs: image)
    result = capture.capture_frame()
    assert result is not None

    fresh = capture.health_snapshot(max_age_ms=500.0)
    assert fresh.status in {"capturing", "degraded"}
    assert fresh.reading is True

    stale = capture.health_snapshot(
        now=result.captured_at + 1.0, max_age_ms=250.0
    )
    # A degraded backend stays "degraded" in the public status, but the stale
    # frame must still fail the reading gate.
    assert stale.status in {"stale_frame", "degraded"}
    assert stale.status in CAPTURE_HEALTH_VALUES
    assert stale.reading is False
    assert stale.frame_age_ms is not None and stale.frame_age_ms > 250.0
