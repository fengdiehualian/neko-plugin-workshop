"""Deterministically build the large declarative MO combat doctrine catalog."""
from __future__ import annotations

import json
from itertools import product
from pathlib import Path
from typing import Any

try:
    from .unit_threat import classify_unit_threat
except ImportError:
    from unit_threat import classify_unit_threat


FACTIONS = ("allied", "soviet", "epsilon", "foehn", "unknown")
STAGES = ("opening", "tier2", "tier3", "late", "sudden_death")
ECONOMIES = ("starved", "stable", "surplus")
FORCE_RATIOS = ("behind", "even", "ahead")
TERRAINS = ("open", "chokepoint")

THREATS: dict[str, dict[str, Any]] = {
    "infantry_swarm": {"roles": ["area_anti_infantry", "mobile_suppression"], "targets": ["infantry_production", "infantry", "power"], "formation": "wide_arc", "engagement": "spread_fire"},
    "elite_infantry": {"roles": ["long_range_anti_infantry", "detector", "disabler"], "targets": ["elite_infantry", "infantry_production", "technology"], "formation": "screened_line", "engagement": "focus_fire"},
    "area_anti_infantry": {"roles": ["heavy_armor", "long_range", "air_strike"], "targets": ["area_anti_infantry", "vehicle_production", "technology"], "formation": "armor_front", "engagement": "standoff"},
    "light_armor": {"roles": ["anti_armor", "snare", "medium_armor"], "targets": ["vehicle", "vehicle_production", "resource"], "formation": "layered_line", "engagement": "focus_fire"},
    "heavy_armor": {"roles": ["heavy_anti_armor", "disabler", "air_strike"], "targets": ["heavy_vehicle", "vehicle_production", "technology"], "formation": "crossfire", "engagement": "concentrated"},
    "artillery": {"roles": ["fast_raider", "air_strike", "transport_flank"], "targets": ["artillery", "vehicle_production", "radar"], "formation": "two_prong", "engagement": "close_distance"},
    "siege": {"roles": ["fast_raider", "long_range_counterbattery", "air_strike"], "targets": ["siege", "technology", "vehicle_production"], "formation": "deep_flank", "engagement": "counterbattery"},
    "air_swarm": {"roles": ["mobile_anti_air", "fighter", "static_anti_air"], "targets": ["aircraft", "aircraft_production", "power"], "formation": "aa_umbrella", "engagement": "intercept"},
    "bomber_strike": {"roles": ["fighter", "mobile_anti_air", "decoy"], "targets": ["bomber", "aircraft_production", "radar"], "formation": "dispersed_aa", "engagement": "intercept"},
    "helicopter_raid": {"roles": ["mobile_anti_air", "fast_fighter", "screen"], "targets": ["helicopter", "aircraft_production", "resource"], "formation": "mobile_umbrella", "engagement": "pursuit_limit"},
    "anti_air_net": {"roles": ["heavy_armor", "artillery", "stealth_raider"], "targets": ["anti_air", "power", "technology"], "formation": "ground_spearhead", "engagement": "ground_breakthrough"},
    "stealth_infiltration": {"roles": ["detector", "area_denial", "fast_response"], "targets": ["stealth", "transport", "technology"], "formation": "detector_screen", "engagement": "reveal_then_focus"},
    "naval_surface": {"roles": ["anti_ship", "naval_air", "coastal_artillery"], "targets": ["naval", "naval_production", "resource"], "formation": "naval_screen", "engagement": "range_control"},
    "submarine": {"roles": ["detector_naval", "anti_submarine", "naval_air"], "targets": ["submarine", "naval_production", "radar"], "formation": "sonar_screen", "engagement": "reveal_then_focus"},
    "amphibious_landing": {"roles": ["coastal_defense", "mobile_reserve", "anti_transport"], "targets": ["transport", "amphibious", "naval_production"], "formation": "beachhead_defense", "engagement": "destroy_transport_first"},
    "static_defense": {"roles": ["artillery", "siege", "disabler"], "targets": ["defense", "power", "construction_yard"], "formation": "siege_line", "engagement": "standoff"},
    "superweapon": {"roles": ["fast_strike", "air_strike", "transport_flank"], "targets": ["superweapon", "power", "construction_yard"], "formation": "multiple_axes", "engagement": "deadline_assault"},
    "economy_expansion": {"roles": ["fast_raider", "scout", "harvester_hunter"], "targets": ["resource", "harvester", "construction_yard"], "formation": "raiding_pairs", "engagement": "hit_and_run"},
    "tech_rush": {"roles": ["early_pressure", "fast_raider", "siege"], "targets": ["technology", "power", "production"], "formation": "pressure_column", "engagement": "timing_attack"},
    "early_rush": {"roles": ["cheap_screen", "area_anti_infantry", "mobile_reserve"], "targets": ["rush_units", "infantry_production", "resource"], "formation": "defense_in_depth", "engagement": "hold_then_counter"},
    "harvester_raid": {"roles": ["fast_response", "repair_support", "snare"], "targets": ["raider", "transport", "vehicle_production"], "formation": "resource_screen", "engagement": "protect_economy"},
    "transport_drop": {"roles": ["anti_transport", "mobile_anti_air", "area_denial"], "targets": ["transport", "dropped_units", "aircraft_production"], "formation": "landing_denial", "engagement": "destroy_transport_first"},
    "mind_control": {"roles": ["long_range", "robotic", "air_strike"], "targets": ["mind_control", "technology", "power"], "formation": "loose_robot_screen", "engagement": "focus_controller"},
    "balanced_force": {"roles": ["combined_arms", "repair_support", "scout"], "targets": ["superweapon", "production", "resource"], "formation": "combined_arms_box", "engagement": "concentrated"},
}

