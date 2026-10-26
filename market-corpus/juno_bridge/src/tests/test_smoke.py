# 和猫娘一起开火箭（朱诺新起源）插件 —— 冒烟测试
# Copyright (C) 2026 星河拓航工作室 (Galaxy Exploration Studio)
#
# 完整条款见仓库根目录的 LICENSE 文件。
#
# 全部测试都只用纯函数与假数据：**不需要游戏、不需要联网、不需要启动 N.E.K.O.**

from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.juno_bridge import (
    JunoBridgePlugin,
    _apo_altitude,
    _error_result,
    _extract_list,
    _fmt,
    _fmt_dist,
    _fmt_mass,
    _fmt_percent,
    _fmt_time,
    _juno_docs,
    _not_in_flight_result,
    _num,
    _orbit_verdict,
    _percent_of_100,
    _peri_altitude,
    _situation_text,
    _summarize_bodies,
    _summarize_flight,
    _summarize_orbit,
    _summarize_resources,
    _summarize_state,
    _summarize_target,
    _summarize_vessels,
    _vertical_hint,
)
from plugin.sdk.plugin import SdkError

# ---------------------------------------------------------------------------
# 取值辅助 / 格式化
# ---------------------------------------------------------------------------


def test_num_handles_nan_inf_none_and_bool():
    """游戏侧可能给我们 null / NaN / Inf，一律要变成 None。"""
    assert _num(None) is None
    assert _num("abc") is None
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None
    assert _num(float("-inf")) is None
    assert _num(True) is None  # 布尔不当数字用
    assert _num(False) is None
    assert _num(12.5) == 12.5
    assert _num("12.5") == 12.5
    assert _num(0) == 0.0


def test_fmt_distance_units():
    """距离要按量级自动换单位，否则大数字没法读。"""
    assert _fmt_dist(500) == "500.0 m"
    assert _fmt_dist(1500) == "1.50 km"
    assert _fmt_dist(600000) == "600.00 km"
    assert _fmt_dist(6_000_000) == "6.00 Mm"
    assert _fmt_dist(-5000) == "-5.00 km"
    assert _fmt_dist(None) == "未知"
    assert _fmt_dist(float("nan")) == "未知"


def test_fmt_time_breaks_down_units():
    """秒要拆成天/小时/分，否则「还有 86400 秒」没人看得懂。"""
    assert _fmt_time(45) == "45秒"
    assert _fmt_time(90) == "1分"
    assert _fmt_time(3661) == "1小时1分"
    assert _fmt_time(90061) == "1天1小时1分"
    assert _fmt_time(-30) == "-30秒"
    assert _fmt_time(None) == "未知"


def test_fmt_mass_is_uncalibrated_and_never_tonnes():
    """实测：mass 是游戏内部质量单位、未标定 —— 只原样输出并标注，绝不做 kg/t 换算。"""
    assert _fmt_mass(0.0418) == "0.0418（游戏内部单位，未标定）"
    assert _fmt_mass(12500.0) == "12,500（游戏内部单位，未标定）"
    assert _fmt_mass(None) == "未知"
    for text in (_fmt_mass(0.0418), _fmt_mass(12500.0)):
        assert " t " not in text
        assert "kg" not in text
        assert "吨" not in text
    assert "未标定" in _fmt_mass(0.0418)


def test_fmt_percent_is_normalized_zero_to_one():
    """实测：battery / fuel_all_stages_percent 是 0~1 归一化 —— ×100 显示，并保留原始值。"""
    assert _fmt_percent(None) == "未知"
    assert _fmt_percent(0.9997) == "99.97%（原始值 0.9997）"
    assert _fmt_percent(0.62) == "62%（原始值 0.62）"
    assert _fmt_percent(0.0) == "0%（原始值 0）"
    assert "原始值" in _fmt_percent(0.9997)
    # 万一某个百分数字段给的是 0~100，也原样显示并保留原始值，绝不乘成 6200%
    assert _fmt_percent(62.0) == "62%（原始值 62）"
    assert _percent_of_100(0.05) == 5.0
    assert _percent_of_100(0.9997) == pytest.approx(99.97)
    assert _percent_of_100(62.0) == 62.0
    assert _percent_of_100(None) is None


def test_fmt_number_missing_is_unknown():
    """取不到就输出「未知」，绝不用 0 顶替。"""
    assert _fmt(1234.56, " m/s") == "1,234.6 m/s"
    assert _fmt(None, " m/s") == "未知"
    assert _fmt("abc", " m/s") == "未知"


