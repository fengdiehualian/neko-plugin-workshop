# -*- coding: utf-8 -*-
"""操作宏录制与回放：记录键鼠操作序列，保存为 JSON 宏文件，回放执行。"""

from __future__ import annotations

import json
import os
import time
from typing import Any


class MacroStep:
    """单步操作记录。"""

    def __init__(self, step_type: str, params: dict[str, Any], delay: float = 0):
        self.type = step_type  # "press", "type", "mouse_move", "mouse_click", "mouse_drag", "mouse_wheel", "wait", "sequence"
        self.params = params
        self.delay = delay  # 此步执行后等待的秒数

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "params": self.params, "delay": self.delay}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MacroStep":
        return cls(
            step_type=d["type"],
            params=d.get("params", {}),
            delay=d.get("delay", 0),
        )


class Macro:
    """一个完整的宏定义。"""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.steps: list[MacroStep] = []
        self._created = time.time()
        self.repeat: int = 1           # 循环次数
        self.repeat_delay: float = 0.5  # 循环间延迟

    def add(self, step: MacroStep):
        self.steps.append(step)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "repeat": self.repeat,
            "repeat_delay": self.repeat_delay,
            "created": self._created,
            "step_count": len(self.steps),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Macro":
        m = cls(name=d["name"], description=d.get("description", ""))
        m.repeat = d.get("repeat", 1)
        m.repeat_delay = d.get("repeat_delay", 0.5)
        for s in d.get("steps", []):
            m.add(MacroStep.from_dict(s))
        return m

    def save(self, path: str):
        """保存宏到 JSON 文件。"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Macro":
        """从 JSON 文件加载宏。"""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


class MacroRecorder:
    """宏录制器：记录用户指定的操作步骤。"""

    def __init__(self):
        self._recording: Macro | None = None
        self._started: float | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording is not None

    def start(self, name: str = "", description: str = "") -> dict[str, Any]:
        if self._recording:
            return {"ok": False, "error": "已在录制中"}
        self._recording = Macro(name=name or f"macro_{int(time.time())}", description=description)
        self._started = time.time()
        return {"ok": True, "name": self._recording.name, "step_count": 0}

    def record_step(self, step_type: str, params: dict[str, Any], delay: float = 0) -> dict[str, Any]:
        """记录一步操作。"""
        if not self._recording:
            return {"ok": False, "error": "未在录制中，请先 start"}
        step = MacroStep(step_type=step_type, params=params, delay=delay)
        self._recording.add(step)
        return {"ok": True, "step": len(self._recording.steps), "type": step_type}

    def stop(self) -> dict[str, Any]:
        if not self._recording:
            return {"ok": False, "error": "未在录制中"}
        result = self._recording.to_dict()
        self._recording = None
        self._started = None
        return {"ok": True, "macro": result}

    def cancel(self):
        self._recording = None
        self._started = None

    def get_current(self) -> dict[str, Any] | None:
        if not self._recording:
            return None
        return self._recording.to_dict()


async def play_macro(
    macro: Macro,
    executor: Any,  # async callable (step_type, params) -> result
) -> dict[str, Any]:
    """回放一个宏。

    executor: 异步函数，接收 (step_type, step_params) 并执行。
    executor 可能返回 Err(SdkError)（插件工具层的失败约定）而不是抛
    异常 —— 两种失败形态都要计入 errors，否则宏每步全失败也报
    ok: True, errors: 0。
    """
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    repeat = max(1, min(int(macro.repeat or 1), 100))
    for rep in range(repeat):
        if rep > 0 and macro.repeat_delay > 0:
            await asyncio_sleep(macro.repeat_delay)

        for i, step in enumerate(macro.steps):
            try:
                result = await executor(step.type, step.params)
                step_ok = not _is_err_result(result)
                if not step_ok:
                    errors.append({
                        "step": i + 1,
                        "type": step.type,
                        "error": str(_err_message(result)),
                    })
                results.append({
                    "step": i + 1,
                    "type": step.type,
                    "ok": step_ok,
                    "result": result,
                })
            except Exception as e:
                errors.append({"step": i + 1, "type": step.type, "error": str(e)})
                results.append({"step": i + 1, "type": step.type, "ok": False, "error": str(e)})

            if step.delay > 0:
                await asyncio_sleep(step.delay)

    return {
        "ok": len(errors) == 0,
        "total_steps": len(macro.steps) * repeat,
        "success": len(results) - len(errors),
        "errors": len(errors),
        "error_details": errors if errors else None,
        "results": results,
    }


def _is_err_result(result: Any) -> bool:
    """识别插件 SDK 的 Err 失败结果（避免循环导入，鸭子类型判断）。"""
    if result is None:
        return False
    if isinstance(result, dict):
        return result.get("ok") is False
    # plugin.sdk.result.Err 实例带有 .err_value / .is_err 之类特征；
    # 直接探测类型名字符串，避免硬依赖 SDK 内部结构。
    return type(result).__name__ == "Err"


def _err_message(result: Any) -> str:
    inner = getattr(result, "err_value", None)
    if inner is None:
        inner = getattr(result, "value", None)
    return str(inner if inner is not None else result)


# 内联异步 sleep 避免循环导入
async def asyncio_sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)


# ── 快速宏模板 ─────────────────────────────────────────────────────


def macro_template_launch_game(
    command: str, window_query: str = "", wait_s: float = 5.0
) -> Macro:
    """模板：启动游戏并等待窗口出现。"""
    m = Macro(name="启动游戏", description=f"启动 {command} 并等待窗口")
    m.add(MacroStep("launch_app", {"command": command, "window_query": window_query}))
    m.add(MacroStep("wait", {"seconds": wait_s}))
    return m


def macro_template_click_sequence(
    clicks: list[tuple[int, int]], delay: float = 0.3
) -> Macro:
    """模板：顺序点击一组坐标。"""
    m = Macro(name="点击序列", description=f"点击 {len(clicks)} 个坐标")
    for x, y in clicks:
        m.add(MacroStep("mouse_click", {"x": x, "y": y}))
        m.add(MacroStep("wait", {"seconds": delay}))
    return m


def macro_template_press_sequence(
    keys: list[str], delay: float = 0.1
) -> Macro:
    """模板：顺序按下多个按键。"""
    m = Macro(name="按键序列", description=f"按下 {len(keys)} 个按键")
    for k in keys:
        m.add(MacroStep("press", {"keys": k}))
        m.add(MacroStep("wait", {"seconds": delay}))
    return m


def macro_template_loop(
    inner: Macro, times: int = 10, interval: float = 0.5
) -> Macro:
    """模板：将已有宏循环执行。"""
    inner.repeat = times
    inner.repeat_delay = interval
    return inner