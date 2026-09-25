# -*- coding: utf-8 -*-
"""手柄模拟（Windows）：把手柄按钮映射到键盘键，复用 _win32_input 注入。

真实 XInput 虚拟手柄（摇杆/扳机的精确模拟）需要 ViGEm 内核驱动，这里不引入
额外驱动依赖；按钮按下/长按通过键盘映射实现，摇杆/扳机返回提示信息。
"""

from __future__ import annotations

import sys
import time
from typing import Any


def _is_windows() -> bool:
    return sys.platform == "win32"


# ── 手柄按钮 → 键盘键名映射（复用 _win32_input 的 SendInput 注入路径）──
# 标准 Xbox 手柄默认键位：A/B/X/Y → 键盘字母，十字键 → 方向键，
# start/back → Enter/Backspace，肩键 → Tab/Pause，摇杆按下 → Space/Alt。
_BUTTON_TO_KEY: dict[str, str] = {
    "a": "a",
    "b": "b",
    "x": "x",
    "y": "y",
    "dpad_up": "up",
    "dpad_down": "down",
    "dpad_left": "left",
    "dpad_right": "right",
    "start": "enter",
    "back": "backspace",
    "left_shoulder": "tab",
    "lb": "tab",
    "right_shoulder": "pause",
    "rb": "pause",
    "left_thumb": "space",
    "right_thumb": "alt",
}


def list_buttons() -> dict[str, Any]:
    """列出所有支持的虚拟手柄按钮名。"""
    return {"ok": True, "buttons": sorted(_BUTTON_TO_KEY.keys())}


def can_emulate() -> dict[str, Any]:
    """检查手柄模拟是否可用（Windows 上可用，通过键盘映射）。"""
    return {"ok": True, "available": _is_windows(), "platform": sys.platform}


def press_button(button: str, hold_seconds: float = 0.1) -> dict[str, Any]:
    """模拟手柄按钮按下（映射到键盘键，复用 _win32_input 注入）。

    支持的按钮: a, b, x, y, dpad_up/down/left/right, start, back,
    left_shoulder/lb, right_shoulder/rb, left_thumb, right_thumb
    """
    if not _is_windows():
        return {"ok": False, "error": "手柄模拟仅支持 Windows"}

    key = _BUTTON_TO_KEY.get(str(button).strip().lower())
    if key is None:
        return {
            "ok": False,
            "error": f"不支持的手柄按钮：{button}",
            "supported": sorted(_BUTTON_TO_KEY.keys()),
        }

    try:
        from . import _win32_input as w
        from ._key_map import lookup_vk
        vk = lookup_vk(key)
        if vk is None:
            return {"ok": False, "error": f"无法解析键名：{key}"}
        # 钳制按住时长：to_thread 里的 time.sleep 不可被工具超时取消，
        # 不设上限时 LLM 传 3600 就占死一个线程池线程一小时。
        hold = min(max(0.0, float(hold_seconds)), 30.0)
        w.key_down(vk)
        time.sleep(hold)
        w.key_up(vk)
        return {"ok": True, "button": button, "key": key, "hold_seconds": hold}
    except Exception as e:
        return {"ok": False, "error": f"注入失败：{e}"}


def set_thumbstick(left_x: float = 0, left_y: float = 0,
                   right_x: float = 0, right_y: float = 0) -> dict[str, Any]:
    """设置虚拟摇杆位置（值域 -1.0 ~ 1.0）。

    精确摇杆模拟需要 ViGEm 内核驱动；这里返回提示，不做注入。
    """
    if not _is_windows():
        return {"ok": False, "error": "手柄模拟仅支持 Windows"}
    return {
        "ok": True,
        "left_x": left_x, "left_y": left_y,
        "right_x": right_x, "right_y": right_y,
        "note": "摇杆精确模拟需要 ViGEm 驱动；请用 keyboard_mouse 模拟鼠标移动替代",
    }


def set_triggers(left: float = 0, right: float = 0) -> dict[str, Any]:
    """设置扳机值（0.0 ~ 1.0）。"""
    if not _is_windows():
        return {"ok": False, "error": "手柄模拟仅支持 Windows"}
    return {
        "ok": True,
        "left": left, "right": right,
        "note": "扳机精确模拟需要 ViGEm 驱动",
    }
