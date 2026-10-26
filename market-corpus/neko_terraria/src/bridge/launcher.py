"""启动独立的 tModLoader 客户端——有头模式（完整渲染，非 Terraria-Bot 无头方案）"""

import asyncio
import ctypes
import os
import subprocess
from pathlib import Path
from typing import Optional, Set

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4   # 显示/恢复但不激活（不抢焦点，避免键盘同时控制 AI 角色）

# Win32 窗口 API（显式 argtypes，避免 64 位句柄截断导致 SetWindowPos 等静默失败）。
#
# ⚠️ 仅 Windows 可用：Linux/macOS 既没有 ctypes.wintypes 也没有 ctypes.WinDLL。
# 这里必须可降级（user32=None），否则模块级 ImportError 会让整个 bridge.agent
# 在非 Windows 上无法导入——CI 在 Linux 上跑测试就是这么挂的。生产环境是
# Windows，装载路径与行为完全不变；非 Windows 下窗口控制退化为空操作
# （launch() 本来就找不到 tModLoader.dll，会提前返回 False）。
try:
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = ctypes.c_bool
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = ctypes.c_bool
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = ctypes.c_bool
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_bool
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = ctypes.c_bool
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.restype = ctypes.c_bool
except (ImportError, AttributeError, OSError):
    wintypes = None      # type: ignore[assignment]
    user32 = None        # type: ignore[assignment]

#  Win32 窗口工具

