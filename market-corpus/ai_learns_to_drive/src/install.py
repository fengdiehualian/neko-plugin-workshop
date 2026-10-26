"""Startup preparation for ai_learns_to_drive.

No external DLL is required: the companion reads and writes the game's own
gzip+JSON settings/genomes and sends F5/Shift+F5 to the game window.
"""

from __future__ import annotations

from . import ald


def install(cfg: dict) -> dict:
    game = ald.resolve_game_dir(cfg)
    if not game:
        return {"ok": False, "stage": "detect", "reason": "找不到 AI Learns To Drive 安装目录"}
    exe = game / ald.GAME_EXE
    if not exe.exists():
        return {"ok": False, "stage": "detect", "reason": f"找不到可执行文件：{exe}"}
    return {
        "ok": True,
        "stage": "ready",
        "game": str(game),
        "mods": [],
        "note": "无需外部 mod；使用存档文件与按键控制",
    }
