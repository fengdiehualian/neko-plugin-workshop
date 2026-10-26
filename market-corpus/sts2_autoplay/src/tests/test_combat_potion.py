"""战斗中用药水：预计损血 > 8 就鼓励喝；会死则无条件喝；同一回合只喝一瓶。

规格：docs/shop_scoring.md 「八」（玩家口径 2026-10-07：「还有每次对战如果损血大于8就鼓励使用药水」）。
覆盖「六、测试矩阵」的 13~18 条，外加服务层的接线（抢在战斗计划前面 + 记账 + 日志）。
字段名对齐 mod 的 /state：raw_state.combat.player.{current_hp,max_hp,block}、
raw_state.combat.enemies[].intents[].total_damage、raw_state.run.potions[]。
"""
from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import (
    COMBAT_POTION_HP_LOSS_THRESHOLD,
    STS2HeuristicPlanner,
)
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class CaptureLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self.lines.append(str(message) % args if args else str(message))

    def debug(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None

    def exception(self, *args: Any, **kwargs: Any) -> None:
        return None

    def text(self) -> str:
        return "\n".join(self.lines)


def _potion(
    index: int,
    potion_id: str,
    name: str,
    *,
    description: str = "",
    can_use: bool = True,
    requires_target: bool = False,
    valid_targets: tuple[int, ...] = (),
) -> dict[str, Any]:
    return {
        "index": index,
        "potion_id": potion_id,
        "name": name,
        "description": description,
        "occupied": True,
        "can_use": can_use,
        "requires_target": requires_target,
        "target_index_space": "enemies" if requires_target else "",
        "valid_target_indices": list(valid_targets),
    }


BLOCK = _potion(0, "BLOCK_POTION", "Block Potion", description="Gain 12 Block. 格挡")
FIRE = _potion(1, "FIRE_POTION", "Fire Potion", description="Deal 20 damage. 伤害")
BLOOD = _potion(2, "BLOOD_POTION", "Blood Potion", description="Heal 20% of your max HP. 回复")
SWIFT = _potion(3, "SWIFT_POTION", "Swift Potion", description="Draw 3 cards. 抽牌")


def _snapshot(
    *,
    incoming: int | None = 12,
    hp: int = 40,
    max_hp: int = 80,
    block: int = 0,
    potions: tuple[dict[str, Any], ...] = (),
    turn: int = 3,
    enemies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if enemies is None:
        if incoming is None:
            # 意图数据缺失：敌人没有 intents[]，也没有 intent_damage。
            enemies = [{"index": 0, "enemy_id": "LOUSE", "current_hp": 20, "max_hp": 20, "is_alive": True}]
        else:
            enemies = [
                {
                    "index": 0,
                    "enemy_id": "LOUSE",
                    "current_hp": 20,
                    "max_hp": 20,
                    "is_alive": True,
                    "intents": [{"intent_type": "Attack", "damage": incoming, "hits": 1, "total_damage": incoming}],
                }
            ]
    return {
        "screen": "COMBAT",
        "in_combat": True,
        "run_id": "RUN1",
        "floor": 7,
        "available_actions": [{"type": "use_potion", "raw": {"name": "use_potion"}}, {"type": "play_card", "raw": {"name": "play_card"}}],
        "raw_state": {
            "turn": turn,
            "combat": {
                "player": {"current_hp": hp, "max_hp": max_hp, "block": block, "energy": 3},
                "hand": [],
                "enemies": enemies,
            },
            "run": {"floor": 7, "current_hp": hp, "max_hp": max_hp, "potions": list(potions)},
        },
    }


def _planner() -> tuple[STS2HeuristicPlanner, CaptureLogger]:
    logger = CaptureLogger()
    return STS2HeuristicPlanner(logger=logger), logger


# ---- 13. 预计损血 12、手里有减伤药水 → 喝 --------------------------------------------------


def test_drinks_defense_potion_when_expected_loss_exceeds_eight() -> None:
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=12, potions=(BLOCK,)))

    assert decision["use"] is True
    assert decision["option_index"] == 0
    assert decision["expected_loss"] == 12
    assert decision["score"] == 88.0, "减伤类最高分"
    assert "预计损血=12 > 8" in decision["log"]


def test_expected_loss_is_incoming_minus_block() -> None:
    planner, _logger = _planner()

    # 20 点意图 - 12 点格挡 = 8 点，**不**超过 8 -> 不喝（边界）。
    decision = planner.decide_combat_potion(_snapshot(incoming=20, block=12, potions=(BLOCK,)))

    assert decision["use"] is False
    assert decision["expected_loss"] == 8
    assert "不喝" in decision["log"]


