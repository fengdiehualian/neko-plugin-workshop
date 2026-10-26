"""小世界: 开辟/栽种/收获/演化(懒结算养成)。

单人化: 小世界是玩家的私人空间(原版多人生存玩法降维成种田养成),
作物生长用 harvest_time 懒结算。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from .player import PlayerSave

OPEN_COST = 10000          # 开辟灵石
EVOLVE_COST = 20000        # 演化灵石
GROW_SECONDS = 300         # 作物生长周期(秒)
CROPS = ["灵稻", "天麻", "仙竹", "悟道茶"]


class XiaoShiJie:
    """小世界中间件。"""

    def __init__(self, game: Any) -> None:
        self.game = game

    def _state(self, save: PlayerSave) -> Dict[str, Any]:
        return save.extra.setdefault("xiaoshijie", {"opened": False, "level": 1})

    async def open_world(self, save: PlayerSave) -> Dict[str, Any]:
        st = self._state(save)
        if st["opened"]:
            return {"ok": False, "msg": "已有小世界喵"}
        if save.lingshi_total() < OPEN_COST:
            return {"ok": False, "msg": f"灵石不足(开辟需 {OPEN_COST})"}
        save.add_lingshi(-OPEN_COST)
        st["opened"] = True
        st["level"] = 1
        st["last_settle"] = time.time()
        return {"ok": True, "msg": f"开辟小世界成功喵!(花费{OPEN_COST}灵石),试试「小世界栽种 灵稻」"}

    async def status(self, save: PlayerSave) -> Dict[str, Any]:
        st = self._state(save)
        if not st["opened"]:
            return {"opened": False, "msg": "还没有小世界,发「开辟小世界」喵(需一万灵石)"}
        crop = st.get("crop")
        crop_txt = "无"
        if crop:
            remain = int(crop["harvest_time"] - time.time())
            if remain > 0:
                crop_txt = f"{crop['name']} 生长中(剩{remain}s)"
            else:
                crop_txt = f"{crop['name']} 可收获喵!"
        return {"opened": True, "level": st["level"], "crop": crop_txt,
                "msg": f"【小世界】Lv.{st['level']}\n作物: {crop_txt}\n用法: 小世界栽种 X / 收获小世界作物 / 演化小世界"}

    async def plant(self, save: PlayerSave, crop: str) -> Dict[str, Any]:
        st = self._state(save)
        if not st["opened"]:
            return {"ok": False, "msg": "先「开辟小世界」喵"}
        if st.get("crop") and st["crop"]["harvest_time"] > time.time():
            return {"ok": False, "msg": "小世界作物还在生长中喵"}
        if crop not in CROPS:
            return {"ok": False, "msg": f"可种: {'、'.join(CROPS)}"}
        yield_total = 10 * st["level"]
        st["crop"] = {"name": crop, "harvest_time": time.time() + GROW_SECONDS,
                      "yield_total": yield_total}
        return {"ok": True, "msg": f"在小世界种下 {crop} 喵,{GROW_SECONDS//60}分钟后可收获"}

    async def harvest(self, save: PlayerSave) -> Dict[str, Any]:
        st = self._state(save)
        crop = st.get("crop")
        if not crop:
            return {"ok": False, "msg": "小世界还没种作物,先「小世界栽种 灵稻」喵"}
        if crop["harvest_time"] > time.time():
            return {"ok": False, "msg": f"{crop['name']} 还在生长,剩 {int(crop['harvest_time']-time.time())}s 喵"}
        st.pop("crop")
        return {"ok": True, "msg": f"收获 {crop['name']} ×{crop['yield_total']} 喵!",
                "crop": crop["name"], "yield": crop["yield_total"]}

    async def evolve(self, save: PlayerSave) -> Dict[str, Any]:
        st = self._state(save)
        if not st["opened"]:
            return {"ok": False, "msg": "先「开辟小世界」喵"}
        cost = EVOLVE_COST * st["level"]
        if save.lingshi_total() < cost:
            return {"ok": False, "msg": f"灵石不足(演化需 {cost})"}
        save.add_lingshi(-cost)
        st["level"] += 1
        return {"ok": True, "msg": f"小世界演化为 Lv.{st['level']} 喵!(花费{cost}灵石)",
                "level": st["level"], "price": cost}
