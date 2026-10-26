"""主动抓取目标游戏窗口画面 → 裸 base64（喂 OcrEngine）。

降级链 PrintWindow -> mss -> 宿主抓屏桥。全程内存、不落盘；绝不记录任何图像或
OCR 内容（隐私红线）。第三方依赖(mss/PIL/win32gui/psutil)一律函数内 lazy import，
顶层只用 stdlib —— 本模块不在红线扫描名单里，但保持 ocr_engine.py 的既有惯例。
"""
from __future__ import annotations

import base64
import io
import threading
from dataclasses import dataclass
from typing import Any, Optional, Tuple

BLACK_FRAME_LUMA_THRESHOLD = 12.0

# 拍板 2.0.69：mss 全局锁——mss.mss() 官方文档明确非线程安全（grab 会改共享内部缓冲）。
# 真机 2.0.68 暴露：2 worker 并发抓屏时 mss 内部死锁，_ocr_in_flight_count 永远 = workers，
# 后续所有 tick 被 worker 闸静默挡掉 → OCR 完全停滞。所有 mss.mss() 调用都必须走 _MSS_LOCK。
# mss_lock 是模块级同步原语；只保护 mss.mss() 这一段，不影响 PIL/bridge。
# 拍板 2.0.73：改 RLock——非 reentrant Lock 在 capture_active_frame → _capture_mss 嵌套调用时
# 双重 acquire 导致 worker 永久挂起（真机 2.0.72 暴露：3 分钟 body_exit 冻结、watchdog 救不了）。
# watchdog 触发 cf_future.cancel() 对运行中任务返回 False 不抛、只标 cancelled——worker 仍卡死。
# 唯一修法：让 _MSS_LOCK 可重入。
_MSS_LOCK = threading.RLock()

# 本地游戏进程/标题（拍板 2.0.14：YuanShen/GenshinImpact/StarRail/ZenlessZoneZero）
_GAME_EXE = {
    "yuanshen.exe", "genshinimpact.exe", "starrail.exe", "zenlesszonezero.exe",
}
_GAME_TITLE_HINTS = (
    "原神", "genshin", "崩坏：星穹铁道", "崩坏星穹铁道", "starrail", "绝区零", "zenless",
)


@dataclass(frozen=True)
class CaptureResult:
    ok: bool
    image_base64: Optional[str]  # 裸 base64，绝不带 "data:" 前缀
    width: int
    height: int
    method: str  # "printwindow" | "mss" | "bridge" | ""
    error: str  # "" | "no_window" | "black_frame" | "capture_failed" | ...
    avg_luma: float  # 0-255 平均亮度，未知填 -1.0


def _img_luma(img: Any) -> float:
    """PIL Image -> 平均亮度(0-255)。失败返回 -1.0。"""
    try:
        gray = img.convert("L").resize((32, 32))
        px = list(gray.getdata())
        return float(sum(px)) / float(max(1, len(px)))
    except Exception:
        return -1.0


def _encode(img: Any, fmt: str = "JPEG") -> Tuple[str, int, int]:
    """PIL Image -> (裸 base64, width, height)。"""
    try:
        buf = io.BytesIO()
        if fmt == "JPEG" and img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format=fmt, quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii"), int(img.width), int(img.height)
    except Exception:
        return "", 0, 0


def _avg_luma_from_b64(image_base64: str) -> float:
    """裸 base64 图 -> 平均亮度。解码失败返回 -1.0。"""
    try:
        from PIL import Image  # lazy: 第三方

        raw = base64.b64decode(image_base64)
        img = Image.open(io.BytesIO(raw))
        return _img_luma(img)
    except Exception:
        return -1.0


def is_black_frame(image_base64: str, ocr_len: Optional[int] = None) -> bool:
    """黑帧判定。

    * 给了 ``ocr_len``（严格 D6 判据）：0 个字即判黑，非 0 判非黑，覆盖亮度。
    * 未给 ``ocr_len``：平均亮度 < BLACK_FRAME_LUMA_THRESHOLD 判黑。
    """
    if ocr_len is not None:
        return int(ocr_len) == 0
    return _avg_luma_from_b64(image_base64) < BLACK_FRAME_LUMA_THRESHOLD