# ---- 14. 预计损血 12、手里只有抽牌类药水 → 不喝（不当保命手段）------------------------------


def test_draw_potion_is_not_a_life_saver() -> None:
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=12, hp=40, potions=(SWIFT,)))

    assert decision["use"] is False
    assert decision["option_index"] is None
    assert "不当保命手段" in decision["log"]


def test_heal_potion_is_not_drunk_when_the_hit_will_not_kill_and_hp_is_fine() -> None:
    """回血不减少这一下的伤害 -> 平时不因这条规则触发（规格「八、用哪一瓶」第 3 条）。"""
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=12, hp=60, max_hp=80, potions=(BLOOD,)))

    assert decision["use"] is False
    assert "回血" in decision["log"]


# ---- 15. 预计损血 6（未到线）、但会死 → 无条件喝（保命优先）--------------------------------


def test_lethal_hit_drinks_even_below_the_threshold() -> None:
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=6, hp=6, max_hp=80, potions=(BLOCK,)))

    assert decision["use"] is True
    assert decision["lethal"] is True
    assert decision["expected_loss"] == 6 <= COMBAT_POTION_HP_LOSS_THRESHOLD
    assert "无条件用" in decision["log"]


def test_lethal_hit_drinks_even_with_only_a_draw_potion() -> None:
    """保命优先「永远压过其它考虑」：会死的时候连抽牌类也喝（评分最低，只排在保命类后面）。"""
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=6, hp=3, max_hp=80, potions=(SWIFT,)))

    assert decision["use"] is True
    assert decision["score"] == 0.0


def test_lethal_prefers_the_life_saving_potion_over_a_draw_potion() -> None:
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=6, hp=4, max_hp=80, potions=(SWIFT, BLOCK)))

    assert decision["use"] is True
    assert decision["option_index"] == 0, "两瓶都在手里时优先用能抵这一下的那瓶（SWIFT 在下标 0 也不选它）"


# ---- 16. 同一回合第二次触发 → 不重复喝 ----------------------------------------------------


def test_does_not_drink_twice_in_the_same_turn() -> None:
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=12, potions=(BLOCK,)), already_used_this_turn=True)

    assert decision["use"] is False
    assert "本回合已经喝过" in decision["log"]


# ---- 17. 意图数据缺失 → 不触发 ------------------------------------------------------------


def test_missing_intents_do_not_trigger() -> None:
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=None, hp=5, max_hp=80, potions=(BLOCK,)))

    assert decision["use"] is False
    assert "拿不到意图数据" in decision["log"]


def test_no_potions_means_nothing_to_drink() -> None:
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=12, potions=()))

    assert decision["use"] is False
    assert "没有可用的药水" in decision["log"]


def test_unusable_potion_is_skipped() -> None:
    planner, _logger = _planner()
    unusable = dict(BLOCK, can_use=False)

    decision = planner.decide_combat_potion(_snapshot(incoming=12, potions=(unusable,)))

    assert decision["use"] is False


def test_targeted_potion_without_valid_targets_is_skipped() -> None:
    planner, _logger = _planner()
    # 需要目标却没有合法目标：这一瓶现在按不下去。
    no_target = _potion(0, "FIRE_POTION", "Fire Potion", description="Deal 20 damage.", requires_target=True)

    decision = planner.decide_combat_potion(_snapshot(incoming=12, potions=(no_target,)))

    assert decision["use"] is False


def test_targeted_potion_aims_at_the_lowest_hp_enemy() -> None:
    planner, _logger = _planner()
    aimed = _potion(0, "FIRE_POTION", "Fire Potion", description="Deal 20 damage.", requires_target=True, valid_targets=(0, 1))
    enemies = [
        {"index": 0, "enemy_id": "A", "current_hp": 40, "max_hp": 40, "is_alive": True,
         "intents": [{"intent_type": "Attack", "damage": 12, "hits": 1, "total_damage": 12}]},
        {"index": 1, "enemy_id": "B", "current_hp": 9, "max_hp": 40, "is_alive": True,
         "intents": [{"intent_type": "Attack", "damage": 12, "hits": 1, "total_damage": 12}]},
    ]

    decision = planner.decide_combat_potion(_snapshot(incoming=24, hp=40, potions=(aimed,), enemies=enemies))

    assert decision["use"] is True
    assert decision["target_index"] == 1, "挑血最少的那只（能秒就秒）"


