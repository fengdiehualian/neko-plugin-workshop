"""Bundled mod installer for tc_companion.

Runs from the plugin startup hook. Detects the game, matches the EXE against
the bundled loader variants, installs or updates the loader, copies the bridge
mod, and enables it through the loader's CLI.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import tc

BUNDLE = Path(__file__).resolve().parent / "bundle"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest() -> dict:
    return json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))


def _enable(game: Path, cli: Path, mod_id: str) -> dict:
    if not cli.exists():
        return {"ok": False, "error": f"缺少 {cli.name}"}
    try:
        proc = subprocess.run(
            [str(cli), str(game), "enable", mod_id],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "stdout": (proc.stdout or "").strip()[:400],
        "stderr": (proc.stderr or "").strip()[:400],
    }


def install(cfg: dict) -> dict:
    manifest = _manifest()
    game = tc.resolve_game_dir(cfg)
    if not game:
        return {"ok": False, "stage": "detect", "reason": "找不到 Turing Complete 安装目录"}

    exe = game / "Turing Complete.exe"
    engine = game / "game_engine.dll"
    backup = game / "tc_game_engine.dll"
    if not exe.exists() or not engine.exists():
        return {"ok": False, "stage": "detect", "reason": f"游戏文件不全：{game}"}
    if tc.is_game_running(str(cfg.get("game_exe") or "Turing Complete.exe")):
        return {"ok": False, "stage": "busy", "reason": "游戏正在运行；安装加载器前请先退出游戏"}

    exe_hash = _sha256(exe).lower()
    variant = next(
        (item for item in manifest.get("loaders", [])
         if item.get("exe_sha256", "").lower() == exe_hash),
        None,
    )
    if not variant:
        return {
            "ok": False,
            "stage": "unsupported",
            "reason": "当前游戏版本没有匹配的加载器",
            "exe_sha256": exe_hash,
        }

    loader = BUNDLE / variant["file"]
    if not loader.exists():
        return {"ok": False, "stage": "bundle", "reason": f"捆绑文件缺失：{loader}"}
    if _sha256(loader).lower() != variant["sha256"].lower():
        return {"ok": False, "stage": "bundle", "reason": "捆绑加载器哈希不匹配"}

    original = manifest["engine_sha256"].lower()
    bundled = {item["sha256"].lower() for item in manifest.get("loaders", [])}
    engine_hash = _sha256(engine).lower()
    action = "unchanged"

    if engine_hash == variant["sha256"].lower():
        action = "already"
    elif engine_hash in bundled:
        try:
            shutil.copy2(loader, engine)
            action = "updated"
        except OSError as exc:
            return {"ok": False, "stage": "update", "reason": f"{type(exc).__name__}: {exc}"}
    elif backup.exists() and _sha256(backup).lower() == original:
        # An older loader is installed; the original engine is safely kept in
        # tc_game_engine.dll, so replacing the proxy is a normal update.
        try:
            shutil.copy2(loader, engine)
            action = "updated"
        except OSError as exc:
            return {"ok": False, "stage": "update", "reason": f"{type(exc).__name__}: {exc}"}
    elif engine_hash == original:
        if backup.exists() and _sha256(backup).lower() != original:
            return {"ok": False, "stage": "backup", "reason": "tc_game_engine.dll 已存在但不是原版引擎"}
        try:
            if not backup.exists():
                os.replace(engine, backup)
            else:
                engine.unlink()
            shutil.copy2(loader, engine)
            action = "installed"
        except OSError as exc:
            try:
                if not engine.exists() and backup.exists():
                    os.replace(backup, engine)
            except OSError:
                pass
            return {"ok": False, "stage": "install", "reason": f"{type(exc).__name__}: {exc}"}
    else:
        return {
            "ok": False,
            "stage": "engine",
            "reason": "game_engine.dll 既不是原版也不是已知加载器，拒绝覆盖",
            "engine_sha256": engine_hash,
        }

    mods_dir = game / "mods"
    mods_dir.mkdir(exist_ok=True)
    installed_mods = []
    for name in manifest.get("mods", []):
        source = BUNDLE / name
        if not source.exists():
            return {"ok": False, "stage": "bundle", "reason": f"捆绑 mod 缺失：{source}"}
        target = mods_dir / name
        try:
            if not target.exists() or _sha256(target) != _sha256(source):
                shutil.copy2(source, target)
            installed_mods.append(name)
        except OSError as exc:
            return {"ok": False, "stage": "mod", "reason": f"{type(exc).__name__}: {exc}"}

    enable = _enable(game, BUNDLE / manifest["cli"], manifest.get("mod_id", "local.tc-bridge"))
    return {
        "ok": bool(enable.get("ok")),
        "stage": "ready" if enable.get("ok") else "enable",
        "game": str(game),
        "variant": variant.get("game"),
        "action": action,
        "mods": installed_mods,
        "enable": enable,
        "restart_required": True,
    }
