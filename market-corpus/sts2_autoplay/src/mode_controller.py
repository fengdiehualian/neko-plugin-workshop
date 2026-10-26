from __future__ import annotations

from typing import Any


class STS2ModeController:
    """猫娘的控制档位。

    - ``program``（自己玩）：照常决策并执行。默认。
    - ``advise``（听安排）：照常**计算**但**不执行**；非战斗决策界面一律等玩家发话
      （``sts2_execute_planned_operation`` 就是"我点头了，走一步"）。
      战斗永远自动——玩家替不了猫娘出牌，停手会把回合卡死。
    - ``standby``（彻底停手）：连计划都不算。

    ``advise`` 与 ``standby`` 都会被同步给 mod（``coop_control_mode``），让 mod 自己的
    自动玩驱动一起停手；只停插件等于没停。
    """

    _ALIASES = {
        "program": "program",
        "full-program": "program",
        "full_program": "program",
        "half-program": "program",
        "half_program": "program",
        "full-model": "program",
        "full_model": "program",
        "model": "program",
        "auto": "program",
        "全程序": "program",
        "半程序": "program",
        "全模型": "program",
        "自己玩": "program",
        "你自己玩": "program",
        "advise": "advise",
        "advise-only": "advise",
        "advise_only": "advise",
        "listen": "advise",
        "听安排": "advise",
        "听我安排": "advise",
        "你听我安排": "advise",
        "等我发话": "advise",
        "standby": "standby",
        "待机": "standby",
        "停手": "standby",
    }

    def __init__(self, mode: str = "program") -> None:
        self._default_mode = self.normalize(mode)

    def normalize(self, mode: Any) -> str:
        raw = str(mode or self._default_mode or "program").strip().lower().replace(" ", "_")
        return self._ALIASES.get(raw, "program")

    def is_standby(self, mode: Any) -> bool:
        return self.normalize(mode) == "standby"

    def is_advise(self, mode: Any) -> bool:
        return self.normalize(mode) == "advise"

    def describe(self, mode: Any) -> dict[str, Any]:
        normalized = self.normalize(mode)
        return {
            "mode": normalized,
            "standby": self.is_standby(normalized),
            "advise": self.is_advise(normalized),
            # advise 仍然要算：玩家问"你打算怎么走"时得答得出来，所以规划器照跑。
            "allows_planner": normalized in {"program", "advise"},
            # 是否允许自动执行。advise 下由 state_machine.ADVISE_HOLD_SCREENS 再收一道，
            # 战斗不在那份清单里，所以战斗仍然自动打。
            "allows_execute": normalized == "program",
            "allows_game_llm": False,
            "prefers_heuristic": normalized in {"program", "advise"},
            "prefers_model": False,
        }


__all__ = ["STS2ModeController"]