def test_situation_text_maps_known_values():
    assert _situation_text("ORBITING") == "ORBITING（环绕轨道）"
    assert _situation_text("sub_orbital") == "sub_orbital（亚轨道（还会掉回来））"
    assert _situation_text("WEIRD") == "WEIRD"
    assert _situation_text(None) == "未知"


def test_vertical_hint():
    assert _vertical_hint(120.0) == "正在上升"
    assert _vertical_hint(-120.0) == "正在下降"
    assert _vertical_hint(0.0) == "基本平飞"
    assert _vertical_hint(None) == "未知"


def test_altitude_helpers_fall_back_to_distance_minus_radius():
    """模组只给距离不给高度时，要能用「距离 - 天体半径」补出来。"""
    orbit = {"body_radius": 600000.0, "apoapsis_distance": 752340.0, "periapsis_distance": 588000.0}
    assert _apo_altitude(orbit) == 152340.0
    assert _peri_altitude(orbit) == -12000.0
    assert _apo_altitude({}) is None
    assert _peri_altitude({"body_radius": 600000.0}) is None
    # 有高度字段时优先用它
    assert _peri_altitude({"periapsis_altitude": 88000.0, "periapsis_distance": 1.0}) == 88000.0


def test_extract_list_accepts_both_shapes():
    """/bodies 可能直接给数组，也可能包在 {"ok":true,"bodies":[...]} 里。"""
    assert len(_extract_list({"ok": True, "bodies": [{"name": "A"}]}, "bodies")) == 1
    assert len(_extract_list([{"name": "A"}, {"name": "B"}], "bodies")) == 2
    assert _extract_list({"ok": True, "bodies": "nope"}, "bodies") == []
    assert _extract_list(None, "bodies") == []


# ---------------------------------------------------------------------------
# 入轨判断（本插件最核心的结论）
# ---------------------------------------------------------------------------


def test_orbit_verdict_stable_when_periapsis_above_atmosphere():
    text = _orbit_verdict({
        "periapsis_altitude": 88000.0,
        "atmosphere_height": 50000.0,
        "is_stable_orbit": True,
    })
    assert "稳定轨道" in text
    assert "50.00 km" in text


def test_orbit_verdict_warns_when_periapsis_inside_atmosphere():
    text = _orbit_verdict({
        "periapsis_altitude": 30000.0,
        "atmosphere_height": 50000.0,
        "is_stable_orbit": False,
    })
    assert "大气" in text
    assert "拖下来" in text
    assert "稳定轨道" not in text


def test_orbit_verdict_warns_about_impact_when_negative():
    text = _orbit_verdict({
        "periapsis_altitude": -5000.0,
        "atmosphere_height": 50000.0,
        "is_stable_orbit": False,
    })
    assert "撞" in text


def test_orbit_verdict_says_unknown_when_no_data():
    text = _orbit_verdict({})
    assert "未知" in text or "无法判断" in text


def test_orbit_verdict_airless_body_is_stable_when_above_surface():
    """没有大气的天体，近点只要在地表以上就算稳定。"""
    text = _orbit_verdict({"periapsis_altitude": 12000.0, "atmosphere_height": 0.0})
    assert "稳定轨道" in text


# ---------------------------------------------------------------------------
# 摘要（要能直接念出来，并且主动给结论）
# ---------------------------------------------------------------------------


def test_summarize_state_in_flight_gives_conclusions():
    text = _summarize_state({
        "ok": True,
        "in_flight": True,
        "scene": {"body": "Droo", "craft_name": "朱雀一号", "game_time": 1234.0, "warp_multiplier": 1},
        "vessel": {
            "name": "朱雀一号",
            "situation": "ORBITING",
            "body": "Droo",
            "altitude": 120000.0,
            "altitude_agl": 118000.0,
            "mass": 12500.0,
            "current_mass": 9800.0,
            "pitch": 2.5,
            "heading": 90.0,
            "crew_count": 2,
            "current_stage": 2,
            "stage_count": 3,
        },
        "flight": {"surface_speed": 2280.4, "orbital_speed": 2300.1, "vertical_speed": 0.4},
        "orbit": {
            "body": "Droo",
            "body_radius": 600000.0,
            "atmosphere_height": 50000.0,
            "apoapsis_altitude": 152340.0,
            "periapsis_altitude": 88000.0,
            "is_stable_orbit": True,
        },
        "resources": {"fuel_all_stages_percent": 0.62},
    })
    assert "朱雀一号" in text
    assert "Droo" in text
    assert "ORBITING" in text
    assert "120.00 km" in text
    assert "12,500（游戏内部单位，未标定）" in text
    assert "当前质量 9,800（游戏内部单位，未标定）" in text
    assert " t " not in text
    assert "kg" not in text
    assert "第 2/3 级" in text
    assert "2 人" in text
    assert "稳定轨道" in text


