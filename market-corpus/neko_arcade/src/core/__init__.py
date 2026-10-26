"""核心层。"""

from .config_manager import ConfigManager, GameConfig
from .contracts import GameAdapter, build_fact
from .registry import GameRegistry
from .runtime import ArcadeRuntime

__all__ = ["ConfigManager", "GameConfig", "GameAdapter", "build_fact", "GameRegistry", "ArcadeRuntime"]