def _process_name(pid: Any) -> str:
    """pid -> 小写进程名（如 'yuanshen.exe'）。失败返回 ''。"""
    try:
        import psutil  # lazy: 第三方

        return (psutil.Process(pid).name() or "").lower()
    except Exception:
        pass
    try:
        import win32process  # lazy: 第三方

        return (win32process.GetModuleFileNameEx(pid, 0) or "").lower()
    except Exception:
        return ""


def find_genshin_window() -> Optional[dict]:
    """找本地游戏窗口（原神/星铁/绝区零；进程名优先，标题兜底）。返回 {hwnd,pid,title,rect} 或 None。"""
    try:
        import win32gui  # lazy: 第三方
    except Exception:
        return None

    found: dict = {}

    def _cb(hwnd: Any, _extra: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            if not title:
                return True
            _tid, pid = win32gui.GetWindowThreadProcessId(hwnd)
            name = _process_name(pid)
            is_gen = (name in _GAME_EXE) or any(h in title.lower() for h in _GAME_TITLE_HINTS)
            if is_gen and not found:
                rect = win32gui.GetWindowRect(hwnd)
                found.update({"hwnd": hwnd, "pid": pid, "title": title, "rect": tuple(rect)})
            return True
        except Exception:
            return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return None
    return dict(found) if found else None


def _enum_large_visible_windows() -> list[dict]:
    """枚举所有可见顶层窗口（rect ≥ 500x400，过滤任务栏/小工具/桌面 helper），按面积降序。

    拍板 2.0.15 兜底用：GetForegroundWindow 在 worker 线程里可能返回 0（前台在别的
    session / 调用线程没附着到前台输入队列），这时退而求其次抓屏幕上最大的可见窗口
    —— 云游戏跑 Edge 全屏时 GetWindowText 可能为空但 rect 占满主屏。
    """
    try:
        import win32gui  # lazy: 第三方
    except Exception:
        return []
    out: list[dict] = []

    def _cb(hwnd: Any, _extra: Any) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            rect = win32gui.GetWindowRect(hwnd)
            if not rect:
                return True
            left, top_, right, bottom = rect
            w = int(right) - int(left)
            h_ = int(bottom) - int(top_)
            if w < 500 or h_ < 400:
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            _tid, pid = win32gui.GetWindowThreadProcessId(hwnd)
            out.append({
                "hwnd": hwnd, "pid": pid, "title": title,
                "rect": tuple(rect), "area": w * h_,
            })
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    out.sort(key=lambda x: x["area"], reverse=True)
    return out


def find_foreground_window() -> Optional[dict]:
    """找前台窗口；GetForegroundWindow 拿不到 → 降级到最大可见窗口（云游戏跑 Edge）。

    返回 {hwnd, pid, title, rect} 或 None（连一个像样窗口都没有）。
    """
    try:
        import win32gui  # lazy: 第三方
    except Exception:
        return None

    # 路径 1：直接问 shell 谁是前台（worker 线程也可能拿到，但常常返回 0）
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            try:
                title = win32gui.GetWindowText(hwnd) or ""
                _tid, pid = win32gui.GetWindowThreadProcessId(hwnd)
                rect = win32gui.GetWindowRect(hwnd)
                left, top_, right, bottom = (rect or (0, 0, 0, 0))
                if int(right) > int(left) and int(bottom) > int(top_):
                    return {"hwnd": hwnd, "pid": pid, "title": title, "rect": tuple(rect)}
            except Exception:
                pass
    except Exception:
        pass

    # 路径 2：EnumWindows 取最大可见窗口（≥500x400，跳过任务栏/桌面 helper）
    candidates = _enum_large_visible_windows()
    if candidates:
        top = candidates[0]
        return {"hwnd": top["hwnd"], "pid": top["pid"], "title": top["title"], "rect": top["rect"]}
    return None


def _capture_printwindow(hwnd: Any, rect: Any) -> CaptureResult:
    """ctypes GDI PrintWindow 抓被遮挡窗口自身内容。"""
    try:
        import ctypes
        from ctypes import wintypes

        from PIL import Image  # lazy: 第三方

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        pw_renderfullcontent = 2
        left, top, right, bottom = (rect or (0, 0, 0, 0))
        w = max(1, int(right) - int(left))
        h = max(1, int(bottom) - int(top))

        hwnd_dc = user32.GetWindowDC(hwnd)
        mfc_dc = gdi32.CreateCompatibleDC(hwnd_dc)
        bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        old = gdi32.SelectObject(mfc_dc, bitmap)
        ok = user32.PrintWindow(hwnd, mfc_dc, pw_renderfullcontent)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mfc_dc, bitmap, 0, h, buf, ctypes.byref(bmi), 0)
        img = Image.frombytes("RGBA", (w, h), bytes(buf), "raw", "BGRA").convert("RGB")

        gdi32.SelectObject(mfc_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mfc_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)

        luma = _img_luma(img)
        b64, ww, hh = _encode(img)
        good = bool(ok) and ww > 0
        return CaptureResult(good, b64 if good else None, ww, hh, "printwindow",
                             "" if good else "capture_failed", luma if good else -1.0)
    except Exception as exc:
        return CaptureResult(False, None, 0, 0, "printwindow", f"error:{type(exc).__name__}", -1.0)


