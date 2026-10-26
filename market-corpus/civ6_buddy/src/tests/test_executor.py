"""civ6_ops 执行通道测试：懒加载遮蔽回归 + PrintWindow 错误路径。

真实窗口截图无法在无头 CI 下稳定复现，这里覆盖：
1. 懒加载方法名≠属性名（回归：_pg/_PIL 同名遮蔽曾导致所有截图/键鼠调用 TypeError）
2. ctypes 兜底函数可用（v0.4.12 起插件进程无 pyautogui，键鼠/截图全走 ctypes）
3. PrintWindow 对无效窗口给出稳定错误码（不崩溃）
"""
import platform

import pytest
from civ6_ops import OpsError, _Executor

# CI（GitHub Actions）跑在 Ubuntu/Linux：ctypes.WinDLL 仅 Windows 存在，
# _win32 顶层导入在 Linux 会抛 AttributeError 导致测试收集失败（v0.6.3 教训）。
# Windows 专属符号延迟到平台守卫内导入，Windows 专属测试在 Linux 上跳过。
_WINDOWS = platform.system() == "Windows"
if _WINDOWS:
    from _win32 import BITMAPINFOHEADER, mouse_click_abs, press_key, screen_region_rgb
else:
    BITMAPINFOHEADER = mouse_click_abs = press_key = screen_region_rgb = None


def test_executor_lazy_load_returns_modules():
    ex = _Executor()
    # PIL 懒加载（仅 steam_manager 通道需要）
    pil = ex._pil_instance()
    assert pil is not None
    assert hasattr(pil, "open")


@pytest.mark.skipif(not _WINDOWS, reason="Windows-only ctypes fallback")
def test_executor_ctypes_fallback_available():
    # 插件进程无 pyautogui → 键鼠/截图兜底必须来自 ctypes 兼容层
    assert BITMAPINFOHEADER is not None
    assert callable(mouse_click_abs)
    assert callable(press_key)
    assert callable(screen_region_rgb)


def test_executor_methods_do_not_shadow_attributes():
    ex = _Executor()
    # 回归：方法名与属性名曾经同名（_pg/_PIL），实例属性遮蔽类方法，
    # 导致 `self._pg()` 解析为 None 而 TypeError: 'NoneType' object is not callable。
    assert callable(ex._pil_instance)
    # 属性与方法是不同名字
    assert hasattr(ex, "_pil_mod")


@pytest.mark.skipif(not _WINDOWS, reason="PrintWindow is Windows-only")
def test_capture_printwindow_invalid_hwnd_raises_stable_code():
    ex = _Executor()
    # 无效窗口句柄 → PrintWindow 通道必须给出稳定错误码（而不是裸崩溃）
    with pytest.raises(OpsError):
        ex.capture({"hwnd": 0, "left": 0, "top": 0, "right": 10, "bottom": 10})
