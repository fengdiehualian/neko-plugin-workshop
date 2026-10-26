"""Loopback-only Star Rail window capture service.

This module intentionally contains no N.E.K.O imports.  It can run through the
host Python as a compatibility helper or be frozen into a separately downloaded
Windows executable.  Frames stay in memory and are never written to disk.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any

if __package__:
    from .window_capture import (
        assert_capturable_window,
        capture_error_message,
        capture_isolated_window,
        close_window_capture,
        physical_window_coordinates,
    )
else:
    from window_capture import (
        assert_capturable_window,
        capture_error_message,
        capture_isolated_window,
        close_window_capture,
        physical_window_coordinates,
    )

PROTOCOL_VERSION = "1"
SERVICE_VERSION = "0.9.3"
PROCESS_NAMES = {"starrail.exe", "honkaistarrail.exe"}
TITLE_TOKENS = ("崩坏：星穹铁道", "崩坏: 星穹铁道", "Honkai: Star Rail")


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    pid: int
    process_name: str
    left: int
    top: int
    width: int
    height: int
    minimized: bool
    foreground: bool

    def public(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("hwnd", None)
        return value


def _is_game(title: str, process_name: str) -> bool:
    process = str(process_name or "").strip().lower()
    lowered_title = str(title or "").strip().lower()
    return process in PROCESS_NAMES or any(token.lower() in lowered_title for token in TITLE_TOKENS)


def _find_window() -> Window | None:
    try:
        import psutil
        import win32gui
        import win32process
    except ImportError as exc:
        raise RuntimeError("missing_window_dependencies") from exc
    foreground = int(win32gui.GetForegroundWindow() or 0)
    candidates: list[tuple[int, Window]] = []

    def visit(hwnd: int, _: object) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = str(win32gui.GetWindowText(hwnd) or "").strip()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                process_name = str(psutil.Process(pid).name() or "")
            except (psutil.Error, OSError):
                process_name = ""
            if not _is_game(title, process_name):
                return
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            screen_left, screen_top = win32gui.ClientToScreen(hwnd, (left, top))
            screen_right, screen_bottom = win32gui.ClientToScreen(hwnd, (right, bottom))
            width = max(0, screen_right - screen_left)
            height = max(0, screen_bottom - screen_top)
            if width < 320 or height < 180:
                return
            value = Window(
                hwnd=int(hwnd),
                title=title,
                pid=int(pid),
                process_name=process_name,
                left=int(screen_left),
                top=int(screen_top),
                width=int(width),
                height=int(height),
                minimized=bool(win32gui.IsIconic(hwnd)),
                foreground=int(hwnd) == foreground,
            )
            score = (100 if process_name.lower() in PROCESS_NAMES else 0) + (20 if value.foreground else 0)
            candidates.append((score, value))
        except Exception:
            return

    with physical_window_coordinates():
        win32gui.EnumWindows(visit, None)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _capture(window: Window, *, timings: dict[str, Any] | None = None, codec: str = "png") -> tuple[bytes, str, float]:
    started = time.monotonic()
    assert_capturable_window(window)
    image, captured_at = capture_isolated_window(window)
    acquired = time.monotonic()
    age_at_read = time.time() - captured_at
    if image.width * image.height > 16_000_000:
        raise RuntimeError("window_capture_resolution_limit")
    output = BytesIO()
    # Loopback transport is not a chat payload. Keep native HUD pixels until
    # the recognizer has cropped them; enlarging a lossy thumbnail is too late.
    # Internal transport is uncompressed RGB24 BMP: exact native pixels, no
    # expensive deflate under OCR/system load. Legacy callers still get PNG.
    bmp_size = ((image.width * 3 + 3) // 4 * 4) * image.height + 54
    actual_codec = "bmp" if codec == "bmp" and bmp_size <= 16 * 1024 * 1024 else "png"
    if actual_codec == "bmp":
        (image if image.mode == "RGB" else image.convert("RGB")).save(output, format="BMP")
    else:
        image.save(output, format="PNG", compress_level=1)
    if output.tell() > 16 * 1024 * 1024:
        raise RuntimeError("window_capture_payload_limit")
    assert_capturable_window(window)
    if timings is not None:
        timings.update(
            native_read_seconds=round(acquired - started, 4),
            encode_seconds=round(time.monotonic() - acquired, 4),
            capture_age_at_read_seconds=round(age_at_read, 4),
            image_bytes=output.tell(),
            source_size=[image.width, image.height],
        )
    return output.getvalue(), "image/bmp" if actual_codec == "bmp" else "image/png", captured_at


class CaptureState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.frame_seq = 0
        self.timestamp = 0.0
        self.image = b""
        self.mime = "image/jpeg"
        self.signature = ""
        self.game: dict[str, Any] = {"running": False}
        self.capture: dict[str, Any] = {"state": "starting"}
        self.supply: dict[str, Any] = {}
        self.stop = threading.Event()

    def update(self) -> None:
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                window = _find_window()
            except Exception as exc:
                with self.lock:
                    self.image = b""
                    self.game = {"running": False}
                    self.capture = {"state": "error", "reason": str(exc)}
                self.stop.wait(3)
                continue
            if window is None:
                with self.lock:
                    self.image = b""
                    self.game = {"running": False}
                    self.capture = {"state": "waiting_game", "reason": "game_window_not_found"}
                self.stop.wait(1.5)
                continue
            if window.minimized:
                with self.lock:
                    self.image = b""
                    self.game = {"running": True, **window.public()}
                    self.capture = {
                        "state": "paused",
                        "reason": "game_minimized",
                    }
                self.stop.wait(1.0)
                continue
            try:
                timings: dict[str, Any] = {"find_window_seconds": round(time.monotonic() - started, 4)}
                image, mime, captured_at = _capture(window, timings=timings, codec="bmp")
                signature = hashlib.sha256(image).hexdigest()[:24]
                timings.update(
                    cycle_work_seconds=round(time.monotonic() - started, 4),
                    frame_age_at_publish_seconds=round(time.time() - captured_at, 4),
                )
                with self.lock:
                    # Sequence counts captures, not different pixel contents.
                    # Two independently captured static frames can confirm OCR.
                    self.frame_seq += 1
                    self.timestamp = captured_at
                    self.image = image
                    self.mime = mime
                    self.signature = signature
                    self.supply = timings
                    self.game = {"running": True, **window.public()}
                    self.capture = {
                        "state": "capturing", "image_stored": False, "frame_semantics": "every_capture",
                        "backend": "windows_graphics_capture", "isolated_window": True,
                        "desktop_overlays_included": False,
                        "focus_required": False,
                        "native_resolution": True,
                    }
            except Exception as exc:
                with self.lock:
                    self.game = {"running": True, **window.public()}
                    self.image = b""
                    self.capture = {
                        "state": "error", "reason": str(exc), "message": capture_error_message(str(exc)),
                        "backend": "windows_graphics_capture", "isolated_window": True,
                        "desktop_overlays_included": False,
                    }
            # Bound the start-to-start cadence, not work time PLUS 750ms.
            # Still request a new native event; never re-stamp static pixels.
            self.stop.wait(max(.01, .75 - (time.monotonic() - started)))

    def frame_bundle(self, *, include_image: bool, since_frame: int = 0) -> tuple[dict[str, Any], bytes]:
        with self.lock:
            include = bool(
                include_image and self.frame_seq > since_frame and self.image
                and self.capture.get("state") == "capturing"
                and self.game.get("running") and not self.game.get("minimized")
                and 0 <= time.time() - self.timestamp <= 15
            )
            payload = {
                "protocol_version": PROTOCOL_VERSION,
                "service_version": SERVICE_VERSION,
                "frame_transport": "binary_bmp_v1",
                "frame_seq": self.frame_seq,
                "timestamp": self.timestamp,
                "mime": self.mime,
                "signature": self.signature,
                "image_base64": "",
                "game": dict(self.game),
                "capture": dict(self.capture),
                "supply": dict(self.supply),
            }
            # bytes are immutable. Take one atomic bundle, then release the
            # publisher lock BEFORE any large encoding or HTTP serialization.
            image = self.image if include else b""
        payload["supply"]["frame_age_at_snapshot_seconds"] = round(time.time() - payload["timestamp"], 4) if payload["timestamp"] else None
        return payload, image

    def snapshot(self, *, include_image: bool, since_frame: int = 0) -> dict[str, Any]:
        payload, image = self.frame_bundle(include_image=include_image, since_frame=since_frame)
        if image and payload["mime"] == "image/bmp":
            from PIL import Image
            output = BytesIO()
            with Image.open(BytesIO(image)) as native:
                native.save(output, format="PNG", compress_level=1)
            image = output.getvalue()
            payload["mime"] = "image/png"
            if len(image) > 16 * 1024 * 1024:
                image = b""  # Do not exceed the legacy protocol's limit.
        payload["image_base64"] = base64.b64encode(image).decode("ascii") if image else ""
        return payload


def _handler(state: CaptureState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/health":
                payload = state.snapshot(include_image=False)
            elif parsed.path in {"/api/frame", "/api/frame.bin"}:
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    since = max(0, int((query.get("since_frame") or ["0"])[0]))
                except ValueError:
                    since = 0
                if parsed.path == "/api/frame.bin":
                    payload, image = state.frame_bundle(include_image=True, since_frame=since)
                    payload.pop("image_base64", None)
                    payload["image_bytes_length"] = len(image)
                    metadata = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                    if len(metadata) > 65536:
                        self.send_error(500)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.neko.hsr-frame")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(4 + len(metadata) + len(image)))
                    self.end_headers()
                    # No Base64/JSON image, no second giant concatenated copy.
                    self.wfile.write(struct.pack("!I", len(metadata)))
                    self.wfile.write(metadata)
                    self.wfile.write(image)
                    return
                payload = state.snapshot(include_image=True, since_frame=since)
            else:
                self.send_error(404)
                return
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8219)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--self-test", action="store_true", help="Check packaged imports and lossless pixels without capture or a server.")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=True))
        return
    port = min(65535, max(1024, int(args.port)))
    state = CaptureState()
    # Bind before starting capture. A port conflict must not leave an orphan
    # native session/worker in a failed helper startup.
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(state))
    worker = threading.Thread(target=state.update, name="hsr-window-capture", daemon=True)
    worker.start()
    if args.parent_pid > 0:
        threading.Thread(
            target=_watch_parent, args=(server, state.stop, args.parent_pid),
            name="hsr-plugin-parent-watch", daemon=True,
        ).start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        state.stop.set()
        server.server_close()
        worker.join(timeout=2)
        close_window_capture()


def self_test() -> dict[str, Any]:
    """No window acquisition, socket binding, image files or host imports."""
    import psutil
    import win32gui
    import win32process
    import windows_capture
    from PIL import Image

    image = Image.new("RGB", (2560, 1600), (17, 29, 41))
    image.putpixel((2445, 777), (255, 254, 253))
    for codec in ("PNG", "BMP"):
        encoded = BytesIO()
        image.save(encoded, format=codec)
        with Image.open(BytesIO(encoded.getvalue())) as restored:
            assert restored.size == image.size and restored.tobytes() == image.tobytes()
    assert all((psutil, win32gui, win32process, windows_capture))
    return {"service_version": SERVICE_VERSION, "protocol_version": PROTOCOL_VERSION,
            "native_lossless_roundtrip": True, "window_capture_import": True,
            "server_started": False, "image_stored": False}


def _watch_parent(server: ThreadingHTTPServer, stop: threading.Event, parent_pid: int) -> None:
    """Stop capture if a host kills/replaces the owning plugin process."""
    import psutil

    try:
        parent = psutil.Process(parent_pid)
    except psutil.NoSuchProcess:
        server.shutdown()
        return
    while not stop.wait(1):
        if not parent.is_running():
            server.shutdown()
            return


if __name__ == "__main__":
    main()