def test_summarize_state_suborbital_warns_about_atmosphere():
    """还在大气层里的飞船必须被点出来，这是新手最常犯的错。"""
    text = _summarize_state({
        "ok": True,
        "in_flight": True,
        "scene": {"body": "Droo"},
        "vessel": {"name": "试验箭", "situation": "SUB_ORBITAL", "body": "Droo", "altitude": 60000.0},
        "flight": {"surface_speed": 1500.0},
        "orbit": {"body": "Droo", "periapsis_altitude": 30000.0, "atmosphere_height": 50000.0},
    })
    assert "SUB_ORBITAL" in text
    assert "大气" in text
    assert "稳定轨道" not in text


def test_summarize_state_missing_data_says_unknown():
    """字段拿不到时要说「未知」，不能编数字。"""
    text = _summarize_state({})
    assert "未知" in text
    assert "0.0" not in text


def test_summarize_state_warns_when_fuel_almost_gone():
    text = _summarize_state({
        "vessel": {"name": "备份箭", "body": "Droo", "altitude": 1000.0},
        "resources": {"fuel_all_stages_percent": 0.03},
    })
    assert "快见底" in text


def test_summarize_orbit_stable():
    text = _summarize_orbit({
        "body": "Droo",
        "apoapsis_altitude": 152340.0,
        "periapsis_altitude": 78500.0,
        "atmosphere_height": 50000.0,
        "eccentricity": 0.0531,
        "inclination": 28.61,
        "period": 2412.7,
        "is_valid": True,
        "is_stable_orbit": True,
        "time_to_apoapsis": 1203.4,
        "time_to_periapsis": 1209.3,
    })
    assert "Droo" in text
    assert "0.0531" in text
    assert "28.61" in text
    assert "稳定轨道" in text
    assert "20分" in text


def test_summarize_orbit_suborbital_warns_about_atmosphere():
    """近点在大气层内必须明确警告，且要报出大气层顶的真实数值。"""
    text = _summarize_orbit({
        "body": "Droo",
        "apoapsis_altitude": 90000.0,
        "periapsis_altitude": 40000.0,
        "atmosphere_height": 70000.0,
        "is_stable_orbit": False,
    })
    assert "大气" in text
    assert "70.00 km" in text
    assert "拖下来" in text
    assert "稳定轨道" not in text


def test_summarize_orbit_impact_when_periapsis_below_surface():
    text = _summarize_orbit({
        "body": "Cylero",
        "apoapsis_altitude": 90000.0,
        "periapsis_altitude": -5000.0,
        "is_stable_orbit": False,
    })
    assert "撞" in text


def test_summarize_orbit_missing():
    assert "没有读到轨道数据" in _summarize_orbit({})


def test_summarize_orbit_is_valid_false_adds_caution():
    text = _summarize_orbit({"body": "Droo", "is_valid": False, "periapsis_altitude": 90000.0,
                            "atmosphere_height": 50000.0})
    assert "is_valid=false" in text


def test_summarize_flight_vacuum_and_supersonic():
    text = _summarize_flight({
        "altitude_agl": 30000.0,
        "surface_speed": 900.0,
        "orbital_speed": 910.0,
        "vertical_speed": 250.0,
        "mach": 3.2,
        "air_density": 0.0,
        "air_pressure": 0.0,
        "temperature": 220.0,
        "gravity": 8.4,
        "drag_acceleration": 0.0,
        "thrust": 120000.0,
        "max_thrust": 150000.0,
        "fuel_mass": 4200.0,
    })
    assert "超音速" in text
    assert "真空" in text
    assert "正在上升" in text
    assert "30.00 km" in text
    # 单位必须写死，不能让模型自己猜
    assert "kg/m³" in text
    assert "kPa" in text and "Pa" in text
    assert "℃" in text and "K" in text
    assert " m/s" in text
    assert "未标定" in text  # fuel_mass 是游戏内部单位