FACTION_ACTION = {
    "allied": "deny_allied_mobility_and_air", "soviet": "kite_soviet_armor_and_artillery",
    "epsilon": "screen_against_control_and_stealth", "foehn": "break_foehn_support_synergy",
    "unknown": "scout_to_identify_enemy_doctrine",
}
STAGE_ACTION = {
    "opening": "preserve_opening_economy", "tier2": "contest_map_and_unlock_counters",
    "tier3": "field_combined_arms_group", "late": "rotate_reconstituted_attack_waves",
    "sudden_death": "commit_to_decisive_objective",
}
ECONOMY_ACTION = {
    "starved": "use_cost_efficient_counter", "stable": "maintain_counter_ratio",
    "surplus": "parallel_produce_hard_counters",
}
RATIO_ACTION = {
    "behind": "delay_and_reinforce", "even": "probe_then_commit", "ahead": "press_verified_advantage",
}
TERRAIN_ACTION = {"open": "use_flanks_and_spacing", "chokepoint": "control_choke_with_range_and_area_fire"}

THREAT_PRIORITY = {
    "superweapon": 172, "transport_drop": 164, "early_rush": 160,
    "harvester_raid": 158, "bomber_strike": 156, "mind_control": 154,
    "siege": 150, "artillery": 148, "heavy_armor": 146,
    "air_swarm": 144, "helicopter_raid": 142, "stealth_infiltration": 140,
    "amphibious_landing": 138, "static_defense": 134, "tech_rush": 132,
    "elite_infantry": 130, "infantry_swarm": 128, "area_anti_infantry": 126,
    "light_armor": 124, "anti_air_net": 122, "naval_surface": 120,
    "submarine": 120, "economy_expansion": 116, "balanced_force": 112,
}


def _posture(force_ratio: str, stage: str, economy: str) -> str:
    if force_ratio == "behind":
        return "delay" if economy != "starved" else "defend"
    if force_ratio == "ahead":
        return "decisive_attack" if stage in {"tier3", "late", "sudden_death"} else "pressure"
    return "probe" if stage not in {"late", "sudden_death"} else "attack"


def _unit_counter(record: dict[str, Any]) -> tuple[list[str], list[str], str, str]:
    threat = classify_unit_threat(record)
    role = str(record.get("role") or "")
    building_class = str(record.get("building_class") or "")
    if building_class == "superweapon" or role == "superweapon":
        return ["fast_strike", "air_strike", "transport_flank"], ["superweapon", "power"], "multiple_axes", "deadline_assault"
    if building_class == "defense" or role == "defense":
        return ["artillery", "siege", "disabler"], [str(record.get("type_id")), "power"], "siege_line", "standoff"
    if threat.get("battlefield_class") == "air":
        return ["mobile_anti_air", "fighter", "static_anti_air"], [str(record.get("type_id")), "aircraft_production"], "aa_umbrella", "intercept"
    if threat.get("battlefield_class") == "naval":
        return ["anti_ship", "naval_air", "coastal_artillery"], [str(record.get("type_id")), "naval_production"], "naval_screen", "range_control"
    if threat.get("battlefield_class") == "infantry":
        if threat.get("area_anti_infantry") is True:
            return ["heavy_armor", "long_range", "air_strike"], [str(record.get("type_id")), "infantry_production"], "armor_front", "standoff"
        return ["area_anti_infantry", "long_range_anti_infantry", "mobile_suppression"], [str(record.get("type_id")), "infantry_production"], "wide_arc", "focus_fire"
    if threat.get("battlefield_class") == "armored":
        return ["heavy_anti_armor", "disabler", "air_strike"], [str(record.get("type_id")), "vehicle_production"], "crossfire", "concentrated"
    return ["combined_arms", "scout", "repair_support"], [str(record.get("type_id")), "production"], "combined_arms_box", "concentrated"


