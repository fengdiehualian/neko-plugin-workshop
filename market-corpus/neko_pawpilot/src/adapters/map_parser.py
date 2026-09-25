"""地图知识库管理：自动检测 + 缓存 + 重新解析流程。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Optional

from .game_dir import detect_game_dir


class MapParser:
    """管理地图知识库：检测游戏版本、触发解析、缓存加载。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._game_dir: Optional[str] = None
        self._extractor_exe = (Path(__file__).resolve().parent / "map_extractor"
                               / "publish" / "MapExtractor.exe")
        self._extractor_dll = (Path(__file__).resolve().parent / "map_extractor"
                               / "bin" / "Release" / "net10.0" / "MapExtractor.dll")

    def detect_game_dir(self) -> Optional[str]:
        """探测游戏安装目录。"""
        self._game_dir = detect_game_dir()
        return self._game_dir

    def game_version(self) -> str:
        """读游戏版本（game.log）。"""
        try:
            doc = Path.home() / "Documents" / "Euro Truck Simulator 2" / "game.log.txt"
            if doc.exists():
                for line in doc.read_text(encoding="utf-8", errors="ignore").splitlines()[:20]:
                    if "version" in line.lower() and "1." in line:
                        return line.strip()
        except Exception:
            pass
        return ""

    def extractor_available(self) -> bool:
        return self._extractor_exe.exists() or self._extractor_dll.exists()

    def extract(self, out_json: Path, game_dir: Optional[str] = None) -> bool:
        """运行 C# 伴生工具解析地图（优先 self-contained exe）。

        game_dir 由调用方传入（已探测），None 时自行探测。
        """
        game_dir = game_dir or self.detect_game_dir()
        if not game_dir or not self.extractor_available():
            return False
        scs = Path(game_dir, "base_map.scs")
        runner = (str(self._extractor_exe) if self._extractor_exe.exists()
                  else str(self._extractor_dll))
        try:
            result = subprocess.run(
                [runner, str(scs), str(out_json)],
                capture_output=True, text=True, timeout=600)
            return result.returncode == 0
        except Exception:
            return False

    def needs_reparse(self, kb_version: str) -> bool:
        """版本变化需要重新解析。"""
        game_version = self.game_version()
        if not game_version:
            return False
        return game_version not in kb_version
