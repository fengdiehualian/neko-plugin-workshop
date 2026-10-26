"""数据契约：mod 事件 / 游戏状态快照。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class FishEvent:
    """mod 回报的单个事件。"""

    name: str                      # cast/bite/caught/sold/bet/grill/boss/journal/seagull...
    ts: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)   # 事件附加数据（鱼字段/金额等）
    snapshot: Optional["FishState"] = None               # 事件发生时快照


@dataclass
class FishState:
    """游戏状态快照（mod 状态回报）。"""

    connected: bool = False
    # 钓鱼
    phase: str = "idle"            # idle/casting/waiting/bite/reeling/caught
    last_catch: Optional[Dict[str, Any]] = None   # 最近上钩的鱼
    # 环境
    island: str = ""
    island_index: int = 0
    # 玩家
    money: int = 0
    bait: str = ""
    owned_baits: list = field(default_factory=list)
    held: str = ""
    # 玩法面
    betting: bool = False
    bet_color: str = ""
    boss_active: bool = False
    boss_hp: int = 0
    boss_max_hp: int = 0
    grilling: bool = False
    on_boat: bool = False
    # 图鉴
    journal_count: int = 0
    journal_total: int = 0
    # 原始 mod 快照（保留全部字段，面板/问答用）
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def game_running(self) -> bool:
        return self.connected
