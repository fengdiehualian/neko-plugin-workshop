"""人生重开小游戏包: 人生重开模拟器(选天赋/分配属性/逐年人生/总结图)。

由 GameRegistry.discover() 自动发现，无需手动注册。
"""

from .game import RemakeGame

game_class = "RemakeGame"
__game_class__ = "RemakeGame"

__all__ = ["RemakeGame"]
