"""neko_terraria 人性化打磨层：反应延迟/抖动/注意力漂移/习惯。"""

from .attention import AttentionDrift
from .habits import PersonalHabits
from .human_timing import HumanTiming
from .imperfections import ImperfectionInjector

__all__ = ["HumanTiming", "ImperfectionInjector", "AttentionDrift", "PersonalHabits"]
