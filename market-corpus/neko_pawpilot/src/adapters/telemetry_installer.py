"""遥测插件自动导入：检测游戏目录并安装 scs-telemetry.dll。

面向其他用户：玩家无需手动下载/安装 scs-sdk-plugin，
插件启动时自动把捆绑的 DLL 复制到游戏 plugins 目录。
目标/捆绑路径来自配置（telemetry_plugin_rel / telemetry_bundle_rel）。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Dict, Optional

from .game_dir import detect_game_dir


class TelemetryInstaller:
    """自动安装/校验遥测插件文件。"""

    def __init__(self, plugin_rel: Optional[str] = None,
                 bundle_rel: Optional[str] = None) -> None:
        from ..core.config_model import DEFAULTS
        self._plugin_rel = Path(plugin_rel or DEFAULTS["telemetry_plugin_rel"])
        self._bundled = (Path(__file__).resolve().parent.parent
                         / (bundle_rel or DEFAULTS["telemetry_bundle_rel"]))

    def bundled_available(self) -> bool:
        return self._bundled.exists()

    def bundled_hash(self) -> str:
        return self._sha256(self._bundled) if self._bundled.exists() else ""

    def detect_game_dir(self) -> Optional[str]:
        return detect_game_dir()

    def target_path(self, game_dir: str) -> Path:
        return Path(game_dir, self._plugin_rel)

    def installed_ok(self, game_dir: str) -> bool:
        """已安装且与捆绑版本一致。"""
        target = self.target_path(game_dir)
        return (target.exists()
                and self._sha256(target) == self.bundled_hash())

    def install(self, game_dir: Optional[str] = None) -> Dict[str, str]:
        """自动导入遥测 DLL。

        返回 {status, detail}：
        - ok: 已就绪（版本一致，无需操作）
        - installed: 本次完成导入
        - updated: 覆盖为捆绑版本
        - locked: 游戏运行中无法写入，需重启游戏
        - no_game: 未找到游戏目录
        - no_bundle: 插件缺少捆绑 DLL
        """
        if not self.bundled_available():
            return {"status": "no_bundle", "detail": "插件缺少捆绑遥测文件"}
        game_dir = game_dir or self.detect_game_dir()
        if not game_dir:
            return {"status": "no_game", "detail": "未找到欧卡2安装目录"}
        target = self.target_path(game_dir)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if target.exists():
            if self._sha256(target) == self.bundled_hash():
                return {"status": "ok", "detail": "遥测插件已就绪"}
            try:
                shutil.copy2(self._bundled, target)
                return {"status": "updated", "detail": "已更新为插件捆绑版本"}
            except OSError:
                return {"status": "locked",
                        "detail": "遥测文件被占用，请关闭游戏后重试"}
        try:
            shutil.copy2(self._bundled, target)
            return {"status": "installed", "detail": "遥测插件安装完成，重启游戏生效"}
        except OSError:
            return {"status": "locked", "detail": "无法写入游戏目录，请检查权限"}

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return ""