def test_summarize_flight_real_measured_units():
    """实机实测的 /flight 读数：气压 = Pa、气温 = K、密度 = kg/m³、音速 = m/s。"""
    text = _summarize_flight({
        "surface_speed": 7.9e-07,
        "orbital_speed": 143.258,
        "vertical_speed": -7.8e-07,
        "gravity": 9.7554,
        "drag_acceleration": 0,
        "mach": 2.34e-09,
        "air_density": 0.86481970548629761,
        "air_pressure": 71905.1015625,
        "temperature": 279.64837646484375,
        "speed_of_sound": 341.15325927734375,
        "thrust": 0,
        "max_thrust": 0,
        "fuel_mass": 0,
    })
    assert "71.9 kPa（71905 Pa）" in text  # 气压是帕斯卡，同时给 kPa 方便阅读
    assert "6.5 ℃（279.6 K）" in text  # 气温是开尔文，双标注
    assert "279.6 ℃" not in text  # 绝不能把 279.6 K 念成 279.6 摄氏度
    assert "0.86482 kg/m³" in text  # 空气密度 kg/m³
    assert "341.2 m/s" in text  # 音速 m/s
    assert " m/s" in text
    assert "游戏内部单位，未标定" in text  # fuel_mass 原样给出


def test_summarize_flight_reports_drag():
    text = _summarize_flight({"mach": 2.0, "air_density": 0.9, "drag_acceleration": -12.5})
    assert "阻力" in text
    assert "12.50" in text


def test_summarize_flight_missing():
    assert "没有读到" in _summarize_flight({})


def test_summarize_resources_conclusions():
    text = _summarize_resources({
        "fuel_mass": 5400.0,
        "fuel_in_stage": 1200.0,
        "fuel_all_stages_percent": 0.62,
        "monopropellant": 40.0,
        "battery": 0.85,
        "delta_v_stage": 1850.0,
        "isp": 320.0,
        "thrust_to_weight": 1.35,
        "burn_time_remaining": 145.0,
    })
    assert "5,400（游戏内部单位，未标定）" in text
    assert "1,200（游戏内部单位，未标定）" in text
    assert "62%（原始值 0.62）" in text
    assert "85%（原始值 0.85）" in text
    assert "1,850 m/s" in text
    assert "够用" in text
    assert " t " not in text
    assert "kg" not in text


def test_summarize_resources_battery_is_normalized_zero_to_one():
    """实测 battery = 0.9997 这样的 0~1 归一化值，要显示成约 100% 并保留原始值。"""
    text = _summarize_resources({"battery": 0.9997057318687439})
    assert "99.97%" in text
    assert "原始值 0.9997" in text
    assert " t " not in text


def test_summarize_resources_warns_when_twr_below_one():
    text = _summarize_resources({"thrust_to_weight": 0.8})
    assert "小于 1" in text


def test_summarize_resources_warns_when_fuel_low():
    text = _summarize_resources({"fuel_all_stages_percent": 0.04})
    assert "快见底" in text


def test_summarize_resources_missing():
    text = _summarize_resources({})
    assert "没有读到" in text


def test_summarize_target_unlocked():
    assert "没有锁定" in _summarize_target({"ok": True, "locked": False})


def test_summarize_target_vessel_with_big_relative_speed():
    text = _summarize_target({
        "ok": True,
        "locked": True,
        "name": "空间站",
        "kind": "vessel",
        "distance": 4200.0,
        "relative_speed": 120.0,
        "parent": "Droo",
        "has_orbit": True,
        "apoapsis_distance": 660000.0,
        "periapsis_distance": 640000.0,
    })
    assert "空间站" in text
    assert "4.20 km" in text
    assert "先减速" in text
    assert "Droo" in text


def test_summarize_target_very_close_speed():
    text = _summarize_target({"locked": True, "name": "X", "kind": "other",
                              "distance": 12.0, "relative_speed": 0.4})
    assert "可以尝试对接" in text


def test_summarize_bodies_lists_atmosphere():
    text = _summarize_bodies([
        {"name": "Droo", "radius": 600000.0, "surface_gravity": 9.8, "has_atmosphere": True,
         "atmosphere_height": 50000.0, "parent": "Juno"},
        {"name": "Vizzy", "radius": 200000.0, "surface_gravity": 1.6, "has_atmosphere": False},
    ])
    assert "Droo" in text
    assert "有大气 50.00 km" in text
    assert "无大气" in text
    assert "2 颗天体" in text
    assert "母天体 Juno" in text


def test_summarize_bodies_handles_null_parent_star():
    """实测：恒星（Juno）的 parent 是 null —— 要读成「无母天体（恒星）」，不能当未知、更不能报错。"""
    text = _summarize_bodies([
        {"name": "Juno", "radius": 1000000.0, "surface_gravity": 274.0,
         "has_atmosphere": False, "parent": None},
    ])
    assert "Juno" in text
    assert "母天体 无（恒星）" in text
    assert "未知" not in text


