"""商店统一评分：牌组量化 × 覆盖度 × 商品量化 → 买分最高的，都不高就不买。

规格：docs/shop_scoring.md（玩家口径 2026-10-07）。这里逐条覆盖「六、测试矩阵」的 1~12 条。
用到的字段名全部对齐 mod 的 /state（见 game_mod/nekospire/Game/GameStateService.cs）：
  run.deck[]   —— index/card_id/name/upgraded/card_type/energy_cost/dynamic_values/resolved_rules_text
  run.relics[] —— index/relic_id/name/description
  run.potions[]—— index/potion_id/occupied/can_use
  shop.cards[] / shop.relics[] / shop.potions[] / shop.card_removal
"""
from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import (
    SHOP_SCORE_THRESHOLD,
    STS2HeuristicPlanner,
)


class CaptureLogger:
    """把 info 级日志收起来：规格「四、日志与可观测」要求日志能读出"为什么买/为什么不买"。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.lines.append(str(message) % args if args else str(message))

    def debug(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def text(self) -> str:
        return "\n".join(self.lines)


def _planner() -> tuple[STS2HeuristicPlanner, CaptureLogger]:
    logger = CaptureLogger()
    return STS2HeuristicPlanner(logger=logger), logger


# ---- 牌组 -------------------------------------------------------------------------------


def _strike(index: int, upgraded: bool = False) -> dict[str, Any]:
    return {
        "index": index,
        "card_id": "STRIKE_IRONCLAD",
        "name": "Strike",
        "card_type": "Attack",
        "energy_cost": 1,
        "upgraded": upgraded,
        "dynamic_values": [{"name": "Damage", "current_value": 9 if upgraded else 6}],
    }


def _defend(index: int, upgraded: bool = False) -> dict[str, Any]:
    return {
        "index": index,
        "card_id": "DEFEND_IRONCLAD",
        "name": "Defend",
        "card_type": "Skill",
        "energy_cost": 1,
        "upgraded": upgraded,
        "dynamic_values": [{"name": "Block", "current_value": 8 if upgraded else 5}],
    }


def _starter_deck() -> list[dict[str, Any]]:
    """起始牌组（铁甲战士）：5 打击 + 4 防御 + 重击 + 诅咒 + 无抽牌。"""
    cards = [_strike(i) for i in range(5)] + [_defend(i) for i in range(5, 9)]
    cards.append(
        {
            "index": 9,
            "card_id": "BASH",
            "name": "Bash",
            "card_type": "Attack",
            "energy_cost": 2,
            "upgraded": False,
            "resolved_rules_text": "造成 8 点伤害。施加 2 层易伤。",
            "dynamic_values": [{"name": "Damage", "current_value": 8}, {"name": "VulnerablePower", "current_value": 2}],
        }
    )
    cards.append({"index": 10, "card_id": "ASCENDERS_BANE", "name": "Ascender's Bane", "card_type": "Curse", "energy_cost": -1, "upgraded": False})
    return cards


def _mature_deck() -> list[dict[str, Any]]:
    """成型牌组：全部升级、有抽牌/群伤/0 费，六维缺口只剩一点点群伤。"""
    cards = [_strike(i, upgraded=True) for i in range(4)] + [_defend(i, upgraded=True) for i in range(4, 8)]
    cards.append(
        {
            "index": 8,
            "card_id": "WHIRLWIND",
            "name": "Whirlwind+",
            "card_type": "Attack",
            "energy_cost": 1,
            "upgraded": True,
            "resolved_rules_text": "Deal 5 damage to ALL enemies.",
            "dynamic_values": [{"name": "Damage", "current_value": 5}],
        }
    )
    cards.append(
        {
            "index": 9,
            "card_id": "POWER_THROUGH",
            "name": "Power Through+",
            "card_type": "Skill",
            "energy_cost": 1,
            "upgraded": True,
            "resolved_rules_text": "Draw 2 cards. Gain 15 Block.",
            "dynamic_values": [{"name": "Block", "current_value": 15}, {"name": "Cards", "current_value": 2}],
        }
    )
    return cards


def _output_starved_deck() -> list[dict[str, Any]]:
    """一张攻击牌都没有的牌组：输出缺口接近拉满（用来测"补缺分"真的在起作用）。"""
    return [_defend(i, upgraded=True) for i in range(8)] + [
        {
            "index": 8,
            "card_id": "POWER_THROUGH",
            "name": "Power Through+",
            "card_type": "Skill",
            "energy_cost": 1,
            "upgraded": True,
            "resolved_rules_text": "Draw 2 cards. Gain 15 Block.",
            "dynamic_values": [{"name": "Block", "current_value": 15}, {"name": "Cards", "current_value": 2}],
        },
        {
            "index": 9,
            "card_id": "SHRUG_IT_OFF",
            "name": "Shrug It Off+",
            "card_type": "Skill",
            "energy_cost": 1,
            "upgraded": True,
            "resolved_rules_text": "Gain 11 Block. Draw 1 card.",
            "dynamic_values": [{"name": "Block", "current_value": 11}],
        },
    ]


def _wide_deck() -> list[dict[str, Any]]:
    """20 张的"哪一维都差一点"的牌组：输出/格挡/抽牌/费用/群伤都有缺口，质量没缺口。

    用来测「遗物覆盖度整体很高（构筑已成型）→ 门槛上调」：只有缺口铺得开、又恰好被遗物覆盖掉一半以上，
    才谈得上"构筑成型"。
    """
    cards: list[dict[str, Any]] = []
    for i in range(4):
        cards.append(_strike(i, upgraded=True))
    for i in range(4, 12):
        cards.append(_defend(i, upgraded=True))
    cards.append(
        {
            "index": 12,
            "card_id": "BASH",
            "name": "Bash+",
            "card_type": "Attack",
            "energy_cost": 2,
            "upgraded": True,
            "resolved_rules_text": "Deal 10 damage. Apply 3 Vulnerable.",
            "dynamic_values": [{"name": "Damage", "current_value": 10}, {"name": "VulnerablePower", "current_value": 3}],
        }
    )
    for i in (13, 14):
        cards.append(
            {
                "index": i,
                "card_id": "IRON_WAVE",
                "name": "Iron Wave+",
                "card_type": "Attack",
                "energy_cost": 1,
                "upgraded": True,
                "resolved_rules_text": "Gain 7 Block. Deal 7 damage.",
                "dynamic_values": [{"name": "Damage", "current_value": 7}, {"name": "Block", "current_value": 7}],
            }
        )
    for i in range(15, 20):
        # 高费"能力牌"：把费用曲线拉高（费用缺口），但既不补输出也不补格挡。
        cards.append(
            {
                "index": i,
                "card_id": "DEMON_FORM",
                "name": "Demon Form+",
                "card_type": "Power",
                "energy_cost": 3,
                "upgraded": True,
                "resolved_rules_text": "At the start of each turn, gain 2 Strength.",
                "dynamic_values": [],
            }
        )
    return cards


_MATURITY_RELICS: list[dict[str, Any]] = [
    {"index": 0, "relic_id": "VAJRA", "name": "Vajra", "description": "Gain 1 Strength. 力量"},
    {"index": 1, "relic_id": "ODDLY_SMOOTH_STONE", "name": "Oddly Smooth Stone", "description": "Gain 1 Dexterity. 敏捷 格挡"},
    {"index": 2, "relic_id": "BAG_OF_PREPARATION", "name": "Bag of Preparation", "description": "At the start of each combat, draw 2 cards. 抽牌"},
    {"index": 3, "relic_id": "CRACKED_CORE", "name": "Cracked Core", "description": "Gain 1 Energy each turn. 能量"},
]


# ---- 商店 -------------------------------------------------------------------------------


def _shop_card(
    index: int,
    name: str,
    price: int = 50,
    *,
    card_id: str = "",
    text: str = "Deal 9 damage.",
    cost: int = 1,
    dynamic: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if dynamic is None:
        dynamic = [{"name": "Damage", "current_value": 9}] if "damage" in text.lower() else []
    return {
        "index": index,
        "card_id": card_id or name.upper().replace(" ", "_"),
        "name": name,
        "card_type": "Attack" if "damage" in text.lower() else "Skill",
        "energy_cost": cost,
        "price": price,
        "resolved_rules_text": text,
        "dynamic_values": dynamic,
        "is_stocked": True,
        "enough_gold": True,
    }


def _shop_relic(index: int, name: str, *, relic_id: str = "", rarity: str = "Common", price: int = 150, description: str = "") -> dict[str, Any]:
    # 注意：mod 的 ShopRelicPayload **只有** index/relic_id/name/rarity/price/is_stocked/enough_gold
    # （没有 description），这里只在个别用例里补 description 以便测"读得出效果类别"的那条路。
    relic: dict[str, Any] = {
        "index": index,
        "relic_id": relic_id or name.upper().replace(" ", "_"),
        "name": name,
        "rarity": rarity,
        "price": price,
        "is_stocked": True,
        "enough_gold": True,
    }
    if description:
        relic["description"] = description
    return relic


def _shop_potion(index: int, name: str, *, potion_id: str = "", price: int = 60) -> dict[str, Any]:
    return {
        "index": index,
        "potion_id": potion_id or name.upper().replace(" ", "_"),
        "name": name,
        "rarity": "Common",
        "price": price,
        "is_stocked": True,
        "enough_gold": True,
    }


def _context(
    *,
    shop: dict[str, Any],
    run: dict[str, Any],
    elite: bool | None = None,
    actions: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    action_names = actions or ("buy_relic", "buy_card", "buy_potion", "remove_card_at_shop", "close_shop_inventory")
    payload: dict[str, Any] = {
        "floor": run.get("floor", 6),
        "gold": run.get("gold", 0),
        "current_hp": run.get("current_hp", 70),
        "max_hp": run.get("max_hp", 80),
        "deck": {"cards": run.get("deck", [])},
        "shop": shop,
        "shop_cards": shop.get("cards", []),
        "shop_relics": shop.get("relics", []),
        "shop_potions": shop.get("potions", []),
    }
    if elite is not None:
        payload["elite_ahead"] = elite
    return {
        "classification": {"state_name": "shop"},
        "summary_context": {"payload": payload},
        "snapshot": {
            "screen": "SHOP",
            "available_actions": [{"type": name, "raw": {"name": name}} for name in action_names],
            "raw_state": {"run": run, "shop": shop, "floor": run.get("floor", 6), "turn": None},
        },
        "strategy_context": {"preferences": {}},
    }


def _empty_slots(count: int) -> list[dict[str, Any]]:
    return [{"index": i, "potion_id": None, "occupied": False, "can_use": False} for i in range(count)]


def _filled_slots(count: int, potion_id: str = "BLOCK_POTION") -> list[dict[str, Any]]:
    return [{"index": i, "potion_id": potion_id, "occupied": True, "can_use": True, "description": "Gain 12 Block."} for i in range(count)]


# ---- 1. 前方有精英 + 钱够两瓶药 → 买药水而不买遗物 ----------------------------------------


def test_elite_ahead_buys_potion_instead_of_relic() -> None:
    planner, logger = _planner()
    shop = {
        "cards": [],
        "relics": [_shop_relic(0, "Vajra", relic_id="VAJRA", rarity="Rare", price=150, description="Gain 2 Strength. 力量")],
        "potions": [_shop_potion(0, "Block Potion", potion_id="BLOCK_POTION", price=60)],
        "card_removal": {"price": 75, "available": True, "used": False, "enough_gold": True},
    }
    run = {"floor": 6, "gold": 300, "current_hp": 70, "max_hp": 80, "deck": _output_starved_deck(), "relics": [], "potions": _empty_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=True))

    assert op is not None
    assert op.action_type == "buy_potion", "前方有精英 + 钱够两瓶药 -> 先买药，不许把买药的钱花在遗物上"
    assert op.kwargs.get("option_index") == 0
    assert op.reason == "shop_scored_buy"
    # 遗物是被"留给药水 + 底钱"的预算拦下的，不是被门槛拦下的。
    assert "遗物预算" in logger.text()
    assert "留给药水" in logger.text()


def test_second_potion_still_bought_for_the_elite_push() -> None:
    """买完第一瓶之后（栏位还剩 1），第二瓶仍然该买 —— 玩家要的就是"买两瓶药冲精英"。"""
    planner, _logger = _planner()
    shop = {
        "cards": [],
        "relics": [],
        "potions": [_shop_potion(1, "Fire Potion", potion_id="FIRE_POTION", price=60)],
    }
    run = {
        "floor": 6,
        "gold": 240,
        "current_hp": 70,
        "max_hp": 80,
        "deck": _output_starved_deck(),
        "relics": [],
        "potions": [{"index": 0, "potion_id": "BLOCK_POTION", "occupied": True, "can_use": True, "description": "Gain 12 Block."},
                    {"index": 1, "potion_id": None, "occupied": False, "can_use": False}],
    }

    op = planner.plan(_context(shop=shop, run=run, elite=True))

    assert op is not None and op.action_type == "buy_potion" and op.kwargs.get("option_index") == 1


# ---- 2. 前方没精英、遗物平庸 → 什么都不买，钱留着 ------------------------------------------


def test_nothing_bought_when_no_elite_and_relic_is_mediocre() -> None:
    planner, logger = _planner()
    shop = {
        "cards": [],
        "relics": [_shop_relic(0, "Small Rock", relic_id="SMALL_ROCK", rarity="Common", price=150)],
        "potions": [],
    }
    run = {"floor": 9, "gold": 260, "current_hp": 70, "max_hp": 80, "deck": _mature_deck(), "relics": [], "potions": _empty_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None
    assert op.action_type in {"close_shop_inventory", "proceed"}, "都不高就该关门走人，一分不花"
    assert op.reason == "shop_score_below_threshold"
    # 规格「四」：没买时要把"最高分 vs 门槛"写进日志，方便调参。
    assert "最高分" in logger.text() and "门槛" in logger.text()


def test_mature_deck_does_not_buy_a_moderate_card() -> None:
    """牌组没缺口 → 不买卡（规格「三、5」）。"""
    planner, _logger = _planner()
    shop = {"cards": [_shop_card(0, "Iron Wave", price=50)], "relics": [], "potions": []}
    run = {"floor": 9, "gold": 300, "current_hp": 70, "max_hp": 80, "deck": _mature_deck(), "relics": [], "potions": _empty_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None and op.action_type == "close_shop_inventory"


# ---- 3 / 12. 药水栏位满 → 整类不评，钱转去买评分够高的遗物 ----------------------------------


def test_full_potion_slots_skip_potions_and_buy_a_needed_relic() -> None:
    planner, logger = _planner()
    shop = {
        "cards": [],
        "relics": [_shop_relic(0, "Vajra", relic_id="VAJRA", rarity="Rare", price=150, description="Gain 2 Strength. 力量")],
        "potions": [_shop_potion(0, "Block Potion", potion_id="BLOCK_POTION", price=60)],
    }
    run = {"floor": 9, "gold": 500, "current_hp": 70, "max_hp": 80, "deck": _output_starved_deck(), "relics": [], "potions": _filled_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None
    assert op.action_type == "buy_relic" and op.kwargs.get("option_index") == 0, "栏位满就买不了药水，钱该转去买补缺的遗物"
    assert op.reason == "shop_scored_buy"
    # 第 12 条：栏位满 -> 整类药水不评（日志里能看出来）。
    assert "药水栏位已满" in logger.text()
    assert "药水「Block Potion」" not in logger.text()


def test_full_potion_slots_do_not_score_potions_at_all() -> None:
    """第 12 条（与第 3 条互为印证）：栏位满时药水**根本不进池子**，不能靠"把遗物价格抬高"来伪装。"""
    planner, _logger = _planner()
    shop = {"cards": [], "relics": [], "potions": [_shop_potion(0, "Block Potion", potion_id="BLOCK_POTION", price=10)]}
    run = {"floor": 9, "gold": 500, "current_hp": 70, "max_hp": 80, "deck": _output_starved_deck(), "relics": [], "potions": _filled_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None and op.action_type == "close_shop_inventory", "栏位满 + 只有药水 -> 什么都不买"


# ---- 4. 遗物与已有重复 → 直接排除 ----------------------------------------------------------


def test_duplicate_relic_is_excluded_not_discounted() -> None:
    planner, logger = _planner()
    shop = {
        "cards": [],
        "relics": [_shop_relic(0, "Vajra", relic_id="VAJRA", rarity="Rare", price=100, description="Gain 2 Strength. 力量")],
        "potions": [],
    }
    run = {
        "floor": 9,
        "gold": 500,
        "current_hp": 70,
        "max_hp": 80,
        "deck": _output_starved_deck(),
        "relics": [{"index": 0, "relic_id": "VAJRA", "name": "Vajra", "description": "Gain 2 Strength."}],
        "potions": _empty_slots(2),
    }

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None and op.action_type == "close_shop_inventory", "重复遗物要直接排除（不是降分）"
    assert "重复" in logger.text()


# ---- 5. 钱很多 → 该买的照买（不误伤）-------------------------------------------------------


def test_rich_purse_still_buys_what_is_needed() -> None:
    planner, logger = _planner()
    shop = {
        "cards": [],
        "relics": [],
        "potions": [_shop_potion(0, "Block Potion", potion_id="BLOCK_POTION", price=60)],
    }
    run = {"floor": 9, "gold": 500, "current_hp": 70, "max_hp": 80, "deck": _output_starved_deck(), "relics": [], "potions": _empty_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None and op.action_type == "buy_potion"
    # 金币 > 300 -> 门槛略降（40 -> 32），不再"攒着不花"。
    assert "门槛=32.0" in logger.text()


# ---- 6. 牌组满是无用牌 + 删牌服务 → 买删牌 ------------------------------------------------


def test_junk_deck_buys_card_removal() -> None:
    planner, _logger = _planner()
    shop = {"cards": [], "relics": [], "potions": [], "card_removal": {"price": 75, "available": True, "used": False, "enough_gold": True}}
    run = {"floor": 5, "gold": 260, "current_hp": 70, "max_hp": 80, "deck": _starter_deck(), "relics": [], "potions": _empty_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None and op.action_type == "remove_card_at_shop"
    assert op.kwargs == {}, "remove_card_at_shop 不接 option_index"
    assert op.reason == "shop_scored_buy"


def test_mature_deck_does_not_buy_card_removal() -> None:
    """没有低价值牌 → 不删牌（规格「三、5」）。"""
    planner, _logger = _planner()
    shop = {"cards": [], "relics": [], "potions": [], "card_removal": {"price": 75, "available": True, "used": False, "enough_gold": True}}
    run = {"floor": 9, "gold": 500, "current_hp": 70, "max_hp": 80, "deck": _mature_deck(), "relics": [], "potions": _empty_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None and op.action_type == "close_shop_inventory"


# ---- 7. 最高分恰好等于门槛 → 买（边界）-----------------------------------------------------


def test_score_exactly_at_threshold_is_bought() -> None:
    """门槛是"低于才不买"，等于门槛仍然买 —— 用可配的门槛把分数正好卡在边界上。"""
    planner, logger = _planner()
    shop = {"cards": [], "relics": [], "potions": [_shop_potion(0, "Block Potion", potion_id="BLOCK_POTION", price=50)]}
    # 金币 200：既不触发"钱多降门槛"，也不触发"钱少抬门槛"，门槛就是配置里的那个数。
    run = {"floor": 9, "gold": 200, "current_hp": 70, "max_hp": 80, "deck": _output_starved_deck(), "relics": [], "potions": _empty_slots(2)}
    context = _context(shop=shop, run=run, elite=False)

    reference = planner._shop_preferred_purchase(context["summary_context"], {}, context["snapshot"])
    assert reference is not None and reference.get("score") is not None
    threshold = float(reference["score"])
    context["strategy_context"]["preferences"] = {"records": [{"value": {"instruction_type": "shop_policy", "score_threshold": threshold}}]}

    op = planner.plan(context)

    assert op is not None and op.action_type == "buy_potion", "恰好等于门槛要买"
    assert f"门槛={threshold:.1f}" in logger.text()


def test_score_just_below_threshold_is_not_bought() -> None:
    planner, _logger = _planner()
    shop = {"cards": [], "relics": [], "potions": [_shop_potion(0, "Block Potion", potion_id="BLOCK_POTION", price=50)]}
    run = {"floor": 9, "gold": 200, "current_hp": 70, "max_hp": 80, "deck": _output_starved_deck(), "relics": [], "potions": _empty_slots(2)}
    context = _context(shop=shop, run=run, elite=False)
    reference = planner._shop_preferred_purchase(context["summary_context"], {}, context["snapshot"])
    assert reference is not None and reference.get("score") is not None
    context["strategy_context"]["preferences"] = {
        "records": [{"value": {"instruction_type": "shop_policy", "score_threshold": float(reference["score"]) + 0.5}}]
    }

    op = planner.plan(context)

    assert op is not None and op.action_type == "close_shop_inventory" and op.reason == "shop_score_below_threshold"


# ---- 8. 日志里能读出"没买是因为要留钱给药水" -----------------------------------------------


def test_log_says_money_is_reserved_for_potions() -> None:
    """第 8 条：日志里能读出"没买是因为要留钱给药水"。

    场景：前方有精英 + 手里已有 1 瓶减伤 + 栏位剩 1 + 店里还有一瓶减伤。
    1) 那瓶减伤"同类已有" -> 分数低，不买；
    2) 补缺的稀有遗物被"留给药水 60 + 下一个商店的底钱 120"的预算拦下 -> 一分不花。
    """
    planner, logger = _planner()
    shop = {
        "cards": [],
        "relics": [_shop_relic(0, "Vajra", relic_id="VAJRA", rarity="Rare", price=200, description="Gain 2 Strength. 力量")],
        "potions": [_shop_potion(0, "Block Potion", potion_id="BLOCK_POTION", price=60)],
    }
    run = {
        "floor": 6,
        "gold": 300,
        "current_hp": 70,
        "max_hp": 80,
        "deck": _output_starved_deck(),
        "relics": [],
        "potions": [
            {"index": 0, "potion_id": "BLOCK_POTION", "occupied": True, "can_use": True, "description": "Gain 12 Block."},
            {"index": 1, "potion_id": None, "occupied": False, "can_use": False},
        ],
    }

    op = planner.plan(_context(shop=shop, run=run, elite=True))

    text = logger.text()
    assert op is not None and op.action_type == "close_shop_inventory"
    assert "留给药水 60" in text, "剩 1 格空栏位 × 药水标价 60 = 预留 60"
    assert "遗物预算=120" in text, "300 - 60(药水预留) - 120(底钱) = 120 < 200"
    assert "底钱" in text


def test_no_potion_reserve_when_the_shop_sells_no_potions() -> None:
    """这家店没有药水可买 -> 不预留：钱花不出去，压着只会白白错过大遗物。"""
    planner, _logger = _planner()
    shop = {
        "cards": [],
        "relics": [_shop_relic(0, "Vajra", relic_id="VAJRA", rarity="Rare", price=200, description="Gain 2 Strength. 力量")],
        "potions": [],
    }
    run = {"floor": 6, "gold": 350, "current_hp": 70, "max_hp": 80, "deck": _output_starved_deck(), "relics": [], "potions": _empty_slots(2)}

    op = planner.plan(_context(shop=shop, run=run, elite=True))

    # 若是留了 2×60=120 的药水钱，预算只剩 350-120-120=110 < 200 -> 买不了。
    assert op is not None and op.action_type == "buy_relic"


# ---- 9. 已有同类遗物 → 再买加力量的遗物降分到不买 ------------------------------------------


def test_same_category_relic_owned_drops_score_below_threshold() -> None:
    planner, logger = _planner()
    deck = _starter_deck()
    shop = {
        "cards": [],
        "relics": [_shop_relic(0, "Akabeko", relic_id="AKABEKO", rarity="Rare", price=100, description="Gain 3 Strength. 力量")],
        "potions": [],
    }
    base_run = {"floor": 6, "gold": 500, "current_hp": 70, "max_hp": 80, "deck": deck, "relics": [], "potions": _empty_slots(2)}
    owned_run = dict(base_run, relics=[{"index": 0, "relic_id": "VAJRA", "name": "Vajra", "description": "Gain 2 Strength. 力量"}])

    without = planner._shop_preferred_purchase(
        _context(shop=shop, run=base_run, elite=False)["summary_context"],
        {},
        _context(shop=shop, run=base_run, elite=False)["snapshot"],
    )
    op = planner.plan(_context(shop=shop, run=owned_run, elite=False))

    assert without is not None and without.get("action_type") == "buy_relic", "先确认没同类遗物时它确实会买"
    assert op is not None and op.action_type == "close_shop_inventory", "同类覆盖之后不买"
    assert "补缺=输出0.00" in logger.text(), "既有力量遗物 -> 输出维缺口被覆盖成 0"


# ---- 10. 遗物覆盖度已很高（构筑成型）→ 门槛上调，倾向留钱 ----------------------------------


def test_mature_coverage_raises_the_threshold() -> None:
    planner, logger = _planner()
    shop = {
        "cards": [
            _shop_card(0, "Battle Trance", price=50, text="Draw 3 cards.", dynamic=[{"name": "Cards", "current_value": 3}]),
        ],
        "relics": [],
        "potions": [],
    }
    base_run = {"floor": 9, "gold": 200, "current_hp": 70, "max_hp": 80, "deck": _wide_deck(), "relics": [], "potions": _empty_slots(2)}
    covered_run = dict(base_run, relics=_MATURITY_RELICS)
    base_context = _context(shop=shop, run=base_run, elite=False)
    reference = planner._shop_preferred_purchase(base_context["summary_context"], {}, base_context["snapshot"])
    assert reference is not None and reference.get("score") is not None
    # 把门槛设成这张卡的分数：没有遗物覆盖时"恰好够门槛 -> 买"，覆盖度上去之后门槛 +10 -> 不买。
    policy = {"records": [{"value": {"instruction_type": "shop_policy", "score_threshold": float(reference["score"])}}]}
    base_context["strategy_context"]["preferences"] = policy
    covered_context = _context(shop=shop, run=covered_run, elite=False)
    covered_context["strategy_context"]["preferences"] = policy

    plain = planner.plan(base_context)
    op = planner.plan(covered_context)

    assert plain is not None and plain.action_type == "buy_card", "没有覆盖时这张卡刚好够门槛"
    assert op is not None and op.action_type == "close_shop_inventory", "构筑成型 -> 门槛上调，倾向留钱"
    assert f"门槛={float(reference['score']) + 10.0:.1f}" in logger.text()


def test_coverage_maturity_adds_ten_to_the_threshold() -> None:
    """「构筑成型 -> 门槛上调」这一步的数值证据（覆盖掉一半以上缺口 -> +10）。"""
    planner = STS2HeuristicPlanner()
    gaps = planner._deck_gap_vector({"cards": _wide_deck()}, current_hp=70, max_hp=80, floor=9)
    coverage = planner._relic_coverage(_MATURITY_RELICS)
    residual = {dim: max(0.0, gaps[dim] - coverage.get(dim, 0.0)) for dim in gaps}

    assert sum(residual.values()) < sum(gaps.values()) * 0.5, "这组遗物确实覆盖掉了一半以上缺口"
    assert planner._shop_threshold(gold=200, elite_ahead=False, gaps=gaps, residual=residual, policy={}) == SHOP_SCORE_THRESHOLD + 10.0


# ---- 11. 已有 1 瓶减伤、栏位剩 1 → 只给爆发类药水高分 ---------------------------------------


def test_covered_potion_category_only_boosts_the_missing_category() -> None:
    planner, logger = _planner()
    shop = {
        "cards": [],
        "relics": [],
        "potions": [
            _shop_potion(0, "Block Potion", potion_id="BLOCK_POTION", price=60),
            _shop_potion(1, "Fire Potion", potion_id="FIRE_POTION", price=60),
        ],
    }
    run = {
        "floor": 6,
        "gold": 400,
        "current_hp": 70,
        "max_hp": 80,
        "deck": _output_starved_deck(),
        "relics": [],
        "potions": [
            {"index": 0, "potion_id": "BLOCK_POTION", "occupied": True, "can_use": True, "description": "Gain 12 Block."},
            {"index": 1, "potion_id": None, "occupied": False, "can_use": False},
        ],
    }

    op = planner.plan(_context(shop=shop, run=run, elite=False))

    assert op is not None and op.action_type == "buy_potion"
    assert op.kwargs.get("option_index") == 1, "减伤已经有了 -> 只给还缺的爆发类高分"
    text = logger.text()
    assert "同类已有" in text and "还缺这一类" in text


# ---- 牌组量化本身的形状 -------------------------------------------------------------------


def test_deck_gap_vector_dimensions_and_ranges() -> None:
    planner = STS2HeuristicPlanner()
    gaps = planner._deck_gap_vector({"cards": _starter_deck()}, current_hp=70, max_hp=80, floor=5)

    assert set(gaps) == {"output", "defense", "draw", "energy", "aoe", "quality"}
    assert all(0.0 <= value <= 1.0 for value in gaps.values())
    assert gaps["quality"] > 0.5, "起始牌组满是未升级的打击/防御 + 一张诅咒 -> 删牌/升级缺口高"
    assert gaps["draw"] == 0.0, "13 张的小牌组不缺循环"


def test_deck_gap_vector_is_all_zero_without_a_readable_deck() -> None:
    planner = STS2HeuristicPlanner()

    gaps = planner._deck_gap_vector({}, current_hp=70, max_hp=80, floor=5)

    assert all(value == 0.0 for value in gaps.values()), "读不到牌组就按'什么都不缺'处理（宁可这一家不买）"


def test_relic_coverage_is_diminishing() -> None:
    planner = STS2HeuristicPlanner()
    one = planner._relic_coverage([{"relic_id": "VAJRA", "description": "Gain 1 Strength. 力量"}])
    two = planner._relic_coverage(
        [
            {"relic_id": "VAJRA", "description": "Gain 1 Strength. 力量"},
            {"relic_id": "AKABEKO", "description": "Gain 3 Strength. 力量"},
        ]
    )

    assert one["output"] == 0.4, "第 1 件几乎补满（1-0.6^1）"
    assert two["output"] == 0.64, "第 2 件只补 40%（1-0.6^2）"


def test_multi_category_relic_shares_its_coverage() -> None:
    """横跨两类的遗物按类别**分摊**：一类各算半件（n=0.5 -> 1-0.6^0.5 ≈ 0.225）。"""
    planner = STS2HeuristicPlanner()

    coverage = planner._relic_coverage([{"relic_id": "X", "description": "Gain 1 Strength and 1 Dexterity. 力量 敏捷 格挡"}])

    assert coverage["output"] == pytest.approx(1 - 0.6 ** 0.5, abs=1e-4)
    assert coverage["defense"] == pytest.approx(1 - 0.6 ** 0.5, abs=1e-4)
    assert coverage["output"] < 0.4, "分摊之后单类覆盖度低于只补一类的那件"


def test_shop_threshold_defaults_and_floating() -> None:
    planner = STS2HeuristicPlanner()
    gaps = {dim: 0.5 for dim in ("output", "defense", "draw", "energy", "aoe", "quality")}

    plain = planner._shop_threshold(gold=200, elite_ahead=False, gaps=gaps, residual=gaps, policy={})
    rich = planner._shop_threshold(gold=500, elite_ahead=False, gaps=gaps, residual=gaps, policy={})
    poor = planner._shop_threshold(gold=50, elite_ahead=False, gaps=gaps, residual=gaps, policy={})

    assert plain == SHOP_SCORE_THRESHOLD
    assert rich < plain < poor


# ---- "前方有精英"是从选路那一刻记下来的（商店界面拿不到地图）--------------------------------


def test_elite_ahead_is_memoised_from_the_map_route() -> None:
    """选路时若这条路 4 层内会撞上精英，进店后就按"前方有精英"算（药水加权、遗物预算）。

    商店界面拿不到地图（mod 的 BuildMapPayload 一离开地图屏就返回 null），所以结论只能在选路那一刻算。
    """
    planner = STS2HeuristicPlanner()
    graph = {
        "nodes": [
            {"row": 0, "col": 0, "node_type": "Monster", "children": [{"row": 1, "col": 0}]},
            {"row": 1, "col": 0, "node_type": "Elite", "children": [{"row": 2, "col": 0}]},
            {"row": 2, "col": 0, "node_type": "RestSite", "children": []},
        ],
        "boss_node": {"row": 3, "col": 0},
    }

    planner._note_route_choice({"row": 0, "col": 0, "node_type": "Monster"}, graph)

    assert planner._shop_elite_ahead({"payload": {}}, None) is True


def test_no_elite_on_the_chosen_route_leaves_the_flag_off() -> None:
    planner = STS2HeuristicPlanner()
    graph = {
        "nodes": [
            {"row": 0, "col": 0, "node_type": "Monster", "children": [{"row": 1, "col": 0}]},
            {"row": 1, "col": 0, "node_type": "Shop", "children": [{"row": 2, "col": 0}]},
            {"row": 2, "col": 0, "node_type": "RestSite", "children": []},
        ],
        "boss_node": {"row": 3, "col": 0},
    }

    planner._note_route_choice({"row": 0, "col": 0, "node_type": "Monster"}, graph)

    assert planner._shop_elite_ahead({"payload": {}}, None) is False


def test_elite_room_makes_the_combat_an_elite_fight() -> None:
    planner = STS2HeuristicPlanner()

    planner._note_route_choice({"row": 0, "col": 0, "node_type": "Elite"}, None)

    assert planner._route_elite_ahead is True, "精英房本身就是一场要冲的硬仗"
    assert planner.in_elite_or_boss_combat() is True


def test_explicit_payload_flag_wins() -> None:
    planner = STS2HeuristicPlanner()

    assert planner._shop_elite_ahead({"payload": {"elite_ahead": True}}, None) is True
    assert planner._shop_elite_ahead({"payload": {"elite_ahead": False}}, None) is False
