"""商店：货架开关行为 + 统一评分池（规格 docs/shop_scoring.md）。

口径变更说明（2026-10-07 玩家原话）：「商店好像很喜欢买遗物？这里最好评估一下那些是真正需要的。
如果没有需要的就留到下一个商店」「还可以买药，买两瓶药可以冲精英」「买评分最高的，都不高就不买」。

于是本文件里三条老测试（"遗物无条件优先 / 只剩药水就买药水 / 没得买就删牌"）**已按新口径改写**：
采购不再按类别硬排（遗物 → 高价值卡 → 药水 → 删牌 → 其他卡），而是卡/遗物/药水/删牌进同一个池子
比总分、低于门槛一分不花。老的"只看买得起"（`_preferred_shop_relic_index`）正是玩家抱怨的那个行为，
它的期望值不再是正确期望 —— 新口径来自玩家原话，优先级更高。
"""
from __future__ import annotations

from typing import Any

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner


def _card(index: int, name: str = "卡", dtype: str = "attack", desc: str = "damage") -> dict[str, Any]:
    return {
        "index": index,
        "card_id": name,
        "name": name,
        "card_type": dtype,
        "description": desc,
        "rules_text": desc,
        "price": 50,
        "is_stocked": True,
        "enough_gold": True,
    }


def _ctx(state: str, shop: dict[str, Any], avail: list[str], run: dict[str, Any] | None = None) -> dict[str, Any]:
    # payload 的形状与 `_build_shop_context` 一致：既有一份完整的 `shop`，也有铺平的
    # shop_cards / shop_relics / shop_potions（这样删牌服务的 `shop.card_removal` 才取得到）。
    payload = dict(shop)
    payload["shop"] = dict(shop)
    return {
        "classification": {"state_name": state},
        "summary_context": {"payload": payload},
        "snapshot": {
            "screen": state,
            "available_actions": [{"type": a, "name": a} for a in avail],
            "raw_state": {"run": run or {}},
        },
        "strategy_context": {"preferences": {}},
    }


def _junk_deck() -> list[dict[str, Any]]:
    """满是未升级打击/防御 + 一张诅咒的牌组（删牌缺口高）。"""
    cards = [
        {"index": i, "card_id": "STRIKE_IRONCLAD", "name": "Strike", "card_type": "Attack", "energy_cost": 1,
         "dynamic_values": [{"name": "Damage", "current_value": 6}]}
        for i in range(5)
    ]
    cards += [
        {"index": 5 + i, "card_id": "DEFEND_IRONCLAD", "name": "Defend", "card_type": "Skill", "energy_cost": 1,
         "dynamic_values": [{"name": "Block", "current_value": 5}]}
        for i in range(4)
    ]
    cards.append({"index": 9, "card_id": "ASCENDERS_BANE", "name": "Ascender's Bane", "card_type": "Curse", "energy_cost": -1})
    return cards



def test_shop_opens_inventory_when_closed() -> None:
    op = STS2HeuristicPlanner().plan(_ctx("shop", {}, ["open_shop_inventory"]))
    assert op is not None and op.action_type == "open_shop_inventory"


def test_shop_does_not_buy_a_relic_just_because_it_is_affordable() -> None:
    """**旧口径的回归测试（已按新口径改写）。**

    老实现：`_preferred_shop_relic_index` 只判断"买得起"，于是商店**永远先买遗物**，
    reason 却写着 `shop_high_value_relic` —— 玩家抱怨的就是这条（"商店好像很喜欢买遗物？"）。
    新口径：遗物平平（没有补上任何缺口）就不买，钱留给下一个商店 / 留给药水。
    """
    planner = STS2HeuristicPlanner()
    shop = {
        "shop_relics": [{"index": 0, "name": "小石头", "rarity": "Common", "price": 150, "is_stocked": True, "enough_gold": True}],
        "shop_cards": [_card(0)],
        "shop_potions": [{"index": 0, "name": "格挡药水", "potion_id": "BLOCK_POTION", "price": 60, "is_stocked": True, "enough_gold": True}],
    }
    run = {"gold": 200, "current_hp": 70, "max_hp": 80, "floor": 6, "deck": _junk_deck(), "potions": [
        {"index": 0, "potion_id": None, "occupied": False},
        {"index": 1, "potion_id": None, "occupied": False},
    ]}

    op = planner.plan(_ctx("shop", shop, ["buy_relic", "buy_card", "buy_potion"], run=run))

    assert op is not None
    assert not (op.action_type == "buy_relic" and op.reason == "shop_high_value_relic"), "不许再只管买得起就买遗物"
    assert op.action_type in {"buy_card", "buy_potion", "remove_card_at_shop", "close_shop_inventory"}


def test_shop_buys_the_potion_when_it_is_the_only_needed_item() -> None:
    """只剩药水值得买时仍然买药水（新口径下由分数决定，而不是"轮到药水这一类了"）。"""
    planner = STS2HeuristicPlanner()
    shop = {
        "shop_cards": [],
        "shop_relics": [],
        "shop_potions": [{"index": 3, "name": "格挡药水", "potion_id": "BLOCK_POTION", "price": 60, "is_stocked": True, "enough_gold": True}],
    }
    run = {"gold": 200, "current_hp": 70, "max_hp": 80, "floor": 6, "deck": _junk_deck(), "potions": [
        {"index": 0, "potion_id": None, "occupied": False},
        {"index": 1, "potion_id": None, "occupied": False},
    ]}

    op = planner.plan(_ctx("shop", shop, ["buy_potion"], run=run))

    assert op is not None and op.action_type == "buy_potion" and op.kwargs.get("option_index") == 3
    assert op.reason == "shop_scored_buy"