def test_summarize_bodies_without_atmosphere_says_so():
    text = _summarize_bodies([{"name": "Luna", "radius": 100000.0, "has_atmosphere": False}])
    assert "没有一颗天体有大气" in text


def test_summarize_bodies_missing():
    assert "没有读到天体数据" in _summarize_bodies([])


def test_summarize_vessels_marks_player_ship():
    text = _summarize_vessels([
        {"name": "朱雀一号", "node_id": 7, "is_player": True, "altitude": 120000.0,
         "mass": 12500.0, "current_mass": 9800.0, "part_count": 42, "crew_count": 2},
        {"name": "补给船", "node_id": 9, "is_player": False, "altitude": 122000.0,
         "mass": 3000.0, "part_count": 11, "crew_count": 0},
    ])
    assert "你的飞船" in text
    assert "其他飞船" in text
    assert "2 艘飞船" in text
    assert "质量 12,500（游戏内部单位，未标定），当前质量 9,800（游戏内部单位，未标定）" in text
    assert " t " not in text
    assert "kg" not in text


def test_summarize_vessels_missing_current_mass_is_fine():
    """模组旧版本没有 current_mass，缺字段走 _num() 得 null，绝不能报错。"""
    text = _summarize_vessels([{"name": "New", "is_player": True, "mass": 0.0418,
                                "part_count": 1, "crew_count": 1}])
    assert "0.0418（游戏内部单位，未标定）" in text
    assert "当前" not in text


def test_summarize_vessels_missing():
    assert "没有读到飞船" in _summarize_vessels([])


# ---------------------------------------------------------------------------
# 说明书
# ---------------------------------------------------------------------------


def test_docs_topics():
    assert "get_juno_status" in _juno_docs("tools")
    assert "KSP" in _juno_docs("game")
    assert "大气层顶" in _juno_docs("game") or "大气层顶" in _juno_docs("all")
    assert "只读" in _juno_docs("rules")
    assert "delta_v_stage" in _juno_docs("tasks")
    assert len(_juno_docs("all")) > len(_juno_docs("terms"))
    assert _juno_docs("TERMS") == _juno_docs("terms")
    assert _juno_docs() == _juno_docs("all")


def test_docs_units_section_is_reachable():
    """新增的「数值单位」一节必须能通过 topic 取到，且 topic="all" 一定包含。"""
    units = _juno_docs("units")
    assert "数值单位" in units
    assert "实机实测" in units
    # 1. 气压 = Pa（不是 kPa）
    assert "帕斯卡" in units and "71905" in units
    # 2. 空气密度 = kg/m³
    assert "kg/m³" in units
    # 3. 气温 = K（不是摄氏度）
    assert "开尔文" in units and "摄氏度" in units and "6.5 ℃" in units
    # 4. 音速 m/s、马赫无量纲
    assert "speed_of_sound" in units and "无量纲" in units
    # 5. / 6. battery 与 fuel_all_stages_percent 是 0~1
    assert "battery" in units and "fuel_all_stages_percent" in units and "0~1" in units
    # 7. 质量：未标定，以游戏界面为准
    assert "未标定" in units and "以游戏界面为准" in units
    assert "current_mass" in units and "fuel_mass" in units
    # topic="all" 一定包含这一节；别名和大小写也要能取到
    assert _juno_docs("all").count("数值单位") >= 1
    assert units in _juno_docs("all")
    assert _juno_docs("UNIT") == units
    assert _juno_docs(" unit ") == units
    # 从 game / terms / tools 这几节能找到指路
    assert "units" in _juno_docs("game")
    assert "units" in _juno_docs("tools")
    assert "未标定" in _juno_docs("rules")


def test_docs_units_never_claims_kg_or_tonnes_for_mass():
    """说明书里关于质量的说法必须是「未标定」，不能出现「质量是 kg」这种断言。"""
    units = _juno_docs("units")
    assert "不要做 kg→t 换算" in units
    assert "也不要声称它是 kg 或 t" in units
    assert "质量是 **kg**" not in _juno_docs("all")


def test_docs_unknown_topic_lists_options():
    text = _juno_docs("nonsense")
    assert "可选" in text
    assert "get_juno_status" in text  # 仍然给出全部内容，不至于把模型卡死


# ---------------------------------------------------------------------------
# 连不上模组 / 不在飞行场景：绝不抛异常，ok=False
# ---------------------------------------------------------------------------


def test_error_result_shape():
    result = _error_result("连不上朱诺桥接服务。")
    assert result["is_error"] is False
    assert result["output"]["ok"] is False
    assert result["output"]["connected"] is False
    assert "读不到" in result["output"]["message"]