# ---- 18. 预计损血 12、手里有减伤与回血各一瓶 → 选减伤（评分更高）---------------------------


def test_defense_potion_beats_heal_potion() -> None:
    planner, _logger = _planner()
    blood = _potion(0, "BLOOD_POTION", "Blood Potion", description="Heal 20% of your max HP. 回复")
    block = _potion(1, "BLOCK_POTION", "Block Potion", description="Gain 12 Block. 格挡")

    decision = planner.decide_combat_potion(_snapshot(incoming=12, hp=60, max_hp=80, potions=(blood, block)))

    assert decision["use"] is True
    assert decision["option_index"] == 1, "减伤(88) 高于回血(不致命时不评)"
    assert decision["category"] == "defense"


def test_heal_potion_is_chosen_when_it_is_the_only_life_saver() -> None:
    planner, _logger = _planner()

    decision = planner.decide_combat_potion(_snapshot(incoming=12, hp=10, max_hp=80, potions=(BLOOD,)))

    assert decision["use"] is True and decision["category"] == "heal"


# ---- 节流：普通小怪战更省 / 精英 Boss 战更愿意喝 -------------------------------------------


def test_trash_fight_uses_a_slightly_higher_threshold() -> None:
    planner, _logger = _planner()
    # 明确的普通小怪战：阈值 8 -> 10；11 点损血仍然喝，9 点就省着。
    planner._note_route_choice({"node_type": "Monster"}, None)

    assert planner._combat_potion_threshold() == pytest.approx(COMBAT_POTION_HP_LOSS_THRESHOLD * 1.25)
    assert planner.decide_combat_potion(_snapshot(incoming=9, potions=(BLOCK,)))["use"] is False
    assert planner.decide_combat_potion(_snapshot(incoming=11, potions=(BLOCK,)))["use"] is True


def test_elite_fight_keeps_the_player_threshold() -> None:
    planner, _logger = _planner()
    planner._note_route_choice({"node_type": "Elite"}, None)

    assert planner.in_elite_or_boss_combat() is True
    assert planner._combat_potion_threshold() == COMBAT_POTION_HP_LOSS_THRESHOLD


def test_unknown_room_keeps_the_player_threshold() -> None:
    """不知道是哪一场（mod 的 enemies[] 没有 elite 标记）-> 按玩家的原话 8 点，绝不擅自抬高。"""
    planner = STS2HeuristicPlanner()

    assert planner._combat_potion_threshold() == COMBAT_POTION_HP_LOSS_THRESHOLD


# ---- 服务层接线 ---------------------------------------------------------------------------


class _DummyClient:
    async def close(self) -> None:
        return None


def _service(logger: CaptureLogger) -> STS2AutoplayService:
    service = STS2AutoplayService(logger, lambda payload: None)
    service._client = _DummyClient()
    return service


@pytest.mark.asyncio
async def test_run_autoplay_step_drinks_before_playing_the_planned_card(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = CaptureLogger()
    service = _service(logger)
    snapshot = _snapshot(incoming=12, potions=(BLOCK,))
    snapshot["agent_operation"] = {"action_type": "play_card", "kwargs": {"card_index": 0}, "source": "mod_solver", "reason": "mod_combat_search"}
    service._state.snapshot = snapshot
    seen: dict[str, Any] = {}

    async def fake_execute(operation: dict[str, Any]) -> dict[str, Any]:
        seen["operation"] = operation
        return {"status": "ok", "message": "ok", "summary": "ok"}

    monkeypatch.setattr(service, "execute_operation", fake_execute)

    await service.run_autoplay_step()

    assert seen["operation"]["action_type"] == "use_potion"
    assert seen["operation"]["kwargs"] == {"option_index": 0}
    assert seen["operation"]["reason"] == "potion_incoming_damage"
    assert "[sts2_potion]" in logger.text()


@pytest.mark.asyncio
async def test_run_autoplay_step_drinks_even_without_a_planned_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    """规划器这一步给不出动作时，该保命还是要保命（用药水这条硬规则不依赖规划结果）。"""
    logger = CaptureLogger()
    service = _service(logger)
    snapshot = _snapshot(incoming=12, hp=6, max_hp=80, potions=(BLOCK,))
    snapshot["agent_operation"] = None
    service._state.snapshot = snapshot
    seen: dict[str, Any] = {}

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
        return {"status": "ok", "snapshot": service._state.snapshot}

    async def fake_execute(operation: dict[str, Any]) -> dict[str, Any]:
        seen["operation"] = operation
        return {"status": "ok", "message": "ok", "summary": "ok"}

    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)
    monkeypatch.setattr(service, "execute_operation", fake_execute)

    await service.run_autoplay_step()

    assert seen["operation"]["action_type"] == "use_potion"
    assert seen["operation"]["reason"] == "potion_lethal_save"


