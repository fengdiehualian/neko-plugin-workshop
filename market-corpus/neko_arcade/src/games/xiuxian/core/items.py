"""物品目录：从 data/items.json 加载(独立数据体系)。

分类表(按 class 字段)：
- 道具 / 丹药 / 草药 / 材料 / 装备 / 功法
字段: {id, name, class, type, 售价, 稀有度, desc, ...效果字段(xueqi/HP/atk/def/bao)}
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List, Optional

from ....core.datafile import read_json

_ITEMS_DIR = Path(__file__).resolve().parent.parent / "data" / "items"

# 文件名 → 分类
_SOURCES = [
    ("道具列表.json", "道具"),
    ("丹药列表.json", "丹药"),
    ("草药列表.json", "草药"),
    ("材料列表.json", "材料"),
    ("装备列表.json", "装备"),
    ("功法列表.json", "功法"),
    ("仙宠口粮列表.json", "仙宠口粮"),
]


def _load_list(name: str) -> List[Dict[str, Any]]:
    # 优先读 <name>.gz（包里只发布压缩版，见 core/datafile.py）
    data = read_json(_ITEMS_DIR / name, [])
    return data if isinstance(data, list) else []


class ItemCatalog:
    """物品目录：按名称检索，提供买卖价格。"""

    def __init__(self) -> None:
        self.items: Dict[str, Dict[str, Any]] = {}
        self.by_class: Dict[str, List[Dict[str, Any]]] = {}
        for fname, cls in _SOURCES:
            for it in _load_list(fname):
                name = str(it.get("name", "")).strip()
                if not name:
                    continue
                it.setdefault("class", cls)
                it.setdefault("售价", 0)
                self.items[name] = it
                self.by_class.setdefault(it.get("class", cls), []).append(it)
        # 拍卖行列表(独立, 走 NPC 拍卖)
        self.auction_pool: List[Dict[str, Any]] = _load_list("星阁拍卖行列表.json")

    # ── 检索 ─────────────────────────────

    def search(self, name: str) -> Optional[Dict[str, Any]]:
        """按名称精确/模糊查找物品。"""
        name = (name or "").strip()
        if not name:
            return None
        if name in self.items:
            return self.items[name]
        for n, it in self.items.items():
            if name in n or n in name:
                return it
        return None

    def list_class(self, cls: str, limit: int = 20) -> List[Dict[str, Any]]:
        return self.by_class.get(cls, [])[:limit]

    def buy_price(self, item: Dict[str, Any]) -> int:
        return max(1, int(item.get("售价", 100)))

    def sell_price(self, item: Dict[str, Any]) -> int:
        return max(1, int(item.get("售价", 100)) // 2)

    # ── 随机抽取(采集/炼丹/炼器用) ────────

    def random_of_class(self, cls: str) -> Optional[Dict[str, Any]]:
        pool = self.by_class.get(cls, [])
        return random.choice(pool) if pool else None

    def describe(self, item: Dict[str, Any]) -> str:
        parts = [item.get("name", "?")]
        for k in ("type", "class"):
            v = item.get(k)
            if v:
                parts.append(v)
        return "·".join(parts)