def _capture_mss(rect: Any) -> CaptureResult:
    """mss 抓屏幕矩形（顶层窗口内容，被遮挡时是遮挡者）。"""
    try:
        import mss  # lazy: 第三方
        from PIL import Image  # lazy: 第三方

        left, top, right, bottom = (rect or (0, 0, 0, 0))
        mon = {
            "left": int(left), "top": int(top),
            "width": max(1, int(right) - int(left)), "height": max(1, int(bottom) - int(top)),
        }
        # 拍板 2.0.69：mss.mss() 非线程安全——mss 全局锁内串行 grab
        with _MSS_LOCK, mss.mss() as sct:
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.rgb)
        luma = _img_luma(img)
        b64, ww, hh = _encode(img)
        good = ww > 0
        return CaptureResult(good, b64 if good else None, ww, hh, "mss",
                             "" if good else "capture_failed", luma if good else -1.0)
    except Exception as exc:
        return CaptureResult(False, None, 0, 0, "mss", f"error:{type(exc).__name__}", -1.0)


def grab_primary_for_dhash() -> str:
    """拍板 2.0.69：mss 全局锁内抓主屏 → 9x8 灰度 dHash。

    集中 mss.mss() 调用点（之前 _light_capture_dhash 在 __init__.py 里 importlib 调用
    mss 不走 screen_capture 模块——绕过了本模块新加的 _MSS_LOCK）。改用本函数：
    ① 走 _MSS_LOCK 防并发死锁
    ② 失败/缺依赖 → 返回空串（与旧 _light_capture_dhash 契约一致）
    ③ 不存像素，只算 64-bit dHash（16 hex chars）
    """
    import importlib
    try:
        mss = importlib.import_module("mss")
        Image = importlib.import_module("PIL.Image")
        with _MSS_LOCK, mss.mss() as sct:
            monitors = sct.monitors or []
            if len(monitors) < 2:
                return ""
            mon = monitors[1]
            shot = sct.grab({
                "left": int(mon["left"]),
                "top": int(mon["top"]),
                "width": int(mon["width"]),
                "height": int(mon["height"]),
            })
        img = Image.frombytes("RGB", shot.size, shot.rgb).convert("L").resize((9, 8))
        pixels = list(img.getdata())  # 72 个 0-255（9*8）
        bits = 0
        for y in range(8):
            for x in range(8):
                bits <<= 1
                # 横向差：左 > 右 → 1（dHash 标准约定；让亮→暗边界明显）
                if pixels[y * 9 + x] > pixels[y * 9 + x + 1]:
                    bits |= 1
        return f"{bits:016x}"
    except Exception:
        return ""


def _main_server_port() -> int:
    try:
        from config import MAIN_SERVER_PORT  # 动态读宿主端口

        return int(MAIN_SERVER_PORT)
    except Exception:
        return 48911


def _capture_bridge(pid: Any, title: Any) -> CaptureResult:
    """宿主抓屏桥兜底：POST /api/capture/screenshot（回环 HTTP，最慢）。"""
    try:
        import json
        import urllib.request

        url = f"http://127.0.0.1:{_main_server_port()}/api/capture/screenshot"
        body = json.dumps({"target_id": 0, "pid": int(pid or 0), "title": str(title or "")}).encode()
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        if not data.get("success") or not data.get("image"):
            return CaptureResult(False, None, 0, 0, "bridge",
                                 str(data.get("error") or "bridge_failed"), -1.0)
        img_b64 = data["image"]
        if isinstance(img_b64, str) and img_b64.startswith("data:"):
            img_b64 = img_b64.split(",", 1)[-1]
        luma = _avg_luma_from_b64(img_b64)
        return CaptureResult(True, img_b64, int(data.get("width") or 0), int(data.get("height") or 0),
                             "bridge", "", luma)
    except Exception as exc:
        return CaptureResult(False, None, 0, 0, "bridge", f"error:{type(exc).__name__}", -1.0)


