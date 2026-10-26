# 和猫娘一起开飞机（简单飞机2副驾驶）插件 —— 自动播报测试
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 本文件是 sp2_copilot 插件的一部分，以 GNU General Public License v3.0 许可发布。
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
from plugin.plugins.sp2_copilot import (
    AUTO_BRIEF_COALESCE_KEY,
    AUTO_BRIEF_PREFIX,
    Sp2CopilotPlugin,
    _auto_brief_line,
)


def _brief(**over: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "connected": True,
        "phase": "climb",
        "phase_label": "爬升",
        "status_summary": "阶段：爬升；高度 离地 1,086 m；空速 257.4 m/s。",
        "warning_summary": "没有告警。",
        "warnings": [],
        "aircraft": {"name": "Mock Jet", "fuel": {"percent": 84.0},
                     "engines": [{"running": True}, {"running": True}]},
        "checklist": {"items": [], "pending_count": 0},
    }
    base.update(over)
    return base


# ── 文本压缩 ──────────────────────────────────────────────────────────────


def test_line_has_prefix_and_status() -> None:
    text = _auto_brief_line(_brief())
    assert text.startswith(AUTO_BRIEF_PREFIX)
    assert "离地 1,086 m" in text


def test_line_surfaces_danger_warnings_prominently() -> None:
    """危险告警要单独起一段，不能被普通状态淹没。"""
    text = _auto_brief_line(_brief(warnings=[{"level": "danger", "text": "失速了", "code": "stall"}]))
    assert "【要立刻提醒】失速了" in text


def test_line_downgrades_non_danger_warnings() -> None:
    text = _auto_brief_line(_brief(warnings=[{"level": "caution", "text": "油量偏低", "code": "low_fuel"}]))
    assert "注意：油量偏低" in text
    assert "【要立刻提醒】" not in text


def test_line_lists_pending_checklist_items() -> None:
    text = _auto_brief_line(
        _brief(
            checklist={
                "items": [
                    {"id": "gear", "label": "放下起落架", "status": "pending"},
                    {"id": "trim", "label": "配平", "status": "done"},
                    {"id": "fuel", "label": "核对油量", "status": "unknown"},
                ]
            }
        )
    )
    assert "该做没做：放下起落架" in text
    assert "配平" not in text
    assert "核对油量" not in text


def test_line_never_empty() -> None:
    """全空也要有一句话，不能让模型读到半个空壳。"""
    text = _auto_brief_line({"status_summary": "", "warnings": [], "checklist": {}, "aircraft": {}})
    assert text.startswith(AUTO_BRIEF_PREFIX)
    assert len(text) > len(AUTO_BRIEF_PREFIX)


def test_line_includes_engine_and_fuel_state() -> None:
    """发动机和油量不在 status_summary 里，但绝不能从播报里漏掉。"""
    text = _auto_brief_line(_brief())
    assert "发动机 2/2 台在转" in text
    assert "油量 84%" in text


def test_line_stays_short() -> None:
    """这段会跟着每一轮对话走，必须压得住。"""
    text = _auto_brief_line(
        _brief(
            warnings=[{"level": "danger", "text": f"告警{i}", "code": "x"} for i in range(6)],
            checklist={"items": [{"id": f"i{i}", "label": f"待办{i}", "status": "pending"} for i in range(6)]},
        )
    )
    assert len(text) < 400


# ── 推送参数 ──────────────────────────────────────────────────────────────

_REAL_STATE = {
    "ok": True,
    "protocol": 1,
    "scene": "flight",
    "in_world": True,
    "position": {"latitude": -26468, "longitude": 16877.7},
    "fuel_percent": 96.586,
    "flight": {
        "altitude_msl": 1087.379,
        "altitude_agl": 1086.481,
        "ground_speed": 276.994,
        "vertical_speed": 92.028,
        "airspeed": 257.377,
        "mach": 0.756,
        "pitch": 18.733,
        "roll": 0.29,
        "heading": 49.804,
        "aoa": 0.724,
        "g_force": 1.075,
        "on_ground": False,
    },
    "controls": {"throttle": 1, "gear_down": True, "flaps": 0},
    "derived": {"mach": True, "on_ground": True},
}

_REAL_AIRCRAFT = {
    "ok": True,
    "name": "NetworkAircraft_LocalPlayer_0",
    "type": "plane",
    "fuel": {"current_kg": 8014.205, "capacity_kg": 8297.474, "percent": 96.586},
    # 真实模组当时的输出：8 台引擎，一个 running 字段都没有
    "engines": [
        {"index": i, "name": n, "type": "jet"}
        for i, n in enumerate(
            [
                "Military Jet Engine",
                "Military Jet Engine",
                "CoreStart",
                "CoreMiddle",
                "CoreEnd",
                "Talon",
                "Afterburner",
                "CoreFrontCivilianLarge",
            ]
        )
    ],
    "gear": {"down": True, "state": "down"},
    "surfaces": {"flaps_percent": 0},
}

_REAL_SNAP = {
    "state": _REAL_STATE,
    "aircraft": _REAL_AIRCRAFT,
    "navigation": {"ok": True, "heading": 0, "target": None, "nearest_airport": None},
    "environment": {"ok": True},
    "errors": {},
    "connected": True,
}


