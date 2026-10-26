"""civ6_buddy win32 兼容层（v0.4.11）。

插件进程运行在 N.E.K.O 打包环境（resources\\bin）下，**没有 pywin32**
（win32gui/win32api/win32con/win32process 均不可用）。本模块用 ctypes 调用
user32/kernel32 提供同名的 win32gui / win32api / win32con / win32process，
API 与 pywin32 常用调用兼容（civ6_ops 的窗口查找/前置逻辑无需改动）。

覆盖：
- win32gui.IsWindowVisible / GetWindowText / EnumWindows / GetWindowRect
  / ShowWindow / GetForegroundWindow / SetForegroundWindow / BringWindowToTop
- win32api.GetCurrentThreadId
- win32process.GetWindowThreadProcessId / AttachThreadInput
- win32con.SW_RESTORE 等常量
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ── user32 签名 ──
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL

_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL

_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int

_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int

_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL

_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL

_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND

_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL

_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.restype = wintypes.BOOL

_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
_user32.AttachThreadInput.restype = wintypes.BOOL

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# ── 键鼠（SetCursorPos / mouse_event / keybd_event）──
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL

_user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
_user32.mouse_event.restype = None

_user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
_user32.keybd_event.restype = None

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
KEYEVENTF_KEYUP = 0x0002


class BITMAPINFOHEADER(ctypes.Structure):
    """标准 BITMAPINFOHEADER（ctypes.wintypes 未提供，PrintWindow/GetDIBits 需要）。"""

    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def mouse_click_abs(screen_x: int, screen_y: int) -> None:
    """绝对坐标左键单击（无需 pyautogui）。"""
    _user32.SetCursorPos(int(screen_x), int(screen_y))
    _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)


def press_key(vk: int) -> None:
    """虚拟键码按键（无需 pyautogui）。"""
    _user32.keybd_event(vk, 0, 0, None)
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)


# ── 屏幕区域截图（BitBlt，无需 pyautogui/PIL）──
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
_gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.SelectObject.restype = ctypes.c_void_p
_gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_gdi32.BitBlt.restype = wintypes.BOOL
_gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                             ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT]
_gdi32.GetDIBits.restype = ctypes.c_int
_gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi32.DeleteObject.restype = wintypes.BOOL

_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = ctypes.c_void_p
_user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
_user32.ReleaseDC.restype = ctypes.c_int

SRCCOPY = 0x00CC0020


def screen_region_rgb(left: int, top: int, right: int, bottom: int):
    """截取屏幕区域 → RGB numpy（纯 ctypes BitBlt，无 PIL/pyautogui 依赖）。"""
    import numpy as np

    w = int(right) - int(left)
    h = int(bottom) - int(top)
    if w < 2 or h < 2:
        raise ValueError(f"屏幕区域尺寸异常 {w}x{h}")
    screen_dc = _user32.GetDC(0)
    if not screen_dc:
        raise OSError("GetDC(NULL) 失败")
    try:
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
        if not mem_dc:
            raise OSError("CreateCompatibleDC 失败")
        try:
            bmp = _gdi32.CreateCompatibleBitmap(screen_dc, w, h)
            if not bmp:
                raise OSError("CreateCompatibleBitmap 失败")
            try:
                _gdi32.SelectObject(mem_dc, bmp)
                if not _gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, int(left), int(top), SRCCOPY):
                    raise OSError("BitBlt 失败")
                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = w
                bmi.biHeight = -h
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0
                buf = ctypes.create_string_buffer(w * h * 4)
                if not _gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0):
                    raise OSError("GetDIBits 失败")
                arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape(h, w, 4)
                return np.ascontiguousarray(arr[:, :, [2, 1, 0]], dtype=np.uint8)  # BGRA→RGB
            finally:
                _gdi32.DeleteObject(bmp)
        finally:
            _gdi32.DeleteDC(mem_dc)
    finally:
        _user32.ReleaseDC(0, screen_dc)


class win32gui:
    """win32gui 的 ctypes 等价实现。"""

    @staticmethod
    def IsWindowVisible(hwnd: int) -> bool:
        return bool(_user32.IsWindowVisible(hwnd))

    @staticmethod
    def GetWindowText(hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    @staticmethod
    def EnumWindows(callback, lParam) -> None:
        """枚举所有顶层窗口。callback(hwnd, lParam) 每次调用后返回 None/True 即继续
        （与 pywin32 语义一致：回调返回 0 才停止；ctypes 回调返回 None 会被转成 0，
        因此包装为恒 True）。"""
        @WNDENUMPROC
        def _wrap(hwnd, lparam):  # noqa: ANN001
            try:
                callback(hwnd, lparam)
            except Exception:
                pass
            return True
        _user32.EnumWindows(_wrap, lParam or 0)

    @staticmethod
    def GetWindowRect(hwnd: int) -> tuple[int, int, int, int]:
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (rect.left, rect.top, rect.right, rect.bottom)

    @staticmethod
    def ShowWindow(hwnd: int, cmdShow: int) -> bool:
        return bool(_user32.ShowWindow(hwnd, cmdShow))

    @staticmethod
    def GetForegroundWindow() -> int:
        return _user32.GetForegroundWindow()

    @staticmethod
    def SetForegroundWindow(hwnd: int) -> bool:
        return bool(_user32.SetForegroundWindow(hwnd))

    @staticmethod
    def BringWindowToTop(hwnd: int) -> bool:
        return bool(_user32.BringWindowToTop(hwnd))


class win32api:
    """win32api 的 ctypes 等价实现（civ6_ops 用到的子集）。"""

    @staticmethod
    def GetCurrentThreadId() -> int:
        return int(_kernel32.GetCurrentThreadId())


class win32process:
    """win32process 的 ctypes 等价实现（civ6_ops 用到的子集）。"""

    @staticmethod
    def GetWindowThreadProcessId(hwnd: int) -> tuple[int, int]:
        pid = wintypes.DWORD()
        tid = _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (int(tid), int(pid.value))

    @staticmethod
    def AttachThreadInput(idAttach: int, idAttachTo: int, fAttach: bool) -> bool:
        return bool(_user32.AttachThreadInput(idAttach, idAttachTo, fAttach))


class win32con:
    """win32con 常量（civ6_ops 用到的子集）。"""

    SW_RESTORE = 9
    SW_SHOWNORMAL = 1
    SW_MINIMIZE = 6
    SW_MAXIMIZE = 3
