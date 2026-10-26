"""完整合成/加工配方表(原版移植) + 炼制服务。

- 合成列表.json(87 条): {name, class, amount, materials: [{name, class, amount}]}
  消耗全部材料 → 产出 1 份(丹药/装备/道具/材料 等)。
- 加工列表.json(14 条): {name, 等级, inputs: [{name, class, amount, const_amount}],
  outputs: [{name, class, amount}]}
  const_amount>0 的输入不消耗(如工具/炉子), 其余消耗 → 产出 outputs。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ....core.datafile import read_json
from .player import PlayerSave

_ITEMS_DIR = Path(__file__).resolve().parent.parent / "data" / "items"


def _load_list(name: str) -> List[Dict[str, Any]]:
    # 优先读 <name>.gz（包里只发布压缩版，见 core/datafile.py）
    data = read_json(_ITEMS_DIR / name, [])
    return data if isinstance(data, list) else []


class RecipeCatalog:
    """配方目录。"""

    def __init__(self) -> None:
        self.combine: List[Dict[str, Any]] = _load_list("合成列表.json")
        self.process: List[Dict[str, Any]] = _load_list("加工列表.json")

    def find_combine(self, name: str) -> Optional[Dict[str, Any]]:
        name = (name or "").strip()
        for r in self.combine:
            if r.get("name") == name or name in str(r.get("name", "")):
                return r
        return None

    def find_process(self, name: str) -> Optional[Dict[str, Any]]:
        name = (name or "").strip()
        for r in self.process:
            if r.get("name") == name or name in str(r.get("name", "")):
                return r
        return None

    def list_combine(self, cls: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        rows = self.combine if not cls else [r for r in self.combine if r.get("class") == cls]
        return rows[:limit]

    def describe(self, recipe: Dict[str, Any]) -> str:
        mats = "、".join(f"{m['name']}x{m.get('amount', 1)}" for m in recipe.get("materials", []))
        return f"{recipe.get('name')}({recipe.get('class', '')}) ← {mats}"


class CraftService:
    """合成/加工中间件。"""

    def __init__(self, game: Any) -> None:
        self.game = game
        self.recipes = RecipeCatalog()

    def can_craft(self, save: PlayerSave, recipe: Dict[str, Any]) -> Dict[str, Any]:
        """检查材料是否足够(合成列表)。"""
        for m in recipe.get("materials", []):
            need = int(m.get("amount", 1))
            have = save.bag.get(m["name"], 0)
            if have < need:
                return {"ok": False, "msg": f"材料不足: {m['name']}(需{need},有{have})"}
        return {"ok": True}

    async def combine(self, save: PlayerSave, name: str) -> Dict[str, Any]:
        """按合成配方炼制。"""
        recipe = self.recipes.find_combine(name)
        if recipe is None:
            return {"ok": False, "msg": f"没有「{name}」的合成配方"}
        check = self.can_craft(save, recipe)
        if not check["ok"]:
            return {"ok": False, "msg": check["msg"]}
        # 消耗材料
        for m in recipe.get("materials", []):
            need = int(m.get("amount", 1))
            save.bag[m["name"]] -= need
            if save.bag[m["name"]] <= 0:
                del save.bag[m["name"]]
        # 产出
        out_name = recipe.get("name", name)
        out_count = int(recipe.get("amount", 1))
        save.bag[out_name] = save.bag.get(out_name, 0) + out_count
        return {"ok": True, "msg": f"合成成功!获得 {out_name} ×{out_count}",
                "item": out_name, "count": out_count, "cls": recipe.get("class", "")}

    async def process(self, save: PlayerSave, name: str) -> Dict[str, Any]:
        """加工(消耗 inputs 中的非工具项, 产出 outputs)。"""
        recipe = self.recipes.find_process(name)
        if recipe is None:
            return {"ok": False, "msg": f"没有「{name}」的加工配方"}
        # 检查输入
        for inp in recipe.get("inputs", []):
            if int(inp.get("const_amount", 0)) > 0:
                continue  # 工具/设施不消耗
            need = int(inp.get("amount", 1))
            have = save.bag.get(inp["name"], 0)
            if have < need:
                return {"ok": False, "msg": f"材料不足: {inp['name']}(需{need},有{have})"}
        # 消耗
        for inp in recipe.get("inputs", []):
            if int(inp.get("const_amount", 0)) > 0:
                continue
            need = int(inp.get("amount", 1))
            save.bag[inp["name"]] -= need
            if save.bag[inp["name"]] <= 0:
                del save.bag[inp["name"]]
        # 产出
        lines = []
        for out in recipe.get("outputs", [{"name": recipe.get("name", name), "class": "", "amount": 1}]):
            o_count = int(out.get("amount", 1))
            save.bag[out["name"]] = save.bag.get(out["name"], 0) + o_count
            lines.append(f"{out['name']} ×{o_count}")
        return {"ok": True, "msg": f"加工成功!获得 {'、'.join(lines)}",
                "item": "、".join(lines), "count": len(lines)}
