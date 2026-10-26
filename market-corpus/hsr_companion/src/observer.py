"""Privacy-bounded observation of the Honkai: Star Rail window.

The observer deliberately captures only the detected game window surface.
Frames stay in memory and are never written to disk.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Any

from .assistant_runtime.window_capture import (
    WindowCaptureError,
    capture_error_message,
    capture_isolated_window,
    physical_window_coordinates,
)

HSR_PROCESS_NAMES = {"starrail.exe", "honkaistarrail.exe"}
HSR_TITLE_TOKENS = ("崩坏：星穹铁道", "崩坏: 星穹铁道", "Honkai: Star Rail")


@dataclass(frozen=True)
class GameWindow:
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


def is_hsr_window(*, title: str, process_name: str) -> bool:
    """Return True only for a known HSR process or an explicit game title."""

    normalized_process = str(process_name or "").strip().lower()
    normalized_title = str(title or "").strip().lower()
    return normalized_process in HSR_PROCESS_NAMES or any(
        token.lower() in normalized_title for token in HSR_TITLE_TOKENS
    )


def frame_signature(image: Any, *, width: int = 32, height: int = 18) -> bytes:
    """Build a tiny luminance signature used only for in-memory deduplication."""

    from PIL import Image

    resampling = getattr(Image, "Resampling", Image).BILINEAR
    return image.convert("L").resize((width, height), resampling).tobytes()


def frame_difference(previous: bytes | None, current: bytes) -> float:
    if not previous or len(previous) != len(current) or not current:
        return 1.0
    return sum(abs(a - b) for a, b in zip(previous, current)) / (255.0 * len(current))


def find_hsr_window() -> GameWindow | None:
    """Find the best visible top-level Star Rail window on Windows."""

    try:
        import psutil
        import win32gui
        import win32process
    except ImportError as exc:
        raise RuntimeError("备用识别缺少窗口读取组件，请下载并启动独立视觉助手。") from exc

    candidates: list[tuple[int, GameWindow]] = []
    foreground_hwnd = int(win32gui.GetForegroundWindow() or 0)

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
            if not is_hsr_window(title=title, process_name=process_name):
                return
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            screen_left, screen_top = win32gui.ClientToScreen(hwnd, (left, top))
            screen_right, screen_bottom = win32gui.ClientToScreen(hwnd, (right, bottom))
            width = max(0, screen_right - screen_left)
            height = max(0, screen_bottom - screen_top)
            if width < 320 or height < 180:
                return
            minimized = bool(win32gui.IsIconic(hwnd))
            foreground = int(hwnd) == foreground_hwnd
            window = GameWindow(
                hwnd=int(hwnd),
                title=title,
                pid=int(pid),
                process_name=process_name,
                left=int(screen_left),
                top=int(screen_top),
                width=int(width),
                height=int(height),
                minimized=minimized,
                foreground=foreground,
            )
            score = (
                (100 if process_name.lower() in HSR_PROCESS_NAMES else 0)
                + (20 if foreground else 0)
                + min(width * height // 100_000, 10)
            )
            candidates.append((score, window))
        except Exception:
            return

    with physical_window_coordinates():
        win32gui.EnumWindows(visit, None)
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def capture_game_window(window: GameWindow) -> tuple[bytes, str, bytes]:
    """Capture only the game client, independently of chat focus."""

    try:
        image, _ = capture_isolated_window(window)
    except WindowCaptureError as exc:
        raise RuntimeError(capture_error_message(str(exc))) from exc

    signature = frame_signature(image)
    if image.width * image.height > 16_000_000:
        raise RuntimeError("游戏窗口分辨率超过当前本地识别限制")
    output = BytesIO()
    image.save(output, format="PNG", compress_level=1)
    payload = output.getvalue()
    if len(payload) > 16 * 1024 * 1024:
        raise RuntimeError("游戏窗口画面超过当前本地识别限制")
    return payload, "image/png", signature


__all__ = [
    "GameWindow",
    "capture_game_window",
    "find_hsr_window",
    "frame_difference",
    "frame_signature",
    "is_hsr_window",
]
