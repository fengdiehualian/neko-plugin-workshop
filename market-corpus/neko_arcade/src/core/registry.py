"""游戏注册表：发现小游戏（标准为每个游戏一个文件夹），开关管理，配置注入。"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from typing import Any, Dict, Iterator, List, Optional

from .config_manager import ConfigManager
from .contracts import GameAdapter

log = logging.getLogger("neko_arcade.registry")


class GameRegistry:
    """管理全部小游戏。支持包游戏（games/xxx/）和单文件游戏（games/xxx.py）。"""

    def __init__(self, plugin: Any, config_manager: ConfigManager) -> None:
        self.plugin = plugin
        self.cfg_mgr = config_manager
        self._games: Dict[str, GameAdapter] = {}
        self._push: Any = None
        self._img: Any = None
        self._tts: Any = None
        self._llm: Any = None
        self._photo: Any = None
        self._render: Any = None          # 渲染桥接(插件主体提供, 游戏只给数据)
        self._companion: Any = None       # 陪伴层(带宿主猫娘人格)
        self._llm_gateway: Any = None     # 统一 LLM 入口(场景/缓存/统计)

    # ── 注册与发现 ────────────────────────────

    def register(self, game: GameAdapter, config: Optional[Dict] = None) -> None:
        if game.id in self._games:
            log.warning("游戏 %s 已注册，跳过", game.id)
            return
        gc = self.cfg_mgr.load(game.id)
        game._config = gc.config
        game._help = gc.help
        # 关键词 = data/config/{id}/keywords.json + 游戏名(兜底, 保证游戏名一定能路由)
        kws = list(gc.get_keywords() or [])
        if game.name and game.name not in kws:
            kws.append(game.name)
        game._keywords = kws
        game._emotion_templates = gc.emotion_templates
        game._help_data = gc.help or {}       # 渲染桥接读它出帮助图(游戏不碰渲染)
        # 恢复启停状态(从 data/config/main/games.json)
        states = self.cfg_mgr.load_game_states()
        if isinstance(states, dict) and game.id in states:
            game.enabled = bool(states.get(game.id, True))
        game.bind_services(self._push, self._img, self._tts, self._llm, self._photo,
                           render=self._render, companion=self._companion,
                           llm_gateway=self._llm_gateway,
                           assets=getattr(self, "_assets", None))
        self._games[game.id] = game
        log.info("已注册游戏: %s (%s)%s", game.name, game.id,
                 "" if game.enabled else " [停用]")

    def unregister(self, gid: str) -> None:
        game = self._games.pop(gid, None)
        if game:
            log.info("已卸载游戏: %s", gid)

    async def discover(self) -> int:
        """扫描 games 包：包游戏（__init__.py）和单文件（.py）。"""
        from .. import games as games_pkg
        count = 0
        gpath = os.path.dirname(games_pkg.__file__)
        # 包游戏（walk_packages）
        # 标准形式：每个游戏一个文件夹（games/{id}/，含 __init__.py）
        for _, modname, ispkg in pkgutil.walk_packages(
                games_pkg.__path__, prefix=f"{games_pkg.__name__}."):
            if not ispkg:
                continue
            try:
                module = importlib.import_module(modname)
                cls = self._find_game_class(module)
                if cls is None:
                    continue
                self.register(cls(self.plugin))
                count += 1
            except Exception as exc:
                log.error("加载包游戏 %s 失败: %s", modname, exc)
        # 兜底：games/ 根目录下的单 .py 文件（兼容旧习惯，标准不推荐）
        for fname in os.listdir(gpath):
            if not fname.endswith(".py") or fname == "__init__.py":
                continue
            modname = f"{games_pkg.__name__}.{fname[:-3]}"
            try:
                module = importlib.import_module(modname)
                cls = self._find_game_class(module)
                if cls is None:
                    continue
                self.register(cls(self.plugin))
                count += 1
            except Exception as exc:
                log.error("加载单文件 %s 失败: %s", fname, exc)
        # 启动时自动把游戏启停状态写入 data/config/main/games.json
        # (保留已保存的启停, 新游戏默认启用; 之后开关操作也会更新此文件)
        try:
            states = self.cfg_mgr.load_game_states()
            if not isinstance(states, dict):
                states = {}
            changed = False
            for gid, game in self._games.items():
                if gid not in states:
                    states[gid] = game.enabled
                    changed = True
            if changed or not states:
                self.cfg_mgr.save_game_states(states)
            log.info("游戏启停状态已同步到 data/config/main/games.json (%d 个)", len(states))
        except Exception as exc:
            log.warning("写入游戏启停状态失败: %s", exc)
        return count

    def _find_game_class(self, module) -> Optional[type]:
        name = getattr(module, "game_class", None) or getattr(module, "__game_class__", None)
        if name:
            cls = getattr(module, name, None)
            if self._is_valid_game(cls):
                return cls
        for attr in dir(module):
            obj = getattr(module, attr)
            if self._is_valid_game(obj):
                return obj
        return None

    @staticmethod
    def _is_valid_game(cls) -> bool:
        return (isinstance(cls, type) and issubclass(cls, GameAdapter)
                and cls is not GameAdapter and not getattr(cls, "__abstractmethods__", None))

    # ── 开关管理 ──────────────────────────────

    async def set_enabled(self, gid: str, enabled: bool) -> bool:
        game = self._games.get(gid)
        if not game:
            return False
        game.enabled = enabled
        # 持久化到 data/config/main/games.json
        try:
            states = self.cfg_mgr.load_game_states()
            if not isinstance(states, dict):
                states = {}
            states[gid] = enabled
            self.cfg_mgr.save_game_states(states)
        except Exception as exc:
            log.warning("保存游戏启停状态失败: %s", exc)
        log.info("游戏 %s 已%s", gid, "启用" if enabled else "停用")
        return True

    def is_enabled(self, gid: str) -> bool:
        game = self._games.get(gid)
        return bool(game and game.enabled)

    def enabled_ids(self) -> List[str]:
        return [gid for gid, g in self._games.items() if g.enabled]

    # ── 查询 ──────────────────────────────────

    @property
    def game_ids(self) -> List[str]:
        return list(self._games.keys())

    def game_names(self) -> str:
        """返回游戏名称列表，用逗号分隔。"""
        return "、".join(g.name for g in self._games.values())

    @property
    def games(self) -> Iterator[GameAdapter]:
        return iter(self._games.values())

    def get(self, gid: str) -> Optional[GameAdapter]:
        return self._games.get(gid)

    def get_by_name(self, name: str) -> Optional[GameAdapter]:
        for g in self._games.values():
            if name in g.name or name in g.id:
                return g
        return None

    def get_meta(self, gid: str) -> Optional[Dict[str, Any]]:
        g = self.get(gid)
        return g.get_meta() if g else None

    def get_meta_list(self, with_help: bool = False) -> List[Dict[str, Any]]:
        """游戏卡片信息。with_help=True 时附带帮助命令（前 5 条，供 UI 快捷指令）。"""
        metas = []
        for g in self._games.values():
            meta = g.get_meta()
            meta["has_panel"] = bool(self.get_panel(g.id))
            if with_help:
                help_data = getattr(g, "_help", {}) or {}
                cmds = (help_data.get("commands", []) or [])[:5]
                meta["help_commands"] = [[c[0], c[1]] for c in cmds if isinstance(c, (list, tuple))]
            metas.append(meta)
        return metas

    async def get_statuses(self, user_id: str = "default") -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for gid, game in self._games.items():
            try:
                result[gid] = await game.get_status(user_id)
            except Exception as exc:
                log.error("游戏 %s 状态失败: %s", gid, exc)
                result[gid] = {"error": str(exc)}
        return result

    def get_panel(self, gid: str) -> Optional[Dict[str, Any]]:
        """获取游戏的面板配置 schema（无则 None）。"""
        game = self._games.get(gid)
        if not game:
            return None
        try:
            return game.support_panel() or None
        except Exception as exc:
            log.error("游戏 %s 面板 schema 失败: %s", gid, exc)
            return None

    async def get_help(self, gid: str) -> Optional[Dict[str, Any]]:
        """获取游戏帮助数据。"""
        game = self._games.get(gid)
        if not game:
            return None
        return {"commands": game._help.get("commands", []), "text": game._help.get("text", "")}