"""neko_terraria：AI 猫娘作为独立玩家加入泰拉瑞亚世界的 N.E.K.O 插件。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from plugin.sdk.plugin import NekoPluginBase, neko_plugin

from .entries import (
    GoalToolsMixin,
    LifecycleMixin,
    MemoryEntriesMixin,
    UiActionsMixin,
    UiContextMixin,
)


@neko_plugin
class NTerrariaPlugin(
    NekoPluginBase,
    LifecycleMixin,
    UiActionsMixin,
    UiContextMixin,
    GoalToolsMixin,
    MemoryEntriesMixin,
):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.logger = self.enable_file_logging(log_level="INFO")

        self._agent = None
        self._autonomous_brain = None
        self._config: Dict[str, Any] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        self._init_core_services()
