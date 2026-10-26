"""游戏状态机：从遥测快照推导游戏状态，门控所有交互（照欧卡方案 §2.1）。

状态：
  NO_GAME  — 游戏未开（mod 未连接）
  MENU     — 游戏开着但在主菜单/加载
  FISHING  — 钓鱼中（等待/收线/上鱼）
  CASINO   — 赌场（下注/轮盘）
  BOSS     — Boss 战

门控原则：OCR 屏幕畅聊 / 主动提议 / 氛围闲聊只在 FISHING/CASINO/BOSS
触发——游戏没开或主菜单时猫娘安静（不聊游戏内容）。
"""

from __future__ import annotations

from typing import Any

NO_GAME = "NO_GAME"
MENU = "MENU"
FISHING = "FISHING"
CASINO = "CASINO"
BOSS = "BOSS"

# 允许主动交互（OCR/提议/闲聊）的状态
INTERACTIVE_STATES = frozenset({FISHING, CASINO, BOSS})

# 状态 → 允许的事件类别（照 pawpilot scenario.py）
STATE_CATEGORIES = {
    NO_GAME: frozenset({"lifecycle"}),         # 游戏没开：只报进/出（game_start/game_end）
    MENU: frozenset({"lifecycle"}),            # 主菜单：只报进/出
    FISHING: frozenset({"caught", "casino", "grill", "boss", "journal", "lifecycle", "chatter"}),
    CASINO: frozenset({"casino", "lifecycle", "chatter"}),
    BOSS: frozenset({"boss", "lifecycle", "chatter"}),
}


class GameStateMachine:
    """根据遥测快照推导当前游戏状态。"""

    def __init__(self) -> None:
        self.current = NO_GAME
        self.prev: str | None = None

    def update(self, st: Any) -> str:
        """由快照推导状态并返回。"""
        if st is None or not getattr(st, "connected", False):
            return self._set(NO_GAME)
        if st.boss_active:
            return self._set(BOSS)
        if st.betting:
            return self._set(CASINO)
        # 钓鱼中：有玩家 + 在岛上（phase 有效）
        if getattr(st, "island", "") or st.phase in ("waiting", "reeling", "caught", "idle"):
            return self._set(FISHING)
        return self._set(MENU)

    def allow(self, category: str) -> bool:
        """当前状态是否允许该事件类别。"""
        return category in STATE_CATEGORIES.get(self.current, frozenset())

    def interactive(self) -> bool:
        """当前是否可主动交互（OCR/提议/闲聊）。"""
        return self.current in INTERACTIVE_STATES

    def force_no_game(self) -> None:
        """强制回到 NO_GAME（游戏退出/断连时调用）。"""
        self._set(NO_GAME)

    def _set(self, new: str) -> str:
        if new != self.current:
            self.prev = self.current
            self.current = new
        return self.current

    def snapshot(self) -> dict:
        return {"current": self.current, "prev": self.prev}
