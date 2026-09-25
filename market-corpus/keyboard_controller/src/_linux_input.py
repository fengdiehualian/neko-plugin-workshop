"""Linux (X11) input backend for keyboard_controller.

Implements the same public surface as ``_win32_input`` so the plugin can
dispatch through ``_input_backend.backend`` transparently:

* 键盘：XTest fake key events（键名经 _key_map 的 VK 码 → keysym → keycode）
* 鼠标：XTestFakeMotionEvent / XTestFakeButtonEvent，绝对坐标为全局屏幕像素
* 窗口：EWMH（_NET_CLIENT_LIST/_NET_ACTIVE_WINDOW/_NET_WM_PID…）枚举、聚焦、状态控制
* 剪贴板：优先 wl-copy/wl-paste（Wayland 会话），回退 xclip/xsel

限制：
* 仅支持 X11 会话；Wayland 原生窗口无法注入/枚举（安全模型使然），
  XWayland 应用仍可用。检测不到 DISPLAY 时各操作返回空值并记录日志。
* 不模拟提权检查（Linux 无 UAC 等价物），反作弊黑名单仍然生效。
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Optional

# 接口兼容：与 Windows 后端保持同名符号（显式重导出，供 __init__ 统一引用）
from ._key_map import EXTENDED_KEYS as EXTENDED_KEYS

logger = logging.getLogger(__name__)


# --- 兼容接口 --------------------------------------------------------------------------
def is_windows() -> bool:
    """兼容旧调用名；语义为“当前后端是否支持本机运行”。"""
    return sys.platform.startswith("linux")


def is_supported() -> bool:
    return sys.platform.startswith("linux")


def _wait_seconds(delay: float) -> None:
    if delay and delay > 0:
        time.sleep(min(delay, 30.0))


def _warn(message: str, exc: Exception) -> None:
    logger.debug("{}: {}: {}", message, type(exc).__name__, exc)


INPUT_SAFETY_DENY_MARKERS = (
    "anti-cheat", "anticheat", "easy anti-cheat", "easyanticheat",
    "battleye", "battl-eye", "vanguard", "ricochet", "xigncode",
    "gameguard", "faceit", "equ8", "ace anti",
)

VK_MENU = 0x12


# --- X11 绑定（懒加载）-----------------------------------------------------------------
_x11_lock = threading.Lock()
_dpy = None
_root = 0

_X_LIB_NAMES = ("libX11.so.6", "libX11.so")
_XTST_LIB_NAMES = ("libXtst.so.6", "libXtst.so")
_xlib = None
_xtst = None
_load_err: str = ""

Atom = ctypes.c_ulong
Window = ctypes.c_ulong


class XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p), ("root", Window),
        ("class_", ctypes.c_int), ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_bool), ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_bool), ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_bool), ("screen", ctypes.c_void_p),
    ]


def _load_x11():
    global _xlib, _xtst, _load_err
    if _xlib is not None or _load_err:
        return _xlib is not None
    try:
        for name in _X_LIB_NAMES:
            try:
                _xlib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if _xlib is None:
            raise OSError("libX11 not found")
        for name in _XTST_LIB_NAMES:
            try:
                _xtst = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if _xtst is None:
            raise OSError("libXtst not found")
    except Exception as exc:
        _load_err = f"{type(exc).__name__}: {exc}"
        logger.warning("X11 初始化失败（Linux 输入不可用）：{}", _load_err)
        return False
    return True


def _display():
    """打开并缓存 Display；失败返回 None。"""
    global _dpy, _root, _load_err
    if not os.environ.get("DISPLAY"):
        return None
    if not _load_x11():
        return None
    if _dpy:
        return _dpy
    with _x11_lock:
        if _dpy:
            return _dpy
        _xlib.XOpenDisplay.restype = ctypes.c_void_p
        dpy = _xlib.XOpenDisplay(None)
        if not dpy:
            _load_err = _load_err or "XOpenDisplay failed"
            return None
        _dpy = ctypes.c_void_p(dpy)
        _xlib.XDefaultRootWindow.restype = Window
        _xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        _root = _xlib.XDefaultRootWindow(_dpy)
    return _dpy


def _atom(name: str) -> Atom:
    dpy = _display()
    if not dpy:
        return Atom(0)
    _xlib.XInternAtom.restype = Atom
    _xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_bool]
    return _xlib.XInternAtom(dpy, name.encode(), False)


def _get_window_property(window: int, prop: str, atom_type: str = "CARDINAL"):
    """读取窗口属性，返回 unsigned long 列表（失败/无属性返回 []）。"""
    dpy = _display()
    if not dpy:
        return []
    ret_type = Atom(0)
    ret_fmt = ctypes.c_int(0)
    n_items = ctypes.c_ulong(0)
    bytes_after = ctypes.c_ulong(0)
    data = ctypes.c_void_p()
    _xlib.XGetWindowProperty.restype = ctypes.c_int
    _xlib.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, Window, Atom, ctypes.c_long, ctypes.c_long,
        ctypes.c_bool, Atom, ctypes.POINTER(Atom), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    status = _xlib.XGetWindowProperty(
        dpy, Window(window), _atom(prop), 0, 1024, False,
        Atom(0) if atom_type == "ANY" else _atom(atom_type.upper()) if atom_type != "CARDINAL" else Atom(6),
        ctypes.byref(ret_type), ctypes.byref(ret_fmt), ctypes.byref(n_items),
        ctypes.byref(bytes_after), ctypes.byref(data),
    )
    if status != 0 or not data.value or n_items.value == 0:
        if data.value:
            _xlib.XFree(data)
        return []
    count = int(n_items.value)
    result = [ctypes.cast(data.value + i * 8, ctypes.POINTER(ctypes.c_ulong)).contents.value for i in range(count)]
    _xlib.XFree(data)
    return result


def _send_client_message(window: int, message_type: str, fmt32: list[int], *, subwindow: int = 0) -> bool:
    dpy = _display()
    if not dpy:
        return False

    class XClientMessageEventData(ctypes.Structure):
        _fields_ = [
            ("b", ctypes.c_char * 20),
            ("s", ctypes.c_short * 10),
            ("l", ctypes.c_long * 5),
        ]

    class XClientMessageEvent(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_int), ("serial", ctypes.c_ulong),
            ("send_event", ctypes.c_bool), ("display", ctypes.c_void_p),
            ("window", Window), ("message_type", Atom), ("format", ctypes.c_int),
            ("data", XClientMessageEventData),
        ]

    class XEvent(ctypes.Union):
        _fields_ = [("xclient", XClientMessageEvent), ("pad", ctypes.c_byte * 192)]

    ev = XEvent()
    ev.xclient.type = 33  # ClientMessage
    ev.xclient.send_event = True
    ev.xclient.display = dpy
    ev.xclient.window = Window(subwindow or window)
    ev.xclient.message_type = _atom(message_type)
    ev.xclient.format = 32
    for i, value in enumerate(fmt32[:5]):
        ev.xclient.data.l[i] = int(value)
    _xlib.XSendEvent.restype = ctypes.c_int
    _xlib.XSendEvent.argtypes = [ctypes.c_void_p, Window, ctypes.c_bool, ctypes.c_long, ctypes.POINTER(XEvent)]
    # SubstructureRedirect | SubstructureNotify
    sent = _xlib.XSendEvent(dpy, _root, False, 0x14 | 0x02, ctypes.byref(ev))
    _flush()
    return bool(sent)


def _flush() -> None:
    if _dpy:
        _xlib.XFlush(_dpy)


def _virtual_screen() -> tuple[int, int, int, int]:
    """虚拟屏几何：优先 Xinerama 多屏并集，回退主屏尺寸。"""
    dpy = _display()
    if not dpy:
        return (0, 0, 1, 1)
    try:
        xinerama = ctypes.CDLL("libXinerama.so.1")

        class XineramaScreenInfo(ctypes.Structure):
            _fields_ = [
                ("screen_number", ctypes.c_int),
                ("x_org", ctypes.c_short),
                ("y_org", ctypes.c_short),
                ("width", ctypes.c_short),
                ("height", ctypes.c_short),
            ]

        number = ctypes.c_int(0)
        xinerama.XineramaQueryScreens.restype = ctypes.POINTER(XineramaScreenInfo)
        xinerama.XineramaQueryScreens.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        with _x11_lock:
            infos_ptr = xinerama.XineramaQueryScreens(dpy, ctypes.byref(number))
        count = int(number.value)
        if infos_ptr and count > 0:
            screens = [infos_ptr[i] for i in range(count)]
            min_x = min(s.x_org for s in screens)
            min_y = min(s.y_org for s in screens)
            max_r = max(s.x_org + s.width for s in screens)
            max_b = max(s.y_org + s.height for s in screens)
            return (min_x, min_y, max(1, max_r - min_x), max(1, max_b - min_y))
    except Exception:
        pass
    with _x11_lock:
        _xlib.XDefaultScreen.restype = ctypes.c_int
        _xlib.XDefaultScreen.argtypes = [ctypes.c_void_p]
        screen = _xlib.XDefaultScreen(dpy)
        _xlib.XDisplayWidth.restype = ctypes.c_int
        _xlib.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _xlib.XDisplayHeight.restype = ctypes.c_int
        _xlib.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
        width = max(1, int(_xlib.XDisplayWidth(dpy, screen)))
        height = max(1, int(_xlib.XDisplayHeight(dpy, screen)))
    return (0, 0, width, height)


# --- 窗口枚举 --------------------------------------------------------------------------
def _list_client_windows() -> list[int]:
    return [int(w) for w in _get_window_property(_root, "_NET_CLIENT_LIST")]


def _window_name(window: int) -> str:
    dpy = _display()
    values = _get_window_property(window, "_NET_WM_NAME", "UTF8_STRING".upper())
    raw = b"".join(v.to_bytes(8, "little") for v in values)
    name_bytes = raw.rstrip(b"\x00") if raw else b""
    if not name_bytes:
        if not dpy:
            return ""
        buf = ctypes.c_char_p()
        _xlib.XFetchName.restype = ctypes.c_int
        _xlib.XFetchName.argtypes = [ctypes.c_void_p, Window, ctypes.POINTER(ctypes.c_char_p)]
        if _xlib.XFetchName(dpy, Window(window), ctypes.byref(buf)) and buf.value:
            return buf.value.decode("utf-8", "replace")
        return ""
    return name_bytes.decode("utf-8", "replace")


def _window_pid_of(window: int) -> int:
    pids = _get_window_property(window, "_NET_WM_PID")
    return int(pids[0]) if pids else 0


def _process_name_for_pid(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def enumerate_windows(min_width: int = 120, min_height: int = 80) -> list[dict[str, Any]]:
    """枚举 EWMH 客户端窗口，返回与 win32 后端相同结构的列表。"""
    out: list[dict[str, Any]] = []
    for window in _list_client_windows():
        title = _window_name(window)
        pid = _window_pid_of(window)
        rect = raw_window_rect(window)
        if rect is None:
            continue
        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width < min_width or height < min_height or not title:
            continue
        process_name = _process_name_for_pid(pid)
        out.append({
            "hwnd": int(window),
            "pid": int(pid),
            "title": title,
            "process_name": process_name,
            "width": width,
            "height": height,
        })
    out.sort(key=lambda w: w["title"].lower())
    return out


def find_window_for_pid(pid: int) -> Optional[dict[str, Any]]:
    if pid <= 0:
        return None
    for window in _list_client_windows():
        if _window_pid_of(window) == int(pid):
            title = _window_name(window)
            rect = raw_window_rect(window)
            if rect is None or not title:
                continue
            left, top, right, bottom = rect
            return {
                "hwnd": int(window),
                "pid": int(pid),
                "title": title,
                "process_name": _process_name_for_pid(int(pid)),
                "width": right - left,
                "height": bottom - top,
            }
    return None


def foreground_hwnd() -> int:
    values = _get_window_property(_root, "_NET_ACTIVE_WINDOW")
    return int(values[0]) if values else 0


def foreground_window() -> Optional[dict[str, Any]]:
    hwnd = foreground_hwnd()
    if not hwnd:
        return None
    title = _window_name(hwnd)
    pid = _window_pid_of(hwnd)
    return {
        "hwnd": hwnd,
        "pid": pid,
        "title": title,
        "process_name": _process_name_for_pid(pid),
    }


def foreground_matches(hwnd: int, pid: int) -> bool:
    active = foreground_hwnd()
    if not active or not hwnd:
        return False
    if active == int(hwnd):
        return True
    return pid > 0 and _window_pid_of(active) == int(pid)


def is_window(hwnd: int) -> bool:
    dpy = _display()
    if not dpy or not hwnd:
        return False
    attrs = XWindowAttributes()
    _xlib.XGetWindowAttributes.restype = ctypes.c_int
    _xlib.XGetWindowAttributes.argtypes = [ctypes.c_void_p, Window, ctypes.POINTER(XWindowAttributes)]
    return bool(_xlib.XGetWindowAttributes(dpy, Window(hwnd), ctypes.byref(attrs)))


# --- 几何 -----------------------------------------------------------------------------
def raw_window_rect(window: int) -> Optional[tuple[int, int, int, int]]:
    dpy = _display()
    if not dpy or not window:
        return None
    attrs = XWindowAttributes()
    _xlib.XGetWindowAttributes.restype = ctypes.c_int
    _xlib.XGetWindowAttributes.argtypes = [ctypes.c_void_p, Window, ctypes.POINTER(XWindowAttributes)]
    if not _xlib.XGetWindowAttributes(dpy, Window(window), ctypes.byref(attrs)):
        return None
    x = ctypes.c_int(0)
    y = ctypes.c_int(0)
    child = Window(0)
    _xlib.XTranslateCoordinates.restype = ctypes.c_bool
    _xlib.XTranslateCoordinates.argtypes = [
        ctypes.c_void_p, Window, Window, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(Window),
    ]
    if not _xlib.XTranslateCoordinates(dpy, Window(window), _root, 0, 0, ctypes.byref(x), ctypes.byref(y), ctypes.byref(child)):
        return None
    left, top = int(x.value - attrs.x), int(y.value - attrs.y)
    # attrs.x/y 是相对父窗口的偏移；Translate 给的是窗口原点在根坐标的位置，
    # 直接用 Translate 结果作为左上角。
    left, top = int(x.value), int(y.value)
    return left, top, left + int(attrs.width), top + int(attrs.height)


def window_rect(hwnd: int) -> Optional[dict[str, int]]:
    rect = raw_window_rect(hwnd)
    if rect is None:
        return None
    left, top, right, bottom = rect
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def window_client_rect(hwnd: int) -> Optional[dict[str, int]]:
    rect = window_rect(hwnd)
    if rect is None:
        return None
    # X11 客户区即窗口内容区（无标题栏概念由 WM 装饰承担），与 win32 不同，
    # 这里直接等同窗口矩形，click_in_window 语义保持“相对窗口左上角”。
    return {
        "left": rect["left"], "top": rect["top"],
        "right": rect["right"], "bottom": rect["bottom"],
        "width": rect["right"] - rect["left"],
        "height": rect["bottom"] - rect["top"],
    }


def client_to_screen(hwnd: int, x: int, y: int) -> Optional[tuple[int, int]]:
    rect = window_client_rect(hwnd)
    if rect is None:
        return None
    return rect["left"] + int(x), rect["top"] + int(y)


def move_window(hwnd: int, x: int, y: int, width: int, height: int) -> bool:
    dpy = _display()
    if not dpy or not hwnd:
        return False
    _xlib.XMoveResizeWindow.restype = ctypes.c_int
    _xlib.XMoveResizeWindow.argtypes = [ctypes.c_void_p, Window, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    ok = _xlib.XMoveResizeWindow(dpy, Window(hwnd), int(x), int(y), max(1, int(width)), max(1, int(height)))
    _flush()
    return bool(ok)


_SHOW_STATE_MAP = {
    "minimize": "_NET_WM_STATE_HIDDEN",
    "maximize": "_MAXIMIZED",
    "restore": "_REMOVE",
}


def show_window(hwnd: int, command: str) -> bool:
    act = str(command or "").strip().lower()
    if act == "minimize":
        return _wm_state(hwnd, "_NET_WM_STATE_HIDDEN", True)
    if act == "maximize":
        ok1 = _wm_state(hwnd, "_NET_WM_STATE_MAXIMIZED_VERT", True)
        ok2 = _wm_state(hwnd, "_NET_WM_STATE_MAXIMIZED_HORZ", True)
        return bool(ok1 or ok2)
    if act == "restore":
        _wm_state(hwnd, "_NET_WM_STATE_HIDDEN", False)
        _wm_state(hwnd, "_NET_WM_STATE_MAXIMIZED_VERT", False)
        _wm_state(hwnd, "_NET_WM_STATE_MAXIMIZED_HORZ", False)
        return True
    return False


def _wm_state(hwnd: int, state_prop: str, add: bool) -> bool:
    action = 1 if add else 0  # _NET_WM_STATE_REMOVE=0 ADD=1 TOGGLE=2
    state_atom = _atom(state_prop.replace("_MAXIMIZED", "_NET_WM_STATE_MAXIMIZED"))
    if not state_atom:
        return False
    return _send_client_message(hwnd, "_NET_WM_STATE", [action, int(state_atom), 0, 1])


def enumerate_child_controls(hwnd: int, max_results: int = 100) -> list[dict[str, Any]]:
    """枚举 X11 子窗口（对等 win32 版接口）：WM_CLASS 实例类 / 窗口名 / 相对矩形。

    X11 无统一“控件文本”概念（GTK/Qt 控件需 AT-SPI），此处提供子窗口几何与
    名称信息，供 click_in_window 定位。"""
    dpy = _display()
    if not dpy or not hwnd:
        return []
    limit = max(1, min(500, int(max_results or 100)))
    root = Window(0)
    parent = Window(0)
    children_ptr = ctypes.POINTER(Window)()
    n_children = ctypes.c_uint(0)
    _xlib.XQueryTree.restype = ctypes.c_int
    _xlib.XQueryTree.argtypes = [
        ctypes.c_void_p, Window, ctypes.POINTER(Window),
        ctypes.POINTER(Window), ctypes.POINTER(ctypes.POINTER(Window)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    with _x11_lock:
        if not _xlib.XQueryTree(dpy, Window(hwnd), ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children_ptr), ctypes.byref(n_children)):
            return []
        count = int(n_children.value)
        if count == 0 or not children_ptr:
            return []
        child_ids = [int(children_ptr[i]) for i in range(count)]
        _xlib.XFree(children_ptr)

    results: list[dict[str, Any]] = []
    for child in child_ids[:limit]:
        rect = raw_window_rect(child)
        if rect is None:
            continue
        left, top, right, bottom = rect
        # 相对父窗口坐标
        rel_left = left - (window_rect(hwnd) or {"left": left})["left"]
        rel_top = top - (window_rect(hwnd) or {"top": top})["top"]
        wm_class_raw = b""
        values = _get_window_property(child, "WM_CLASS", "STRING")
        raw = b"".join(v.to_bytes(8, "little") for v in values)
        wm_class_raw = raw.rstrip(b"\x00")
        class_name = wm_class_raw.decode("utf-8", "replace").split("\x00")[0] if wm_class_raw else ""
        results.append({
            "hwnd": child,
            "class_name": class_name,
            "text": _window_name(child),
            "control_id": 0,
            "left": rel_left,
            "top": rel_top,
            "width": right - left,
            "height": bottom - top,
            "center_x": rel_left + (right - left) // 2,
            "center_y": rel_top + (bottom - top) // 2,
        })
    return results


def close_window(hwnd: int) -> bool:
    wm_protocols = _atom("WM_PROTOCOLS")
    delete_atom = _atom("WM_DELETE_WINDOW")
    if not wm_protocols or not delete_atom:
        return False
    return _send_client_message(hwnd, "WM_PROTOCOLS", [int(delete_atom), 0, 0])


def set_topmost(hwnd: int, topmost: bool) -> bool:
    """置顶/取消置顶（_NET_WM_STATE_ABOVE）。"""
    return _wm_state(hwnd, "_NET_WM_STATE_ABOVE", bool(topmost))


# --- 聚焦 ------------------------------------------------------------------------------
def focus_window(hwnd: int, *, attempts: int = 1, retry_delay: float = 0.25) -> bool:
    for attempt in range(max(1, int(attempts or 1))):
        if attempt > 0:
            _wait_seconds(retry_delay)
        if _focus_once(hwnd):
            return True
    return False


def _focus_once(hwnd: int) -> bool:
    target_pid = _window_pid_of(hwnd)
    active = foreground_hwnd()
    if active == int(hwnd):
        return True
    timestamp = _get_server_time()
    sent = _send_client_message(
        hwnd, "_NET_ACTIVE_WINDOW",
        [1, int(timestamp), int(hwnd)],  # 1 = FromTool
        subwindow=_root,
    )
    if not sent:
        dpy = _display()
        if dpy:
            _xlib.XRaiseWindow.restype = ctypes.c_int
            _xlib.XRaiseWindow.argtypes = [ctypes.c_void_p, Window]
            _xlib.XRaiseWindow(dpy, Window(hwnd))
            _xlib.XSetInputFocus.restype = ctypes.c_int
            _xlib.XSetInputFocus.argtypes = [ctypes.c_void_p, Window, ctypes.c_int, ctypes.c_long]
            _xlib.XSetInputFocus(dpy, Window(hwnd), 2, 0)  # RevertToPointerRoot
            _flush()
    _wait_seconds(0.12)
    return foreground_matches(hwnd, target_pid)


_SERVER_TIME_ATOM_READY = False


def _get_server_time() -> int:
    """取服务器时间戳（ PropertyNotify 技巧的简化版：0 亦可被多数 WM 接受）。"""
    return 0


# --- 键盘 -----------------------------------------------------------------------------
def _scan_for_vk(vk: int) -> int:  # 兼容占位：X11 无扫描码概念
    return 0


_KEYSYM_BY_VK = {
    0x08: "BackSpace", 0x09: "Tab", 0x0D: "Return", 0x10: "Shift_L", 0x11: "Control_L",
    0x12: "Alt_L", 0x13: "Pause", 0x14: "Caps_Lock", 0x1B: "Escape", 0x20: "space",
    0x21: "Prior", 0x22: "Next", 0x23: "End", 0x24: "Home",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x2C: "Print", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "Super_L", 0x5C: "Super_R", 0x5F: "Sleep",
    0x60: "KP_0", 0x61: "KP_1", 0x62: "KP_2", 0x63: "KP_3", 0x64: "KP_4",
    0x65: "KP_5", 0x66: "KP_6", 0x67: "KP_7", 0x68: "KP_8", 0x69: "KP_9",
    0x6A: "KP_Multiply", 0x6B: "KP_Add", 0x6D: "KP_Subtract",
    0x6E: "KP_Decimal", 0x6F: "KP_Divide", 0x90: "Num_Lock", 0x91: "Scroll_Lock",
    0xA0: "Shift_L", 0xA1: "Shift_R", 0xA2: "Control_L", 0xA3: "Control_R",
    0xA4: "Alt_L", 0xA5: "Alt_R",
    0xBA: "semicolon", 0xBB: "equal", 0xBC: "comma", 0xBD: "minus",
    0xBE: "period", 0xBF: "slash", 0xC0: "grave",
    0xDB: "bracketleft", 0xDC: "backslash", 0xDD: "bracketright", 0xDE: "apostrophe",
}
for _i in range(24):
    _KEYSYM_BY_VK[0x70 + _i] = f"F{_i + 1}"
for _i in range(10):
    _KEYSYM_BY_VK[0x30 + _i] = chr(ord("0") + _i)
for _i in range(26):
    _KEYSYM_BY_VK[0x41 + _i] = chr(ord("a") + _i)


def _keycode_for_vk(vk: int) -> int:
    dpy = _display()
    if not dpy:
        return 0
    keysym_name = _KEYSYM_BY_VK.get(int(vk))
    if not keysym_name:
        return 0
    _xlib.XStringToKeysym.restype = ctypes.c_ulong
    _xlib.XStringToKeysym.argtypes = [ctypes.c_char_p]
    keysym = _xlib.XStringToKeysym(keysym_name.encode())
    if not keysym:
        return 0
    _xlib.XKeysymToKeycode.restype = ctypes.c_int
    _xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    return int(_xlib.XKeysymToKeycode(dpy, keysym))


def key_down(vk: int) -> None:
    dpy = _display()
    if not dpy:
        return
    keycode = _keycode_for_vk(vk)
    if keycode:
        with _x11_lock:
            _xtst.XTestFakeKeyEvent.restype = ctypes.c_bool
            _xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_bool, ctypes.c_ulong]
            _xtst.XTestFakeKeyEvent(dpy, keycode, True, 0)
            _flush()


def key_up(vk: int) -> None:
    dpy = _display()
    if not dpy:
        return
    keycode = _keycode_for_vk(vk)
    if keycode:
        with _x11_lock:
            _xtst.XTestFakeKeyEvent.restype = ctypes.c_bool
            _xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_bool, ctypes.c_ulong]
            _xtst.XTestFakeKeyEvent(dpy, keycode, False, 0)
            _flush()


def tap_key(vk: int, *, count: int = 1, delay: float = 0.05) -> None:
    for _ in range(max(1, int(count))):
        key_down(vk)
        _wait_seconds(delay)
        key_up(vk)
        _wait_seconds(delay)


def press_combo(modifiers: list[int], main_vk: int, *, delay: float = 0.05) -> None:
    for mod in modifiers:
        key_down(mod)
        _wait_seconds(delay * 0.35)
    tap_key(main_vk, count=1, delay=delay)
    for mod in reversed(modifiers):
        key_up(mod)
        _wait_seconds(delay * 0.35)


def hold_key(spec: str, *, seconds: float = 1.0) -> None:
    from ._key_map import parse_combo

    modifiers, main_vk = parse_combo(spec)
    for mod in modifiers:
        key_down(mod)
    key_down(main_vk)
    _wait_seconds(max(0.05, float(seconds or 0.05)))
    key_up(main_vk)
    for mod in reversed(modifiers):
        key_up(mod)


def type_text(
    text: str,
    *,
    char_delay: float = 0.01,
    use_clipboard: bool = True,
    clipboard_threshold: int = 80,
    retries: int = 2,
) -> bool:
    """Wayland 会话优先 wtype 直输（原生 Unicode）；X11 ASCII 走逐字符
    keysym；其余剪贴板粘贴。"""
    text = str(text or "")
    if not text:
        return True

    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    wtype_exe = shutil.which("wtype") if wayland else None
    if wtype_exe:
        try:
            proc = subprocess.run([wtype_exe, "-d", "10", text], timeout=30.0)
            return proc.returncode == 0
        except Exception as exc:
            _warn("wtype failed, falling back", exc)

    needs_paste = use_clipboard and (
        len(text) >= clipboard_threshold or any(ord(c) > 126 or ord(c) < 32 for c in text)
    )
    if needs_paste:
        previous = get_clipboard_text()
        if not set_clipboard_text(text):
            return False
        press_combo([0x11], ord("V"), delay=max(char_delay, 0.04))
        _wait_seconds(0.2)
        if previous is not None:
            set_clipboard_text(previous)
        return True
    for ch in text:
        vk = ord(ch.upper())
        if ord(ch) > 126:
            return False
        if ch.isupper() or ch in "!@#$%^&*()_+{}|:\"<>?~":
            key_down(0x10)
        key_down(vk)
        key_up(vk)
        if ch.isupper() or ch in "!@#$%^&*()_+{}|:\"<>?~":
            key_up(0x10)
        _wait_seconds(char_delay)
    return True


# --- 鼠标 -----------------------------------------------------------------------------
_BUTTON_MAP = {"left": 1, "right": 3, "middle": 2}


def _fake_button(button: str, press: bool) -> None:
    dpy = _display()
    if not dpy:
        return
    index = _BUTTON_MAP.get(str(button or "left").lower(), 1)
    with _x11_lock:
        _xtst.XTestFakeButtonEvent.restype = ctypes.c_bool
        _xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_bool, ctypes.c_ulong]
        _xtst.XTestFakeButtonEvent(dpy, index, press, 0)
        _flush()


def mouse_move(x: int, y: int) -> None:
    dpy = _display()
    if not dpy:
        return
    with _x11_lock:
        _xtst.XTestFakeMotionEvent.restype = ctypes.c_bool
        _xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        _xtst.XTestFakeMotionEvent(dpy, -1, int(x), int(y), 0)
        _flush()
    _wait_seconds(0.015)


def _mouse_button_down(button: str) -> None:
    _fake_button(button, True)


def _mouse_button_up(button: str) -> None:
    _fake_button(button, False)


def mouse_click(x: int, y: int, *, button: str = "left", clicks: int = 1, click_interval: float = 0.08) -> None:
    mouse_move(x, y)
    clicks = max(1, min(10, int(clicks or 1)))
    for _ in range(clicks):
        _mouse_button_down(button)
        _wait_seconds(0.02)
        _mouse_button_up(button)
        if clicks > 1:
            _wait_seconds(click_interval)
    _wait_seconds(0.03)


def hold_mouse_button(button: str = "left", *, seconds: float = 1.0) -> None:
    button = str(button or "left").lower()
    _mouse_button_down(button)
    try:
        _wait_seconds(max(0.05, float(seconds or 0.05)))
    finally:
        _mouse_button_up(button)
    _wait_seconds(0.04)


def mouse_drag(x1: int, y1: int, x2: int, y2: int, *, button: str = "left", steps: int = 20) -> None:
    steps = max(1, min(200, int(steps)))
    mouse_move(x1, y1)
    _wait_seconds(0.05)
    _mouse_button_down(button)
    try:
        for i in range(1, steps + 1):
            t = i / steps
            eased = t * t * (3.0 - 2.0 * t)
            mouse_move(int(x1 + (x2 - x1) * eased), int(y1 + (y2 - y1) * eased))
            _wait_seconds(0.008)
    finally:
        _mouse_button_up(button)
    _wait_seconds(0.04)


def mouse_wheel(x: int, y: int, *, delta: int = 120) -> None:
    """delta>0 向上滚。X11 按钮 4/5 = 上/下，一格约 120 单位换算成次数。"""
    mouse_move(x, y)
    notches = max(1, min(40, abs(int(delta)) // 120 or 1))
    up = delta > 0
    for _ in range(notches):
        _fake_button("wheel_up" if up else "wheel_down", True)
        _fake_button("wheel_up" if up else "wheel_down", False)
        _wait_seconds(0.02)


def _BUTTON_INDEX(button: str) -> int:
    return {"wheel_up": 4, "wheel_down": 5}.get(button, _BUTTON_MAP.get(button, 1))


# --- 安全边界 --------------------------------------------------------------------------
def _matching_input_safety_deny_marker(*values: str) -> str:
    haystacks = [str(v or "").lower() for v in values]
    for marker in INPUT_SAFETY_DENY_MARKERS:
        if any(marker in h for h in haystacks):
            return marker
    return ""


def input_safety_block_reason(
    *,
    pid: int,
    hwnd: int,
    process_name: str,
    window_title: str,
    block_anti_cheat: bool = True,
    block_elevated: bool = True,
) -> str:
    """Linux 版：仅反作弊黑名单（提权检查不适用）。"""
    if pid <= 0 or not hwnd:
        return "no valid target window"
    if not process_name:
        return "missing target process name"
    if block_anti_cheat:
        marker = _matching_input_safety_deny_marker(process_name, window_title)
        if marker:
            return f"deny marker {marker} (anti-cheat target)"
    return ""


# --- 剪贴板 ---------------------------------------------------------------------------
def _clipboard_cmd_args(read: bool) -> list[str]:
    if bool(os.environ.get("WAYLAND_DISPLAY")):
        if read:
            return ["wl-paste", "--no-newline"]
        return ["wl-copy"]
    if read:
        for cmd in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
            if shutil.which(cmd[0]):
                return cmd
        return ["xclip", "-selection", "clipboard", "-o"]
    for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(cmd[0]):
            return cmd
    return ["xclip", "-selection", "clipboard"]


def get_clipboard_text() -> Optional[str]:
    args = _clipboard_cmd_args(read=True)
    exe = shutil.which(args[0])
    if not exe:
        logger.warning("读取剪贴板失败：缺少 {}（请安装 xclip 或 wl-clipboard）", args[0])
        return None
    try:
        proc = subprocess.run([exe] + args[1:], capture_output=True, timeout=3.0)
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("utf-8", "replace")
    except Exception as exc:
        _warn("clipboard read failed", exc)
        return None


def set_clipboard_text(text: str) -> bool:
    args = _clipboard_cmd_args(read=False)
    exe = shutil.which(args[0])
    if not exe:
        logger.warning("写入剪贴板失败：缺少 {}（请安装 xclip 或 wl-clipboard）", args[0])
        return False
    try:
        proc = subprocess.run([exe] + args[1:], input=str(text or "").encode("utf-8"), timeout=3.0)
        return proc.returncode == 0
    except Exception as exc:
        _warn("clipboard set failed", exc)
        return False


__all__ = [
    "close_window", "client_to_screen", "enumerate_windows", "find_window_for_pid",
    "focus_window", "foreground_hwnd", "foreground_matches", "foreground_window",
    "get_clipboard_text", "hold_key", "hold_mouse_button", "input_safety_block_reason",
    "is_supported", "is_window", "is_windows", "key_down", "key_up", "move_window",
    "mouse_click", "mouse_drag", "mouse_move", "mouse_wheel", "press_combo",
    "raw_window_rect", "set_clipboard_text", "show_window", "tap_key", "type_text",
    "window_client_rect", "window_rect", "_wait_seconds",
]