def test_shop_removes_card_when_the_deck_is_full_of_junk() -> None:
    """删牌服务也要赢下池子才买：牌组满是未升级打击/防御 + 诅咒 -> 质量缺口高 -> 买。"""
    planner = STS2HeuristicPlanner()
    shop = {"shop_cards": [], "shop_relics": [], "shop_potions": [], "card_removal": {"price": 75, "available": True, "used": False, "enough_gold": True}}
    run = {"gold": 260, "current_hp": 70, "max_hp": 80, "floor": 5, "deck": _junk_deck(), "potions": [
        {"index": 0, "potion_id": None, "occupied": False},
        {"index": 1, "potion_id": None, "occupied": False},
    ]}

    op = planner.plan(_ctx("shop", shop, ["remove_card_at_shop"], run=run))

    assert op is not None and op.action_type == "remove_card_at_shop" and op.kwargs == {}


def test_shop_does_not_remove_card_without_a_quality_gap() -> None:
    """没有低价值牌（全升级、无诅咒）-> 不删牌（规格「三、5」）。"""
    planner = STS2HeuristicPlanner()
    shop = {"shop_cards": [], "shop_relics": [], "shop_potions": [], "card_removal": {"price": 75, "available": True, "used": False, "enough_gold": True}}
    deck = [
        {"index": i, "card_id": "STRIKE_IRONCLAD", "name": "Strike", "card_type": "Attack", "energy_cost": 1, "upgraded": True,
         "dynamic_values": [{"name": "Damage", "current_value": 9}]}
        for i in range(10)
    ]
    run = {"gold": 260, "current_hp": 70, "max_hp": 80, "floor": 5, "deck": deck, "potions": []}

    op = planner.plan(_ctx("shop", shop, ["remove_card_at_shop", "close_shop_inventory"], run=run))

    assert op is not None and op.action_type == "close_shop_inventory"


def test_shop_closes_when_nothing_to_do() -> None:
    op = STS2HeuristicPlanner().plan(_ctx("shop", {"shop_cards": []}, ["close_shop_inventory", "proceed"]))
    assert op is not None and op.action_type == "close_shop_inventory"


def test_shop_affordable_items_filters_gold() -> None:
    items = STS2HeuristicPlanner()._shop_affordable_items(
        {
            "payload": {
                "shop_cards": [{"index": 0, "is_stocked": True, "enough_gold": False}],
                "shop_relics": [{"index": 0, "is_stocked": True, "enough_gold": True}],
                "shop_potions": [],
            }
        }
    )
    assert items[0] == [] and len(items[1]) == 1 and items[2] == []


# ---- 反复开关货架（线上实测到的死循环）--------------------------------------------------


def test_shop_does_not_reopen_after_closing() -> None:
    """**"商店反复打开又关闭"的回归测试。**

    线上实测：`open_shop_inventory` 是**开关**，而开关两态的 `screen` 都叫 `SHOP`。
    于是 开 → 没值得买的就关 → 下一 tick 又看见 `open_shop_inventory` 就又开 → 无限来回，
    整局就耗在商店里。修法：一次进店只开一次货架（用 `close_shop_inventory` 是否在册判断"看过货了"）。

    真实动作清单（2026-10-07 01:24 实测）：
      关：proceed / open_shop_inventory / discard_potion / save_and_quit
      开：close_shop_inventory / buy_card / discard_potion / save_and_quit
    """
    planner = STS2HeuristicPlanner()
    shop = {"floor": 4, "shop_cards": []}  # 没东西可买 -> 开着也会关掉
    closed = _ctx("shop", shop, ["proceed", "open_shop_inventory", "discard_potion"])
    inside = _ctx("shop", shop, ["close_shop_inventory", "buy_card", "discard_potion"])

    first = planner.plan(closed)
    assert first is not None and first.action_type == "open_shop_inventory"

    shut = planner.plan(inside)
    assert shut is not None and shut.action_type == "close_shop_inventory"

    after = planner.plan(closed)
    assert after is not None
    assert after.action_type == "proceed", "关掉之后不许再开，否则就是无限开关"


def test_shop_reopens_on_a_new_visit() -> None:
    """换店（楼层/货品变了）要重新开货架，不能被上一家店的"看过"状态挡住。"""
    planner = STS2HeuristicPlanner()
    first_shop = _ctx("shop", {"floor": 4, "shop_cards": []}, ["proceed", "open_shop_inventory"])
    planner.plan(first_shop)
    planner.plan(_ctx("shop", {"floor": 4, "shop_cards": []}, ["close_shop_inventory"]))
    assert planner.plan(first_shop) is not None  # 同一家店：不再开（这里只确认不炸）

    next_shop = _ctx("shop", {"floor": 7, "shop_cards": [{"index": 0, "card_id": "X"}]}, ["proceed", "open_shop_inventory"])
    op = planner.plan(next_shop)
    assert op is not None and op.action_type == "open_shop_inventory"
