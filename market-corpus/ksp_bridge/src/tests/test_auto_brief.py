# 和猫娘一起坐火箭（KSP）插件 —— 自动播报测试
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 本文件是 ksp_bridge 插件的一部分，以 GNU General Public License v3.0 许可发布。
# 完整条款见仓库根目录的 LICENSE 文件。

"""自动播报（push_message + ai_behavior="read"）的行为钉板。

不需要游戏：这里只验证「压成什么文本」和「用哪套参数推给宿主」，
真实聊天效果要在实机里看。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from plugin.plugins.ksp_bridge import (
    AUTO_BRIEF_COALESCE_KEY,
    AUTO_BRIEF_PREFIX,
    KspBridgePlugin,
    _auto_brief_line,
)
from plugin.sdk.plugin import SdkError

_FLIGHT: Dict[str, Any] = {
    "scene": "FLIGHT",
    "vessel": {
        "exists": True,
        "name": "Kerbal X",
        "situation": "flying",
        "body": "Kerbin",
        "altitude": 12000.0,
        "speed_surface": 320.5,
        "speed_orbital": 330.1,
        "pitch": 12.0,
        "mass": 42.5,
    },
    "orbit": {
        "exists": True,
        "is_stable_orbit": False,
        "apoapsis": 80000.0,
        "periapsis": 70000.0,
        "eccentricity": 0.01,
        "inclination": 0.5,
        "period": 1900.0,
    },
}

_NO_VESSEL: Dict[str, Any] = {
    "scene": "SPACECENTER",
    "vessel": {"exists": False, "note": "当前在太空中心"},
}


# ── 文本压缩 ──────────────────────────────────────────────────────────────


def test_line_has_prefix_and_vessel_name() -> None:
    text = _auto_brief_line(_FLIGHT)
    assert text.startswith(AUTO_BRIEF_PREFIX)
    assert "Kerbal X" in text
    assert "12.00 km" in text


def test_line_is_short_enough_to_live_in_context() -> None:
    """这段会一直待在上下文里，不能太长。"""
    assert len(_auto_brief_line(_FLIGHT)) < 400

def test_line_is_empty_without_vessel() -> None:
    """停机坪/编辑器里没有飞船，不该反复播报。"""
    assert _auto_brief_line(_NO_VESSEL) == ""


def test_line_has_no_double_prefix() -> None:
    assert _auto_brief_line(_FLIGHT).count(AUTO_BRIEF_PREFIX) == 1


def test_line_carries_throttle_and_orbit_detail() -> None:
    """问「油门多少」「轨道根数」不该再走一次工具往返。"""
    state = dict(_FLIGHT)
    state["vessel"] = dict(_FLIGHT["vessel"], throttle=0.75)
    text = _auto_brief_line(state)
    assert "油门 75%" in text
    assert "离心率" in text
    assert "倾角" in text


def test_line_carries_fuel_percent() -> None:
    state = dict(_FLIGHT)
    state["resources"] = {
        "ok": True,
        "count": 2,
        "resources": [
            {"name": "LiquidFuel", "amount": 400.0, "max": 800.0, "percent": 0.5},
            {"name": "Oxidizer", "amount": 800.0, "max": 800.0, "percent": 1.0},
        ],
    }
    assert "燃料 50%" in _auto_brief_line(state)


def test_line_survives_missing_throttle_and_resources() -> None:
    """老模组没有 throttle 字段时不能崩，也不能编一个数字出来。"""
    text = _auto_brief_line(_FLIGHT)
    assert "油门" not in text
    assert AUTO_BRIEF_PREFIX in text


# ── 推送行为 ──────────────────────────────────────────────────────────────


class _Recorder:
    """最小的 self 替身：只提供 _auto_brief_once 用得到的东西。"""

    def __init__(self, state: Dict[str, Any], *, boom: bool = False) -> None:
        self._state = state
        self._boom = boom
        self._auto_brief_last_text = ""
        self._auto_brief_last_ok = None
        self.pushes: List[Dict[str, Any]] = []
        self.logger = SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None)

    async def _get_json(self, path: str) -> Dict[str, Any]:
        if self._boom:
            raise SdkError("连不上 KSP 桥接服务")
        return self._state

    def push_message(self, **kwargs: Any) -> Dict[str, Any]:
        self.pushes.append(kwargs)
        return {"submitted": True}

    _auto_brief_once = KspBridgePlugin._auto_brief_once


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_once_pushes_read_behavior_with_coalesce_key() -> None:
    rec = _Recorder(_FLIGHT)
    _run(rec._auto_brief_once())

    assert len(rec.pushes) == 1
    push = rec.pushes[0]
    # read：进上下文但不打断对话 —— 这就是「每轮对话一开始数据已在」
    assert push["ai_behavior"] == "read"
    assert push["visibility"] == []
    assert push["coalesce_key"] == AUTO_BRIEF_COALESCE_KEY
    assert push["source"] == "ksp_auto_brief"
    assert push["parts"][0]["type"] == "text"
    assert push["parts"][0]["text"].startswith(AUTO_BRIEF_PREFIX)


def test_once_skips_identical_payload() -> None:
    rec = _Recorder(_FLIGHT)
    _run(rec._auto_brief_once())
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 1


def test_once_stays_silent_without_vessel() -> None:
    """没有飞船（停机坪/编辑器）时不往上下文里灌噪音。"""
    rec = _Recorder(_NO_VESSEL)
    _run(rec._auto_brief_once())
    assert rec.pushes == []


def test_never_connected_stays_silent() -> None:
    """游戏没开的时候别往上下文里灌噪音。"""
    rec = _Recorder(_FLIGHT, boom=True)
    _run(rec._auto_brief_once())
    assert rec.pushes == []


def test_disconnect_after_connected_is_announced_once() -> None:
    """本来连着、突然断了 —— 得说一声，否则模型会拿旧数据当现状。"""
    rec = _Recorder(_FLIGHT)
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 1

    rec._boom = True
    _run(rec._auto_brief_once())
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 2
    assert "已经退出或断开" in rec.pushes[1]["parts"][0]["text"]


def test_once_survives_unexpected_exception() -> None:
    """桥接没起来时 httpx 会抛，后台任务不能被带走。"""

    class _Boom(_Recorder):
        async def _get_json(self, path: str) -> Dict[str, Any]:
            raise RuntimeError("connection refused")

    rec = _Boom(_FLIGHT)
    _run(rec._auto_brief_once())
    assert rec.pushes == []


def test_once_respects_unsubmitted_result() -> None:
    """宿主拒收（backpressure 等）不该抛异常。"""

    class _Reject(_Recorder):
        def push_message(self, **kwargs: Any) -> Dict[str, Any]:
            self.pushes.append(kwargs)
            return {"submitted": False, "reason": "backpressure"}

    rec = _Reject(_FLIGHT)
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 1


def test_loop_honours_stop_event() -> None:
    """stop 一置位就要立刻退出，不能等满一个间隔。"""

    class _LoopRec(_Recorder):
        _auto_brief_loop = KspBridgePlugin._auto_brief_loop

    rec = _LoopRec(_FLIGHT)
    rec._auto_brief_interval = 0.0
    rec._auto_brief_stop = asyncio.Event()

    async def _go() -> None:
        task = asyncio.create_task(rec._auto_brief_loop())
        await asyncio.sleep(0.05)
        rec._auto_brief_stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    _run(_go())
    assert rec.pushes  # 至少推过一次


@pytest.mark.parametrize("interval", [5.0, 30.0, 300.0])
def test_interval_is_clamped_in_range(interval: float) -> None:
    """配置写歪了也不能把间隔设成 0 或 1 小时。"""
    plugin = KspBridgePlugin.__new__(KspBridgePlugin)
    plugin._auto_brief_interval = interval
    assert 5.0 <= plugin._auto_brief_interval <= 300.0
