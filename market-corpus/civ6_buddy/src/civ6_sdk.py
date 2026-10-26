"""civ6_sdk.py — 文明6 Development Assets（SDK / FireTuner 服务端）自动安装与配置（v0.4.0）。

目标：把"装 SDK → 开 Tuner → 探测 4318"整条前置链路变成插件可自动完成的事，
YUI 在需要底层通道时自己触发，不再让玩家手工找 Steam 库。

链路：
1. SDK 是 Steam 免费 Tools（appid 597260），安装后目录
   `<库>/steamapps/common/Sid Meier's Civilization VI SDK`（tuner server 在 SDK 里）。
2. 游戏启用 Tuner：`Documents\\My Games\\Sid Meier's Civilization VI\\AppOptions.txt`
   写入 `[Debug] EnableTuner 1`（或游戏内选项 Tuner，开启会禁用成就）。
3. 重启游戏后 FireTuner 端口 4318 开始监听（同屏只能一个 tuner 连接，
   需先关闭 SDK 自带的 FireTuner.exe）。

只读检查 + 明确授权的动作（触发 Steam 安装 / 修配置文件），不做其他系统改动。
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from pathlib import Path

_logger = logging.getLogger("N.E.K.O.Plugin_civ6_buddy.civ6_sdk")

SDK_APPID = 597260
SDK_DIR_NAME = "Sid Meier's Civilization VI SDK"
GAME_DIR_NAME = "Sid Meier's Civilization VI"

TUNER_PORT = 4318


def _user_docs() -> Path:
    """用户真实文档目录（兼容 OneDrive/重定向）。"""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)  # CSIDL_MYDOCUMENTS
        if buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path(os.path.expanduser("~")) / "Documents"


def candidate_steam_libs() -> list[str]:
    """候选 Steam 库根目录（含 steamapps 的父目录）。

    来源：① 注册表 SteamPath 安装目录；② 常用库盘符路径。
    返回去重、存在的目录列表。
    """
    libs: list[str] = []
    # 1) 注册表：HKCU/HKLM Software\Valve\Steam SteamPath
    for hive in ("HKCU", "HKLM"):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER if hive == "HKCU"
                                else winreg.HKEY_LOCAL_MACHINE,
                                r"Software\Valve\Steam") as key:
                path, _ = winreg.QueryValueEx(key, "SteamPath")
                if path:
                    libs.append(str(path))
        except Exception:
            continue
    # 2) 常用位置（覆盖多库盘）
    for root in ("D:\\SteamLibrary", "C:\\Program Files (x86)\\Steam",
                 "E:\\SteamLibrary", "F:\\SteamLibrary", "G:\\SteamLibrary"):
        p = Path(root)
        if p.is_dir() and (p / "steamapps").is_dir():
            libs.append(str(p))
    seen: set[str] = set()
    out: list[str] = []
    for lib in libs:
        key = lib.rstrip("\\/").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(lib.rstrip("\\/"))
    return out


def sdk_dir(steam_libs: list[str] | None = None) -> str | None:
    """定位已安装的 SDK 目录；未安装返回 None。

    目录名实测有 "Sid Meier's Civilization VI SDK" 与
    "Sid Meier's Civilization VI SDK Assets"（appid 597260 下载后即为 Assets 变体），
    按 "civilization vi sdk" 子串宽松匹配，避免漏判。
    """
    for lib in (steam_libs if steam_libs is not None else candidate_steam_libs()):
        common = Path(lib) / "steamapps" / "common"
        if not common.is_dir():
            continue
        for d in common.iterdir():
            if not d.is_dir():
                continue
            name = d.name.lower()
            if "civilization vi sdk" not in name:
                continue
            if any(d.iterdir()):
                return str(d)
    return None


def game_dir(steam_libs: list[str] | None = None) -> str | None:
    """定位游戏本体目录（用于配置联动）；未找到返回 None。"""
    for lib in (steam_libs if steam_libs is not None else candidate_steam_libs()):
        d = Path(lib) / "steamapps" / "common" / GAME_DIR_NAME
        if d.is_dir():
            return str(d)
    return None


def app_options_path(docs: Path | None = None) -> Path:
    """文明6 AppOptions.txt 的规范位置（文档 My Games 下）。"""
    base = docs or _user_docs()
    return base / "My Games" / GAME_DIR_NAME / "AppOptions.txt"


def _read_app_options(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def tuner_enabled(p: Path | None = None) -> bool:
    """AppOptions.txt 是否已启用 Tuner（含 [Debug] 节下的 EnableTuner 1）。"""
    ap = p or app_options_path()
    for line in _read_app_options(ap).splitlines():
        s = line.strip().lower()
        if "enabletuner" in s:
            try:
                return int(line.split()[-1]) == 1
            except ValueError:
                return False
    return False


def ensure_tuner_enabled(p: Path | None = None) -> dict:
    """确保 AppOptions.txt 含 [Debug] EnableTuner 1。

    - 文件不存在 → 创建（备份不算，首次创建无旧值）。
    - 已有 EnableTuner 0 → 备份 .bak_<ts> 后改为 1。
    - 已有 1 → 不动。
    - 已有文件但无 EnableTuner 行 → 备份后追加；已有 [Debug] 节则不再重复添加节头。
    返回 {"changed": bool, "backup": str|None, "path": str, "detail": str}。
    """
    ap = p or app_options_path()
    text = _read_app_options(ap)
    backup: str | None = None

    def _backup() -> None:
        nonlocal backup
        if backup is None:
            ap.parent.mkdir(parents=True, exist_ok=True)
            backup = str(ap) + f".bak_{time.strftime('%Y%m%d_%H%M%S')}"
            Path(backup).write_text(text, encoding="utf-8")

    if text:
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if "enabletuner" in line.strip().lower():
                try:
                    if int(line.split()[-1]) == 1:
                        return {"changed": False, "backup": None,
                                "path": str(ap), "detail": "EnableTuner 已为 1，无需修改"}
                except ValueError:
                    pass
                _backup()
                lines[i] = "EnableTuner 1"  # 保持原 [Debug] 节位置
                ap.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return {"changed": True, "backup": backup, "path": str(ap),
                        "detail": "EnableTuner 0 → 1（已备份原文件）"}
        # 文件存在但没有 EnableTuner 行：备份后追加
        _backup()
        has_debug = any(line.strip().lower() == "[debug]" for line in lines)
        if has_debug:
            ap.write_text(text.rstrip("\n") + "\nEnableTuner 1\n", encoding="utf-8")
            return {"changed": True, "backup": backup, "path": str(ap),
                    "detail": "已追加 EnableTuner 1（复用已有 [Debug] 节）"}
        ap.write_text(text.rstrip("\n") + "\n[Debug]\nEnableTuner 1\n", encoding="utf-8")
        return {"changed": True, "backup": backup, "path": str(ap),
                "detail": "已追加 [Debug] 节并启用 EnableTuner"}
    # 文件不存在：直接创建
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text("[Debug]\nEnableTuner 1\n", encoding="utf-8")
    return {"changed": True, "backup": None, "path": str(ap),
            "detail": "AppOptions.txt 不存在，已创建并启用 Tuner"}


def trigger_steam_install(appid: int = SDK_APPID) -> dict:
    """触发 Steam 安装指定 appid（steam://install/<appid>）。

    返回 {"ok": bool, "detail": str}。Steam 未运行时用 protocol 仍会拉起 Steam 客户端。
    """
    url = f"steam://install/{appid}"
    try:
        if platform.system() == "Windows":
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", url])
        return {"ok": True, "detail": f"已触发 Steam 安装（steam://install/{appid}）。"
                                      "若 Steam 弹出安装确认请点确认；若没弹，"
                                      "打开 Steam 库 → 左上角筛选'工具' → 找到"
                                      "'Sid Meier's Civilization VI Development Assets' 手动安装。"}
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "detail": f"触发失败：{exc}。请手动在 Steam 库→工具中搜索"
                                        "'Sid Meier's Civilization VI Development Assets' 安装。"}


def _port_listening(port: int = TUNER_PORT) -> bool:
    """4318 是否在本机监听（Tuner server 就绪）。"""
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def game_running() -> bool:
    """文明6 主进程是否在运行（粗略：遍历常见进程名）。"""
    try:
        import psutil  # type: ignore
        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in ("civilizationvi.exe", "civilizationvi_dx12.exe"):
                return True
    except Exception:
        pass
    # 无 psutil 时用 Windows 命令
    if platform.system() == "Windows":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq CivilizationVI.exe"],
                capture_output=True, text=True, timeout=5, check=False)
            if "CivilizationVI.exe" in out.stdout:
                return True
        except Exception:
            pass
    return False


def sdk_status(steam_libs: list[str] | None = None) -> dict:
    """汇总 SDK/Tuner 前置链路状态。供 civ6_ensure_sdk check 输出。"""
    sdk = sdk_dir(steam_libs)
    game = game_dir(steam_libs)
    ap = app_options_path()
    return {
        "sdk_installed": bool(sdk),
        "sdk_dir": sdk,
        "game_dir": game,
        "app_options_path": str(ap),
        "app_options_exists": ap.is_file(),
        "tuner_enabled": tuner_enabled(ap),
        "port_4318_listening": _port_listening(),
        "game_running": game_running(),
    }
