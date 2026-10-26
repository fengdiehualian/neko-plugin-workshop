"""Startup preparation for tfwr_companion.

There is no external DLL to install. The game's own ``file watcher`` option is
the integration point, so this ensures it is enabled whenever the game is
closed. Nothing is written while the game is running.
"""

from __future__ import annotations

from . import tfr


def install(cfg: dict) -> dict:
    game = tfr.resolve_game_dir(cfg)
    if not game:
        return {"ok": False, "stage": "detect", "reason": "找不到 The Farmer Was Replaced 安装目录"}
    exe = game / "TheFarmerWasReplaced.exe"
    core = game / "TheFarmerWasReplaced_Data" / "Managed" / "Core.dll"
    if not exe.exists() or not core.exists():
        return {"ok": False, "stage": "detect", "reason": f"游戏文件不全：{game}"}

    user = tfr.resolve_user_dir(cfg)
    watcher = tfr.read_options(user).get("file watcher")
    if watcher == "enabled":
        return {"ok": True, "stage": "ready", "game": str(game), "file_watcher": watcher, "mods": []}
    if tfr.is_game_running(str(cfg.get("game_exe") or tfr.GAME_EXE)):
        return {
            "ok": True,
            "stage": "pending",
            "game": str(game),
            "file_watcher": watcher,
            "reason": "游戏运行中；退出后会自动开启 file watcher",
            "mods": [],
        }
    tfr.set_option(user, "file watcher", "enabled")
    return {"ok": True, "stage": "ready", "game": str(game), "file_watcher": "enabled", "mods": []}