@pytest.mark.asyncio
async def test_run_autoplay_step_does_not_drink_twice_in_the_same_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = CaptureLogger()
    service = _service(logger)
    snapshot = _snapshot(incoming=12, potions=(BLOCK,))
    snapshot["agent_operation"] = {"action_type": "play_card", "kwargs": {"card_index": 0}, "source": "mod_solver", "reason": "mod_combat_search"}
    service._state.snapshot = snapshot
    # 模拟"本回合已经喝过一瓶"。
    service._state.potion_used_turn_key = service._combat_turn_key(snapshot)
    seen: dict[str, Any] = {}

    async def fake_execute(operation: dict[str, Any]) -> dict[str, Any]:
        seen["operation"] = operation
        return {"status": "ok", "message": "ok", "summary": "ok"}

    monkeypatch.setattr(service, "execute_operation", fake_execute)

    await service.run_autoplay_step()

    assert seen["operation"]["action_type"] == "play_card", "同一回合只喝一瓶"
    assert "本回合已经喝过" in logger.text()


@pytest.mark.asyncio
async def test_run_autoplay_step_leaves_combat_alone_without_use_potion(monkeypatch: pytest.MonkeyPatch) -> None:
    """`use_potion` 不在册（没药水/非战斗）时不许抢步，一切照旧。"""
    logger = CaptureLogger()
    service = _service(logger)
    snapshot = _snapshot(incoming=12, potions=(BLOCK,))
    snapshot["available_actions"] = [{"type": "play_card", "raw": {"name": "play_card"}}]
    snapshot["agent_operation"] = {"action_type": "play_card", "kwargs": {"card_index": 0}, "source": "mod_solver", "reason": "mod_combat_search"}
    service._state.snapshot = snapshot
    seen: dict[str, Any] = {}

    async def fake_execute(operation: dict[str, Any]) -> dict[str, Any]:
        seen["operation"] = operation
        return {"status": "ok", "message": "ok", "summary": "ok"}

    monkeypatch.setattr(service, "execute_operation", fake_execute)

    await service.run_autoplay_step()

    assert seen["operation"]["action_type"] == "play_card"


@pytest.mark.asyncio
async def test_execute_operation_records_the_potion_turn_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """记账由**真的喝下去**推动（与选牌光标/领奖励同一个道理）。"""
    logger = CaptureLogger()
    service = _service(logger)
    snapshot = _snapshot(incoming=12, potions=(BLOCK,))
    service._state.snapshot = snapshot

    async def fake_execute(client: Any, snapshot: dict[str, Any], operation: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "status": "ok",
            "operation": {"action_type": "use_potion", "kwargs": {"option_index": 0}, "source": "heuristic", "reason": "potion_incoming_damage"},
        }

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict[str, Any]:
        return {"status": "ok", "snapshot": service._state.snapshot}

    monkeypatch.setattr(service._action_engine, "execute", fake_execute)
    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)

    result = await service.execute_operation({"action_type": "use_potion", "kwargs": {"option_index": 0}})

    assert result["status"] == "ok"
    assert service._state.potion_used_turn_key == service._combat_turn_key(service._state.snapshot)
    assert service._state.potion_used_turn_key.endswith("|3"), "键里带回合号"

    # 换了回合（turn 3 -> 4）之后又能喝了。
    next_turn = _snapshot(incoming=12, potions=(BLOCK,), turn=4)
    assert service._combat_turn_key(next_turn) != service._state.potion_used_turn_key


def test_combat_turn_key_separates_two_battles() -> None:
    """只用回合号会把两场战斗混成一场（回合 1 vs 回合 1），所以键里带层与房间。"""
    logger = CaptureLogger()
    service = _service(logger)
    first = _snapshot(incoming=12, turn=1)
    second = _snapshot(incoming=12, turn=1)
    second["floor"] = 9

    assert service._combat_turn_key(first) != service._combat_turn_key(second)
