"""Translate historical strategy records to editable atomic Scratch trees.

The database stores only block trees after migration.  The reverse adapter is
used by the existing combat director so it reads the same tree that players
see in the editor instead of a parallel collection of hidden legacy fields.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


FACTION_ZH = {
    "allied": "盟军", "soviet": "苏联", "epsilon": "厄普西隆",
    "foehn": "焚风", "unknown": "未知阵营",
}
STAGE_ZH = {
    "opening": "开局", "tier2": "二级科技", "tier3": "三级科技",
    "late": "后期", "sudden_death": "决战阶段",
}
ECONOMY_ZH = {"starved": "资源不足", "stable": "经济稳定", "surplus": "资源富余"}
FORCE_RATIO_ZH = {"behind": "兵力落后", "even": "兵力均势", "ahead": "兵力占优"}
TERRAIN_ZH = {"open": "开阔地", "chokepoint": "狭窄口"}
POSTURE_ZH = {
    "defend": "防守", "delay": "迟滞", "probe": "试探", "pressure": "施压",
    "attack": "进攻", "decisive_attack": "决战",
}
THREAT_ZH = {
    "infantry_swarm": "步兵海", "elite_infantry": "精英步兵",
    "area_anti_infantry": "范围反步兵", "light_armor": "轻装甲",
    "heavy_armor": "重装甲", "artillery": "炮兵", "siege": "攻城单位",
    "air_swarm": "空军群", "bomber_strike": "轰炸机突袭",
    "helicopter_raid": "直升机袭击", "anti_air_net": "防空网",
    "stealth_infiltration": "隐形渗透", "naval_surface": "水面舰队",
    "submarine": "潜艇", "amphibious_landing": "两栖登陆",
    "static_defense": "基地防御", "superweapon": "超级武器",
    "economy_expansion": "经济扩张", "tech_rush": "科技速攻",
    "early_rush": "早期突袭", "harvester_raid": "矿车袭击",
    "transport_drop": "运输空降", "mind_control": "心灵控制",
    "balanced_force": "综合部队",
}
ROLE_ZH = {
    "area_anti_infantry": "范围反步兵", "mobile_suppression": "机动压制",
    "long_range_anti_infantry": "远程反步兵", "detector": "探测单位",
    "disabler": "控制单位", "heavy_armor": "重装甲", "long_range": "远程火力",
    "air_strike": "空袭单位", "anti_armor": "反装甲", "snare": "减速控制",
    "medium_armor": "中型装甲", "heavy_anti_armor": "重型反装甲",
    "fast_raider": "高速突击", "transport_flank": "运输侧袭",
    "long_range_counterbattery": "远程反炮兵", "mobile_anti_air": "机动防空",
    "fighter": "战斗机", "static_anti_air": "固定防空", "decoy": "诱饵单位",
    "fast_fighter": "高速战斗机", "screen": "掩护单位",
    "stealth_raider": "隐形突击", "fast_response": "快速反应",
    "anti_ship": "反舰单位", "naval_air": "海军航空兵",
    "coastal_artillery": "岸防火炮", "detector_naval": "海军探测",
    "anti_submarine": "反潜单位", "coastal_defense": "海岸防御",
    "mobile_reserve": "机动预备队", "anti_transport": "反运输单位",
    "artillery": "火炮", "siege": "攻城单位", "fast_strike": "快速打击",
    "harvester_hunter": "矿车猎杀", "scout": "侦察单位",
    "early_pressure": "前期施压", "cheap_screen": "廉价前排",
    "repair_support": "维修支援", "robotic": "机器人单位",
    "combined_arms": "混合兵种",
}
TARGET_ZH = {
    "infantry_production": "步兵生产建筑", "infantry": "步兵",
    "power": "电力建筑", "elite_infantry": "精英步兵", "technology": "科技建筑",
    "area_anti_infantry": "范围反步兵单位", "vehicle_production": "装甲生产建筑",
    "vehicle": "装甲单位", "resource": "经济建筑", "heavy_vehicle": "重装甲单位",
    "artillery": "火炮单位", "radar": "雷达建筑", "siege": "攻城单位",
    "aircraft": "飞机", "aircraft_production": "机场", "bomber": "轰炸机",
    "helicopter": "直升机", "anti_air": "防空单位", "stealth": "隐形单位",
    "transport": "运输单位", "naval": "海军", "naval_production": "海军生产建筑",
    "submarine": "潜艇", "amphibious": "两栖单位", "defense": "防御建筑",
    "construction_yard": "建造场", "superweapon": "超级武器",
    "harvester": "采矿单位", "production": "生产建筑", "rush_units": "突袭部队",
    "raider": "骚扰单位", "mind_control": "心灵控制单位",
    "dropped_units": "空降部队",
}
FORMATION_ZH = {
    "wide_arc": "宽弧阵型", "screened_line": "掩护线阵", "armor_front": "装甲前排",
    "layered_line": "分层线阵", "crossfire": "交叉火力", "two_prong": "双路夹击",
    "deep_flank": "纵深侧翼", "aa_umbrella": "防空伞阵", "dispersed_aa": "分散防空",
    "mobile_umbrella": "机动防空伞", "ground_spearhead": "地面矛头",
    "detector_screen": "探测屏障", "naval_screen": "海军屏卫", "sonar_screen": "声呐屏卫",
    "beachhead_defense": "滩头防御", "siege_line": "攻城线阵", "multiple_axes": "多轴进攻",
    "raiding_pairs": "双单位袭扰", "pressure_column": "施压纵队",
    "defense_in_depth": "纵深防御", "resource_screen": "矿区屏卫",
    "landing_denial": "拒止登陆", "loose_robot_screen": "松散机器人屏卫",
    "combined_arms_box": "混合兵种方阵",
}
ENGAGEMENT_ZH = {
    "spread_fire": "分散火力", "focus_fire": "集中火力", "standoff": "保持射程",
    "concentrated": "集中突击", "close_distance": "快速贴近", "counterbattery": "反炮兵",
    "intercept": "拦截", "pursuit_limit": "限制追击", "ground_breakthrough": "地面突破",
    "reveal_then_focus": "探测后集火", "range_control": "射程控制",
    "destroy_transport_first": "优先摧毁运输单位", "deadline_assault": "限时强攻",
    "hit_and_run": "打带跑", "timing_attack": "时机进攻", "hold_then_counter": "守住后反击",
    "protect_economy": "保护经济", "focus_controller": "优先集火控制单位",
}
CONSTRAINT_BLOCKS = {
    "visible_information_only": "约束.只用可见信息",
    "preserve_player_authority": "约束.尊重玩家控制",
    "do_not_block_independent_production_lanes": "约束.产线独立",
    "verify_native_action_progress": "约束.验证动作",
    "verify_target_type_before_order": "约束.确认目标类型",
    "do_not_switch_ifv_passengers_in_combat": "约束.禁止战中换乘",
}
ACTION_BLOCKS = {
    "deny_allied_mobility_and_air": "战术.压制盟军机动",
    "kite_soviet_armor_and_artillery": "战术.拉扯苏军装甲",
    "screen_against_control_and_stealth": "战术.防范心灵隐形",
    "break_foehn_support_synergy": "战术.拆解焚风协同",
    "scout_to_identify_enemy_doctrine": "战术.侦察识别",
    "preserve_opening_economy": "战术.保护开局经济",
    "contest_map_and_unlock_counters": "战术.争夺地图",
    "field_combined_arms_group": "战术.组建混编部队",
    "rotate_reconstituted_attack_waves": "战术.轮换进攻波次",
    "commit_to_decisive_objective": "战术.发起决战",
    "use_cost_efficient_counter": "战术.经济型反制",
    "maintain_counter_ratio": "战术.维持反制比例",
    "parallel_produce_hard_counters": "战术.并行生产反制",
    "delay_and_reinforce": "战术.迟滞补员",
    "probe_then_commit": "战术.试探后投入",
    "press_verified_advantage": "战术.扩大优势",
    "use_flanks_and_spacing": "战术.侧翼与间距",
    "control_choke_with_range_and_area_fire": "战术.控制狭窄口",
    "reassess_when_target_type_disappears": "战术.目标消失重判",
}


def _reverse(values: dict[str, str]) -> dict[str, str]:
    return {label: key for key, label in values.items()}


@lru_cache(maxsize=1)
def _unit_catalog() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(Path(__file__).with_name("unit_catalog.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    units = raw.get("units") if isinstance(raw, dict) else {}
    return {
        str(key).upper(): value for key, value in (units or {}).items()
        if isinstance(value, dict)
    }


def _node(block_id: str, inputs: dict[str, Any] | None = None, stacks: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"block_id": block_id, "inputs": inputs or {}}
    if stacks is not None:
        value["stacks"] = stacks
    return value


def _text(value: Any) -> dict[str, Any]:
    return _node("运算.文字", {"内容": str(value or "")})


def _equals(reporter: str, value: str) -> dict[str, Any]:
    return _node("运算.等于", {"左值": _node(reporter), "右值": _text(value)})


def _at_least(reporter: dict[str, Any], value: float | int) -> dict[str, Any]:
    return _node("运算.大于等于", {"左值": reporter, "右值": value})


def _and_all(conditions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(conditions)
    if not values:
        return _node("侦测.对局开始")
    result = values[0]
    for value in values[1:]:
        result = _node("运算.并且", {"条件一": result, "条件二": value})
    return result


def _category_for_unit(type_id: str) -> str:
    record = _unit_catalog().get(str(type_id).upper(), {})
    kind = str(record.get("kind") or "")
    movement = str(record.get("movement_zone") or "")
    if kind == "infantry":
        return "步兵"
    if kind == "aircraft":
        return "飞机"
    if kind == "naval" or "Water" in movement:
        return "海军"
    if kind == "building":
        return "建筑"
    return "装甲"


def _selected_enemy(type_id: str) -> dict[str, Any]:
    category = _category_for_unit(type_id)
    if category == "建筑":
        return _node("建筑.选择", {
            "关系": "敌方", "子阵营": "任意子阵营", "类别": "任意建筑",
            "具体建筑": str(type_id).upper(),
        })
    return _node("单位.选择", {
        "关系": "敌方", "子阵营": "任意子阵营", "类别": category,
        "具体单位": str(type_id).upper(),
    })


def _atomic_body(source: dict[str, Any]) -> list[dict[str, Any]]:
    body: list[dict[str, Any]] = [
        _node("战术.策略优先级", {"优先级": int(source.get("priority", 100))}),
    ]
    activation = source.get("activation") if isinstance(source.get("activation"), dict) else {}
    if activation:
        body.extend((
            _node("战术.最低触发数量", {"数量": int(activation.get("minimum_count", 1))}),
            _node("战术.最低威胁值", {"威胁值": float(activation.get("minimum_total_threat", 1))}),
        ))
    posture = POSTURE_ZH.get(str(source.get("posture") or ""))
    if posture:
        body.append(_node("战术.设置姿态", {"姿态": posture}))
    for role in source.get("production_roles") or ():
        body.append(_node("战术.生产角色", {"角色": ROLE_ZH.get(str(role), "未识别作战角色")}))
    for index, target in enumerate(source.get("target_priorities") or (), 1):
        label = TARGET_ZH.get(str(target))
        if not label:
            record = _unit_catalog().get(str(target).upper(), {})
            label = str(record.get("display_name") or "指定敌军单位")
        if str(target).upper() in _unit_catalog():
            body.append(_node("战术.指定目标优先级", {
                "目标": _selected_enemy(str(target)), "顺序": index,
            }))
        else:
            body.append(_node("战术.目标优先级", {"目标类型": label, "顺序": index}))
    formation = FORMATION_ZH.get(str(source.get("formation") or ""))
    if formation:
        body.append(_node("战术.设置阵型", {"阵型": formation}))
    engagement = ENGAGEMENT_ZH.get(str(source.get("engagement") or ""))
    if engagement:
        body.append(_node("战术.设置交战方式", {"方式": engagement}))
    controls = source.get("tactical_controls") if isinstance(source.get("tactical_controls"), dict) else {}
    numeric_controls = (
        ("minimum_assembly_count", "战术.最低集结数量", "数量", False),
        ("frontline_fraction", "战术.设置前线比例", "百分比", True),
        ("anti_armor_fraction", "战术.设置反装甲比例", "百分比", True),
        ("anti_air_fraction", "战术.设置防空比例", "百分比", True),
        ("reserve_fraction", "战术.设置预备队比例", "百分比", True),
        ("engagement_distance_cells", "战术.设置交战距离", "地图格", False),
        ("pursuit_radius_cells", "战术.设置追击半径", "地图格", False),
        ("retreat_health_ratio", "战术.设置撤退生命", "百分比", True),
        ("regroup_seconds", "战术.设置重组时间", "秒数", False),
    )
    for key, block_id, input_name, percent in numeric_controls:
        raw = controls.get(key, source.get(key))
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = round(float(raw) * 100, 2) if percent else raw
            body.append(_node(block_id, {input_name: value}))
    support_window = controls.get("support_power_window")
    if support_window:
        body.append(_node("战术.设置支援时机", {"时机": str(support_window)}))
    for action in source.get("actions") or ():
        token = str(action)
        if token.startswith("counter_"):
            body.append(_node("克制.切换方案", {"威胁类型": THREAT_ZH.get(token[8:], "综合威胁")}))
            continue
        block_id = ACTION_BLOCKS.get(token)
        if block_id:
            body.append(_node(block_id))
    for constraint in source.get("constraints") or ():
        block_id = CONSTRAINT_BLOCKS.get(str(constraint))
        if block_id:
            body.append(_node(block_id))
    return body or [_node("控制.重新判断")]


def _matrix_script(source: dict[str, Any]) -> dict[str, Any]:
    conditions = source.get("conditions") if isinstance(source.get("conditions"), dict) else {}
    condition = _and_all((
        _equals("侦测.敌方阵营", FACTION_ZH.get(str(conditions.get("enemy_faction")), "未知阵营")),
        _equals("侦测.主要威胁", THREAT_ZH.get(str(conditions.get("primary_threat")), "综合部队")),
        _equals("侦测.战局阶段", STAGE_ZH.get(str(conditions.get("stage")), "开局")),
        _equals("侦测.经济状态", ECONOMY_ZH.get(str(conditions.get("economy")), "经济稳定")),
        _equals("侦测.兵力态势", FORCE_RATIO_ZH.get(str(conditions.get("force_ratio")), "兵力均势")),
        _equals("侦测.地形类型", TERRAIN_ZH.get(str(conditions.get("terrain")), "开阔地")),
    ))
    branch = _node("控制.如果", {"条件": condition}, {"那么": _atomic_body(source)})
    return _node("事件.战场变化", stacks={"执行": [branch]})


def _unit_response_script(source: dict[str, Any]) -> dict[str, Any]:
    target = _selected_enemy(str(source.get("enemy_type_id") or ""))
    count = (
        _node("数量.数量", {"对象": target})
        if target.get("block_id") == "建筑.选择"
        else _node("侦测.单位数量", {"单位": target})
    )
    activation = source.get("activation") if isinstance(source.get("activation"), dict) else {}
    minimum_count = max(1, int(activation.get("minimum_count", 1)))
    minimum_threat = max(1.0, float(activation.get("minimum_total_threat", 1.0)))
    visible = _at_least(count, minimum_count)
    dangerous = _at_least(_node("数量.战斗力", {"对象": target}), minimum_threat)
    condition = _and_all((
        visible,
        dangerous,
        _equals("侦测.兵力态势", FORCE_RATIO_ZH.get(str(source.get("force_ratio")), "兵力均势")),
        _equals("侦测.地形类型", TERRAIN_ZH.get(str(source.get("terrain")), "开阔地")),
    ))
    branch = _node("控制.如果", {"条件": condition}, {"那么": _atomic_body(source)})
    return _node("事件.敌军出现", stacks={"执行": [branch]})


def _legacy_action(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("custom_id"):
        return value
    text = str(value.get("legacy_id") or value.get("label_zh") or value.get("opcode") or "") if isinstance(value, dict) else str(value or "")
    lowered = text.casefold()
    if "counter_" in lowered:
        threat = next((key for key in THREAT_ZH if key in lowered), "balanced_force")
        return _node("克制.切换方案", {"威胁类型": THREAT_ZH[threat]})
    if any(token in lowered for token in ("commit", "decisive")):
        return _node("战术.发起决战")
    if any(token in lowered for token in ("attack", "assault", "进攻", "攻击")):
        return _node("战术.扩大优势")
    if any(token in lowered for token in ("deploy", "部署", "garrison")):
        return _node("行动.部署", {"单位": _node("单位.可部署")})
    if any(token in lowered for token in ("repair", "维修")):
        return _node("行动.维修", {"对象": _node("建筑.受损")})
    if any(token in lowered for token in ("produce", "生产", "reinforce", "补员")):
        return _node("战术.并行生产反制")
    return _node("控制.重新判断")


def _core_script(strategy_id: str, source: dict[str, Any]) -> dict[str, Any] | None:
    if strategy_id == "core_gi_deployment_posture":
        gi = _node("单位.选择", {"关系": "我方", "子阵营": "任意子阵营", "类别": "步兵", "具体单位": "E1"})
        branch = _node("控制.如果否则", {"条件": _node("侦测.存在敌人")}, {
            "那么": [_node("行动.部署", {"单位": gi}), _node("控制.等待条件", {"条件": _node("验证.已部署", {"单位": gi})})],
            "否则": [_node("行动.解除部署", {"单位": gi})],
        })
        return _node("事件.战场变化", stacks={"执行": [branch]})
    if strategy_id == "core_harvester_noncombat":
        miner = _node("单位.选择", {"关系": "我方", "子阵营": "任意子阵营", "类别": "采矿单位", "具体单位": "任意"})
        return _node("事件.对局开始", stacks={"执行": [_node("约束.矿车不参战"), _node("行动.释放控制", {"单位": miner})]})
    if strategy_id == "core_base_target_layers":
        return _node("事件.敌军出现", stacks={"执行": [
            _node("战术.目标优先级", {"目标类型": "敌方作战单位", "顺序": 1}),
            _node("战术.目标优先级", {"目标类型": "防御建筑", "顺序": 2}),
            _node("战术.目标优先级", {"目标类型": "生产与科技建筑", "顺序": 3}),
            _node("战术.目标优先级", {"目标类型": "建造场", "顺序": 4}),
        ]})
    if strategy_id == "core_aircraft_reload_release":
        aircraft = _node("单位.选择", {"关系": "我方", "子阵营": "任意子阵营", "类别": "飞机", "具体单位": "任意"})
        return _node("事件.战场变化", stacks={"执行": [_node("约束.飞机补弹释放"), _node("行动.释放控制", {"单位": aircraft})]})
    if strategy_id == "core_independent_production_lanes":
        return _node("事件.对局开始", stacks={"执行": [_node("约束.产线独立"), _node("经济.恢复生产")]})
    return None


def atomic_scripts_from_payload(source: dict[str, Any], *, strategy_id: str = "", strategy_type: str = "") -> list[dict[str, Any]]:
    """Return a complete event-script tree for any historical payload."""
    current = source.get("block_program") if isinstance(source.get("block_program"), dict) else {}
    scripts = current.get("scripts") if isinstance(current, dict) else None
    if isinstance(scripts, list) and scripts:
        return scripts
    core = _core_script(strategy_id or str(source.get("id") or ""), source)
    if core is not None:
        return [core]
    kind = strategy_type or str(source.get("strategy_type") or "")
    if kind == "combat_matrix":
        return [_matrix_script(source)]
    if kind == "unit_response" or source.get("enemy_type_id"):
        return [_unit_response_script(source)]
    actions = current.get("actions") if isinstance(current, dict) else None
    if not isinstance(actions, list):
        actions = source.get("flow") or source.get("actions") or source.get("constraints") or []
    body = [_legacy_action(value) for value in actions] or [_node("控制.重新判断")]
    return [_node("事件.对局开始", stacks={"执行": body})]


def clean_atomic_payload(source: dict[str, Any], program: dict[str, Any], diagnostics: list[str]) -> dict[str, Any]:
    """Keep only editable Scratch state; historical fields remain in DB history."""
    clean: dict[str, Any] = {
        "block_program": {"scripts": program.get("scripts", [])},
        "block_diagnostics": list(diagnostics),
        "block_editor": "原子积木树",
    }
    for key in ("variables", "lists", "custom_blocks"):
        value = source.get(key)
        if isinstance(value, dict) if key != "custom_blocks" else isinstance(value, list):
            clean[key] = value
    return clean


def _walk(node: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield node
    for value in (node.get("inputs") or {}).values():
        if isinstance(value, dict) and value.get("block_id"):
            yield from _walk(value)
    for children in (node.get("stacks") or {}).values():
        for child in children or ():
            if isinstance(child, dict):
                yield from _walk(child)


def repair_atomic_selectors(program: dict[str, Any]) -> bool:
    """Correct selector categories from authoritative unit kinds."""
    changed = False
    scripts = program.get("scripts") if isinstance(program, dict) else None
    for script in scripts if isinstance(scripts, list) else ():
        if not isinstance(script, dict):
            continue
        for node in _walk(script):
            if node.get("block_id") != "单位.选择":
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            type_id = str(inputs.get("具体单位") or "")
            if not type_id or type_id == "任意":
                continue
            category = _category_for_unit(type_id)
            if inputs.get("类别") != category:
                inputs["类别"] = category
                node["inputs"] = inputs
                changed = True
    return changed


def _input_text(node: dict[str, Any], name: str) -> str:
    value = (node.get("inputs") or {}).get(name, "")
    if isinstance(value, dict) and value.get("block_id") == "运算.文字":
        return str((value.get("inputs") or {}).get("内容") or "")
    return str(value or "")


def doctrine_from_atomic_program(program: dict[str, Any], *, strategy_id: str, strategy_type: str) -> dict[str, Any] | None:
    """Reconstruct the combat director view exclusively from atomic blocks."""
    scripts = program.get("scripts") if isinstance(program, dict) else None
    if not isinstance(scripts, list) or not scripts:
        return None
    nodes = [node for script in scripts if isinstance(script, dict) for node in _walk(script)]
    by_id: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        by_id.setdefault(str(node.get("block_id") or ""), []).append(node)
    reverse_reporters = {
        "侦测.敌方阵营": ("enemy_faction", _reverse(FACTION_ZH)),
        "侦测.主要威胁": ("primary_threat", _reverse(THREAT_ZH)),
        "侦测.战局阶段": ("stage", _reverse(STAGE_ZH)),
        "侦测.经济状态": ("economy", _reverse(ECONOMY_ZH)),
        "侦测.兵力态势": ("force_ratio", _reverse(FORCE_RATIO_ZH)),
        "侦测.地形类型": ("terrain", _reverse(TERRAIN_ZH)),
    }
    conditions: dict[str, str] = {}
    for compare in by_id.get("运算.等于", []):
        left = (compare.get("inputs") or {}).get("左值")
        if not isinstance(left, dict) or left.get("block_id") not in reverse_reporters:
            continue
        key, reverse = reverse_reporters[str(left["block_id"])]
        conditions[key] = reverse.get(_input_text(compare, "右值"), "")
    result: dict[str, Any] = {"id": strategy_id}
    priority_nodes = by_id.get("战术.策略优先级", [])
    if priority_nodes:
        result["priority"] = int(float((priority_nodes[0].get("inputs") or {}).get("优先级") or 100))
    if strategy_type == "combat_matrix":
        result["conditions"] = conditions
    elif strategy_type == "unit_response":
        selectors = by_id.get("单位.选择", []) + by_id.get("建筑.选择", [])
        selected = next((node for node in selectors if (node.get("inputs") or {}).get("关系") == "敌方"), None)
        if not selected:
            return None
        inputs = selected.get("inputs") or {}
        result.update({
            "enemy_type_id": str(inputs.get("具体单位") or inputs.get("具体建筑") or "").upper(),
            "enemy_name": str((_unit_catalog().get(str(inputs.get("具体单位") or inputs.get("具体建筑") or "").upper()) or {}).get("display_name") or "指定敌军单位"),
            "force_ratio": conditions.get("force_ratio", "even"),
            "terrain": conditions.get("terrain", "open"),
        })
        trigger_count = by_id.get("战术.最低触发数量", [])
        trigger_threat = by_id.get("战术.最低威胁值", [])
        result["activation"] = {
            "minimum_count": int(float((trigger_count[0].get("inputs") or {}).get("数量") or 1)) if trigger_count else 1,
            "minimum_total_threat": float((trigger_threat[0].get("inputs") or {}).get("威胁值") or 1) if trigger_threat else 1.0,
        }
        record = _unit_catalog().get(result["enemy_type_id"], {})
        result["enemy_tech_level"] = record.get("tech_level")
        result["enemy_role"] = record.get("role")
    else:
        return None
    posture_nodes = by_id.get("战术.设置姿态", [])
    if posture_nodes:
        result["posture"] = _reverse(POSTURE_ZH).get(_input_text(posture_nodes[0], "姿态"), "probe")
    result["production_roles"] = [
        _reverse(ROLE_ZH).get(_input_text(node, "角色"), "combined_arms")
        for node in by_id.get("战术.生产角色", [])
    ]
    ordered_targets = sorted(by_id.get("战术.目标优先级", []), key=lambda node: float((node.get("inputs") or {}).get("顺序") or 999))
    reverse_targets = _reverse(TARGET_ZH)
    result["target_priorities"] = [reverse_targets.get(_input_text(node, "目标类型"), _input_text(node, "目标类型")) for node in ordered_targets]
    specified_targets = sorted(by_id.get("战术.指定目标优先级", []), key=lambda node: float((node.get("inputs") or {}).get("顺序") or 999))
    for node in specified_targets:
        target = (node.get("inputs") or {}).get("目标")
        inputs = target.get("inputs") if isinstance(target, dict) else {}
        type_id = str(inputs.get("具体单位") or inputs.get("具体建筑") or "").upper()
        if type_id:
            result["target_priorities"].insert(max(0, int(float((node.get("inputs") or {}).get("顺序") or 1)) - 1), type_id)
    formation_nodes = by_id.get("战术.设置阵型", [])
    engagement_nodes = by_id.get("战术.设置交战方式", [])
    result["formation"] = _reverse(FORMATION_ZH).get(_input_text(formation_nodes[0], "阵型"), "combined_arms_box") if formation_nodes else "combined_arms_box"
    result["engagement"] = _reverse(ENGAGEMENT_ZH).get(_input_text(engagement_nodes[0], "方式"), "concentrated") if engagement_nodes else "concentrated"
    controls: dict[str, Any] = {}
    for block_id, key, input_name, percent in (
        ("战术.最低集结数量", "minimum_assembly_count", "数量", False),
        ("战术.设置前线比例", "frontline_fraction", "百分比", True),
        ("战术.设置反装甲比例", "anti_armor_fraction", "百分比", True),
        ("战术.设置防空比例", "anti_air_fraction", "百分比", True),
        ("战术.设置预备队比例", "reserve_fraction", "百分比", True),
        ("战术.设置交战距离", "engagement_distance_cells", "地图格", False),
        ("战术.设置追击半径", "pursuit_radius_cells", "地图格", False),
        ("战术.设置撤退生命", "retreat_health_ratio", "百分比", True),
        ("战术.设置重组时间", "regroup_seconds", "秒数", False),
    ):
        values = by_id.get(block_id, [])
        if values:
            raw = (values[0].get("inputs") or {}).get(input_name)
            if isinstance(raw, (int, float)):
                controls[key] = float(raw) / 100 if percent else raw
    support = by_id.get("战术.设置支援时机", [])
    if support:
        controls["support_power_window"] = _input_text(support[0], "时机")
    result["tactical_controls"] = controls
    reverse_actions = {value: key for key, value in ACTION_BLOCKS.items()}
    actions = [reverse_actions[node_id] for node_id in reverse_actions if node_id in by_id]
    for counter in by_id.get("克制.切换方案", []):
        threat = _reverse(THREAT_ZH).get(_input_text(counter, "威胁类型"))
        if threat:
            actions.append("counter_" + threat)
    result["actions"] = actions
    reverse_constraints = {value: key for key, value in CONSTRAINT_BLOCKS.items()}
    result["constraints"] = [reverse_constraints[node_id] for node_id in reverse_constraints if node_id in by_id]
    if strategy_type == "combat_matrix":
        result.setdefault("retreat_health_ratio", controls.get("retreat_health_ratio", 0.38))
        result.setdefault("reserve_fraction", controls.get("reserve_fraction", 0.25))
        result.setdefault("reassess_seconds", 6)
    return result