def test_not_in_flight_result_shape():
    result = _not_in_flight_result({"ok": False, "in_flight": False,
                                    "error": "当前不在飞行场景（主菜单、设计器或加载中）"})
    assert result["is_error"] is False
    assert result["output"]["ok"] is False
    assert result["output"]["in_flight"] is False
    assert "主菜单" in result["output"]["message"]
    assert "不要编造" in result["output"]["message"]


def _bare_plugin() -> JunoBridgePlugin:
    """不跑 __init__ 造一个插件实例：只测工具方法本身的错误处理。"""
    plugin = object.__new__(JunoBridgePlugin)
    plugin._bridge_url = "http://127.0.0.1:21577"
    plugin._timeout = 12.0
    plugin._client = None
    plugin._client_loop = None
    return plugin


_NETWORK_TOOLS = (
    "get_juno_status",
    "get_juno_orbit",
    "get_juno_resources",
    "get_juno_flight_data",
    "get_juno_target",
    "list_juno_bodies",
    "list_juno_vessels",
)


def test_tools_never_raise_and_report_not_ok_when_bridge_is_down():
    """模拟连接异常：工具必须返回 ok=False 而不是抛出去，也不许编数据。"""
    plugin = _bare_plugin()

    async def boom(path: str):
        raise SdkError("连不上朱诺桥接服务（http://127.0.0.1:21577）。")

    plugin._get_json = boom  # type: ignore[assignment]

    for name in _NETWORK_TOOLS:
        method = getattr(plugin, name)
        result = asyncio.run(method())
        assert result["is_error"] is False, name
        assert result["output"]["ok"] is False, name
        assert "读不到" in result["output"]["message"], name


def test_tools_report_not_in_flight_when_game_is_in_menu():
    """模组通了但不在飞行场景：同样 ok=False，并提示先进入飞行场景。"""
    plugin = _bare_plugin()

    async def in_menu(path: str):
        return {"ok": False, "in_flight": False,
                "error": "当前不在飞行场景（主菜单、设计器或加载中）"}

    plugin._get_json = in_menu  # type: ignore[assignment]

    for name in _NETWORK_TOOLS:
        method = getattr(plugin, name)
        result = asyncio.run(method())
        assert result["is_error"] is False, name
        assert result["output"]["ok"] is False, name
        assert result["output"]["in_flight"] is False, name
        assert "不在飞行场景" in result["output"]["message"], name


def test_status_tool_returns_summary_when_flying():
    plugin = _bare_plugin()

    async def flying(path: str):
        return {
            "ok": True,
            "in_flight": True,
            "scene": {"body": "Droo"},
            "vessel": {"name": "朱雀一号", "situation": "ORBITING", "body": "Droo",
                       "altitude": 120000.0},
            "flight": {"surface_speed": 2280.4},
            "orbit": {"body": "Droo", "periapsis_altitude": 88000.0, "atmosphere_height": 50000.0},
            "resources": {"fuel_all_stages_percent": 0.62},
            "target": {"ok": True, "locked": False},
        }

    plugin._get_json = flying  # type: ignore[assignment]
    result = asyncio.run(plugin.get_juno_status())
    assert result["is_error"] is False
    assert result["output"]["ok"] is True
    assert "稳定轨道" in result["output"]["summary"]
    assert result["output"]["vessel"]["name"] == "朱雀一号"


def test_docs_tool_needs_no_bridge():
    plugin = _bare_plugin()
    result = asyncio.run(plugin.read_juno_docs(topic="rules"))
    assert result["is_error"] is False
    assert result["output"]["ok"] is True
    assert "只读" in result["output"]["docs"]
    assert result["output"]["topic"] == "rules"


def test_eight_llm_tools_are_registered_with_metadata():
    """8 个 LLM 工具都要挂上 @llm_tool 元数据，描述里要有触发场景。"""
    from plugin.sdk.plugin.llm_tool import LLM_TOOL_META_ATTR

    expected = set(_NETWORK_TOOLS) | {"read_juno_docs"}
    assert len(expected) == 8

    for name in expected:
        func = getattr(JunoBridgePlugin, name)
        meta = getattr(func, LLM_TOOL_META_ATTR, None)
        assert meta is not None, f"{name} 缺少 @llm_tool 元数据"
        assert meta.name == name
        assert len(meta.description) > 30, f"{name} 的描述太短"

    # 读数据的工具都要在描述里说明自己是只读的
    for name in _NETWORK_TOOLS:
        meta = getattr(getattr(JunoBridgePlugin, name), LLM_TOOL_META_ATTR)
        assert "只读" in meta.description, name

    # 说明书工具要能被 topic 分段读，并且要能取到新增的「数值单位」一节
    docs_meta = getattr(getattr(JunoBridgePlugin, "read_juno_docs"), LLM_TOOL_META_ATTR)
    assert "topic" in docs_meta.parameters["properties"]
    assert "units" in docs_meta.parameters["properties"]["topic"]["description"]


