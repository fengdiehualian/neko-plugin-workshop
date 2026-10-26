"""screen_reader — 屏幕捕获与 OCR 实现（Windows 桌面）。

全部功能优先使用标准库 + PIL/win32gui（已安装）；Tesseract-OCR 为可选引擎，
缺失时 OCR 返回降级提示（截图仍可用，由视觉能力直接查看）。
"""

from __future__ import annotations

import datetime as _dt
import os
import threading

try:
    from PIL import ImageGrab
    _HAS_PIL = True
except Exception:  # pragma: no cover - 环境无 PIL 时降级
    _HAS_PIL = False

try:
    import win32gui
    import win32process
    _HAS_WIN32 = True
except Exception:  # pragma: no cover
    _HAS_WIN32 = False

try:
    import pytesseract
    _TESSERACT = True
except Exception:  # pragma: no cover
    _TESSERACT = False

try:
    import shutil
    _TESS_EXE = bool(shutil.which("tesseract"))
except Exception:
    _TESS_EXE = False

_SAVE_ROOT = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                         "N.E.K.O", "screen_reader")
_LOCK = threading.Lock()
_LAST_SIZE = (0, 0)

MAX_CAPTURE_PIXELS = 40_000_000  # 超大屏保底上限（约 8000×5000）
SAVE_SUBDIR = "captures"


def init_save_dir() -> str:
    d = os.path.join(_SAVE_ROOT, SAVE_SUBDIR)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.join(os.environ.get("TEMP", "."), "neko_screen_reader", SAVE_SUBDIR)
        os.makedirs(d, exist_ok=True)
    return d


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def last_size() -> str:
    return f"{_LAST_SIZE[0]}×{_LAST_SIZE[1]}"


def _save_png(img, save_dir: str) -> str | None:
    if img is None:
        return None
    w, h = img.size
    if w * h > MAX_CAPTURE_PIXELS:
        return None
    global _LAST_SIZE
    _LAST_SIZE = (w, h)
    path = os.path.join(save_dir, f"screen_{_timestamp()}.png")
    try:
        img.save(path, "PNG")
    except OSError:
        return None
    return path


# ══════════ 窗口信息 ══════════


def get_foreground_window() -> dict | None:
    """返回前台窗口信息：标题/进程/PID/矩形/宽高；无可识别窗口返回 None。"""
    if not _HAS_WIN32:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd).strip()
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = max(right - left, 0)
        height = max(bottom - top, 0)
        if width <= 0 or height <= 0:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = ""
        try:
            import psutil
            process = psutil.Process(pid).name()
        except Exception:
            process = f"pid{pid}"
        return {
            "hwnd": hwnd, "title": title or "(无标题)", "pid": pid, "process": process,
            "left": left, "top": top, "width": width, "height": height,
        }
    except Exception:
        return None


# ══════════ 截屏 ══════════


def capture_full(save_dir: str, region: str = "") -> str | None:
    """全屏截图；region 可选 'x,y,w,h' 像素区域。"""
    if not _HAS_PIL:
        return None
    try:
        if region:
            parts = [int(p) for p in region.replace("，", ",").split(",")]
            if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
                img = ImageGrab.grab(bbox=tuple(parts))
            else:
                img = ImageGrab.grab()
        else:
            img = ImageGrab.grab()
        return _save_png(img, save_dir)
    except Exception:
        return None


def capture_window(info: dict, save_dir: str) -> str | None:
    """按前台窗口矩形截图。"""
    if not _HAS_PIL:
        return None
    try:
        img = ImageGrab.grab(bbox=(info["left"], info["top"],
                                   info["left"] + info["width"],
                                   info["top"] + info["height"]))
        return _save_png(img, save_dir)
    except Exception:
        return None


# ══════════ OCR（可选引擎） ══════════


def ocr_image(path: str) -> str | None:
    """对图片做 OCR；引擎缺失返回 None（调用方降级提示）。"""
    if not _TESSERACT or not _TESS_EXE:
        return None
    try:
        return pytesseract.image_to_string(path, lang="chi_sim+eng", timeout=30)
    except Exception:
        try:
            return pytesseract.image_to_string(path, timeout=30)
        except Exception:
            return None


# ══════════ 供测试的纯逻辑 ══════════


def _save_virtual_image(img, save_dir: str) -> str | None:
    """把已构造的 PIL 图片存盘（测试用，不触屏）。"""
    return _save_png(img, save_dir)
