"""NPC 市场：商店(买/卖) + 拍卖行(NPC 自动出价)。

单人化：买卖双方都是 NPC(市场 NPC 收购/供货, 星阁拍卖行自动出价)。
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional

from .items import ItemCatalog
from .player import PlayerSave


class MarketManager:
    """市场中间件。"""

    AUCTION_NPC_CEIL_RATIO = 1.5   # NPC 最高出到底价 1.5 倍
    AUCTION_MIN_BID_RATIO = 1.1    # 最低出价 = 底价 1.1 倍

    def __init__(self, game: Any, catalog: ItemCatalog) -> None:
        self.game = game
        self.catalog = catalog
        self._auction: Optional[Dict[str, Any]] = None

    # ── 商店 ─────────────────────────────

    def shop_goods(self, limit: int = 6) -> List[Dict[str, Any]]:
        """商店推荐(各分类抽点)。"""
        goods: List[Dict[str, Any]] = []
        for cls in ("丹药", "材料", "装备", "道具"):
            items = self.catalog.list_class(cls, limit=4)
            goods.extend(random.sample(items, min(2, len(items))))
        return goods[:limit]

    async def buy(self, save: PlayerSave, item: Dict[str, Any],
                  count: int = 1, price: Optional[int] = None) -> Dict[str, Any]:
        """从市场 NPC 购买。price 可覆盖(用于定情物等特殊定价)。"""
        if price is None:
            price = self.catalog.buy_price(item) * count
        else:
            price = price * count
        if save.lingshi_total() < price:
            return {"ok": False, "msg": f"灵石不足(需要 {price},当前 {save.lingshi_total()})"}
        save.add_lingshi(-price)
        save.bag[item["name"]] = save.bag.get(item["name"], 0) + count
        return {"ok": True, "msg": f"购得 {item['name']} ×{count}(花费 {price} 灵石)",
                "item": item["name"], "count": count, "price": price}

    async def sell(self, save: PlayerSave, item: Dict[str, Any],
                   count: int = 1) -> Dict[str, Any]:
        """卖给市场 NPC。"""
        cur = save.bag.get(item["name"], 0)
        if cur < count:
            return {"ok": False, "msg": f"背包里没有 {count} 个{item['name']}"}
        price = self.catalog.sell_price(item) * count
        save.bag[item["name"]] = cur - count
        if save.bag[item["name"]] <= 0:
            del save.bag[item["name"]]
        save.add_lingshi(price)
        return {"ok": True, "msg": f"出售 {item['name']} ×{count}(获得 {price} 灵石)",
                "item": item["name"], "count": count, "price": price}

    # ── 拍卖行(NPC 星阁) ─────────────────

    def open_auction(self) -> Dict[str, Any]:
        """星阁开拍一件物品。"""
        pool = self.catalog.auction_pool
        item = random.choice(pool) if pool else None
        if item is None:
            return {"ok": False, "msg": "拍卖行暂无可拍物品"}
        base = max(100, self.catalog.sell_price(item))
        self._auction = {
            "item": item, "base": base, "npc_bid": base,
            "bidder": "星阁", "opened": time.time(),
        }
        return {"ok": True, "auction": self._auction}

    async def bid(self, save: PlayerSave, amount: int) -> Dict[str, Any]:
        """玩家出价；压过 NPC 上限即成交，否则 NPC 反超。"""
        a = self._auction
        if not a:
            return {"ok": False, "msg": "当前没有拍卖品,发「拍卖」开拍"}
        base = a["base"]
        min_bid = int(base * self.AUCTION_MIN_BID_RATIO)
        if amount < min_bid:
            return {"ok": False, "msg": f"出价过低(底价 {base},至少 {min_bid})"}
        npc_ceil = int(base * self.AUCTION_NPC_CEIL_RATIO)
        if amount > npc_ceil:
            if save.lingshi_total() < amount:
                return {"ok": False, "msg": f"灵石不足(需要 {amount})"}
            save.add_lingshi(-amount)
            save.bag[a["item"]["name"]] = save.bag.get(a["item"]["name"], 0) + 1
            self._auction = None
            return {"ok": True, "won": True,
                    "msg": f"成交喵!以 {amount} 灵石拍得 {a['item']['name']}",
                    "item": a["item"]["name"], "price": amount}
        a["npc_bid"] = int(amount * 1.1)
        return {"ok": True, "won": False,
                "msg": f"星阁出价 {a['npc_bid']} 灵石,继续竞价可出更高价"}