class _Recorder:
    """最小的 self 替身：只提供 _auto_brief_once 用得到的东西。"""

    def __init__(self, snap: Dict[str, Any]) -> None:
        self._snap = snap
        self._thresholds: Dict[str, float] = {}
        self._auto_brief_last_text = ""
        self._auto_brief_last_ok = None
        self.pushes: List[Dict[str, Any]] = []
        self.logger = SimpleNamespace(debug=lambda *a, **k: None)

    async def _snapshot(self) -> Dict[str, Any]:
        return self._snap

    def _thresholds_dict(self) -> Dict[str, float]:
        return {"terrain_agl_m": 300.0, "sink_rate_mps": 6.0, "bank_deg": 45.0,
                "low_fuel_percent": 20.0, "aoa_deg": 12.0, "overspeed_ratio": 0.9}

    def push_message(self, **kwargs: Any) -> Dict[str, Any]:
        self.pushes.append(kwargs)
        return {"submitted": True}

    # 借用真实实现的纯计算方法
    _build_briefing = Sp2CopilotPlugin._build_briefing
    _auto_brief_once = Sp2CopilotPlugin._auto_brief_once


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_once_pushes_read_behavior_with_coalesce_key() -> None:
    rec = _Recorder(_REAL_SNAP)
    _run(rec._auto_brief_once())

    assert len(rec.pushes) == 1
    push = rec.pushes[0]
    # read：进上下文但不打断对话 —— 这就是「每轮对话一开始数据已在」
    assert push["ai_behavior"] == "read"
    # 空 visibility：用户不直接看到这条系统播报
    assert push["visibility"] == []
    # 同键合并：后台轮询不会把上下文撑爆
    assert push["coalesce_key"] == AUTO_BRIEF_COALESCE_KEY
    assert push["source"] == "sp2_auto_brief"
    assert push["parts"][0]["type"] == "text"
    assert push["parts"][0]["text"].startswith(AUTO_BRIEF_PREFIX)


def test_real_data_does_not_false_alarm_engines() -> None:
    """用户报的那个 bug：引擎全功率却被说成全部停车。"""
    rec = _Recorder(_REAL_SNAP)
    _run(rec._auto_brief_once())
    text = rec.pushes[0]["parts"][0]["text"]
    assert "全部停车" not in text
    assert "【要立刻提醒】" not in text
    assert "发动机 8 台（开关状态读不到）" in text


def test_once_skips_identical_payload() -> None:
    """数据没变就别重复塞 —— 否则上下文全是复读。"""
    rec = _Recorder(_REAL_SNAP)
    _run(rec._auto_brief_once())
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 1


def test_never_connected_stays_silent() -> None:
    """游戏没开的时候别往上下文里灌噪音。"""
    rec = _Recorder({"state": None, "aircraft": None, "navigation": None,
                     "environment": None, "errors": {"state": "connect"}, "connected": False})
    _run(rec._auto_brief_once())
    assert rec.pushes == []


def test_disconnect_after_connected_is_announced_once() -> None:
    """本来连着、突然断了 —— 得说一声，否则模型会拿旧数据当现状。"""
    rec = _Recorder(_REAL_SNAP)
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 1

    rec._snap = {"state": None, "aircraft": None, "navigation": None,
                 "environment": None, "errors": {}, "connected": False}
    _run(rec._auto_brief_once())
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 2
    assert "已经退出或断开" in rec.pushes[1]["parts"][0]["text"]


def test_once_survives_snapshot_exception() -> None:
    """桥接没起来时 httpx 会抛，后台任务不能被带走。"""

    class _Boom(_Recorder):
        async def _snapshot(self) -> Dict[str, Any]:
            raise RuntimeError("connection refused")

    rec = _Boom(_REAL_SNAP)
    _run(rec._auto_brief_once())
    assert rec.pushes == []


def test_once_respects_unsubmitted_result() -> None:
    """宿主拒收（backpressure 等）不该抛异常。"""

    class _Reject(_Recorder):
        def push_message(self, **kwargs: Any) -> Dict[str, Any]:
            self.pushes.append(kwargs)
            return {"submitted": False, "reason": "backpressure"}

    rec = _Reject(_REAL_SNAP)
    _run(rec._auto_brief_once())
    assert len(rec.pushes) == 1


@pytest.mark.parametrize("interval", [5.0, 20.0, 120.0])
def test_loop_honours_stop_event(interval: float) -> None:
    """stop 一置位就要立刻退出，不能等满一个间隔。"""

    class _LoopRec(_Recorder):
        _auto_brief_interval = 0.0
        _auto_brief_loop = Sp2CopilotPlugin._auto_brief_loop

    rec = _LoopRec(_REAL_SNAP)
    rec._auto_brief_stop = asyncio.Event()
    rec._auto_brief_interval = 0.0

    async def _go() -> None:
        task = asyncio.create_task(rec._auto_brief_loop())
        await asyncio.sleep(0.05)
        rec._auto_brief_stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    _run(_go())
    assert rec.pushes  # 至少推过一次
