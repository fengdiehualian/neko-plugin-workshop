from __future__ import annotations

import logging
import sys
import types as _types

from .agent_core import GameLLMAgent
from .agent_message_router import AgentMessageRouter
from .agent_prompt import (
    _bounded_choice_instruction_text as _bounded_choice_instruction_text,
)
from .agent_prompt import _context_line_count as _context_line_count
from .agent_scene_tracker import AgentSceneTracker
from .agent_shared import (
    _compute_dynamic_line_limit as _compute_dynamic_line_limit,
)
from .agent_shared import build_summarize_context as build_summarize_context
from .agent_shared import time as time
from .agent_shared import try_focus_target_window as try_focus_target_window

# Preserve the historical public module path for inspect/pickle-style consumers.
AgentMessageRouter.__module__ = __name__
AgentSceneTracker.__module__ = __name__
GameLLMAgent.__module__ = __name__

def _iter_proxy_modules(package: str):
    prefix = f"{package}.agent_"
    for qualified_name, module in sorted(sys.modules.items()):
        if qualified_name.startswith(prefix):
            yield qualified_name, module


class _ShimModule(_types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        package = __name__.rsplit(".", 1)[0]
        failures: list[tuple[str, Exception]] = []
        for qualified_name, module in _iter_proxy_modules(package):
            if module is not None and hasattr(module, name):
                try:
                    setattr(module, name, value)
                except Exception as exc:
                    failures.append((qualified_name, exc))
                    logging.getLogger(__name__).warning(
                        "galgame game_llm_agent shim propagation failed: module=%s attr=%s",
                        qualified_name,
                        name,
                        exc_info=True,
                    )
        if failures:
            failed_modules = ", ".join(module_name for module_name, _exc in failures)
            raise RuntimeError(
                f"game_llm_agent shim propagation failed for {name}: {failed_modules}"
            )


sys.modules[__name__].__class__ = _ShimModule
