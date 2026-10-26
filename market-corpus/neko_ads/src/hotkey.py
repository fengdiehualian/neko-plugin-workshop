"""A round-scoped Windows hotkey. No keyboard hooks or input recording."""
from __future__ import annotations

import ctypes
import re
import sys
import threading
from ctypes import wintypes

DEFAULT_SHORTCUT = "Ctrl+Shift+1"


def parse_shortcut(value: str) -> tuple[str, int, int]:
    parts = str(value).replace(" ", "").split("+")
    mods = parts[:-1]
    key = parts[-1].upper()
    if len(mods) < 1 or len(set(mods)) != len(mods) or any(m not in {"Ctrl", "Alt", "Shift"} for m in mods):
        raise ValueError("请使用 Ctrl、Alt、Shift 与一个字母、数字或 F1–F11 组合")
    if re.fullmatch(r"[A-Z0-9]", key):
        vk = ord(key)
    elif re.fullmatch(r"F(?:[1-9]|10|11)", key):
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError("不支持这个退出按键")
    ordered = [m for m in ("Ctrl", "Alt", "Shift") if m in mods]
    return "+".join([*ordered, key]), sum({"Ctrl": 2, "Alt": 1, "Shift": 4}[m] for m in mods), vk


class GlobalExitHotkey:
    def __init__(self):
        self.thread = None
        self.thread_id = None
        self._user32 = None
        self._closing = threading.Event()

    def register(self, shortcut: str, callback) -> str:
        label, mods, vk = parse_shortcut(shortcut)
        if sys.platform != "win32":
            raise ValueError("此平台尚未接入全局退出快捷键，不能开始桌宠广告游戏")
        ready = threading.Event()
        result = {"ok": False}

        def run():
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            user32.RegisterHotKey.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = wintypes.BOOL
            user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self._user32 = user32
            self.thread_id = kernel32.GetCurrentThreadId()
            msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            result["ok"] = bool(user32.RegisterHotKey(None, 1, mods | 0x4000, vk))
            ready.set()
            if not result["ok"]:
                return
            try:
                if self._closing.is_set():
                    return
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == 0x0312:
                        callback()
            finally:
                user32.UnregisterHotKey(None, 1)

        self.thread = threading.Thread(target=run, name="neko-ads-exit", daemon=True)
        self.thread.start()
        if not ready.wait(2) or not result["ok"]:
            self.close()
            raise ValueError("退出快捷键不可用或已被占用，请换一个组合")
        return label

    def close(self):
        self._closing.set()
        if self.thread_id and self._user32:
            self._user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
        self.thread = None
        self.thread_id = None
