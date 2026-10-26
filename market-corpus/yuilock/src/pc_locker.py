"""Yui PC 锁机程序（由 N.E.K.O. 插件或 web_panel.py 调起）。

锁定期间：
- 打开的任何程序窗口都会被立即关闭（弹回桌面）；
- N.E.K.O. 只按「启动时扫描到的完整可执行路径」放行（不做名字模糊匹配）；
- 系统外壳（explorer/搜索/输入法等）按进程名放行；
- Win 键、Alt+Tab 被拦截；
- 屏幕右上角显示一个小的「已锁定」角标（不提示任何解锁方式）。

解锁方式（对用户不可见）：秘密按键组合（↑↑↓↓←→←→BA）、插件/面板结束本进程、重启电脑。

用法：
    pythonw pc_locker.py --instance <uuid> --state <状态文件> [--allow 可放行程序路径 ...]
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_CLOSE = 0x0010
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
VK_TAB, VK_ALT, VK_LWIN, VK_RWIN = 0x09, 0x12, 0x5B, 0x5C
VK_BY_NAME = {"up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "b": 0x42, "a": 0x41}
NAME_BY_VK = {v: k for k, v in VK_BY_NAME.items()}
KONAMI = ["up", "up", "down", "down", "left", "right", "left", "right", "b", "a"]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x2
CREATE_NO_WINDOW = 0x08000000

state = {"unlocked": False}

# 系统外壳 / 搜索 / 输入法（有窗口但不是"应用"）
WHITELIST_BASE = {
    "explorer", "system", "searchhost", "startmenuexperiencehost",
    "shellexperiencehost", "textinputhost", "lockapp", "runtimebroker",
}


def norm_name(s: str) -> str:
    # 保留点号：只小写、折叠空白（系统外壳比对已改用完整路径，此函数仅作常规小写化备用）
    return re.sub(r"\s+", "", (s or "").lower())


ARGS = argparse.ArgumentParser()
ARGS.add_argument("--instance", required=True, help="本次锁定的唯一标识（写入命令行，供 unlock 校验）")
ARGS.add_argument("--state", required=True, help="状态文件路径")
ARGS.add_argument("--allow", action="append", default=[],
                  help="额外放行的程序完整路径（如 N.E.K.O. 的 python.exe）")
OPTS = ARGS.parse_args()

SELF_PATH = os.path.abspath(sys.executable).lower()
ALLOW_PATHS = {os.path.abspath(p).lower() for p in OPTS.allow}
# 系统外壳白名单：只认「完整路径」，保留扩展名与点号，不做任何名字折叠
SYSTEM_ROOT = os.environ.get("SystemRoot", r"C:\Windows").lower()
SHELL_ROOT_FILES = {"explorer.exe"}
SHELL_SYSTEM32_FILES = {"runtimebroker.exe"}
SHELL_SYSTEMAPP_PACKAGES = (
    "microsoft.windows.search_",
    "microsoft.windows.startmenuexperiencehost_",
    "microsoft.windows.shellexperiencehost_",
    "microsoftwindows.client.cbs_",
    "microsoft.lockapp_",
)
SHELL_SYSTEMAPP_STEMS = {"searchhost", "startmenuexperiencehost",
                         "shellexperiencehost", "textinputhost", "lockapp"}


def is_shell_path(lp: str) -> bool:
    """仅放行 %SystemRoot% 下固定位置的系统外壳（完整路径精确匹配）。"""
    root_prefix = SYSTEM_ROOT + "\\"
    if not lp.startswith(root_prefix):
        return False
    rel = lp[len(root_prefix):]
    directory, name = os.path.split(rel)
    name = name.lower()
    if directory == "" and name in SHELL_ROOT_FILES:
        return True
    if directory in ("system32", "syswow64") and name in SHELL_SYSTEM32_FILES:
        return True
    # SystemApps 的固定包前缀目录（目录名带版本哈希，前缀固定），文件主名保留点号比对
    if directory.startswith("systemapps\\") and "\\" not in directory[len("systemapps\\"):]:
        pkg = directory[len("systemapps\\"):]
        if any(pkg.startswith(pfx) for pfx in SHELL_SYSTEMAPP_PACKAGES):
            stem = os.path.splitext(name)[0].lower()
            return stem in SHELL_SYSTEMAPP_STEMS
    return False


def exe_path(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def allowed_pid(pid: int) -> bool:
    if pid in (0, 4):
        return True
    path = exe_path(pid)
    # 查不到路径（权限不足等）一律拒绝放行，宁可错关不可放过未知进程
    if not path:
        return False
    lp = path.lower()
    # 显式 exe 路径列表（不信任整个目录）
    if lp == SELF_PATH or lp in ALLOW_PATHS:
        return True
    # 系统外壳：只放行 %SystemRoot% 下固定位置的完整路径（SystemApps 包前缀 + 主名白名单）
    return is_shell_path(lp)


def write_state() -> None:
    # 必须带 exe：与 pclock.spawn_locker 写入的内容一致，绝不能覆盖掉三重校验数据
    try:
        with open(OPTS.state, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "instance": OPTS.instance,
                       "exe": os.path.abspath(sys.executable).lower()}, f)
    except Exception:
        pass


def clear_state() -> None:
    try:
        os.remove(OPTS.state)
    except Exception:
        pass


def minimize_all() -> None:
    # Win+D：锁定开始时先回到桌面
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(0x44, 0, 0, 0)  # D
    user32.keybd_event(0x44, 0, 2, 0)
    user32.keybd_event(VK_LWIN, 0, 2, 0)


def watchdog() -> None:
    hits: dict[int, int] = {}
    while not state["unlocked"]:
        try:
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                p = pid.value
                if p and not allowed_pid(p):
                    hits[p] = hits.get(p, 0) + 1
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    if hits.get(p, 0) >= 4:
                        # 不肯自己关的，强杀
                        subprocess.run(["taskkill", "/F", "/PID", str(p)],
                                       capture_output=True, creationflags=CREATE_NO_WINDOW)
            if len(hits) > 64:
                hits = {k: v for k, v in hits.items() if v < 4}
            time.sleep(0.2)
        except Exception:
            time.sleep(0.5)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


def hook_thread() -> None:
    idx = 0

    def proc(ncode, wparam, lparam):
        nonlocal idx
        if ncode == HC_ACTION and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            vk = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
            if vk in (VK_LWIN, VK_RWIN):
                return 1
            if vk == VK_TAB and (user32.GetAsyncKeyState(VK_ALT) & 0x8000):
                return 1
            name = NAME_BY_VK.get(vk)
            if name:
                if name == KONAMI[idx]:
                    idx += 1
                elif name == KONAMI[0]:
                    idx = 1
                else:
                    idx = 0
                if idx >= len(KONAMI):
                    idx = 0
                    state["unlocked"] = True
        return user32.CallNextHookEx(None, ncode, wparam, lparam)

    ptr = HOOKPROC(proc)
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, ptr, None, 0)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        pass
    if hook:
        user32.UnhookWindowsHookEx(hook)


def banner() -> None:
    try:
        import tkinter as tk
    except Exception:
        return
    try:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#E8536B")
        w, h = 240, 52
        sw = root.winfo_screenwidth()
        root.geometry(f"{w}x{h}+{max(sw - w - 20, 0)}+20")
        tk.Label(root, text="🔒 已锁定", font=("Microsoft YaHei UI", 16, "bold"),
                 fg="white", bg="#E8536B").pack(expand=True, fill="both")
        root.mainloop()
    except Exception:
        pass


def main() -> None:
    write_state()
    minimize_all()
    threading.Thread(target=hook_thread, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=banner, daemon=True).start()
    try:
        while not state["unlocked"]:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    clear_state()


if __name__ == "__main__":
    main()