def _unit_activation(record: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Return a visible, data-derived trigger instead of a blanket count > 0."""
    threat_score = max(1.0, float(profile.get("threat_score") or 1.0))
    building_class = str(record.get("building_class") or "")
    urgent_single = (
        profile.get("threat_level") in {"high", "critical"}
        or profile.get("anti_air") is True
        or profile.get("area_anti_infantry") is True
        or building_class in {"superweapon", "defense"}
    )
    minimum_count = 1 if urgent_single else 2 if threat_score >= 28 else 3
    return {
        "minimum_count": minimum_count,
        "minimum_total_threat": round(max(12.0, threat_score * minimum_count * 0.72), 1),
    }


def _unit_priority(record: dict[str, Any], profile: dict[str, Any], force_ratio: str) -> int:
    score = 62 + min(82, round(float(profile.get("threat_score") or 0.0)))
    if str(record.get("building_class") or "") == "superweapon":
        score += 30
    if force_ratio == "behind":
        score += 14
    elif force_ratio == "ahead":
        score -= 6
    return max(55, min(195, score))


def _matrix_priority(threat: str, stage: str, economy: str, force_ratio: str) -> int:
    value = THREAT_PRIORITY.get(threat, 110)
    value += {"opening": 8, "tier2": 4, "tier3": 2, "late": 0, "sudden_death": 10}[stage]
    value += {"behind": 12, "even": 4, "ahead": -4}[force_ratio]
    if economy == "starved" and threat not in {"early_rush", "harvester_raid", "transport_drop"}:
        value -= 4
    return max(70, min(195, value))


def _unit_responses() -> list[dict[str, Any]]:
    catalog_path = Path(__file__).with_name("unit_catalog.json")
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    units = payload.get("units") if isinstance(payload, dict) else {}
    responses: list[dict[str, Any]] = []
    combat_units = [
        record for record in units.values()
        if isinstance(record, dict) and record.get("combat_relevant") is True
        and record.get("kind") in {"infantry", "vehicle", "aircraft", "building"}
    ] if isinstance(units, dict) else []
    combat_units.sort(key=lambda item: str(item.get("type_id") or ""))
    for record, force_ratio, terrain in product(combat_units, FORCE_RATIOS, TERRAINS):
        type_id = str(record.get("type_id") or "").upper()
        profile = classify_unit_threat(record)
        roles, targets, formation, engagement = _unit_counter(record)
        activation = _unit_activation(record, profile)
        priority = _unit_priority(record, profile, force_ratio)
        assembly = (10 if force_ratio == "behind" else 7 if force_ratio == "even" else 5)
        if profile.get("threat_level") == "critical":
            assembly += 2
        response_id = f"unit_{type_id}_{force_ratio}_{terrain}".casefold()
        responses.append({
            "id": response_id,
            "enemy_type_id": type_id,
            "enemy_name": str(record.get("display_name") or record.get("internal_name") or type_id),
            "force_ratio": force_ratio,
            "terrain": terrain,
            "enemy_tech_level": record.get("tech_level"),
            "enemy_role": profile.get("battlefield_class") or record.get("role"),
            "enemy_threat_level": profile.get("threat_level"),
            "enemy_threat_score": profile.get("threat_score"),
            "activation": activation,
            "priority": priority,
            "weight": round(float(profile.get("threat_score") or 1.0), 2),
            "risk_level": "high" if profile.get("threat_level") in {"high", "critical"} else "medium",
            "production_roles": roles,
            "target_priorities": targets,
            "formation": formation,
            "engagement": engagement,
            "tactical_controls": {
                "minimum_assembly_count": assembly,
                "frontline_fraction": 0.52 if terrain == "open" else 0.64,
                "anti_armor_fraction": 0.38 if profile.get("battlefield_class") == "armored" else 0.16,
                "anti_air_fraction": 0.34 if profile.get("battlefield_class") == "air" or profile.get("anti_air") is True else 0.10,
                "reserve_fraction": 0.38 if force_ratio == "behind" else 0.24 if force_ratio == "even" else 0.14,
                "engagement_distance_cells": 9 if terrain == "open" else 5,
                "pursuit_radius_cells": 14 if force_ratio == "ahead" else 8,
                "retreat_health_ratio": 0.48 if force_ratio == "behind" else 0.34 if force_ratio == "even" else 0.22,
                "regroup_seconds": 12 if force_ratio == "behind" else 8,
                "building_priority": ["superweapon", "production", "technology", "power", "resource"],
                "support_power_window": "先削弱密集敌军，再推进；未知技能不自动释放",
            },
            "actions": [
                f"focus_type_{type_id}", RATIO_ACTION[force_ratio],
                TERRAIN_ACTION[terrain], "reassess_when_target_type_disappears",
            ],
            "constraints": [
                "visible_information_only", "preserve_player_authority",
                "verify_target_type_before_order", "do_not_switch_ifv_passengers_in_combat",
            ],
        })
    return responses


def build_catalog() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    dimensions = product(FACTIONS, THREATS, STAGES, ECONOMIES, FORCE_RATIOS, TERRAINS)
    for index, (faction, threat, stage, economy, force_ratio, terrain) in enumerate(dimensions, 1):
        profile = THREATS[threat]
        posture = _posture(force_ratio, stage, economy)
        actions = [
            FACTION_ACTION[faction], f"counter_{threat}", STAGE_ACTION[stage],
            ECONOMY_ACTION[economy], RATIO_ACTION[force_ratio], TERRAIN_ACTION[terrain],
        ]
        entries.append({
            "id": f"mo_doctrine_{index:05d}",
            "conditions": {
                "enemy_faction": faction, "primary_threat": threat,
                "stage": stage, "economy": economy,
                "force_ratio": force_ratio, "terrain": terrain,
            },
            "priority": _matrix_priority(threat, stage, economy, force_ratio),
            "weight": 70 + (20 if threat in {"superweapon", "early_rush", "harvester_raid", "transport_drop"} else 0),
            "risk_level": "high" if threat in {"superweapon", "early_rush", "harvester_raid", "transport_drop", "bomber_strike"} else "medium",
            "posture": posture,
            "actions": actions,
            "production_roles": list(profile["roles"]),
            "target_priorities": list(profile["targets"]),
            "formation": profile["formation"],
            "engagement": profile["engagement"],
            "tactical_controls": {
                "minimum_assembly_count": 10 if force_ratio == "behind" else 7 if force_ratio == "even" else 5,
                "frontline_fraction": 0.52 if force_ratio == "behind" else 0.60 if force_ratio == "even" else 0.68,
                "anti_armor_fraction": 0.40 if threat in {"heavy_armor", "light_armor", "artillery", "siege"} else 0.20,
                "anti_air_fraction": 0.35 if threat in {"air_swarm", "bomber_strike", "helicopter_raid"} else 0.15,
                "reserve_fraction": 0.38 if force_ratio == "behind" else 0.24 if force_ratio == "even" else 0.14,
                "engagement_distance_cells": 10 if terrain == "open" else 6,
                "pursuit_radius_cells": 10,
                "retreat_health_ratio": 0.38,
                "regroup_seconds": 10,
                "building_priority": ["superweapon", "production", "technology", "power", "resource"],
                "support_power_window": "目标密集且确认非中立场景物件时释放",
            },
            "retreat_health_ratio": 0.48 if force_ratio == "behind" else 0.34 if force_ratio == "even" else 0.22,
            "reserve_fraction": 0.35 if force_ratio == "behind" else 0.22 if force_ratio == "even" else 0.12,
            "reassess_seconds": 3 if threat in {"superweapon", "early_rush", "transport_drop"} else 6,
            "constraints": [
                "visible_information_only", "preserve_player_authority",
                "do_not_block_independent_production_lanes", "verify_native_action_progress",
            ],
        })
    unit_responses = _unit_responses()
    return {
        "schema": 1,
        "metadata": {
            "entry_count": len(entries) + len(unit_responses),
            "matrix_entry_count": len(entries),
            "unit_response_count": len(unit_responses), "factions": len(FACTIONS),
            "threats": len(THREATS), "stages": len(STAGES),
            "economies": len(ECONOMIES), "force_ratios": len(FORCE_RATIOS),
            "terrains": len(TERRAINS),
            "generation": "cartesian_doctrine_matrix",
        },
        "entries": entries,
        "unit_responses": unit_responses,
    }


def write_catalog(path: str | Path | None = None) -> Path:
    target = Path(path) if path else Path(__file__).with_name("combat_doctrine.json")
    payload = build_catalog()
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return target


def write_classification_md(
    payload: dict[str, Any] | None = None, path: str | Path | None = None,
) -> Path:
    data = payload if isinstance(payload, dict) else build_catalog()
    target = Path(path) if path else Path(__file__).with_name("所有战略库的分类.md")
    metadata = data["metadata"]
    lines = [
        "# 所有战略库的分类",
        "",
        "> 本文档由 `combat_doctrine_generator.py` 与实装 JSON 同源生成。规则只使用可见战场信息，且不越过玩家授权。",
        "",
        "## 一、规模与结构条款",
        "",
        f"- 战略条目总数：**{metadata['entry_count']}**。",
        f"- 多维战局矩阵：**{metadata['matrix_entry_count']}** 条。",
        f"- 真实单位专属应对：**{metadata['unit_response_count']}** 条。",
        f"- 维度：{metadata['factions']} 种阵营识别 × {metadata['threats']} 类威胁 × {metadata['stages']} 个阶段 × {metadata['economies']} 档经济 × {metadata['force_ratios']} 档兵力比 × {metadata['terrains']} 类地形。",
        "- 检索顺序：实时可见信息 → 战局矩阵规则 → 当前最高威胁 TypeID 专属规则 → 合并为一份有效指令。",
        "",
        "## 二、阵营识别条款",
        "",
        "| 分类 | 核心应对 |",
        "|---|---|",
    ]
    faction_names = {
        "allied": "盟军", "soviet": "苏军", "epsilon": "厄普西隆",
        "foehn": "焚风", "unknown": "未知/尚未侦察确认",
    }
    for faction in FACTIONS:
        lines.append(f"| {faction_names[faction]} (`{faction}`) | `{FACTION_ACTION[faction]}` |")
    lines.extend([
        "",
        "## 三、威胁原型分类条款",
        "",
        "| 威胁分类 | 生产反制角色 | 目标优先级 | 阵型 | 交战方式 |",
        "|---|---|---|---|---|",
    ])
    for threat, profile in THREATS.items():
        lines.append(
            f"| `{threat}` | {', '.join(f'`{x}`' for x in profile['roles'])} "
            f"| {', '.join(f'`{x}`' for x in profile['targets'])} "
            f"| `{profile['formation']}` | `{profile['engagement']}` |"
        )
    lines.extend([
        "",
        "## 四、战局阶段分类条款",
        "",
        "| 阶段 | 执行条款 |",
        "|---|---|",
    ])
    for stage in STAGES:
        lines.append(f"| `{stage}` | `{STAGE_ACTION[stage]}` |")
    lines.extend([
        "",
        "## 五、经济分类条款",
        "",
        "| 经济状态 | 执行条款 |",
        "|---|---|",
    ])
    for economy in ECONOMIES:
        lines.append(f"| `{economy}` | `{ECONOMY_ACTION[economy]}` |")
    lines.extend([
        "",
        "## 六、兵力比分类条款",
        "",
        "| 兵力比 | 战斗姿态基础条款 | 典型预备队 |",
        "|---|---|---|",
        f"| `behind` | `{RATIO_ACTION['behind']}` | 35% |",
        f"| `even` | `{RATIO_ACTION['even']}` | 22% |",
        f"| `ahead` | `{RATIO_ACTION['ahead']}` | 12% |",
        "",
        "## 七、地形分类条款",
        "",
        "| 地形 | 执行条款 |",
        "|---|---|",
    ])
    for terrain in TERRAINS:
        lines.append(f"| `{terrain}` | `{TERRAIN_ACTION[terrain]}` |")
    lines.extend([
        "",
        "## 八、真实单位专属反制登记",
        "",
        "每个 TypeID 均有 `behind/even/ahead × open/chokepoint` 六条独立应对。下表按单位种类和职责分类，展示六条规则共享的反制核心；运行时仍会按兵力比和地形选择准确条目。",
        "",
    ])
    responses = data.get("unit_responses", [])
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for item in responses:
        type_id = str(item.get("enemy_type_id") or "")
        if not type_id or type_id in seen:
            continue
        seen.add(type_id)
        source = next(
            (row for row in responses if row.get("enemy_type_id") == type_id), item
        )
        kind = "building" if str(source.get("enemy_role")) in {"defense", "technology", "superweapon", "resource"} else "unit"
        role = str(source.get("enemy_role") or "unclassified")
        grouped.setdefault((kind, role), []).append(source)
    for (kind, role), rows in sorted(grouped.items()):
        lines.extend([
            f"### {kind} / `{role}`",
            "",
            "| TypeID | 名称 | 科技等级 | 反制生产角色 | 首要目标 | 阵型 | 交战 | 规则数 |",
            "|---|---|---:|---|---|---|---|---:|",
        ])
        for item in sorted(rows, key=lambda row: str(row.get("enemy_type_id"))):
            lines.append(
                f"| `{item['enemy_type_id']}` | {str(item.get('enemy_name') or '').replace('|', '/')} "
                f"| {item.get('enemy_tech_level', '')} "
                f"| {', '.join(f'`{x}`' for x in item.get('production_roles', []))} "
                f"| {', '.join(f'`{x}`' for x in item.get('target_priorities', []))} "
                f"| `{item.get('formation')}` | `{item.get('engagement')}` | 6 |"
            )
        lines.append("")
    lines.extend([
        "## 九、动作验证与权限条款",
        "",
        "1. 只能根据当前可见敌军、已验证的己方状态和世界模型记忆做决策。",
        "2. 未获得玩家明确授权时，不启动自主发展或自主进攻。",
        "3. 建筑、支援插件、步兵、车辆、航空、海军六条产线独立轮询，任何单线繁忙不得冻结其他线。",
        "4. 指令被桥接接受不等于完成；必须继续观察游戏原生状态确认进展。",
        "5. 固定 IFV 车队接战后禁止重新装载、卸载或更换乘员。",
        "6. 低于当前规则撤退血量的单位退出攻击分配并返回最近己方建筑。",
        "7. 目标死亡、失去可见性或变成无效场景物件后，旧攻击分配立即失效并重新评估。",
        "8. 每次规则切换均保留规则 ID、单位专属规则 ID、生产角色、目标优先级和原因以供日志审计。",
        "",
    ])
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def write_classification_md(
    payload: dict[str, Any] | None = None, path: str | Path | None = None,
) -> Path:
    """Write the human-facing strategy catalog in Chinese.

    TypeID and rule IDs remain unchanged for log correlation; all descriptive
    labels are translated here instead of exposing generator implementation tags.
    """
    data = payload if isinstance(payload, dict) else build_catalog()
    target = Path(path) if path else Path(__file__).with_name("所有战略库的分类.md")
    meta = data.get("metadata", {})
    zh = {
        "allied": "盟军", "soviet": "苏军", "epsilon": "厄普西隆",
        "foehn": "焚风", "unknown": "未知阵营",
        "opening": "开局", "tier2": "T2 科技阶段", "tier3": "T3 科技阶段",
        "late": "后期", "sudden_death": "决胜阶段",
        "starved": "资源紧张", "stable": "经济稳定", "surplus": "经济盈余",
        "behind": "兵力落后", "even": "兵力相当", "ahead": "兵力领先",
        "open": "开阔地", "chokepoint": "狭窄通道",
        "infantry": "步兵", "vehicle": "装甲车辆", "aircraft": "空军", "building": "建筑",
        "defense": "防御建筑", "technology": "科技建筑", "superweapon": "超级武器",
        "resource": "经济建筑", "unclassified": "未分类",
    }
    action_zh = {
        "deny_allied_mobility_and_air": "限制盟军机动与制空能力",
        "kite_soviet_armor_and_artillery": "风筝苏军装甲与炮兵",
        "screen_against_control_and_stealth": "防范控制与隐形单位",
        "break_foehn_support_synergy": "拆解焚风支援协同",
        "scout_to_identify_enemy_doctrine": "先侦察确认敌方打法",
        "preserve_opening_economy": "保护开局经济并完成基础展开",
        "contest_map_and_unlock_counters": "争夺地图并解锁反制单位",
        "field_combined_arms_group": "组建步兵、装甲、空军联合编队",
        "rotate_reconstituted_attack_waves": "轮换重组后的进攻波次",
        "commit_to_decisive_objective": "集中力量完成决定性目标",
        "use_cost_efficient_counter": "优先使用低成本有效反制",
        "maintain_counter_ratio": "维持反制兵力比例",
        "parallel_produce_hard_counters": "利用独立产线并行生产硬反制",
        "delay_and_reinforce": "延缓交战并补充预备队",
        "probe_then_commit": "试探后集中投入",
        "press_verified_advantage": "确认优势后持续施压",
        "use_flanks_and_spacing": "利用侧翼并保持间距",
        "control_choke_with_range_and_area_fire": "用射程与范围火力封锁通道",
    }
    def translate(value: Any) -> str:
        text = str(value or "")
        if text in zh:
            return zh[text]
        if text in action_zh:
            return action_zh[text]
        return text.replace("_", " ")
    lines = [
        "# 所有战略库的分类",
        "",
        "> 本文档与 `combat_doctrine.json` 同源生成。规则只使用当前可见战场信息，且不越过玩家授权。",
        "",
        "## 一、规模与检索顺序",
        "",
        f"- 战略条目总数：**{meta.get('entry_count', 0)}**。",
        f"- 战局矩阵条目：**{meta.get('matrix_entry_count', 0)}**。",
        f"- 真实 TypeID 专属反制：**{meta.get('unit_response_count', 0)}**。",
        f"- 维度：{meta.get('factions', 0)} 个阵营 × {meta.get('threats', 0)} 类威胁 × {meta.get('stages', 0)} 个阶段 × {meta.get('economies', 0)} 种经济状态 × {meta.get('force_ratios', 0)} 种兵力关系 × {meta.get('terrains', 0)} 种地形。",
        "- 检索顺序：实时可见信息 → 战局矩阵 → 当前最高威胁 TypeID 专属条款 → 动作验证与重新评估。",
        "",
        "## 二、阵营分类",
        "",
        "| 阵营 | 核心应对 |",
        "|---|---|",
    ]
    for faction in FACTIONS:
        lines.append(f"| {translate(faction)}（`{faction}`） | {translate(FACTION_ACTION[faction])} |")
    lines.extend(["", "## 三、威胁原型分类", "", "| 威胁类别 | 反制生产角色 | 优先目标 | 阵型 | 交战方式 |", "|---|---|---|---|---|"])
    for threat, profile in THREATS.items():
        lines.append(
            f"| {translate(threat)}（`{threat}`） | {'、'.join(translate(x) for x in profile['roles'])} | "
            f"{'、'.join(translate(x) for x in profile['targets'])} | {translate(profile['formation'])} | {translate(profile['engagement'])} |"
        )
    for title, values, actions in [
        ("四、战局阶段", STAGES, STAGE_ACTION),
        ("五、经济状态", ECONOMIES, ECONOMY_ACTION),
        ("六、兵力关系", FORCE_RATIOS, RATIO_ACTION),
        ("七、地形状态", TERRAINS, TERRAIN_ACTION),
    ]:
        lines.extend(["", f"## {title}", "", "| 分类 | 执行原则 |", "|---|---|"])
        for key in values:
            lines.append(f"| {translate(key)}（`{key}`） | {translate(actions[key])} |")
    lines.extend(["", "## 八、真实 TypeID 专属反制", "", "每个 TypeID 按兵力关系与地形分别生成六条可执行规则。字段中的内部 ID 保留用于程序检索。", ""])
    seen: set[str] = set()
    for item in data.get("unit_responses", []):
        type_id = str(item.get("enemy_type_id") or "").upper()
        if not type_id or type_id in seen:
            continue
        seen.add(type_id)
        kind = translate(item.get("enemy_kind") or "unit")
        role = translate(item.get("enemy_role") or "unclassified")
        lines.extend([
            f"### {kind}：{role}（`{type_id}`）",
            "",
            "| 名称 | 科技等级 | 反制生产角色 | 优先目标 | 阵型 | 交战方式 |",
            "|---|---:|---|---|---|---|",
            f"| {str(item.get('enemy_name') or type_id).replace('|', '/')} | {item.get('enemy_tech_level', '')} | {'、'.join(translate(x) for x in item.get('production_roles', []))} | {'、'.join(translate(x) for x in item.get('target_priorities', []))} | {translate(item.get('formation'))} | {translate(item.get('engagement'))} |",
            "",
        ])
    lines.extend([
        "## 九、动作验证与权限条款", "",
        "1. 仅依据当前可见敌军、已验证己方状态和世界模型记忆决策。",
        "2. 未获得玩家明确授权时，不启动自主发展、支援技能或自主进攻。",
        "3. 建筑、支援技能、步兵、车辆、空军、海军六条产线独立轮询，单条等待不得冻结其他产线。",
        "4. 桥接接受不等于游戏完成；必须通过下一帧状态确认实际进度。",
        "5. 目标死亡、消失或被识别为中立场景物件后，立即使旧命令失效并重新评估。",
    ])
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def write_classification_md(
    payload: dict[str, Any] | None = None, path: str | Path | None = None,
) -> Path:
    """Final UTF-8 Chinese renderer; escaped literals avoid locale-dependent source encoding."""
    data = payload if isinstance(payload, dict) else build_catalog()
    target = Path(path) if path else Path(__file__).with_name("\u6240\u6709\u6218\u7565\u5e93\u7684\u5206\u7c7b.md")
    meta = data.get("metadata", {})
    labels = {
        "allied":"\u76df\u519b", "soviet":"\u82cf\u519b", "epsilon":"\u5384\u666e\u897f\u9686", "foehn":"\u711a\u98ce", "unknown":"\u672a\u77e5\u9635\u8425",
        "opening":"\u5f00\u5c40", "tier2":"T2\u79d1\u6280", "tier3":"T3\u79d1\u6280", "late":"\u540e\u671f", "sudden_death":"\u51b3\u80dc\u9636\u6bb5",
        "starved":"\u8d44\u6e90\u7d27\u5f20", "stable":"\u7ecf\u6d4e\u7a33\u5b9a", "surplus":"\u7ecf\u6d4e\u76c8\u4f59", "behind":"\u5175\u529b\u843d\u540e", "even":"\u5175\u529b\u76f8\u5f53", "ahead":"\u5175\u529b\u9886\u5148", "open":"\u5f00\u9614\u5730", "chokepoint":"\u72ed\u7a84\u901a\u9053",
        "infantry":"\u6b65\u5175", "vehicle":"\u88c5\u7532\u8f66\u8f86", "aircraft":"\u7a7a\u519b", "building":"\u5efa\u7b51", "defense":"\u9632\u5fa1\u5efa\u7b51", "technology":"\u79d1\u6280\u5efa\u7b51", "superweapon":"\u8d85\u7ea7\u6b66\u5668", "resource":"\u7ecf\u6d4e\u5efa\u7b51", "unclassified":"\u672a\u5206\u7c7b",
    }
    action_labels = {
        "deny_allied_mobility_and_air":"\u9650\u5236\u76df\u519b\u673a\u52a8\u4e0e\u5236\u7a7a", "kite_soviet_armor_and_artillery":"\u98ce\u7b5d\u82cf\u519b\u88c5\u7532\u4e0e\u70ae\u5175", "screen_against_control_and_stealth":"\u9632\u8303\u63a7\u5236\u4e0e\u9690\u5f62", "break_foehn_support_synergy":"\u62c6\u89e3\u711a\u98ce\u652f\u63f4\u534f\u540c", "scout_to_identify_enemy_doctrine":"\u4fa6\u5bdf\u786e\u8ba4\u654c\u65b9\u6253\u6cd5",
        "preserve_opening_economy":"\u4fdd\u62a4\u5f00\u5c40\u7ecf\u6d4e\u5e76\u5b8c\u6210\u5c55\u5f00", "contest_map_and_unlock_counters":"\u4e89\u593a\u5730\u56fe\u5e76\u89e3\u9501\u53cd\u5236", "field_combined_arms_group":"\u7ec4\u5efa\u8054\u5408\u5175\u79cd\u7f16\u961f", "rotate_reconstituted_attack_waves":"\u8f6e\u6362\u91cd\u7ec4\u540e\u7684\u8fdb\u653b\u6ce2\u6b21", "commit_to_decisive_objective":"\u96c6\u4e2d\u529b\u91cf\u5b8c\u6210\u51b3\u5b9a\u6027\u76ee\u6807",
        "use_cost_efficient_counter":"\u4f18\u5148\u4f7f\u7528\u4f4e\u6210\u672c\u6709\u6548\u53cd\u5236", "maintain_counter_ratio":"\u7ef4\u6301\u53cd\u5236\u5175\u529b\u6bd4\u4f8b", "parallel_produce_hard_counters":"\u5229\u7528\u72ec\u7acb\u4ea7\u7ebf\u5e76\u884c\u751f\u4ea7\u786c\u53cd\u5236", "delay_and_reinforce":"\u5ef6\u7f13\u4ea4\u6218\u5e76\u8865\u5145\u9884\u5907\u961f", "probe_then_commit":"\u8bd5\u63a2\u540e\u96c6\u4e2d\u6295\u5165", "press_verified_advantage":"\u786e\u8ba4\u4f18\u52bf\u540e\u6301\u7eed\u65bd\u538b", "use_flanks_and_spacing":"\u5229\u7528\u4fa7\u7ffc\u5e76\u4fdd\u6301\u95f4\u8ddd", "control_choke_with_range_and_area_fire":"\u7528\u5c04\u7a0b\u4e0e\u8303\u56f4\u706b\u529b\u5c01\u9501\u901a\u9053",
    }
    role_words = {"anti_armor":"\u53cd\u88c5\u7532", "heavy_anti_armor":"\u91cd\u578b\u53cd\u88c5\u7532", "area_anti_infantry":"\u8303\u56f4\u53cd\u6b65\u5175", "long_range":"\u8fdc\u7a0b\u706b\u529b", "air_strike":"\u7a7a\u4e2d\u6253\u51fb", "fighter":"\u6218\u6597\u673a", "mobile_anti_air":"\u673a\u52a8\u9632\u7a7a", "static_anti_air":"\u56fa\u5b9a\u9632\u7a7a", "detector":"\u63a2\u6d4b", "disabler":"\u63a7\u5236\u4e0e\u51cf\u901f", "repair_support":"\u4fee\u590d\u652f\u63f4", "combined_arms":"\u8054\u5408\u5175\u79cd", "scout":"\u4fa6\u5bdf", "fast_raider":"\u5feb\u901f\u88ad\u6270", "siege":"\u653b\u57ce\u706b\u529b", "long_range_anti_infantry":"\u8fdc\u7a0b\u53cd\u6b65\u5175", "mobile_suppression":"\u673a\u52a8\u538b\u5236", "snare":"\u9650\u901f\u4e0e\u7275\u5236", "heavy_armor":"\u91cd\u578b\u88c5\u7532", "production":"\u751f\u4ea7\u5efa\u7b51", "power":"\u7535\u529b\u5efa\u7b51", "technology":"\u79d1\u6280\u5efa\u7b51", "resource":"\u7ecf\u6d4e\u5efa\u7b51", "superweapon":"\u8d85\u7ea7\u6b66\u5668"}
    def tr(value: Any) -> str:
        key = str(value or "")
        return labels.get(key, action_labels.get(key, role_words.get(key, "\u7efc\u5408\u6218\u672f")))
    lines = ["# \u6240\u6709\u6218\u7565\u5e93\u7684\u5206\u7c7b", "", "> 本文档与 `combat_doctrine.json` 同源生成；规则只使用可见战场信息，并严格遵守玩家授权。", "", "## 一、规模与检索顺序", "", f"- 战略条目总数：**{meta.get('entry_count', 0)}**。", f"- 战局矩阵条目：**{meta.get('matrix_entry_count', 0)}**。", f"- 真实 TypeID 专属反制：**{meta.get('unit_response_count', 0)}**。", "- 检索顺序：实时可见信息 → 战局矩阵 → 当前最高威胁 TypeID 专属条款 → 动作验证与重新评估。", "", "## 二、阵营分类", "", "| 阵营 | 核心应对 |", "|---|---|"]
    for faction in FACTIONS:
        lines.append(f"| {tr(faction)}（`{faction}`） | {tr(FACTION_ACTION[faction])} |")
    lines += ["", "## 三、威胁原型分类", "", "| 威胁类别 | 反制生产角色 | 优先目标 | 阵型 | 交战方式 |", "|---|---|---|---|---|"]
    for threat, profile in THREATS.items():
        lines.append(f"| {tr(threat)}（`{threat}`） | {'、'.join(tr(x) for x in profile['roles'])} | {'、'.join(tr(x) for x in profile['targets'])} | {tr(profile['formation'])} | {tr(profile['engagement'])} |")
    for title, values, actions in [("四、战局阶段", STAGES, STAGE_ACTION), ("五、经济状态", ECONOMIES, ECONOMY_ACTION), ("六、兵力关系", FORCE_RATIOS, RATIO_ACTION), ("七、地形状态", TERRAINS, TERRAIN_ACTION)]:
        lines += ["", f"## {title}", "", "| 分类 | 执行原则 |", "|---|---|"]
        for key in values:
            lines.append(f"| {tr(key)}（`{key}`） | {tr(actions[key])} |")
    lines += ["", "## 八、真实 TypeID 专属反制", "", "每个 TypeID 均按兵力关系与地形生成六条独立规则；TypeID 和规则 ID 保留用于程序检索。", ""]
    seen: set[str] = set()
    for item in data.get("unit_responses", []):
        type_id = str(item.get("enemy_type_id") or "").upper()
        if not type_id or type_id in seen:
            continue
        seen.add(type_id)
        lines += [f"### {tr(item.get('enemy_role'))}（`{type_id}`）", "", "| 名称 | 科技等级 | 反制生产角色 | 优先目标 | 阵型 | 交战方式 |", "|---|---:|---|---|---|---|", f"| {str(item.get('enemy_name') or type_id).replace('|', '/')} | {item.get('enemy_tech_level', '')} | {'、'.join(tr(x) for x in item.get('production_roles', []))} | {'、'.join(tr(x) for x in item.get('target_priorities', []))} | {tr(item.get('formation'))} | {tr(item.get('engagement'))} |", ""]
    lines += ["## 九、动作验证与权限条款", "", "1. 仅依据当前可见敌军、已验证己方状态和世界模型记忆决策。", "2. 未获得玩家明确授权时，不启动自主发展、支援技能或自主进攻。", "3. 建筑、支援技能、步兵、车辆、空军、海军六条产线独立轮询，单条等待不得冻结其他产线。", "4. 桥接接受不等于游戏完成；必须通过下一帧状态确认实际进度。", "5. 目标死亡、消失或被识别为中立场景物件后，立即使旧命令失效并重新评估。", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


if __name__ == "__main__":
    generated = build_catalog()
    catalog_path = Path(__file__).with_name("combat_doctrine.json")
    catalog_path.write_text(json.dumps(generated, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(catalog_path)
    print(write_classification_md(generated))
