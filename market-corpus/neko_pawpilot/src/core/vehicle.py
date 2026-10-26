"""车辆状态解读：按部位的车损语义、估算修车费、挂车编组。

**真实依据**（不做无根据的推断）：
- ETS2 的车损是**按部位分开记的**（引擎 / 变速箱 / 驾驶室 / 底盘 / 轮胎），
  修理厂也是按部位报价、可以只修其中几项。所以"哪个部位最该修"是有意义的建议。
- 挂车在 SDK 里是 10 槽数组，ETS2 实际最多 3 节（北欧多节挂车就是真实玩法），
  按"真正 attach 的槽位数"判断编组即可。
- 修车费沿用现有估算口径（最大损伤 × 1000 €）按部位占比**分摊**，
  所以是**估算**而不是精确报价，播报时按估算措辞。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# 部位 → 中文
PART_LABELS = {
    "engine": "引擎",
    "transmission": "变速箱",
    "cabin": "驾驶室",
    "chassis": "底盘",
    "wheels": "轮胎",
}

# 挂车编组 → (标签, 驾驶要点)。要点都对应真实的多节挂车难点：
# 内轮差更大、倒车更难、甩尾/折刀风险、变道需要更长空间。
TRAILER_CONFIGS = {
    1: ("单节挂车", ""),
    2: ("双挂车（北欧组合）",
        "两节车厢，转弯内轮差更大、倒车基本要重来，变道要留足空间"),
    3: ("三挂车（超长组合）",
        "三节车厢，任何转向都要提前很多，别做急变道，倒车尽量别试"),
}


def part_report(parts: Optional[Dict[str, Any]],
                total_repair_eur: float = 0.0) -> List[Dict[str, Any]]:
    """按部位给出损伤百分比与分摊后的估算维修费（占比分摊，不是精确报价）。"""
    parts = {k: float(v or 0.0) for k, v in (parts or {}).items() if k in PART_LABELS}
    if not parts:
        return []
    total_wear = sum(parts.values())
    out = []
    for key, wear in parts.items():
        share = (wear / total_wear) if total_wear > 0 else 0.0
        out.append({
            "key": key,
            "label": PART_LABELS[key],
            "pct": round(wear * 100.0, 1),
            "cost": round(float(total_repair_eur or 0.0) * share, 1),
        })
    out.sort(key=lambda e: e["pct"], reverse=True)
    return out


def worst_part(parts: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """损伤最重的部位（0 损伤返回 None）。"""
    report = part_report(parts)
    if not report or report[0]["pct"] <= 0.5:
        return None
    return report[0]


def damage_sentence(parts: Optional[Dict[str, Any]], max_damage: float) -> str:
    """一句话说清"伤在哪、大概多少钱修"。

    金额是**粗估**：遥测里没有修理厂报价，也没有车价，所以沿用插件内部口径
    （最大损伤 × 1000 €）按部位占比分摊——播报时明确说"粗估"，不当精确报价。
    """
    report = part_report(parts, float(max_damage or 0.0) * 1000.0)
    hurt = [e for e in report if e["pct"] >= 1.0]
    if not hurt:
        return ""
    top = "、".join(f"{e['label']} {e['pct']:.0f}%" for e in hurt[:3])
    cost = round(float(max_damage or 0.0) * 1000.0)
    return f"（{top}；粗估修车费约 {cost} €，修理厂可以只修最重的那一项）"


def trailer_config(count: int) -> Tuple[str, str]:
    """挂车编组 → (标签, 驾驶要点)。0 节返回空。"""
    n = int(count or 0)
    if n <= 0:
        return "", ""
    return TRAILER_CONFIGS.get(n, (f"{n} 节编组", "编组很长，转向与变道都要提前"))