def capture_window_base64(target: Optional[dict] = None) -> CaptureResult:
    """降级链 PrintWindow -> mss -> bridge。target=None 时先 find_genshin_window()。

    每路 not ok 或黑帧则降级；返回第一个 ok 且非黑帧的结果；全失败返回最后一路的失败结果。
    """
    if target is None:
        target = find_genshin_window()
    if not target:
        return CaptureResult(False, None, 0, 0, "", "no_window", -1.0)

    hwnd = target.get("hwnd")
    rect = target.get("rect")
    pid = target.get("pid")
    title = target.get("title", "")

    routes = (
        ("printwindow", lambda: _capture_printwindow(hwnd, rect)),
        ("mss", lambda: _capture_mss(rect)),
        ("bridge", lambda: _capture_bridge(pid, title)),
    )
    last: Optional[CaptureResult] = None
    for name, fn in routes:
        try:
            res = fn()
        except Exception as exc:  # 单路炸掉也必须降级，不冒泡
            res = CaptureResult(False, None, 0, 0, name, f"error:{type(exc).__name__}", -1.0)
        last = res
        if res and res.ok and res.image_base64 and not is_black_frame(res.image_base64):
            return res

    if last is None:
        return CaptureResult(False, None, 0, 0, "", "no_window", -1.0)
    err = last.error
    if not err:
        err = "black_frame" if (last.ok and last.image_base64) else "capture_failed"
    return CaptureResult(False, None, last.width, last.height, last.method or "bridge", err, last.avg_luma)


def capture_active_frame() -> Tuple[CaptureResult, str]:
    """拍板 2.0.16：本地进程优先；前景路径走 mss 主屏 → bridge 兜底（不依赖 win32gui 枚举）。

    返回 (CaptureResult, capture_path ∈ {"local_process","foreground"})。
    本地：PrintWindow→mss→bridge 降级链。
    前景：mss 主屏（云游戏整屏即画面，不需要精确知道是哪个窗口）→ 宿主抓屏桥
    （HTTP 抓宿主自己的屏，宿主 UI 线程必有访问权，绕过宿主营养不良的 win32 上下文）。
    前景错误统一归 capture_failed（附 mss/bridge 两路子错误），不再有 no_hwnd/no_rect。
    find_foreground_window / _enum_large_visible_windows 仍保留作可选 helper，
    本函数不再调用（宿主沙箱里 win32gui 拿不到桌面窗口，详见诊断报告）。
    """
    target = find_genshin_window()
    if target:
        return capture_window_base64(target), "local_process"

    def _strip(s: str) -> str:
        return s[len("error:"):] if s.startswith("error:") else s

    # 前景路径 1：mss 主屏（sct.monitors[1] = 主显示器；云游戏跑全屏就是它）
    mss_sub = ""
    try:
        import mss  # lazy: 第三方
        # 拍板 2.0.69：mss.mss() 走 _MSS_LOCK 内串行（防止 2 worker 并发抓屏死锁）
        with _MSS_LOCK, mss.mss() as sct:
            monitors = sct.monitors or []
            if len(monitors) < 2:
                raise RuntimeError("no_primary_monitor")
            mon = monitors[1]
        rect = (int(mon["left"]), int(mon["top"]),
                int(mon["left"]) + int(mon["width"]),
                int(mon["top"]) + int(mon["height"]))
        result = _capture_mss(rect)
        if result.ok:
            return result, "foreground"
        mss_sub = _strip(result.error or "unknown")
    except Exception as exc:
        mss_sub = type(exc).__name__

    # 前景路径 2：宿主抓屏桥（HTTP，宿主 UI 线程一定有访问权）
    bridge = _capture_bridge(0, "")
    if bridge.ok:
        return bridge, "foreground"
    bridge_sub = _strip(bridge.error or "unknown")

    # 都失败 → capture_failed（附两路子错误）
    err = f"capture_failed:mss={mss_sub}|bridge={bridge_sub}"
    return CaptureResult(False, None, 0, 0, bridge.method or "bridge", err, -1.0), "foreground"
