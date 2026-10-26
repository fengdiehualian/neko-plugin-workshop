"""棋类对弈 —— 一个入口, 多种棋(子玩法按 boards/ 协议挂载)。"""

from .game import BoardGame

game_class = "BoardGame"
__game_class__ = "BoardGame"

__all__ = ["BoardGame"]
