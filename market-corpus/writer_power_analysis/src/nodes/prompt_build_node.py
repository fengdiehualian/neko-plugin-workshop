"""Prompt build node."""

from __future__ import annotations

from ..prompts import build_system_prompt as build_full_system_prompt
from .node_logging import LoggedNode


class PromptBuildNode(LoggedNode):
    """Build the full Ink Battles backend system prompt."""

    node_name = "prompt.build"

    def run(self, mode: str) -> str:
        """Build a complete system prompt for one analysis mode."""

        started = self._begin(mode=mode)
        try:
            system_prompt = build_full_system_prompt(mode)
            self._end(
                started,
                mode=mode,
                prompt_chars=len(system_prompt),
                placeholder_remaining="{{MODE_INSTRUCTION}}" in system_prompt,
            )
            return system_prompt
        except Exception as exc:
            self._fail(started, exc)
            raise