# ---------------------------------------------------------------------------
# /bodies、/vessels 的解析（实测：顶层就是数组；恒星的 parent 是 null）
# ---------------------------------------------------------------------------


def test_extract_list_keeps_null_parent_and_top_level_array():
    """parent=null（恒星）是合法数据，不能被丢掉。"""
    items = _extract_list([{"name": "Juno", "parent": None}], "bodies")
    assert len(items) == 1
    assert items[0]["parent"] is None
    # 包装形式也一样
    wrapped = _extract_list({"ok": True, "bodies": [{"name": "Juno", "parent": None}]}, "bodies")
    assert wrapped[0]["parent"] is None


def test_list_juno_bodies_reads_top_level_array():
    """实测 /bodies 顶层直接是 22 个天体的数组，不能因为不是 dict 就整包丢空。"""
    plugin = _bare_plugin()

    async def bodies(path: str):
        assert path == "/bodies"
        return [
            {"name": "Juno", "radius": 1000000.0, "mass": 2.38e30, "surface_gravity": 274.0,
             "has_atmosphere": False, "parent": None},
            {"name": "Droo", "radius": 1274200.0, "mass": 2.3836190410198625e23,
             "surface_gravity": 9.798, "has_atmosphere": True,
             "atmosphere_height": 78146.448, "parent": "Juno"},
        ]

    plugin._get_json = bodies  # type: ignore[assignment]
    result = asyncio.run(plugin.list_juno_bodies())
    assert result["is_error"] is False
    assert result["output"]["ok"] is True
    assert result["output"]["count"] == 2
    text = result["output"]["summary"]
    assert "母天体 无（恒星）" in text
    assert "母天体 Juno" in text
    assert "78.15 km" in text  # atmosphere_height 是米


def test_list_juno_vessels_reads_top_level_array_and_current_mass():
    """实测 /vessels 顶层是数组，元素里 mass 与 current_mass 都是未标定的游戏内部单位。"""
    plugin = _bare_plugin()

    async def vessels(path: str):
        assert path == "/vessels"
        return [{"name": "New", "node_id": 2, "is_player": True, "altitude": 2779.194,
                 "mass": 0.0418, "current_mass": 0.0301, "part_count": 1, "crew_count": 1}]

    plugin._get_json = vessels  # type: ignore[assignment]
    result = asyncio.run(plugin.list_juno_vessels())
    assert result["is_error"] is False
    assert result["output"]["count"] == 1
    text = result["output"]["summary"]
    assert "质量 0.0418（游戏内部单位，未标定），当前质量 0.0301（游戏内部单位，未标定）" in text
    assert " t " not in text
    assert "kg" not in text


# ---------------------------------------------------------------------------
# 离线自测：把实机实测的 /state 片段喂给 8 个工具，确认摘要读得对
# ---------------------------------------------------------------------------

