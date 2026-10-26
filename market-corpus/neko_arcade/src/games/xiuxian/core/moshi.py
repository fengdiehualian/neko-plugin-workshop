"""魔道/神界: 双修路径(魔石/魔功 + 神石/神界参悟) + 周年庆。

单人化: 魔界/神界是 NPC 生态的修炼捷径, 选择堕魔还是成神由玩家决定。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from .player import PlayerSave

MOSHI_RATE = 1000        # 1000 灵石 → 1 魔石
SHENSHI_RATE = 1000      # 1000 灵石 → 1 神石
MOGONG_EXP = 2000        # 每颗魔石修炼魔功获得的修为
SHENSHI_EXP = 1500       # 每颗神石参悟获得的修为


class MoShen:
    """魔道神界中间件。"""

    def __init__(self, game: Any) -> None:
        self.game = game

    # ── 魔道 ─────────────────────────────

    async def offer_moshi(self, save: PlayerSave, n: int = 1) -> Dict[str, Any]:
        """供奉魔石: 灵石 → 魔石(获得魔性)。"""
        cost = MOSHI_RATE * n
        if save.lingshi_total() < cost:
            return {"ok": False, "msg": f"灵石不足(需 {cost})"}
        save.add_lingshi(-cost)
        st = save.extra.setdefault("moshi", {"count": 0, "modao": 0})
        st["count"] = st.get("count", 0) + n
        st["modao"] = st.get("modao", 0) + n       # 魔性累积
        return {"ok": True, "msg": f"供奉 {n} 颗魔石(花费{cost}灵石),魔性+{n}",
                "count": st["count"], "modao": st["modao"]}

    async def xiu_mogong(self, save: PlayerSave, n: int = 1) -> Dict[str, Any]:
        """修炼魔功: 消耗魔石 → 大量修为(魔性更深)。"""
        st = save.extra.setdefault("moshi", {"count": 0, "modao": 0})
        if st.get("count", 0) < n:
            return {"ok": False, "msg": f"魔石不足(当前{st.get('count', 0)}),先「供奉魔石」喵"}
        st["count"] -= n
        st["modao"] = st.get("modao", 0) + n
        gain = MOGONG_EXP * n
        save.exp += gain
        return {"ok": True, "msg": f"修炼魔功!消耗{n}魔石,修为+{gain}(魔性+{n})",
                "gain": gain, "modao": st["modao"]}

    def mojie_status(self, save: PlayerSave) -> Dict[str, Any]:
        st = save.extra.get("moshi", {"count": 0, "modao": 0})
        return {"count": st.get("count", 0), "modao": st.get("modao", 0),
                "msg": f"【魔界】魔石×{st.get('count', 0)} | 魔性 {st.get('modao', 0)}"}

    # ── 神界 ─────────────────────────────

    async def offer_shenshi(self, save: PlayerSave, n: int = 1) -> Dict[str, Any]:
        """供奉神石: 灵石 → 神石。"""
        cost = SHENSHI_RATE * n
        if save.lingshi_total() < cost:
            return {"ok": False, "msg": f"灵石不足(需 {cost})"}
        save.add_lingshi(-cost)
        st = save.extra.setdefault("shenshi", {"count": 0})
        st["count"] = st.get("count", 0) + n
        return {"ok": True, "msg": f"供奉 {n} 颗神石(花费{cost}灵石)",
                "count": st["count"]}

    async def canwu(self, save: PlayerSave, n: int = 1) -> Dict[str, Any]:
        """参悟神石: 消耗神石 → 修为 + 属性提升。"""
        st = save.extra.setdefault("shenshi", {"count": 0})
        if st.get("count", 0) < n:
            return {"ok": False, "msg": f"神石不足(当前{st.get('count', 0)}),先「供奉神石」喵"}
        st["count"] -= n
        gain = SHENSHI_EXP * n
        save.exp += gain
        save.attack += 5 * n
        save.max_hp += 50 * n
        save.hp = min(save.hp + 50 * n, save.max_hp)
        return {"ok": True, "msg": f"参悟神石!消耗{n}神石,修为+{gain},攻击+{5*n},气血上限+{50*n}",
                "gain": gain}

    def shenjie_status(self, save: PlayerSave) -> Dict[str, Any]:
        st = save.extra.get("shenshi", {"count": 0})
        return {"count": st.get("count", 0),
                "msg": f"【神界】神石×{st.get('count', 0)}"}

    # ── 周年庆 ───────────────────────────

    async def anniversary_sign(self, save: PlayerSave) -> Dict[str, Any]:
        """周年签到: 每天一次, 领灵石+修为。"""
        today = time.strftime("%Y-%m-%d")
        st = save.extra.setdefault("anniversary", {"last": ""})
        if st.get("last") == today:
            return {"ok": False, "msg": "今日周年签到已领过喵,明天再来"}
        st["last"] = today
        st["days"] = st.get("days", 0) + 1
        lingshi = 500 + st["days"] * 50
        exp = 1000 + st["days"] * 100
        save.add_lingshi(lingshi)
        save.exp += exp
        return {"ok": True, "msg": f"周年签到第{st['days']}天!灵石+{lingshi},修为+{exp}",
                "days": st["days"], "lingshi": lingshi, "exp": exp}
