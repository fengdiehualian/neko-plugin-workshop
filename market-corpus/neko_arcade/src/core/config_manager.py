"""配置统一管理：每个小游戏的配置和帮助数据集中存放于 data/。

数据目录解析（N.E.K.O 0.9.0.2+ 适配）：
- 新宿主把插件代码装在不可变目录（.neko-plugin-installations/plugins/<id>），
  安装时会剥掉包内 data/；用户数据落在 SDK 的 storage dir：
  %LOCALAPPDATA%\\N.E.K.O\\plugins\\<id>\\data（可用 NEKO_STORAGE_SELECTED_ROOT 改根）。
- 因此优先用插件的 self.data_path()；没有该 API 的老宿主/本地开发回退到代码目录旁 data/。
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Optional

# 兜底默认值(代码目录旁的 data/)，仅在拿不到 SDK 数据目录时使用
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_PLUGIN_ROOT, "data")
CONFIG_DIR = os.path.join(DATA_DIR, "config")


def resolve_data_dir(plugin: Any = None) -> str:
    """解析插件的数据目录(优先 SDK 存储目录, 回退代码目录旁 data/)。"""
    data_path = getattr(plugin, "data_path", None)
    if callable(data_path):
        try:
            resolved = data_path()
            if resolved:
                return str(resolved)
        except Exception:
            pass
    config_dir = getattr(plugin, "config_dir", None) or getattr(plugin, "plugin_dir", None)
    base = str(config_dir) if config_dir else _PLUGIN_ROOT
    return os.path.join(base, "data")


#: 出厂内容文件(随插件升级刷新)。config.json 是**用户设置**, 永不覆盖。
_REFRESH_NAMES = ("help.json", "keywords.json", "emotion.json")


def _same_bytes(a: str, b: str) -> bool:
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _seed_defaults(data_dir: str) -> None:
    """把出厂 ``data/config`` 同步进用户数据目录。

    ⚠️ 旧实现只在 ``config/`` 目录**不存在**时整体拷贝 —— 一旦用户数据目录
    已有 ``config/``（哪怕只存过一次面板设置），升级带来的新 help/keywords/
    emotion 就**永远不会补**，表现为「帮助图不渲染、只有一句『X 玩法帮助：』」。

    现在的规则：
      · 缺的文件 → 从出厂目录补；
      · ``help.json`` / ``keywords.json`` / ``emotion.json``（出厂内容）→ 有变化就刷新；
      · ``config.json``（用户设置）→ **永不覆盖**，用户改过的值保留。
    """
    source = CONFIG_DIR
    target = os.path.join(data_dir, "config")
    if not os.path.isdir(source) or os.path.abspath(source) == os.path.abspath(target):
        return
    for root, _dirs, files in os.walk(source):
        rel = os.path.relpath(root, source)
        for name in files:
            src = os.path.join(root, name)
            dst = os.path.join(target, "" if rel == "." else rel, name)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)
                elif name in _REFRESH_NAMES and not _same_bytes(src, dst):
                    shutil.copy2(src, dst)
            except Exception:
                continue



class GameConfig:
    """一个游戏的配置 + 帮助数据 + 情感模板 + 关键词。"""

    def __init__(self, game_id: str, config: Dict[str, Any],
                 help_data: Optional[Dict[str, Any]] = None,
                 emotion_templates: Optional[Dict[str, Any]] = None,
                 keywords: Optional[List[str]] = None) -> None:
        self.game_id = game_id
        self.config = config
        self.help = help_data or {}
        self.emotion_templates = emotion_templates or {}
        self.keywords = keywords or []

    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def get_help_commands(self) -> list:
        """帮助指令列表 [(指令, 说明)]。"""
        return self.help.get("commands", []) or []

    def get_help_text(self) -> str:
        return self.help.get("text", "") or ""

    def get_emotion(self, key: str, default: Any = None) -> Any:
        """获取情感模板。"""
        return self.emotion_templates.get(key, default)

    def get_keywords(self) -> List[str]:
        """获取关键词列表。"""
        return self.keywords


class ConfigManager:
    """管理所有游戏的配置 + 主插件配置。

    目录结构:
    - data/config/{game_id}/    每个游戏一个文件夹(config/help/emotion/keywords.json)
    - data/main/                主插件配置文件夹(与 data/config 平级)
      - games.json              游戏启停状态(启动时自动扫描写入, 开关时更新)
      - config.json             插件其他配置(LLM 配置等)
    """

    MAIN_DIR_NAME = "main"

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self.data_dir = str(data_dir) if data_dir else DATA_DIR
        self.config_dir = os.path.join(self.data_dir, "config")
        _seed_defaults(self.data_dir)
        self._cache: Dict[str, GameConfig] = {}

    def _game_dir(self, game_id: str) -> str:
        return os.path.join(self.config_dir, game_id)

    def _main_dir(self) -> str:
        d = os.path.join(self.data_dir, self.MAIN_DIR_NAME)
        os.makedirs(d, exist_ok=True)
        return d

    # ── 主插件配置(独立文件) ──────────────────

    def load_main_config(self) -> Dict[str, Any]:
        """读取主插件配置 data/main/config.json。"""
        return self._read_json(os.path.join(self._main_dir(), "config.json"), {})

    def save_main_config(self, cfg: Dict[str, Any]) -> None:
        """保存主插件配置 data/main/config.json。"""
        with open(os.path.join(self._main_dir(), "config.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def load_game_states(self) -> Dict[str, Any]:
        """读取游戏启停配置 data/main/games.json。"""
        return self._read_json(os.path.join(self._main_dir(), "games.json"), {})

    def save_game_states(self, states: Dict[str, Any]) -> None:
        """保存游戏启停配置 data/main/games.json(自动写入)。"""
        with open(os.path.join(self._main_dir(), "games.json"), "w", encoding="utf-8") as f:
            json.dump(states, f, ensure_ascii=False, indent=2)

    # ── 游戏配置 ────────────────────────────

    def load(self, game_id: str) -> GameConfig:
        """加载（或创建默认）一个游戏的配置。

        读不到/空文件时**回读出厂 data/config**（不写盘）：即使播种被跳过、
        用户目录只读、或旧数据目录缺文件，help/keywords/emotion 也不会变空。
        ``config.json`` 是用户设置：存在就用用户的，不存在才用出厂默认。
        """
        if game_id in self._cache:
            return self._cache[game_id]
        gdir = self._game_dir(game_id)
        sdir = os.path.join(CONFIG_DIR, game_id)

        def _local_or_shipped(name: str, empty: Any, *, keep_empty: bool = False) -> Any:
            local = self._read_json(os.path.join(gdir, name), None)
            if local is None or (not keep_empty and not local):
                shipped = self._read_json(os.path.join(sdir, name), None)
                return shipped if shipped is not None else empty
            return local

        config = _local_or_shipped("config.json", {}, keep_empty=True)
        help_data = _local_or_shipped("help.json", {})
        emotion_templates = _local_or_shipped("emotion.json", {})
        keywords = _local_or_shipped("keywords.json", [])
        gc = GameConfig(game_id, config, help_data, emotion_templates, keywords)
        self._cache[game_id] = gc
        return gc

    def save(self, game_id: str, config: Dict[str, Any]) -> None:
        """保存游戏配置。"""
        gdir = self._game_dir(game_id)
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        if game_id in self._cache:
            self._cache[game_id].config = config

    def save_help(self, game_id: str, help_data: Dict[str, Any]) -> None:
        """保存游戏帮助数据。"""
        gdir = self._game_dir(game_id)
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "help.json"), "w", encoding="utf-8") as f:
            json.dump(help_data, f, ensure_ascii=False, indent=2)
        if game_id in self._cache:
            self._cache[game_id].help = help_data

    def get_game_ids(self) -> list:
        if not os.path.isdir(self.config_dir):
            return []
        return [d for d in os.listdir(self.config_dir) if os.path.isdir(os.path.join(self.config_dir, d))]

    def clear(self) -> None:
        self._cache.clear()

    @staticmethod
    def _read_json(path: str, default: Any) -> Any:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default
