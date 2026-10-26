"""Window-only capture using windows-capture (MIT), never desktop pixels.

The HWND is the game window selected by the caller, not a monitor or a title
substring. No client injection, key events, overlay hiding or disk images.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any


class WindowCaptureError(RuntimeError):
    pass


@contextmanager
def physical_window_coordinates():
    """Use physical pixels on this worker thread, without changing N.E.K.O."""
    import ctypes

    set_context = ctypes.windll.user32.SetThreadDpiAwarenessContext
    set_context.restype = ctypes.c_void_p
    previous = set_context(ctypes.c_void_p(-4))
    try:
        yield
    finally:
        if previous:
            set_context(ctypes.c_void_p(previous))


def assert_capturable_window(window: Any) -> None:
    import win32gui
    import win32process

    if not win32gui.IsWindow(window.hwnd):
        raise WindowCaptureError("game_window_closed")
    if int(win32process.GetWindowThreadProcessId(window.hwnd)[1]) != window.pid:
        raise WindowCaptureError("game_window_changed")
    if win32gui.IsIconic(window.hwnd):
        raise WindowCaptureError("game_minimized")
    if not win32gui.IsWindowVisible(window.hwnd):
        raise WindowCaptureError("game_window_hidden")
    # WGC targets this HWND's surface, not whichever application has focus.
    # Chatting with N.E.K.O must not invalidate an otherwise live game frame.


def _client_image(image: Any, window: Any) -> Any:
    """Crop WGC's window surface to its client, without guessing offsets."""
    import ctypes
    from ctypes import wintypes

    import win32gui

    # Read again in physical pixels. At 150% scaling, a pre-capture window
    # snapshot can be 1706x1066 while WGC returns 2560x1600. Cropping using that
    # virtualized size would silently cut off the entire right-side HUD.
    with physical_window_coordinates():
        client_left, client_top, client_right, client_bottom = win32gui.GetClientRect(window.hwnd)
        screen_left, screen_top = win32gui.ClientToScreen(window.hwnd, (client_left, client_top))
        width, height = client_right - client_left, client_bottom - client_top
        rects = [win32gui.GetWindowRect(window.hwnd)]
    if image.size == (width, height):
        return image
    rect = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(window.hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
        rects.append((rect.left, rect.top, rect.right, rect.bottom))
    for left, top, right, bottom in rects:
        if image.size != (right - left, bottom - top):
            continue
        x, y = screen_left - left, screen_top - top
        if x >= 0 and y >= 0 and x + width <= image.width and y + height <= image.height:
            return image.crop((x, y, x + width, y + height))
    raise WindowCaptureError("window_client_geometry_mismatch")


class WindowCaptureSession:
    """Retain a native WGC session; each read requires a new native frame."""

    def __init__(self, *, factory: Any = None, validate: Any = None, crop: Any = None):
        self._factory = factory
        self._validate = validate or assert_capturable_window
        self._crop = crop or _client_image
        self._condition = threading.Condition()
        self._control: Any = None
        self._capture: Any = None
        self._target: tuple[int, int] | None = None
        self._image: Any = None
        self._seq = 0
        self._delivered = 0
        self._captured_at = 0.0
        self._error = ""
        self._generation = 0

    def close(self) -> None:
        with self._condition:
            control = self._control
            self._control = None
            self._capture = None
            self._target = None
            self._image = None
            self._generation += 1
            self._condition.notify_all()
        if control is not None:
            control.stop()

    def _start(self, window: Any) -> None:
        self.close()
        factory = self._factory
        if factory is None:
            try:
                from windows_capture import WindowsCapture
            except ImportError as exc:
                raise WindowCaptureError("window_capture_component_missing") from exc
            factory = WindowsCapture
        self._target = (window.hwnd, window.pid)
        self._error = ""
        self._seq = self._delivered = 0
        generation = self._generation
        capture = factory(
            window_hwnd=window.hwnd, window_name=None, monitor_index=None,
            cursor_capture=False, draw_border=False, secondary_window=False,
            minimum_update_interval=500,
        )

        @capture.event
        def on_frame_arrived(frame: Any, capture_control: Any) -> None:
            arrived_at = time.time()
            from PIL import Image

            try:
                self._validate(window)
                # Own a copy before leaving the native callback/mapped buffer.
                image = Image.frombytes("RGB", (frame.width, frame.height), frame.frame_buffer.tobytes(), "raw", "BGRX")
                image = self._crop(image, window)
                if max(high for _, high in image.getextrema()) <= 4:
                    raise WindowCaptureError("window_capture_black_frame")
                with self._condition:
                    if generation != self._generation:
                        return
                    self._image = image
                    # Includes conversion/crop/lock delays, never renews age
                    # merely because Python finally published the native event.
                    self._captured_at = arrived_at
                    self._seq += 1
                    self._error = ""
                    self._condition.notify_all()
            except Exception as exc:
                with self._condition:
                    if generation == self._generation:
                        self._image = None
                        self._error = str(exc) if isinstance(exc, WindowCaptureError) else "window_frame_conversion_failed"
                        self._condition.notify_all()

        @capture.event
        def on_closed() -> None:
            with self._condition:
                if generation == self._generation:
                    self._image = None
                    self._error = "window_capture_closed"
                    self._condition.notify_all()

        self._capture = capture
        try:
            self._control = capture.start_free_threaded()
        except Exception as exc:
            self.close()
            raise WindowCaptureError("window_capture_start_failed") from exc

    def read(self, window: Any, *, timeout: float = 4.0) -> tuple[Any, float]:
        self._validate(window)
        if self._target != (window.hwnd, window.pid) or self._control is None or self._control.is_finished():
            self._start(window)
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._seq <= self._delivered or self._image is None:
                if self._error:
                    raise WindowCaptureError(self._error)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WindowCaptureError("window_capture_no_new_frame")
                self._condition.wait(remaining)
            if not 0 <= time.time() - self._captured_at <= 5:
                raise WindowCaptureError("window_capture_stale_frame")
            self._validate(window)
            self._delivered = self._seq
            image, captured_at, generation = self._image, self._captured_at, self._generation
        # Callback publishes a new PIL object, never mutates this one. A local
        # strong reference is enough to copy without blocking newest-frame supply.
        copied = image.copy()
        self._validate(window)
        with self._condition:
            if generation != self._generation or self._error:
                raise WindowCaptureError(self._error or "window_capture_closed")
        if not 0 <= time.time() - captured_at <= 5:
            raise WindowCaptureError("window_capture_stale_frame")
        return copied, captured_at


_session: WindowCaptureSession | None = None


def capture_isolated_window(window: Any) -> tuple[Any, float]:
    global _session
    if _session is None:
        _session = WindowCaptureSession()
    return _session.read(window)


def close_window_capture() -> None:
    global _session
    session, _session = _session, None
    if session is not None:
        session.close()


def capture_error_message(reason: str) -> str:
    return {
        "window_capture_component_missing": "窗口画面组件尚未安装，请更新独立视觉助手；不会改用包含猫娘的桌面截图。",
        "window_capture_start_failed": "无法启动独立窗口采集，请检查系统录屏权限或切换星铁的窗口显示模式后重试。",
        "window_capture_black_frame": "窗口采集得到黑屏，当前无法识别；请切换星铁的窗口显示模式后重试。",
        "window_capture_no_new_frame": "暂未收到新的游戏窗口画面，正在重试。",
        "window_capture_stale_frame": "游戏窗口画面已过期，正在重新采集。",
        "window_client_geometry_mismatch": "游戏窗口尺寸正在变化，等待画面稳定后重试。",
        "window_capture_closed": "游戏窗口采集已结束，正在重新连接。",
        "game_minimized": "星铁已最小化，恢复游戏窗口后会自动继续观察。",
        "game_window_hidden": "星铁窗口已隐藏，恢复显示后会自动继续观察。",
        "game_window_closed": "星铁窗口已关闭，正在等待游戏。",
        "game_window_changed": "星铁窗口已变化，正在重新连接。",
        "window_capture_resolution_limit": "游戏画面分辨率过高，当前助手无法处理，请降低窗口分辨率。",
        "window_capture_payload_limit": "游戏画面数据超过本地处理限制，当前无法识别。",
        "game_not_foreground": "当前助手仍要求游戏处于前台，请更新独立视觉助手。",
    }.get(reason, "无法读取独立游戏窗口画面，正在重试；不会改用包含猫娘的桌面截图。")
