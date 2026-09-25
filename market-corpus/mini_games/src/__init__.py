"""
猫娘小游戏屋 v0.1

让猫娘在聊天里陪你玩的小游戏合集：
  - gacha_pull   : 抽卡·单抽
  - gacha_ten    : 抽卡·十连（保底至少 R）
  - gacha_status : 抽卡·图鉴（进度 / 保底 / 收集统计）
  - gacha_reset  : 抽卡·重置（清空当前玩家进度）
  - fortune      : 今日运势（每天同一人结果稳定）
  - dice         : 摇骰子（可指定颗数 / 面数）
"""

from __future__ import annotations

import threading
from typing import Optional

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

from .game_core import (
    GachaEngine,
    Session,
    daily_fortune,
    roll_dice,
)

_PLUGIN_ID = "mini_games"
_DEFAULT_WEIGHTS = {"N": 600, "R": 280, "SR": 90, "SSR": 25, "UR": 5}
_DEFAULT_PITY = 50
_VERSION = "0.1.0"


@neko_plugin
class MiniGamesPlugin(NekoPluginBase):
    """猫娘小游戏屋 - N.E.K.O 插件入口"""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._lock = threading.Lock()
        self._sessions: dict = {}
        self._engine: Optional[GachaEngine] = None

    # ── 会话隔离 ──
    def _get_session(self, session_id: str) -> Session:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session()
            return self._sessions[session_id]

    # ── lifecycle ──
    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        section = cfg.get("mini_games") if isinstance(cfg.get("mini_games"), dict) else {}
        weights = section.get("rarity_weights") or _DEFAULT_WEIGHTS
        pity = int(section.get("pity_ssr", _DEFAULT_PITY))
        self._engine = GachaEngine(weights=weights, pity_ssr=pity)
        self.logger.info("MiniGames 插件已启动 (pity_ssr=%s)", pity)
        return Ok({"status": "running", "version": _VERSION})

    @lifecycle(id="shutdown")
    def shutdown(self, **_):
        self.logger.info("MiniGames 插件已停止")
        return Ok({"status": "shutdown"})

    # ── 抽卡：单抽 ──
    @llm_tool(
        name="mini_games_gacha_pull",
        description="抽一次卡（Gacha），返回稀有度与抽到的猫娘道具。当用户说「抽卡 / 单抽 / 来一发」时使用。",
        parameters={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "玩家/会话标识，用于隔离不同人的抽卡进度与图鉴",
                }
            },
            "required": [],
        },
        timeout=15.0,
    )
    @plugin_entry(
        id="gacha_pull",
        name="抽卡·单抽",
        description="抽一次卡，返回稀有度与道具",
        input_schema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": [],
        },
        llm_result_fields=["rarity", "item", "is_rare", "pity", "total_pulls", "message"],
    )
    async def gacha_pull(self, session_id: str = "default", **_):
        try:
            if self._engine is None:
                return Err(SdkError("抽卡引擎未初始化，请检查插件是否启动"))
            sess = self._get_session(session_id)
            return Ok(self._engine.pull_one(sess))
        except Exception as e:
            return Err(SdkError(f"抽卡失败: {e}"))

    # ── 抽卡：十连 ──
    @llm_tool(
        name="mini_games_gacha_ten",
        description="十连抽（Gacha ten-pull），保底至少 R，更易出货。当用户说「十连 / 十连抽 / 来个十连」时使用。",
        parameters={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "玩家/会话标识，用于隔离不同人的抽卡进度与图鉴",
                }
            },
            "required": [],
        },
        timeout=20.0,
    )
    @plugin_entry(
        id="gacha_ten",
        name="抽卡·十连",
        description="十连抽，保底至少 R",
        input_schema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": [],
        },
        llm_result_fields=[
            "best_rarity",
            "best_item",
            "pity",
            "total_pulls",
            "message",
            "results",
        ],
    )
    async def gacha_ten(self, session_id: str = "default", **_):
        try:
            if self._engine is None:
                return Err(SdkError("抽卡引擎未初始化，请检查插件是否启动"))
            sess = self._get_session(session_id)
            return Ok(self._engine.pull_ten(sess))
        except Exception as e:
            return Err(SdkError(f"十连失败: {e}"))

    # ── 抽卡：图鉴 ──
    @llm_tool(
        name="mini_games_gacha_status",
        description="查看抽卡图鉴与保底进度：累计抽数、当前保底计数、已收集道具数量与 TOP 收集。",
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "玩家/会话标识"}
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="gacha_status",
        name="抽卡·图鉴",
        description="查看抽卡进度与图鉴",
        input_schema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": [],
        },
        llm_result_fields=[
            "total_pulls",
            "pity",
            "unique_items",
            "total_items",
            "top_items",
        ],
    )
    async def gacha_status(self, session_id: str = "default", **_):
        try:
            sess = self._get_session(session_id)
            return Ok(sess.summary())
        except Exception as e:
            return Err(SdkError(f"查询失败: {e}"))

    # ── 抽卡：重置 ──
    @llm_tool(
        name="mini_games_gacha_reset",
        description="重置当前玩家的抽卡进度与图鉴（保底计数、累计抽数、收集全部清空）。",
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "玩家/会话标识"}
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="gacha_reset",
        name="抽卡·重置",
        description="重置抽卡进度与图鉴",
        input_schema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": [],
        },
        llm_result_fields=["status", "session_id"],
    )
    async def gacha_reset(self, session_id: str = "default", **_):
        try:
            with self._lock:
                self._sessions[session_id] = Session()
            return Ok({"status": "reset", "session_id": session_id})
        except Exception as e:
            return Err(SdkError(f"重置失败: {e}"))

    # ── 今日运势 ──
    @llm_tool(
        name="mini_games_fortune",
        description="摇一摇今日运势：大吉/中吉/小吉/平/凶，外加幸运数字、幸运色、宜做之事。每天同一人结果稳定。",
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "玩家/会话标识"},
                "day": {
                    "type": "string",
                    "description": "可选，格式 YYYY-MM-DD，默认今天",
                },
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="fortune",
        name="今日运势",
        description="今日运势占卜",
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "day": {"type": "string"},
            },
            "required": [],
        },
        llm_result_fields=[
            "day",
            "level",
            "lucky_number",
            "lucky_color",
            "suggested_activities",
            "advice",
            "message",
        ],
    )
    async def fortune(self, session_id: str = "default", day: Optional[str] = None, **_):
        try:
            return Ok(daily_fortune(session_id, day))
        except Exception as e:
            return Err(SdkError(f"占卜失败: {e}"))

    # ── 摇骰子 ──
    @llm_tool(
        name="mini_games_dice",
        description="摇骰子，返回每颗结果与合计。可指定颗数与面数（默认 1 颗 6 面）。当用户说「掷骰子 / 丢个骰子」时使用。",
        parameters={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "骰子颗数，默认 1，最多 100"},
                "sides": {"type": "integer", "description": "每颗面数，默认 6，范围 2-1000"},
            },
            "required": [],
        },
        timeout=10.0,
    )
    @plugin_entry(
        id="dice",
        name="摇骰子",
        description="掷骰子，返回每颗结果与合计",
        input_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}, "sides": {"type": "integer"}},
            "required": [],
        },
        llm_result_fields=["results", "sum", "count", "sides", "message"],
    )
    async def dice(self, count: int = 1, sides: int = 6, **_):
        try:
            return Ok(roll_dice(count=count, sides=sides))
        except Exception as e:
            return Err(SdkError(f"掷骰失败: {e}"))
