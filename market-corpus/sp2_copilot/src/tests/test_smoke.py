# 和猫娘一起开飞机（简单飞机2副驾驶）插件 —— 冒烟测试
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 本文件是 sp2_copilot 插件的一部分，以 GNU General Public License v3.0 许可发布。
# 完整条款见仓库根目录的 LICENSE 文件。

"""纯函数单测：不需要启动游戏，也不需要装 N.E.K.O. 之外的依赖。

重点钉住三件事：
1. 异常输入不能炸（None / NaN / 布尔 / 字符串数字）
2. 数据缺失要显示「未知」，不能编一个 0 出来
3. **关键结论必须出现在摘要里** —— 副驾驶的价值就在于替玩家下判断
"""

from __future__ import annotations

from pathlib import Path

from plugin.plugins.sp2_copilot import (
    _build_checklist,
    _default_thresholds,
    _fmt,
    _fmt_alt,
    _fmt_dist,
    _fmt_duration,
    _fmt_pct,
    _fmt_speed,
    _fmt_throttle,
    _fmt_vs,
    _judge_phase,
    _num,
    _scan_warnings,
    _summarize_aircraft,
    _summarize_status,
    _summarize_warnings,
    _turn_advice,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


# ── 清单 ──────────────────────────────────────────────────────────────────


def test_plugin_manifest_exists() -> None:
    manifest = PLUGIN_ROOT / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "sp2_copilot"' in text
    assert 'entry = "plugin.plugins.sp2_copilot:Sp2CopilotPlugin"' in text


def test_bridge_protocol_document_exists() -> None:
    """协议文档是本插件对模组作者的交付物，不能丢。"""
    doc = PLUGIN_ROOT / "docs" / "bridge-protocol.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    for path in ("/ping", "/state", "/aircraft", "/navigation", "/environment"):
        assert path in text


def test_scaffolding_files_exist() -> None:
    """check --release 会校验这些脚手架文件。"""
    for name in (
        "README.md",
        "LICENSE",
        "ruff.toml",
        "pyproject.toml",
        ".gitignore",
        "config.example.toml",
        ".vscode/settings.json",
        ".vscode/tasks.json",
        ".github/workflows/verify.yml",
        ".github/workflows/release.yml",
        "ui/panel.tsx",
        "docs/guide.md",
    ):
        assert (PLUGIN_ROOT / name).is_file(), name


# ── 取值辅助 ──────────────────────────────────────────────────────────────


def test_num_handles_nan_and_none() -> None:
    """游戏侧可能给 null / NaN / Inf，一律要变成 None。"""
    assert _num(None) is None
    assert _num("abc") is None
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None
    assert _num(float("-inf")) is None
    assert _num(True) is None  # 布尔不当数字用
    assert _num(12.5) == 12.5
    assert _num("12.5") == 12.5


# ── 格式化 ────────────────────────────────────────────────────────────────


def test_formatters_return_unknown_for_missing_values() -> None:
    """取不到就写「未知」，绝不写 0 —— 0 米高度是会害死人的假数字。"""
    assert _fmt(None) == "未知"
    assert _fmt_speed(None) == "未知"
    assert _fmt_alt(None) == "未知"
    assert _fmt_vs(None) == "未知"
    assert _fmt_pct(None) == "未知"
    assert _fmt_throttle(None) == "未知"
    assert _fmt_dist(None) == "未知"
    assert _fmt_duration(None) == "未知"


def test_fmt_speed_shows_both_units() -> None:
    """玩家两种单位都用，摘要里都给。"""
    text = _fmt_speed(82.4)
    assert "82.4 m/s" in text
    assert "297 km/h" in text


def test_fmt_alt_switches_to_km() -> None:
    assert _fmt_alt(1010.3) == "1,010 m"
    assert "km" in _fmt_alt(12400.0)


def test_fmt_vs_gives_direction() -> None:
    """升降率必须带方向词，别让模型自己猜正负号。"""
    assert "爬升" in _fmt_vs(4.2)
    assert "下降" in _fmt_vs(-4.2)
    assert "平飞" in _fmt_vs(0.1)


def test_fmt_throttle_clamps() -> None:
    assert _fmt_throttle(0.82) == "82%"
    assert _fmt_throttle(5.0) == "100%"
    assert _fmt_throttle(-1.0) == "0%"


def test_fmt_duration_human_readable() -> None:
    assert _fmt_duration(45) == "45秒"
    assert _fmt_duration(200) == "3分20秒"


# ── 飞行阶段 ──────────────────────────────────────────────────────────────


def _state(**flight: object) -> dict:
    return {"in_world": True, "scene": "flight", "flight": dict(flight), "controls": {}}


def test_phase_out_of_world_is_menu() -> None:
    assert _judge_phase({"in_world": False}) == "menu"
    assert _judge_phase({"in_world": True, "scene": "designer", "flight": {}}) == "menu"


def test_phase_crashed_wins() -> None:
    assert _judge_phase({"in_world": True, "crashed": True}) == "crashed"
    assert _judge_phase(_state(crashed=True, on_ground=False)) == "crashed"


def _ground_state(speed: float, throttle: float) -> dict:
    state = _state(on_ground=True, ground_speed=speed)
    state["controls"] = {"throttle": throttle}
    return state


def test_phase_on_ground() -> None:
    assert _judge_phase(_state(on_ground=True, ground_speed=0.5)) == "parked"
    assert _judge_phase(_state(on_ground=True, ground_speed=15.0)) == "taxi"
    # 高速 + 大油门 = 在冲跑道；高速 + 小油门 = 刚落地在滑跑
    assert _judge_phase(_ground_state(45.0, 0.9)) == "takeoff_roll"
    assert _judge_phase(_ground_state(45.0, 0.1)) == "rollout"


def test_phase_takeoff_roll_needs_both_speed_and_throttle() -> None:
    """只填一半信息不该被判成起飞（没给油门时保守判成着陆滑跑）。"""
    assert _judge_phase(_state(on_ground=True, ground_speed=45.0)) == "rollout"


def test_phase_airborne() -> None:
    assert _judge_phase(_state(on_ground=False, altitude_agl=1200.0, vertical_speed=5.0)) == "climb"
    assert _judge_phase(_state(on_ground=False, altitude_agl=1200.0, vertical_speed=0.0)) == "cruise"
    assert _judge_phase(_state(on_ground=False, altitude_agl=1200.0, vertical_speed=-5.0)) == "descent"
    assert _judge_phase(_state(on_ground=False, altitude_agl=150.0, vertical_speed=-3.0)) == "approach"
    assert _judge_phase(_state(on_ground=False, altitude_agl=150.0, vertical_speed=3.0)) == "initial_climb"


def test_phase_falls_back_when_on_ground_missing() -> None:
    """on_ground 缺失时用离地高度兜底，而不是直接崩掉。"""
    assert _judge_phase(_state(altitude_agl=2.0)) == "parked"
    assert _judge_phase(_state(altitude_agl=1200.0, vertical_speed=0.0)) == "cruise"


def test_phase_unknown_when_nothing_readable() -> None:
    assert _judge_phase({"in_world": True, "scene": "flight", "flight": {}}) == "unknown"


# ── 告警 ──────────────────────────────────────────────────────────────────


def _codes(warnings: list) -> list:
    return [w["code"] for w in warnings]


def test_warn_stalled() -> None:
    warns = _scan_warnings(_state(on_ground=False, stalled=True), {})
    assert "stall" in _codes(warns)
    assert warns[0]["level"] == "danger"


def test_warn_overspeed_only_when_limits_known() -> None:
    """机体没给 Vne 就不判超速 —— 绝不拿现实航空的数字套。"""
    state = _state(on_ground=False, airspeed=250.0)
    assert "overspeed" not in _codes(_scan_warnings(state, {}))
    aircraft = {"limits": {"vne_mps": 220.0}}
    warns = _scan_warnings(state, aircraft)
    assert "overspeed" in _codes(warns)
    assert warns[0]["level"] == "danger"


def test_warn_near_overspeed() -> None:
    state = _state(on_ground=False, airspeed=205.0)
    aircraft = {"limits": {"vne_mps": 220.0}}
    assert "near_overspeed" in _codes(_scan_warnings(state, aircraft))


def test_warn_terrain_when_low_and_sinking() -> None:
    state = _state(on_ground=False, altitude_agl=100.0, vertical_speed=-8.0)
    warns = _scan_warnings(state, {})
    assert "terrain" in _codes(warns)
    assert "拉杆" in warns[0]["action"]


def test_warn_bank_angle() -> None:
    warns = _scan_warnings(_state(on_ground=False, altitude_agl=2000.0, roll=70.0), {})
    assert "bank" in _codes(warns)


def test_warn_gear_up_on_approach() -> None:
    state = _state(on_ground=False, altitude_agl=150.0, vertical_speed=-2.0)
    state["controls"] = {"gear_down": False}
    warns = _scan_warnings(state, {})
    assert "gear_up" in _codes(warns)
    assert warns[0]["level"] == "danger"


def test_warn_engine_states() -> None:
    state = _state(on_ground=False, altitude_agl=2000.0)
    partial = {"engines": [{"running": True}, {"running": False}]}
    assert "engine_out" in _codes(_scan_warnings(state, partial))
    dead = {"engines": [{"running": False}, {"running": False}]}
    assert "all_engines_out" in _codes(_scan_warnings(state, dead))


def test_warn_low_fuel_levels() -> None:
    state = _state(on_ground=False, altitude_agl=2000.0)
    assert "low_fuel" in _codes(_scan_warnings(state, {"fuel": {"percent": 15.0}}))
    assert "fuel_critical" in _codes(_scan_warnings(state, {"fuel": {"percent": 3.0}}))


def test_no_warnings_when_all_normal() -> None:
    state = _state(on_ground=False, altitude_agl=2400.0, vertical_speed=0.0, airspeed=145.0, roll=2.0, aoa=3.0)
    assert _scan_warnings(state, {"fuel": {"percent": 80.0}}) == []


def test_warnings_sorted_by_severity() -> None:
    state = _state(on_ground=False, altitude_agl=2400.0, roll=50.0)
    warns = _scan_warnings(state, {"fuel": {"percent": 15.0}})
    levels = [w["level"] for w in warns]
    assert levels == sorted(levels, key=lambda x: {"danger": 0, "warning": 1, "caution": 2, "info": 3}[x])


def test_summary_of_warnings_gives_conclusion() -> None:
    """摘要要主动给结论，不能只罗列数字。"""
    assert "正常" in _summarize_warnings([])
    warns = _scan_warnings(_state(on_ground=False, stalled=True), {})
    assert "紧急" in _summarize_warnings(warns)


# ── 检查单 ────────────────────────────────────────────────────────────────


def test_checklist_marks_missing_data_as_unknown() -> None:
    """读不到就是 unknown，绝不猜成已完成 —— 那比不做更危险。"""
    result = _build_checklist("parked", {"flight": {}, "controls": {}}, {})
    assert result["unknown_count"] == result["total_count"]
    assert result["done_count"] == 0
    assert all(item["status"] == "unknown" for item in result["items"])


def test_checklist_preflight_flags_unset_flaps_and_gear() -> None:
    state = {"flight": {}, "controls": {"flaps": 0.0, "gear_down": False, "brake": 0.0, "trim": 0.0}}
    aircraft = {"engines": [{"running": True}], "fuel": {"percent": 90.0}, "damage": {"integrity_percent": 100.0}}
    result = _build_checklist("parked", state, aircraft)
    by_id = {item["id"]: item["status"] for item in result["items"]}
    assert by_id["flaps"] == "todo"
    assert by_id["gear"] == "todo"
    assert by_id["engines"] == "done"
    assert by_id["fuel"] == "done"


def test_checklist_preflight_all_done() -> None:
    state = {"flight": {}, "controls": {"flaps": 0.3, "gear_down": True, "brake": 0.0, "trim": 0.0}}
    aircraft = {"engines": [{"running": True}], "fuel": {"percent": 90.0}, "damage": {"integrity_percent": 100.0}}
    result = _build_checklist("parked", state, aircraft)
    assert result["pending_count"] == 0


def test_checklist_approach_asks_for_gear_and_flaps() -> None:
    state = {"flight": {"altitude_agl": 150.0, "vertical_speed": -2.0, "airspeed": 60.0}, "controls": {"gear_down": False, "flaps": 0.0}}
    result = _build_checklist("approach", state, {})
    by_id = {item["id"]: item["status"] for item in result["items"]}
    assert by_id["gear"] == "todo"
    assert by_id["flaps"] == "todo"


def test_checklist_crashed_has_single_item() -> None:
    result = _build_checklist("crashed", {"flight": {}, "controls": {}}, {})
    assert result["total_count"] == 1


# ── 导航 ──────────────────────────────────────────────────────────────────


def test_turn_advice_direction() -> None:
    assert _turn_advice(0.0, 90.0)["turn_direction"] == "右转"
    assert _turn_advice(0.0, 270.0)["turn_direction"] == "左转"
    assert _turn_advice(0.0, 1.0)["turn_direction"] == "保持"
    assert _turn_advice(None, 90.0)["turn_direction"] == "未知"


def test_turn_advice_wraps_around_north() -> None:
    """跨越 0° 时不能算出 350° 这种荒唐的转弯量。"""
    advice = _turn_advice(350.0, 10.0)
    assert advice["turn_direction"] == "右转"
    assert advice["turn_degrees"] == 20.0


# ── 摘要 ──────────────────────────────────────────────────────────────────


def test_status_summary_includes_phase_and_altitude() -> None:
    state = _state(on_ground=False, altitude_agl=1010.0, altitude_msl=1240.0, airspeed=82.4, vertical_speed=4.2, heading=271.0)
    text = _summarize_status(state, {})
    assert "爬升" in text  # 阶段结论
    assert "1,010 m" in text
    assert "82.4 m/s" in text


def test_status_summary_handles_empty_state() -> None:
    """空数据也要能说出一句完整的话，而不是崩掉或输出一堆 0。"""
    text = _summarize_status({}, {})
    assert "阶段未知" in text
    assert "0 m" not in text


def test_aircraft_summary_reports_engine_count() -> None:
    aircraft = {
        "name": "Mock Jet",
        "engines": [{"running": True}, {"running": False}],
        "fuel": {"percent": 37.5, "current_kg": 900.0},
    }
    text = _summarize_aircraft(aircraft)
    assert "1/2" in text  # 两台里只有一台在转
    assert "900 kg" in text


def test_default_thresholds_complete() -> None:
    th = _default_thresholds()
    for key in ("terrain_agl_m", "sink_rate_mps", "bank_deg", "low_fuel_percent", "aoa_deg", "overspeed_ratio"):
        assert key in th
