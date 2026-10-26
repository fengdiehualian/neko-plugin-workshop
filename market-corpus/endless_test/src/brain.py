# -*- coding: utf-8 -*-
"""无尽测试 · AI 伙伴大脑

只放“数据”：8 个 AI 职业的元信息，以及每个职业的 8 级科技树。

所有决策（选职业、姿态、集火、买科技、放不放大招）都由 Neko 通过
llm_tool 完成，这里的函数不参与决策，只提供选项和数值。
"""

from __future__ import annotations

from typing import Any, Dict, List

# ---------------------------------------------------------------- 职业

CLASSES: Dict[str, Dict[str, Any]] = {
    "blade": {
        "name": "刃系", "color": "#8ef5ff", "role": "近战连段", "range": 96,
        "tag": "贴身压制，用连斩和招架打失衡",
        "prefer": ["engage", "focus"],
        "counters": {"cannon": True, "field": True, "link": True, "phase": True},
    },
    "cannon": {
        "name": "炮击系", "color": "#ffd479", "role": "远程爆炸", "range": 520,
        "tag": "站在后排把成群的怪一起炸掉",
        "prefer": ["follow", "focus"],
        "counters": {"blade": True, "field": False, "aegis": True, "blight": True},
    },
    "field": {
        "name": "场控系", "color": "#b48cff", "role": "减速聚怪", "range": 430,
        "tag": "把敌人拉到一起，方便你收人头",
        "prefer": ["follow", "guard"],
        "counters": {"blade": True, "vector": True, "link": True, "phase": True},
    },
    "link": {
        "name": "链接系", "color": "#7df5c0", "role": "链电召唤", "range": 400,
        "tag": "链电跳怪、无人机自动补刀，最省心",
        "prefer": ["follow", "engage"],
        "counters": {"cannon": True, "blight": True, "aegis": True, "field": True},
    },
    "aegis": {
        "name": "重装系", "color": "#d8e4f0", "role": "护盾反击", "range": 120,
        "tag": "挡在你前面吃伤害，还能给你套盾",
        "prefer": ["guard", "engage"],
        "counters": {"blade": True, "cannon": True, "vector": True, "blight": True},
    },
    "vector": {
        "name": "游击系", "color": "#ff9ec4", "role": "位移爆发", "range": 210,
        "tag": "高速切入切出，专挑落单的精英",
        "prefer": ["engage", "focus"],
        "counters": {"cannon": True, "field": True, "link": True, "blight": True},
    },
    "blight": {
        "name": "蚀刻系", "color": "#a6e05f", "role": "持续腐蚀", "range": 380,
        "tag": "毒池铺场，boss 越打越疼",
        "prefer": ["follow", "focus"],
        "counters": {"aegis": True, "blade": True, "field": True, "cannon": True},
    },
    "phase": {
        "name": "相位系", "color": "#7aa8ff", "role": "穿透瞬移", "range": 460,
        "tag": "穿透光束清线，闪现救场",
        "prefer": ["engage", "follow"],
        "counters": {"blade": True, "cannon": True, "vector": True, "link": True},
    },
}

CLASS_ORDER = ["blade", "cannon", "field", "link", "aegis", "vector", "blight", "phase"]


# ---------------------------------------------------------------- 科技树
# 每条科技都是纯数值，游戏端读 stat 直接作用在 AI 身上。
# cost 用“研究点”，AI 自己打怪 / 随时间攒，不跟玩家抢数据与核心。

