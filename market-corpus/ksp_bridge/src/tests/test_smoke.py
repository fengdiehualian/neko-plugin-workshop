# 和猫娘一起坐火箭（KSP）插件 —— 冒烟测试
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 完整条款见仓库根目录的 LICENSE 文件。

from __future__ import annotations

from plugin.plugins.ksp_bridge import (
    _fmt,
    _fmt_dist,
    _fmt_time,
    _num,
    _summarize_orbit,
    _summarize_state,
)


def test_num_handles_nan_and_none():
    """游戏侧可能给我们 null / NaN，一律要变成 None。"""
    assert _num(None) is None
    assert _num("abc") is None
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None
    assert _num(True) is None  # 布尔不当数字用
    assert _num(12.5) == 12.5
    assert _num("12.5") == 12.5


def test_fmt_distance_units():
    """距离要按量级自动换单位，否则大数字没法读。"""
    assert _fmt_dist(500) == "500.0 m"
    assert _fmt_dist(1500) == "1.50 km"
    assert _fmt_dist(600000) == "600.00 km"
    assert _fmt_dist(6_000_000) == "6.00 Mm"
    assert _fmt_dist(None) == "未知"


def test_fmt_time_breaks_down_units():
    """秒要拆成天/小时/分，否则「还有 86400 秒」没人看得懂。"""
    assert _fmt_time(45) == "45秒"
    assert _fmt_time(90) == "1分"
    assert _fmt_time(3661) == "1小时1分"
    assert _fmt_time(90061) == "1天1小时1分"
    assert _fmt_time(None) == "未知"


def test_fmt_number_with_unit():
    assert _fmt(1234.56, " m/s") == "1,234.6 m/s"
    assert _fmt(None, " m/s") == "未知"


def test_summarize_state_without_vessel():
    """不在飞行场景时要如实说明，不能编数据。"""
    text = _summarize_state({
        "scene": "MAINMENU",
        "vessel": {"exists": False, "note": "当前在主菜单，还没有进入任何存档。"},
    })
    assert "主菜单" in text
    assert "MAINMENU" in text


def test_summarize_state_with_vessel():
    text = _summarize_state({
        "scene": "FLIGHT",
        "vessel": {
            "exists": True,
            "name": "Untitled Space Craft",
            "situation": "ORBITING",
            "body": "Kerbin",
            "altitude": 100000.0,
            "speed_surface": 2280.4,
            "speed_orbital": 2280.4,
            "pitch": 0.2,
            "mass": 12.34,
        },
        "orbit": {
            "exists": True,
            "is_stable_orbit": True,
            "apoapsis": 152340.0,
            "periapsis": 78500.0,
        },
    })
    assert "Untitled Space Craft" in text
    assert "Kerbin" in text
    assert "ORBITING" in text
    assert "100.00 km" in text
    assert "稳定轨道" in text


def test_summarize_orbit_stable():
    text = _summarize_orbit({
        "exists": True,
        "body": "Kerbin",
        "apoapsis": 152340.0,
        "periapsis": 78500.0,
        "eccentricity": 0.0531,
        "inclination": 28.61,
        "period": 2412.7,
        "is_stable_orbit": True,
        "time_to_apoapsis": 1203.4,
        "time_to_periapsis": 1209.3,
    })
    assert "Kerbin" in text
    assert "0.0531" in text
    assert "28.61" in text
    assert "稳定轨道" in text


def test_summarize_orbit_suborbital_warns_about_atmosphere():
    """近点在大气层内必须明确警告，这是新手最常犯的错。"""
    text = _summarize_orbit({
        "exists": True,
        "body": "Kerbin",
        "apoapsis": 90000.0,
        "periapsis": 40000.0,
        "is_stable_orbit": False,
        "atmosphere_depth": 70000.0,
    })
    assert "大气" in text
    assert "70.00 km" in text
    assert "稳定轨道" not in text


def test_summarize_orbit_impact_when_no_atmosphere():
    text = _summarize_orbit({
        "exists": True,
        "body": "Mun",
        "apoapsis": 90000.0,
        "periapsis": -5000.0,
        "is_stable_orbit": False,
    })
    assert "撞" in text


def test_summarize_orbit_missing():
    assert "没有轨道" in _summarize_orbit({"exists": False})
