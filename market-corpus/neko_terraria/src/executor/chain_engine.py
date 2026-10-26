"""行为链引擎：序列动作编排与条件分支。"""

import inspect
from typing import Any, Callable, List


class ChainEngine:
    def __init__(self) -> None:
        self.steps: List[Callable[[], Any]] = []

    def add(self, step: Callable[[], Any]) -> None:
        self.steps.append(step)

    async def run(self) -> None:
        for step in self.steps:
            try:
                result = step()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                break
