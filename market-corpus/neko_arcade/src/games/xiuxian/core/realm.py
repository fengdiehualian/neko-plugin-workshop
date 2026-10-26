"""境界系统：练气/炼体双轨境界，数据来自 data/levels/*.json(独立数据体系)。

体系(8 套,均存于 data/levels/)：
- 练气境界.json     64 境(气修主轨)
- 炼体境界.json     63 境(体修副轨)
- 真实虚幻境界.json  64 境
- 元神境界.json     15 境(元神轨)
- 秘境体系.json     24 境
- 神慧体系.json     10 境
- 仙古今世法.json   24 境
- 神魔修炼法.json    4 境

数据字段：{level, exp, level_id, 基础攻击, 基础防御, 基础血量, 基础暴击}
- exp = 突破离开本境界所需的修为
- 属性为绝对值(取当前境界的基础属性)
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List

from ....core.datafile import read_json

_LEVELS_DIR = Path(__file__).resolve().parent.parent / "data" / "levels"


def _load(name: str) -> List[Dict[str, Any]]:
    # 优先读 <name>.gz（包里只发布压缩版，见 core/datafile.py）
    data = read_json(_LEVELS_DIR / name, [])
    return data if isinstance(data, list) else []


REALMS: List[Dict[str, Any]] = _load("练气境界.json")
BODY_REALMS: List[Dict[str, Any]] = _load("炼体境界.json")
AUX_REALMS: Dict[str, List[Dict[str, Any]]] = {
    "真实虚幻": _load("真实虚幻境界.json"),
    "元神": _load("元神境界.json"),
    "秘境": _load("秘境体系.json"),
    "神慧": _load("神慧体系.json"),
    "仙古今世": _load("仙古今世法.json"),
    "神魔": _load("神魔修炼法.json"),
}

# 数据文件缺失时的兜底(避免游戏崩溃,仅作占位)
_FALLBACK_REALMS = [
    {"level": "凡人", "exp": 500, "level_id": 1,
     "基础攻击": 2000, "基础防御": 2000, "基础血量": 4000, "基础暴击": 0.01},
    {"level": "虚妄境初期", "exp": 5000, "level_id": 2,
     "基础攻击": 5000, "基础防御": 5000, "基础血量": 10000, "基础暴击": 0.05},
]
_FALLBACK_BODY = [
    {"level": "凡人", "exp": 500, "level_id": 1,
     "基础攻击": 200, "基础防御": 4000, "基础血量": 4000, "基础暴击": 0.01},
]

if not REALMS:
    REALMS = _FALLBACK_REALMS
if not BODY_REALMS:
    BODY_REALMS = _FALLBACK_BODY

MAX_REALM = len(REALMS) - 1
MAX_BODY = len(BODY_REALMS) - 1

_ATK = "基础攻击"
_DFN = "基础防御"
_HP = "基础血量"
_CRIT = "基础暴击"


class RealmSystem:
    """境界查询与突破计算(练气 + 炼体双轨)。"""

    # ── 查询 ──────────────────────────────

    @staticmethod
    def realm_name(idx: int) -> str:
        idx = max(0, min(idx, MAX_REALM))
        return str(REALMS[idx].get("level", "未知境界"))

    @staticmethod
    def body_name(idx: int) -> str:
        idx = max(0, min(idx, MAX_BODY))
        return str(BODY_REALMS[idx].get("level", "未知炼体"))

    @staticmethod
    def realm_require(idx: int) -> int:
        """突破离开第 idx 境界所需的修为(取该境界的 exp)。"""
        idx = max(0, min(idx, MAX_REALM))
        return int(REALMS[idx].get("exp", 0))

    @staticmethod
    def body_require(idx: int) -> int:
        idx = max(0, min(idx, MAX_BODY))
        return int(BODY_REALMS[idx].get("exp", 0))

    @staticmethod
    def realm_stats(idx: int) -> Dict[str, Any]:
        idx = max(0, min(idx, MAX_REALM))
        e = REALMS[idx]
        return {
            "max_hp": int(e.get(_HP, 0)),
            "attack": int(e.get(_ATK, 0)),
            "defense": int(e.get(_DFN, 0)),
            "max_mana": 0,
            "crit_rate": float(e.get(_CRIT, 0)),
        }

    @staticmethod
    def body_stats(idx: int) -> Dict[str, Any]:
        idx = max(0, min(idx, MAX_BODY))
        e = BODY_REALMS[idx]
        return {
            "max_hp": int(e.get(_HP, 0)),
            "attack": int(e.get(_ATK, 0)),
            "defense": int(e.get(_DFN, 0)),
            "crit_rate": float(e.get(_CRIT, 0)),
        }

    # ── 突破 ──────────────────────────────

    @staticmethod
    def try_breakthrough(current_idx: int, exp: int,
                         rate: float = 0.8, neko_assist: bool = False,
                         assist_bonus: float = 0.05,
                         fail_penalty_ratio: float = 0.1) -> Dict[str, Any]:
        """练气突破。返回 {success, new_idx, exp_lost, msg}。"""
        if current_idx >= MAX_REALM:
            return {"success": False, "new_idx": current_idx,
                    "exp_lost": 0, "msg": "已达最高境界"}
        need = RealmSystem.realm_require(current_idx)
        if exp < need:
            return {"success": False, "new_idx": current_idx,
                    "exp_lost": 0,
                    "msg": f"修为不足(需要 {need},当前 {exp})"}
        rate = min(0.98, rate + (assist_bonus if neko_assist else 0))
        if random.random() < rate:
            return {"success": True, "new_idx": current_idx + 1,
                    "exp_lost": need,
                    "msg": f"突破成功！晋升为{RealmSystem.realm_name(current_idx + 1)}"}
        lost = int(need * fail_penalty_ratio)
        return {"success": False, "new_idx": current_idx,
                "exp_lost": lost,
                "msg": f"突破失败,损失 {lost} 修为"}

    @staticmethod
    def try_body_breakthrough(current_idx: int, exp: int,
                              rate: float = 0.8,
                              fail_penalty_ratio: float = 0.1) -> Dict[str, Any]:
        """炼体突破。"""
        if current_idx >= MAX_BODY:
            return {"success": False, "new_idx": current_idx,
                    "exp_lost": 0, "msg": "已达最高炼体境界"}
        need = RealmSystem.body_require(current_idx)
        if exp < need:
            return {"success": False, "new_idx": current_idx,
                    "exp_lost": 0,
                    "msg": f"炼体修为不足(需要 {need},当前 {exp})"}
        if random.random() < rate:
            return {"success": True, "new_idx": current_idx + 1,
                    "exp_lost": need,
                    "msg": f"炼体突破成功！晋升为{RealmSystem.body_name(current_idx + 1)}"}
        lost = int(need * fail_penalty_ratio)
        return {"success": False, "new_idx": current_idx,
                "exp_lost": lost,
                "msg": f"炼体突破失败,损失 {lost} 修为"}
