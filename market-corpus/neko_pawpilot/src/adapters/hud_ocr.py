"""HUD OCR 感知：屏幕截取 + 宿主 RapidOCR 识别。

**为什么不用 mss / ImageGrab**（2026-10 实地核查）：
- 宿主运行时没装 `mss`（`resources/bin` 下无此包）→ 旧版第一行就 return False，
  所以"识屏"从来没工作过；
- `dxcam` 只残留一个 `processor/_numpy_kernels.pyd`，没有包本体；
- 宿主是 PyInstaller 冻结包（`resources/bin/asyncio/tasks.py` 这类路径只在归档里），
  `PIL` 目录下只有 .pyd 扩展、没有 .py，`PIL.ImageGrab` 这种"没人 import 过"的
  子模块很可能没被打进归档。

所以截屏以**纯 ctypes 调 Windows GDI BitBlt** 为准（标准库，必然可用），
mss / Pillow.ImageGrab 只作为更快的备选先试。宿主 RapidOCR 后端还强制要求
插件自报 `plugin_id`，旧版没传 → `__init__` 直接 TypeError（第二个死因）。

**2026-10 又一处实地翻车：截的是整个虚拟桌面，不是游戏画面。**
主人把插件面板/DSH 桌面端开着玩游戏，全屏截图的文字全被读进来——
面板「教程」页里有"隧道"字样（知识库里就有），于是推出「正在进入隧道」；
别处一句带"雨"的字，就推「确认在下雨」。他车停在车库没动，听到这些就是假话。
所以现在：**只在前台窗口是游戏时、只截游戏窗口客户区**，否则这一次识屏直接跳过。
"""

from __future__ import annotations

import importlib.util
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_ID = "neko_pawpilot"

# 游戏窗口标题（小写子串匹配）。配置里可覆盖：ocr_regions.json → game_window_titles
GAME_TITLES_DEFAULT = (
    "euro truck simulator 2",
    "eurotrucks2",
    "american truck simulator",
    "amtrucks",
)

_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0
# GetSystemMetrics 索引：虚拟屏左上角与尺寸、主屏尺寸
_SM_CXSCREEN = 0
_SM_CYSCREEN = 1
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


def _ensure_dpi_aware() -> None:
    """DPI 感知：不做的话在缩放屏上拿到的坐标会被系统缩放、截出来的图也是模糊的。"""
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:                       # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:                       # noqa: BLE001
        pass


def foreground_window() -> Tuple[int, str]:
    """当前前台窗口 (hwnd, 标题)；拿不到返回 (0, "")。"""
    try:
        import ctypes
        _ensure_dpi_aware()
        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return 0, ""
        length = int(user32.GetWindowTextLengthW(hwnd))
        buf = ctypes.create_unicode_buffer(max(length + 2, 260))
        user32.GetWindowTextW(hwnd, buf, len(buf))
        return hwnd, (buf.value or "")
    except Exception:                       # noqa: BLE001
        return 0, ""


def window_client_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """窗口客户区在屏幕上的 (left, top, width, height)；失败返回 None。

    用客户区而不是整窗，是为了排除标题栏/边框。全屏模式下就等于整屏。
    """
    if not hwnd:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        _ensure_dpi_aware()
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        w = int(rect.right - rect.left)
        h = int(rect.bottom - rect.top)
        if w <= 0 or h <= 0:
            return None
        return (int(pt.x), int(pt.y), w, h)
    except Exception:                       # noqa: BLE001
        return None