_TECHS: Dict[str, List[Dict[str, Any]]] = {
    "blade": [
        {"name": "刃脊", "desc": "近战伤害 +12%", "cost": 1, "stat": {"damage": 1.12}},
        {"name": "回斩", "desc": "攻速 +10%", "cost": 1, "stat": {"rate": 0.90}},
        {"name": "轻步", "desc": "移速 +8%", "cost": 1, "stat": {"speed": 1.08}},
        {"name": "淬锋", "desc": "暴击 +8%", "cost": 2, "stat": {"crit": 0.08}},
        {"name": "吸血刃", "desc": "命中回复生命", "cost": 2, "stat": {"lifesteal": 0.06}},
        {"name": "破甲", "desc": "伤害 +10%，攻速 +6%", "cost": 3, "stat": {"damage": 1.10, "rate": 0.94}},
        {"name": "千刃预备", "desc": "终技范围 +20%", "cost": 3, "stat": {"aoe": 1.20}},
        {"name": "不灭锋刃", "desc": "生命 +25，伤害 +8%", "cost": 4, "stat": {"maxHp": 25, "damage": 1.08}},
    ],
    "cannon": [
        {"name": "装药", "desc": "爆炸伤害 +12%", "cost": 1, "stat": {"damage": 1.12}},
        {"name": "快装", "desc": "射速 +10%", "cost": 1, "stat": {"rate": 0.90}},
        {"name": "爆破半径", "desc": "爆炸范围 +15%", "cost": 1, "stat": {"aoe": 1.15}},
        {"name": "破片", "desc": "暴击 +8%", "cost": 2, "stat": {"crit": 0.08}},
        {"name": "稳固炮架", "desc": "生命 +20，移速 +6%", "cost": 2, "stat": {"maxHp": 20, "speed": 1.06}},
        {"name": "穿甲弹", "desc": "穿透 +1", "cost": 3, "stat": {"pierce": 1}},
        {"name": "重型弹头", "desc": "伤害 +18%", "cost": 3, "stat": {"damage": 1.18}},
        {"name": "轨道校射", "desc": "终技冷却 -20%", "cost": 4, "stat": {"cd": 0.80}},
    ],
    "field": [
        {"name": "引力锚", "desc": "力场范围 +12%", "cost": 1, "stat": {"aoe": 1.12}},
        {"name": "压缩场", "desc": "伤害 +10%", "cost": 1, "stat": {"damage": 1.10}},
        {"name": "稳定场", "desc": "技能冷却 -10%", "cost": 1, "stat": {"cd": 0.90}},
        {"name": "滞缓", "desc": "减速更强，伤害 +6%", "cost": 2, "stat": {"damage": 1.06, "aoe": 1.08}},
        {"name": "场域护体", "desc": "生命 +20", "cost": 2, "stat": {"maxHp": 20}},
        {"name": "奇点", "desc": "伤害 +16%", "cost": 3, "stat": {"damage": 1.16}},
        {"name": "引力井", "desc": "终技范围 +20%", "cost": 3, "stat": {"aoe": 1.20}},
        {"name": "事件视界", "desc": "冷却 -18%，伤害 +8%", "cost": 4, "stat": {"cd": 0.82, "damage": 1.08}},
    ],
    "link": [
        {"name": "导电", "desc": "链电伤害 +12%", "cost": 1, "stat": {"damage": 1.12}},
        {"name": "跳数", "desc": "攻速 +10%", "cost": 1, "stat": {"rate": 0.90}},
        {"name": "备用机", "desc": "无人机伤害 +10%", "cost": 1, "stat": {"drone": 1.10}},
        {"name": "继电器", "desc": "技能冷却 -12%", "cost": 2, "stat": {"cd": 0.88}},
        {"name": "绝缘层", "desc": "生命 +20", "cost": 2, "stat": {"maxHp": 20}},
        {"name": "过载", "desc": "伤害 +16%", "cost": 3, "stat": {"damage": 1.16}},
        {"name": "蜂群", "desc": "无人机伤害 +25%", "cost": 3, "stat": {"drone": 1.25}},
        {"name": "天网", "desc": "终技冷却 -20%", "cost": 4, "stat": {"cd": 0.80}},
    ],
    "aegis": [
        {"name": "加厚装甲", "desc": "生命 +30", "cost": 1, "stat": {"maxHp": 30}},
        {"name": "盾击", "desc": "伤害 +10%", "cost": 1, "stat": {"damage": 1.10}},
        {"name": "缓冲", "desc": "移速 +6%，生命 +15", "cost": 1, "stat": {"speed": 1.06, "maxHp": 15}},
        {"name": "嘲讽", "desc": "守护范围 +20%", "cost": 2, "stat": {"aoe": 1.20}},
        {"name": "反击", "desc": "暴击 +8%", "cost": 2, "stat": {"crit": 0.08}},
        {"name": "堡垒", "desc": "生命 +40", "cost": 3, "stat": {"maxHp": 40}},
        {"name": "盾墙", "desc": "护盾强度 +25%", "cost": 3, "stat": {"shield": 1.25}},
        {"name": "壁垒专精", "desc": "终技冷却 -20%", "cost": 4, "stat": {"cd": 0.80}},
    ],
    "vector": [
        {"name": "轻量化", "desc": "移速 +10%", "cost": 1, "stat": {"speed": 1.10}},
        {"name": "脉冲刃", "desc": "伤害 +10%", "cost": 1, "stat": {"damage": 1.10}},
        {"name": "短闪现", "desc": "位移冷却 -12%", "cost": 1, "stat": {"cd": 0.88}},
        {"name": "背刺", "desc": "暴击 +10%", "cost": 2, "stat": {"crit": 0.10}},
        {"name": "残影", "desc": "攻速 +12%", "cost": 2, "stat": {"rate": 0.88}},
        {"name": "高速切割", "desc": "伤害 +16%", "cost": 3, "stat": {"damage": 1.16}},
        {"name": "影袭", "desc": "移速 +10%，暴击 +4%", "cost": 3, "stat": {"speed": 1.10, "crit": 0.04}},
        {"name": "千影", "desc": "终技冷却 -20%", "cost": 4, "stat": {"cd": 0.80}},
    ],
    "blight": [
        {"name": "腐蚀液", "desc": "持续伤害 +12%", "cost": 1, "stat": {"damage": 1.12}},
        {"name": "扩散", "desc": "毒池范围 +15%", "cost": 1, "stat": {"aoe": 1.15}},
        {"name": "耐毒", "desc": "生命 +20", "cost": 1, "stat": {"maxHp": 20}},
        {"name": "渗透", "desc": "伤害 +10%，攻速 +6%", "cost": 2, "stat": {"damage": 1.10, "rate": 0.94}},
        {"name": "孢子云", "desc": "毒池持续时间 +25%", "cost": 2, "stat": {"duration": 1.25}},
        {"name": "瘟疫", "desc": "伤害 +16%", "cost": 3, "stat": {"damage": 1.16}},
        {"name": "蔓延", "desc": "范围 +20%", "cost": 3, "stat": {"aoe": 1.20}},
        {"name": "疫潮", "desc": "终技冷却 -20%", "cost": 4, "stat": {"cd": 0.80}},
    ],
    "phase": [
        {"name": "折射", "desc": "伤害 +12%", "cost": 1, "stat": {"damage": 1.12}},
        {"name": "聚束", "desc": "穿透 +1", "cost": 1, "stat": {"pierce": 1}},
        {"name": "短距跃迁", "desc": "传送冷却 -12%", "cost": 1, "stat": {"cd": 0.88}},
        {"name": "相位校准", "desc": "暴击 +8%", "cost": 2, "stat": {"crit": 0.08}},
        {"name": "扩散束", "desc": "范围 +12%", "cost": 2, "stat": {"aoe": 1.12}},
        {"name": "过载光束", "desc": "伤害 +18%", "cost": 3, "stat": {"damage": 1.18}},
        {"name": "时空折叠", "desc": "攻速 +12%", "cost": 3, "stat": {"rate": 0.88}},
        {"name": "时间停滞", "desc": "终技冷却 -22%", "cost": 4, "stat": {"cd": 0.78}},
    ],
}


def tech_tree(class_id: str) -> List[Dict[str, Any]]:
    rows = _TECHS.get(class_id, _TECHS["link"])
    return [
        {"id": f"{class_id}_t{i}", "name": r["name"], "desc": r["desc"], "cost": r["cost"],
         "stat": dict(r["stat"])}
        for i, r in enumerate(rows)
    ]
