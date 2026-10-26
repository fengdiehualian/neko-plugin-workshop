"""俄罗斯轮盘小游戏包：和猫娘赌猫粮(轮盘签到/装弹/开枪/认输/排行)。

由 GameRegistry.discover() 自动发现，无需手动注册。
"""

from .game import RussianGame

game_class = "RussianGame"
__game_class__ = "RussianGame"

__all__ = ["RussianGame"]