_REAL_VESSEL = {
    "name": "New", "body": "Droo", "situation": "LANDED",
    "altitude": 2779.194, "altitude_agl": 7.4445,
    "mass": 0.041813526302576065, "current_mass": 0.041813526302576065,
    "part_count": 1, "pitch": 89.9798, "heading": 248.6278,
    "latitude": -2.8699, "longitude": 0.4468,
    "atmosphere_height": 78146.448, "command_pod_count": 1, "crew_count": 1,
    "sea_level_altitude": 2779.194, "angle_of_attack": 3.376, "side_slip": 6.879,
}
_REAL_ORBIT = {
    "body": "Droo", "body_radius": 1274200, "atmosphere_height": 78146.448,
    "apoapsis_altitude": 0, "periapsis_altitude": 0, "eccentricity": 0, "inclination": 0,
    "period": 0, "is_valid": True, "orbit_type": "Elliptical", "is_stable_orbit": False,
    "primary_mass": 2.3836190410198625e23, "semi_major_axis": 639015.966,
}
_REAL_FLIGHT = {
    "surface_speed": 7.9e-07, "orbital_speed": 143.258, "vertical_speed": -7.8e-07,
    "gravity": 9.7554, "drag_acceleration": 0, "mach": 2.34e-09,
    "air_density": 0.86481970548629761, "air_pressure": 71905.1015625,
    "temperature": 279.64837646484375, "speed_of_sound": 341.15325927734375,
    "thrust": 0, "max_thrust": 0, "fuel_mass": 0,
}
_REAL_RESOURCES = {
    "fuel_mass": 0, "fuel_in_stage": 0, "monopropellant": 0,
    "battery": 0.9997057318687439, "delta_v_stage": 0, "isp": 0,
    "thrust_to_weight": 0, "burn_time_remaining": 0, "fuel_all_stages_percent": 0,
}
_REAL_STATE = {
    "ok": True, "in_flight": True,
    "scene": {"body": "Droo", "craft_name": "New"},
    "vessel": _REAL_VESSEL, "orbit": _REAL_ORBIT,
    "flight": _REAL_FLIGHT, "resources": _REAL_RESOURCES,
    "target": {"ok": True, "locked": False},
}
_REAL_BODIES = [
    {"name": "Juno", "radius": 1000000, "mass": 2.3836190410198625e30,
     "surface_gravity": 274.0, "escape_velocity": 20000.0, "has_atmosphere": False,
     "atmosphere_height": 0, "has_water": False, "parent": None, "sphere_of_influence": 1e9},
    {"name": "Droo", "radius": 1274200, "mass": 2.3836190410198625e23,
     "surface_gravity": 9.798, "escape_velocity": 5800.0, "has_atmosphere": True,
     "atmosphere_height": 78146.448, "has_water": True, "parent": "Juno",
     "sphere_of_influence": 8.4e7},
]
_REAL_VESSELS = [
    {"name": "New", "node_id": 2, "is_player": True, "altitude": 2779.194,
     "mass": 0.0418, "current_mass": 0.0418, "part_count": 1, "crew_count": 1},
]

_REAL_PAYLOAD = {
    "/state": _REAL_STATE,
    "/orbit": {"ok": True, **_REAL_ORBIT},
    "/flight": {"ok": True, **_REAL_FLIGHT},
    "/resources": {"ok": True, **_REAL_RESOURCES},
    "/target": {"ok": True, "locked": False},
    "/bodies": _REAL_BODIES,
    "/vessels": _REAL_VESSELS,
}


def test_all_tools_with_real_measured_payload_read_correct_units():
    """离线自测：实机实测数据走一遍全部 8 个工具，摘要里的量和单位必须读得对。"""
    plugin = _bare_plugin()

    async def api(path: str):
        return _REAL_PAYLOAD[path]

    plugin._get_json = api  # type: ignore[assignment]

    status = asyncio.run(plugin.get_juno_status())["output"]["summary"]
    assert "0.0418135（游戏内部单位，未标定）" in status
    assert " t " not in status and "kg" not in status
    assert "7.4 m" in status  # altitude_agl 是米

    flight = asyncio.run(plugin.get_juno_flight_data())
    fsum = flight["output"]["summary"]
    assert "71.9 kPa（71905 Pa）" in fsum  # 气压 = 帕斯卡
    assert "6.5 ℃（279.6 K）" in fsum  # 气温 = 开尔文，双标注
    assert "279.6 ℃" not in fsum
    assert "0.86482 kg/m³" in fsum  # 密度 = kg/m³
    assert "341.2 m/s" in fsum  # 音速 = m/s
    assert "游戏内部单位，未标定" in fsum  # fuel_mass

    resources = asyncio.run(plugin.get_juno_resources())["output"]["summary"]
    assert "99.97%（原始值 0.999706）" in resources  # battery 是 0~1
    assert "0%（原始值 0）" in resources  # fuel_all_stages_percent 也是 0~1
    assert " t " not in resources and "kg" not in resources

    orbit = asyncio.run(plugin.get_juno_orbit())["output"]
    assert orbit["ok"] is True
    assert "78.15 km" in orbit["verdict"]  # 大气层顶用的是真实数字

    bodies = asyncio.run(plugin.list_juno_bodies())["output"]
    assert bodies["count"] == 2
    assert "母天体 无（恒星）" in bodies["summary"]  # 恒星 parent=null

    vessels = asyncio.run(plugin.list_juno_vessels())["output"]
    assert vessels["count"] == 1
    assert "游戏内部单位，未标定" in vessels["summary"]

    target = asyncio.run(plugin.get_juno_target())["output"]
    assert target["locked"] is False

    docs = asyncio.run(plugin.read_juno_docs(topic="units"))["output"]
    assert "数值单位" in docs["docs"]