def _grab_gdi(region: Optional[Tuple[int, int, int, int]] = None) -> Any:
    """纯 ctypes 的 Windows GDI 截屏，返回 PIL.Image（无第三方依赖）。"""
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    _ensure_dpi_aware()

    if region:
        x, y, w, h = (int(region[0]), int(region[1]), int(region[2]), int(region[3]))
    else:
        x = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if w <= 0 or h <= 0:                # 拿不到虚拟屏就退回主屏
            x = y = 0
            w = user32.GetSystemMetrics(_SM_CXSCREEN)
            h = user32.GetSystemMetrics(_SM_CYSCREEN)
    if w <= 0 or h <= 0:
        raise RuntimeError("拿不到屏幕尺寸")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER),
                    ("bmiColors", wintypes.DWORD * 3)]

    hwnd = user32.GetDesktopWindow()
    src_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(src_dc)
    bmp = gdi32.CreateCompatibleBitmap(src_dc, w, h)
    old_obj = gdi32.SelectObject(mem_dc, bmp)
    try:
        gdi32.BitBlt(mem_dc, 0, 0, w, h, src_dc, x, y, _SRCCOPY)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h          # 负高度 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0      # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mem_dc, bmp, 0, h, buf,
                              ctypes.byref(bmi), _DIB_RGB_COLORS)
        if not got:
            raise RuntimeError("GetDIBits 失败")
        # GDI 给的是 BGRX，交给 Pillow 自己按 raw 解
        return Image.frombytes("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(mem_dc, old_obj)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, src_dc)


class HudOcr:
    """截屏（GDI / mss / Pillow）+ 宿主 RapidOCR 识别屏幕文本。

    对外只推荐 `read_game_text()`：它自带"前台是不是游戏"的判定，
    游戏不在前台就一个字都不读——否则读到的会是我们自己的面板和桌面。
    """

    def __init__(self, plugin: Any, game_titles: Optional[Any] = None) -> None:
        self.plugin = plugin
        self.game_titles: Tuple[str, ...] = tuple(
            str(t).strip().lower() for t in (game_titles or GAME_TITLES_DEFAULT) if str(t).strip()
        ) or GAME_TITLES_DEFAULT
        self._backend: Optional[Any] = None
        self._sct = None
        self._capture_kind: str = ""
        self._error: Optional[str] = None
        self._ok = False
        self._last_fg: str = ""            # 最近一次看到的前台窗口标题
        self._last_note: str = ""          # 最近一次截屏的说明（面板/自检要看）
        self._last_game_hwnd: int = 0       # 上次确认是游戏的窗口（手动拍照回退用）

    # ── 前台窗口判定 ──
    def game_foreground(self) -> Tuple[bool, str]:
        """当前前台窗口是不是游戏。返回 (是否游戏, 标题)。"""
        _hwnd, title = foreground_window()
        self._last_fg = title
        low = title.lower()
        return (bool(low) and any(k in low for k in self.game_titles)), title

    # ── 后端选择 ──
    def _capture_order(self) -> List[str]:
        """按优先级给出可尝试的截屏后端；gdi 兜底（标准库，必然可用）。"""
        order: List[str] = []
        if self._sct is not None:
            order.append("mss")
        if importlib.util.find_spec("PIL.ImageGrab") is not None:
            order.append("pillow")
        order.append("gdi")
        return order

    def _try_init_mss(self) -> None:
        if self._sct is not None or importlib.util.find_spec("mss") is None:
            return
        try:
            import mss
            # 新版 mss 用 MSS，旧版是 mss()（直接取 .mss 会触发弃用警告）
            factory = getattr(mss, "MSS", None)
            if factory is None:
                factory = mss.mss
            self._sct = factory()
        except Exception as exc:            # noqa: BLE001
            self._error = f"mss 初始化失败: {exc}"

    def _ensure(self) -> bool:
        if self._backend is not None:
            return self._ok
        self._try_init_mss()
        try:
            from plugin.plugins._shared.rapidocr import RapidOcrBackend
            self._backend = RapidOcrBackend(
                install_target_dir_raw=str(self.plugin.data_path("rapidocr_models")),
                engine_type="onnxruntime",
                lang_type="ch",
                model_type="mobile",
                ocr_version="PP-OCRv4",
                plugin_id=PLUGIN_ID,
            )
            self._ok = True
            self._error = ""
            return True
        except Exception as exc:            # noqa: BLE001
            self._error = str(exc)
            self._ok = False
            return False

    def is_available(self) -> bool:
        """是否真的能用（真的构造一次后端，而不是只查能不能 import）。"""
        return self._ensure()

    # ── 截屏 ──
    def _grab(self, kind: str, region: Optional[Tuple[int, int, int, int]]) -> Any:
        if kind == "mss":
            from PIL import Image
            if region:
                monitor = {"left": region[0], "top": region[1],
                           "width": region[2], "height": region[3]}
            else:
                monitor = self._sct.monitors[1]
            shot = self._sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.rgb)
        if kind == "pillow":
            from PIL import ImageGrab
            bbox = None
            if region:
                bbox = (region[0], region[1],
                        region[0] + region[2], region[1] + region[3])
            return ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
        return _grab_gdi(region)

    def capture(self, region: Optional[Tuple[int, int, int, int]] = None) -> Optional[Any]:
        """截取屏幕（region: left,top,width,height；空 = 全屏）。逐个后端降级。"""
        if not self._ensure():
            return None
        for kind in self._capture_order():
            try:
                img = self._grab(kind, region)
                if img is not None:
                    self._capture_kind = kind
                    return img
            except Exception as exc:        # noqa: BLE001
                self._error = f"{kind} 截屏失败: {exc}"
                if kind == "mss":           # mss 句柄坏了就换后端
                    self._sct = None
        return None

    def read_text(self, region: Optional[Tuple[int, int, int, int]] = None) -> str:
        """识别指定区域文本；空区域 = 全屏。

        注意：空区域是**整个虚拟桌面**（多屏都算），只适合调试；
        正常识屏走 `read_game_text()`，它只截游戏窗口。
        """
        if not self._ensure():
            return ""
        try:
            img = self.capture(region)
            if img is None:
                return ""
            return self._backend.extract_text(img) or ""
        except Exception as exc:            # noqa: BLE001
            self._error = str(exc)
            return ""

    # ── 游戏画面（唯一推荐的识屏入口）──
    def capture_game(self, allow_background: bool = False) -> Tuple[Optional[Any], str]:
        """只截游戏窗口客户区。游戏不在前台 → (None, 原因)。

        allow_background：手动点「拍一张」时前台窗口会变成面板自己，
        这时按**上次记住的游戏窗口**坐标截（游戏还显示在屏幕上就没问题）。
        自动识屏永远走 False —— 绝不能在他没看游戏时凭记忆截屏播报。
        """
        ok, title = self.game_foreground()
        hwnd = 0
        if ok:
            hwnd, title = foreground_window()
            self._last_game_hwnd = hwnd
        elif allow_background and self._last_game_hwnd:
            hwnd = self._last_game_hwnd
            title = f"{title}（按上次记住的游戏窗口回退）"
        else:
            self._last_note = f"前台窗口不是游戏（{title or '未知'}），这次不截"
            return None, self._last_note
        region = window_client_rect(hwnd)
        if region:
            self._last_note = f"游戏窗口 {title}｜客户区 {region[2]}x{region[3]}"
        else:
            self._last_note = f"游戏窗口 {title}｜拿不到客户区，退回全屏"
        return self.capture(region), self._last_note

    def read_game_text(self) -> str:
        """读游戏画面文字。游戏不在前台 / 后端不可用 → 返回空串（不猜）。"""
        if not self._ensure():
            self._last_note = f"OCR 后端不可用：{self._error or '未知'}"
            return ""
        img, note = self.capture_game()
        if img is None:
            return ""
        try:
            return self._backend.extract_text(img) or ""
        except Exception as exc:            # noqa: BLE001
            self._error = str(exc)
            self._last_note = f"{note}｜识别失败：{exc}"
            return ""

    def snapshot(self) -> Dict[str, Any]:
        ok = self._ensure()
        return {"available": ok, "capture": self._capture_kind,
                "error": self._error or "",
                "foreground": self._last_fg,
                "note": self._last_note}
