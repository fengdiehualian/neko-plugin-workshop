"""游戏安装目录探测：Steam 库扫描，不硬编码路径。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

_GAME_NAME = "Euro Truck Simulator 2"


def steam_library_dirs() -> List[str]:
    """从 Steam 配置探测游戏库目录。"""
    dirs: List[str] = []
    steam_path = os.environ.get("STEAM_PATH", "")
    if not steam_path:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Valve\Steam") as key:
                steam_path = winreg.QueryValueEx(key, "SteamPath")[0]
        except Exception:
            pass
    if not steam_path:
        return dirs
    vdf = Path(steam_path, "steamapps", "libraryfolders.vdf")
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            p = m.group(1).replace("\\\\", "\\")
            if p and p not in dirs:
                dirs.append(p)
    except OSError:
        pass
    if steam_path not in dirs:
        dirs.append(steam_path)
    return dirs


def detect_game_dir() -> Optional[str]:
    """探测 ETS2 安装目录（环境变量优先，其次 Steam 库）。"""
    env = os.environ.get("ETS2_GAME_DIR", "")
    if env and Path(env, "base_map.scs").exists():
        return env
    for lib in steam_library_dirs():
        cand = Path(lib, "steamapps", "common", _GAME_NAME)
        if Path(cand, "base_map.scs").exists():
            return str(cand)
    return None
