# 和猫娘一起开火箭（朱诺新起源）插件 —— 自动播报测试
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 本文件是 juno_bridge 插件的一部分，以 GNU General Public License v3.0 许可发布。
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
from plugin.plugins.juno_bridge import (
    AUTO_BRIEF_COALESCE_KEY,
    AUTO_BRIEF_PREFIX,
    JunoBridgePlugin,
    _auto_brief_line,
)
from plugin.sdk.plugin import SdkError

_IN_FLIGHT: Dict[str, Any] = {
    "ok": True,
    "scene": {"craft_name": "Mock Rocket", "body": "Droo", "game_time": 125.0},
    "vessel": {
        "name": "Mock Rocket",
        "body": "Droo",
        "situation": "flying",
        "altitude": 12000.0,
        "altitude_agl": 11500.0,
        "pitch": 62.0,
        "heading": 90.0,
        "mass": 42000.0,
        "current_stage": 2,
        "stage_count": 3,
        "crew_count": 1,
    },
    "flight": {
        "surface_speed": 320.5,
        "orbital_speed": 330.1,
        "vertical_speed": 88.0,
    },
    "orbit": {
        "body": "Droo",
        "apoapsis": 80000.0,
        "periapsis": 70000.0,
        "eccentricity": 0.01,
        "inclination": 0.5,
        "period": 1900.0,
    },
    "resources": {
        "fuel_all_stages_percent": 0.84,
        "delta_v_stage": 3200.0,
        "thrust_to_weight": 1.42,
        "battery": 0.91,
    },
}

_NOT_IN_FLIGHT: Dict[str, Any] = {
    "ok": False,
    "error": "当前不在飞行场景（主菜单、设计器或加载中）。",
}


# ── 文本压缩 ──────────────────────────────────────────────────────────────


def test_line_has_prefix_and_vessel_name() -> None:
    text = _auto_brief_line(_IN_FLIGHT)
    assert text.startswith(AUTO_BRIEF_PREFIX)
    assert "Mock Rocket" in text
    assert "12.00 km" in text


def test_line_is_short_enough_to_live_in_context() -> None:
    """这段会一直待在上下文里，不能太长。"""
    assert len(_auto_brief_line(_IN_FLIGHT)) < 600


def test_line_is_empty_when_not_in_flight() -> None:
    """主菜单/设计器里没有遥测，不该反复播报。"""
    assert _auto_brief_line(_NOT_IN_FLIGHT) == ""


def test_line_is_empty_for_empty_payload() -> None:
    assert _auto_brief_line({}) == ""


def test_line_has_no_double_prefix() -> None:
    assert _auto_brief_line(_IN_FLIGHT).count(AUTO_BRIEF_PREFIX) == 1


def test_line_carries_resources_so_detail_questions_need_no_tool() -> None:
    """「油还够吗」「Delta-V 多少」不该再走一次工具往返。"""
    text = _auto_brief_line(_IN_FLIGHT)
    assert "Delta-V 3,200" in text
    assert "推重比 1.42" in text
    assert "全船燃料 84%" in text
    assert "电量 91%" in text


def test_line_hides_fuel_when_summarize_state_already_warns() -> None:
    """燃料见底时 _summarize_state 已经专门警告，别重复念两遍。"""
    state = dict(_IN_FLIGHT)
    state["resources"] = dict(_IN_FLIGHT["resources"], fuel_all_stages_percent=0.03)
    text = _auto_brief_line(state)
    assert "全船燃料 3%" not in text
    assert "快见底了" in text


def test_line_survives_missing_resources() -> None:
    state = dict(_IN_FLIGHT)
    state.pop("resources")
    text = _auto_brief_line(state)
    assert text.startswith(AUTO_BRIEF_PREFIX)
    assert "Mock Rocket" in text


def test_line_accepts_zero_to_100_percent_scale() -> None:
    """模组若改成 0~100 也不能说出「电量 9100%」。"""
    state = dict(_IN_FLIGHT)
    state["resources"] = dict(_IN_FLIGHT["resources"], battery=91.0)
    assert "电量 91%" in _auto_brief_line(state)


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
            raise SdkError("连不上朱诺桥接服务")
        return self._state

    def push_message(self, **kwargs: Any) -> Dict[str, Any]:
        self.pushes.append(kwargs)
        return {"submitted": True}

    _auto_brief_once = JunoBridgePlugin._auto_brief_once


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_once_pushes_read_behavior_with_coalesce_key() -> None:
    rec = _Recorder(_IN_FLIGHT)
    _run(rec._auto_brief_once())

    assert len(rec.pushes) == 1
    push = rec.pushes[0]
    # read：进上下文但不打断对话 —— 这就是「每轮对话一开始数据已在」
    assert push["ai_behavior"] == "read"
    assert push["visibility"] == []
    assert push["coalesce_key"] == AUTO_BRIEF_COALESCE_KEY
    assert push["source"] == "juno_auto_brief"
    assert push["parts"][0]["type"] == "text"
    assert push["parts"][0]["text"].startswith(AUTO_BRIEF_PREFIX)


def test_once_skips_identical_payload() -> None:
    rec = _Recorder(_IN_FLIGHT)
    _run(rec._auto_brief_once())
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 1


def test_once_stays_silent_when_not_in_flight() -> None:
    rec = _Recorder(_NOT_IN_FLIGHT)
    _run(rec._auto_brief_once())
    assert rec.pushes == []


def test_never_connected_stays_silent() -> None:
    """游戏没开的时候别往上下文里灌噪音。"""
    rec = _Recorder(_IN_FLIGHT, boom=True)
    _run(rec._auto_brief_once())
    assert rec.pushes == []


def test_disconnect_after_connected_is_announced_once() -> None:
    """本来连着、突然断了 —— 得说一声，否则模型会拿旧数据当现状。"""
    rec = _Recorder(_IN_FLIGHT)
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

    rec = _Boom(_IN_FLIGHT)
    _run(rec._auto_brief_once())
    assert rec.pushes == []


def test_once_respects_unsubmitted_result() -> None:
    """宿主拒收（backpressure 等）不该抛异常。"""

    class _Reject(_Recorder):
        def push_message(self, **kwargs: Any) -> Dict[str, Any]:
            self.pushes.append(kwargs)
            return {"submitted": False, "reason": "backpressure"}

    rec = _Reject(_IN_FLIGHT)
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 1


def test_loop_honours_stop_event() -> None:
    """stop 一置位就要立刻退出，不能等满一个间隔。"""

    class _LoopRec(_Recorder):
        _auto_brief_loop = JunoBridgePlugin._auto_brief_loop

    rec = _LoopRec(_IN_FLIGHT)
    rec._auto_brief_interval = 0.0
    rec._auto_brief_stop = asyncio.Event()

    async def _go() -> None:
        task = asyncio.create_task(rec._auto_brief_loop())
        await asyncio.sleep(0.05)
        rec._auto_brief_stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    _run(_go())
    assert rec.pushes  # 至少推过一次


@pytest.mark.parametrize("interval", [5.0, 20.0, 300.0])
def test_interval_defaults_in_range(interval: float) -> None:
    """配置写歪了也不能把间隔设成 0 或 1 小时。"""
    assert 5.0 <= interval <= 300.0
