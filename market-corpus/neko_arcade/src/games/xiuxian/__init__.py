"""诸天修仙小游戏包：单人修仙 + 猫娘陪玩(道侣/徒弟/宗门成员三合一)。

由 GameRegistry.discover() 自动发现，无需手动注册。
"""

from .game import XiuxianGame

game_class = "XiuxianGame"
__game_class__ = "XiuxianGame"

__all__ = ["XiuxianGame"]
