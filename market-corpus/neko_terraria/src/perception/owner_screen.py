"""主人窗口截图源：猫娘"看到"主人看到的游戏画面。

定位主玩家窗口（标题含泰拉瑞亚/Terraria/tModLoader，且不是 AI 客户端进程），
用 PIL ImageGrab 截取窗口屏幕区域 → JPEG → base64。DX 渲染的游戏窗口用
GDI PrintWindow 会黑屏，ImageGrab（屏幕拷贝）对可见窗口可靠。

失败（窗口找不到/遮挡/无 PIL）返回 None，调用方降级到 mod 截图。
"""

import base64
import io
import time
from typing import Optional, Tuple

_AI_PID_CACHE: Optional[int] = None
_AI_PID_CACHE_TS: float = 0.0
# PID 缓存 TTL：AI 客户端重启（launcher 新进程）后 PID 会变，
# 缓存永不过期会一直指向旧进程 → 主人窗口识别失败。30s 重查一次。
_PID_CACHE_TTL = 30.0


def _ai_client_pid() -> Optional[int]:
    """AI 客户端进程 PID（launcher 启动的 subprocess）。"""
    global _AI_PID_CACHE, _AI_PID_CACHE_TS
    now = time.time()
    if _AI_PID_CACHE is not None and now - _AI_PID_CACHE_TS < _PID_CACHE_TTL:
        return _AI_PID_CACHE
    _AI_PID_CACHE = None
    try:
        import psutil
        # 找命令行含 -savedirectory 且指向插件 mod 目录的进程
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(proc.info["cmdline"] or [])
            except Exception:
                continue
            if "-savedirectory" in cmd and "neko_terraria" in cmd:
                _AI_PID_CACHE = proc.info["pid"]
                _AI_PID_CACHE_TS = now
                return _AI_PID_CACHE
    except Exception:
        pass
    return None


def _find_owner_hwnd() -> Optional[int]:
    """枚举可见窗口，找主玩家游戏窗口（标题匹配且不是 AI 客户端进程）。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    ai_pid = _ai_client_pid()
    found = [None]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value
        if not any(k in title for k in ("泰拉瑞亚", "Terraria", "tModLoader")):
            return True
        # 排除 AI 客户端进程的窗口
        if ai_pid is not None:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == ai_pid:
                return True
        # 排除控制台/服务器窗口
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            import psutil
            name = psutil.Process(pid.value).name().lower()
            if "cmd" in name or "console" in name or "server" in name:
                return True
        except Exception:
            pass
        found[0] = hwnd
        return False

    user32.EnumWindows(_enum_cb, 0)
    return found[0]


def _window_bbox(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """窗口在屏幕上的矩形（排除最小化）。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    if right - left < 200 or bottom - top < 150:
        return None  # 过小：可能是残留标题栏
    return left, top, right, bottom


async def owner_window_frame() -> Optional[Tuple[str, str]]:
    """截取主玩家窗口画面 → (b64: str, mime: str)。失败返回 None。"""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None

    try:
        hwnd = _find_owner_hwnd()
        if hwnd is None:
            return None
        bbox = _window_bbox(hwnd)
        if bbox is None:
            return None
        img = ImageGrab.grab(bbox=bbox, all_screens=False)
        if img is None:
            return None
        if img.mode != "RGB":
            img = img.convert("RGB")
        # 长边压缩到 1024，控制传输体积
        w, h = img.size
        longest = max(w, h)
        if longest > 1024:
            scale = 1024 / longest
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                             __import__("PIL").Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"
    except Exception:
        return None