def _enum_visible_hwnds() -> Set[int]:
    if user32 is None:      # 非 Windows：无 Win32 窗口 API
        return set()
    hwnds = set()
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def enum_cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            hwnds.add(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    return hwnds


def _find_window_by_pid(target_pid: int) -> Optional[int]:
    if user32 is None:      # 非 Windows：无 Win32 窗口 API
        return None
    found = [None]
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def enum_cb(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == target_pid and user32.IsWindowVisible(hwnd):
            # PID 已精确匹配 AI 客户端进程；只排除服务器控制台窗口。
            # 不设尺寸限制——AI 窗口可能被用户缩放成任意大小。
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if "server" in buf.value.lower():
                return True
            found[0] = hwnd
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    return found[0]


class GameLauncher:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.process: Optional[subprocess.Popen] = None
        self.hwnd = None
        self._before_hwnds: Set[int] = set()

        self._detect_tmodloader_path()


    # ----------------------------------------------------------------
    #  tModLoader / dotnet 检测
    # ----------------------------------------------------------------

    def _detect_tmodloader_path(self):
        game_path = self.cfg.get("game_path", "")
        if game_path and os.path.exists(game_path):
            self.cfg["tmodloader_path"] = game_path
            self.cfg["tmodloader_dir"] = str(Path(game_path).parent)
            return

        # 动态检测：先读 Steam 注册表拿真实安装路径，再回退常见目录
        for tml_dll in self._candidate_tml_dlls():
            if tml_dll.exists():
                self.cfg["tmodloader_path"] = str(tml_dll)
                self.cfg["tmodloader_dir"] = str(tml_dll.parent)
                return
        self.cfg["tmodloader_path"] = ""
        self.cfg["tmodloader_dir"] = ""

    @staticmethod
    def _steam_paths_from_registry() -> list:
        """从注册表读 Steam 实际安装路径（HKLM/HKCU 两个位置，兼容 x86/x64）。"""
        try:
            import winreg
        except ImportError:      # 非 Windows：没有注册表，交给常见目录兜底
            return []
        out = []
        for hive, subkey in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, subkey) as k:
                    sp, _ = winreg.QueryValueEx(k, "SteamPath")
                    if sp and os.path.isdir(sp):
                        out.append(sp)
            except OSError:
                continue
        return out

    @classmethod
    def _candidate_tml_dlls(cls) -> list:
        """生成 tModLoader.dll 候选路径：注册表 Steam 路径优先，再回退常见目录。"""
        cands = []
        for sp in cls._steam_paths_from_registry():
            cands.append(Path(sp) / "steamapps/common/tModLoader/tModLoader.dll")
        # 常见 Steam 目录兜底（可能 Steam 装在非注册表路径的移动盘）
        for sp in (
            r"D:\SteamLibrary",
            r"E:\SteamLibrary",
            r"D:\Steam",
            r"C:\Program Files (x86)\Steam",
            r"C:\Program Files\Steam",
        ):
            cands.append(Path(sp) / "steamapps/common/tModLoader/tModLoader.dll")
        return cands

    def _dotnet_path(self) -> str:
        import shutil
        # PATH 里的 dotnet 优先（用户装了 dotnet 一般就在 PATH）
        found = shutil.which("dotnet")
        if found:
            return found
        for p in (r"C:\Program Files\dotnet\dotnet.exe",
                  r"C:\Program Files (x86)\dotnet\dotnet.exe"):
            if os.path.exists(p):
                return p
        return "dotnet"

    async def launch(self) -> bool:
        from ..core.config_store import (
            AI_MODS_DIR,
            AI_PLAYERS_DIR,
            RESOURCE_DIR,
            clean_human_mods,
            ensure_player_name_matches,
            mute_ai_client,
            set_unfocused_keep_running,
        )

        tml_path = self.cfg.get("tmodloader_path", "")
        if not tml_path or not os.path.exists(tml_path):
            self._log("tModLoader.dll 未找到，请在设置中填入 game_path")
            return False

        cwd = self.cfg.get("tmodloader_dir", str(Path(tml_path).parent))
        self._before_hwnds = _enum_visible_hwnds()
        dotnet = self._dotnet_path()

        # —— 全部指向插件资源目录，零复制 ——
        save_dir = str(RESOURCE_DIR)
        mods_dir = str(AI_MODS_DIR)

        clean_human_mods()

        # 确保角色文件名与配置的 character_name 一致（不一致则自动重命名）
        matched_name = ensure_player_name_matches()
        if matched_name:
            self._log(f"角色匹配完成: '{matched_name}'")
        else:
            self._log("警告: ensure_player_name_matches 未能匹配角色")

        if mute_ai_client():
            self._log("AI 客户端已静音（VolumeSound/Ambient/Music = 0）")

        if set_unfocused_keep_running():
            self._log("AI 客户端失焦保活已配置（AutoPause/ThrottleWhenInactive = False）")

        # 检查 AI 客户端是否有可用角色
        plr_files = [
            f for f in os.listdir(str(AI_PLAYERS_DIR))
            if f.endswith(".plr") and not f.endswith(".tplr")
        ] if os.path.isdir(str(AI_PLAYERS_DIR)) else []
        if not plr_files:
            self._log("AI 客户端没有角色文件！请先在「角色管理」页创建/导入角色。")
            return False

        self._log(f"资源目录: {save_dir}")
        self._log(f"Mods    : {mods_dir}")
        self._log(f"角色    : {', '.join(f.replace('.plr','') for f in plr_files)}")

        args = [dotnet, tml_path,
                "-savedirectory", save_dir,
                "-modpath", mods_dir]

        try:
            self.process = subprocess.Popen(
                args, cwd=cwd,
                # CREATE_NEW_CONSOLE 仅 Windows 存在；非 Windows 上没有该常量
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._log(f"启动 AI tML 失败: {e}")
            return False

        await asyncio.sleep(3)

        if self.process.poll() is not None:
            self._log(f"AI tML 进程 {self.process.pid} 已退出")
            return False

        # 有头客户端：必须显示窗口，保证渲染管线完整（GPU 渲染模型/光影/贴图全工作）。
        # 隐藏窗口会暂停 FNA 渲染，导致 AI 角色不生成/动作异常——即使配置 window_hidden=True 也强制显示。
        asyncio.create_task(self._show_window_async())
        asyncio.create_task(self._keep_window_visible_loop())

        return True

    def _set_topmost(self):
        """桌宠式置顶：WS_EX_TOPMOST 扩展样式（永续）+ SetWindowPos（立即生效）。"""
        if user32 is None:      # 非 Windows：无 Win32 窗口 API
            return
        try:
            if self.hwnd:
                GWL_EXSTYLE = -20
                WS_EX_TOPMOST = 0x00000008
                style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE,
                                      style | WS_EX_TOPMOST)
                user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0,
                                    0x0002 | 0x0001)
        except Exception:
            pass

    async def _keep_window_visible_loop(self):
        """窗口保活：每秒检查——置顶缺失即重申（移动/缩放后 SDL 可能清样式）、
        防最小化/隐藏。每轮重新查找窗口句柄（AI 客户端重启后句柄会变）。"""
        if user32 is None:      # 非 Windows：无 Win32 窗口 API
            return
        while self.process and self.process.poll() is None:
            try:
                pid = self.process.pid
                hwnd = self.hwnd
                if hwnd is None or not user32.IsWindow(hwnd):
                    hwnd = _find_window_by_pid(pid)
                    if hwnd:
                        self.hwnd = hwnd
                if hwnd:
                    if (not user32.IsWindowVisible(hwnd)
                            or user32.IsIconic(hwnd)):
                        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                    self.hwnd = hwnd
                    style = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
                    if not (style & 0x00000008):              # WS_EX_TOPMOST
                        self._set_topmost()
            except Exception:
                pass
            await asyncio.sleep(1)

    def _log(self, msg: str) -> None:
        print(f"[GameLauncher] {msg}")

    async def _hide_window_async(self):
        if user32 is None:      # 非 Windows：无 Win32 窗口 API
            return
        pid = self.process.pid
        for _ in range(10):
            hwnd = _find_window_by_pid(pid)
            if hwnd:
                self.hwnd = hwnd
                user32.ShowWindow(hwnd, SW_HIDE)
                return
            await asyncio.sleep(2)

    async def _show_window_async(self):
        """有头客户端：窗口可见 + 桌宠式置顶，但不抢焦点（主玩家键盘不受影响）。"""
        if user32 is None:      # 非 Windows：无 Win32 窗口 API
            return
        pid = self.process.pid
        for _ in range(10):
            hwnd = _find_window_by_pid(pid)
            if hwnd:
                self.hwnd = hwnd
                user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
                self._set_topmost()  # 置顶：点击其他窗口也不被覆盖
                return
            await asyncio.sleep(2)

    def show_window(self):
        if user32 is not None and self.hwnd:    # 非 Windows：无 Win32 窗口 API
            user32.ShowWindow(self.hwnd, 9)

    def close(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
