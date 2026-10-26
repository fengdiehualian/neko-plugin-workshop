"""路线图：逐房间的损血与收获。

玩家口径：「需要路线图，对应每个房间的损血和收获」。所以每局结束的记录里要有一条按访问顺序的
路线，每个房间写清：掉了多少血、拿了多少金币、新增了哪些卡/遗物/药水。

两个判定要点（都有测试钉住）：

* **奖励类界面并入上一个房间** —— 奖励是"这个房间打完之后"发的（战斗 → REWARD →
  选牌 → REWARD → MAP），另起一段就看不出"这个房间值不值"。
* **房间标识用敌人组合/事件 id，不是层数** —— 地图随机，层数跨局不可复用。
"""

from __future__ import annotations

from plugin.plugins.sts2_autoplay.history_view import build_history_payload
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


def _service() -> STS2AutoplayService:
    return STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: None)


def _snap(
    *,
    screen: str,
    floor: int,
    hp: int,
    gold: int = 0,
    enemies: list[str] | None = None,
    deck: list[str] | None = None,
    relics: list[str] | None = None,
    potions: list[str] | None = None,
    event_id: str = "",
    run_id: str = "run-a",
) -> dict:
    raw: dict = {
        "run": {
            "current_hp": hp,
            "max_hp": 80,
            "gold": gold,
            "deck": [{"index": i, "card_id": cid} for i, cid in enumerate(deck or [])],
            "relics": [{"index": i, "relic_id": rid} for i, rid in enumerate(relics or [])],
            "potions": [{"index": i, "potion_id": pid} for i, pid in enumerate(potions or [])],
        }
    }
    if enemies:
        raw["combat"] = {
            "player": {"current_hp": hp, "max_hp": 80},
            "enemies": [{"enemy_id": eid, "is_alive": True} for eid in enemies],
        }
    if event_id:
        raw["event"] = {"event_id": event_id}
    return {
        "screen": screen,
        "floor": floor,
        "in_combat": bool(enemies),
        "run_id": run_id,
        "raw_state": raw,
    }


def _step(service: STS2AutoplayService, snapshot: dict) -> None:
    """模拟 refresh_state 的顺序：先记生命采样，再记路线。"""
    service._remember_hp_sample(snapshot)
    service._remember_route_step(snapshot)


# ---- 路线图内容 -------------------------------------------------------------------------


def test_route_records_hp_loss_and_gains_per_room() -> None:
    service = _service()

    # 战斗房：两只史莱姆，血 80 -> 55，金币 +12，打完后奖励里拿了一张牌
    _step(service, _snap(screen="COMBAT", floor=3, hp=80, gold=0, enemies=["LEAF_SLIME_M", "LEAF_SLIME_S"], deck=["STRIKE"]))
    _step(service, _snap(screen="COMBAT", floor=3, hp=55, gold=12, enemies=["LEAF_SLIME_M", "LEAF_SLIME_S"], deck=["STRIKE"]))
    _step(service, _snap(screen="REWARD", floor=3, hp=55, gold=12, deck=["STRIKE"]))
    _step(service, _snap(screen="REWARD", floor=3, hp=55, gold=12, deck=["STRIKE", "BASH"]))
    # 换到事件房 -> 收尾战斗房
    _step(service, _snap(screen="EVENT", floor=4, hp=55, gold=12, event_id="THE_CLAW", deck=["STRIKE", "BASH"]))

    service._close_route_room(reason="test")
    route = service._state.route_log

    combat = route[0]
    assert combat["room"] == "LEAF_SLIME_M+LEAF_SLIME_S", "房间标识应是敌人组合"
    assert combat["screen"] == "COMBAT"
    assert combat["hp_start"] == 80 and combat["lowest_hp"] == 55
    assert combat["hp_lost"] == 25
    assert combat["gold_gained"] == 12
    assert combat["cards_gained"] == ["BASH"], "奖励里拿到的牌应算进这个房间的收获"


def test_reward_screens_merge_into_the_previous_room() -> None:
    """战斗 → REWARD → 选牌 → REWARD 属于**同一个房间**，不能拆成四段。"""
    service = _service()
    _step(service, _snap(screen="COMBAT", floor=3, hp=80, gold=0, enemies=["A"]))
    _step(service, _snap(screen="REWARD", floor=3, hp=80, gold=0))
    _step(service, _snap(screen="CARD_SELECTION_REWARD", floor=3, hp=80, gold=0))
    _step(service, _snap(screen="REWARD", floor=3, hp=80, gold=0))
    _step(service, _snap(screen="EVENT", floor=4, hp=80, gold=0, event_id="X"))

    route = service._state.route_log
    assert len(route) == 1, f"应只有 1 个房间，实际 {[item['screen'] for item in route]}"
    assert route[0]["screen"] == "COMBAT"


def test_event_room_is_keyed_by_event_id() -> None:
    service = _service()
    _step(service, _snap(screen="EVENT", floor=5, hp=70, gold=0, event_id="THE_CLAW"))
    _step(service, _snap(screen="EVENT", floor=5, hp=62, gold=0, event_id="THE_CLAW"))
    _step(service, _snap(screen="MAP", floor=5, hp=62, gold=0))

    route = service._state.route_log
    assert route and route[0]["room"] == "THE_CLAW"
    assert route[0]["hp_lost"] == 8


def test_map_hops_are_not_recorded_as_rooms() -> None:
    """地图上走一格是过渡，不是房间（否则路线图全是噪声）。"""
    service = _service()
    _step(service, _snap(screen="MAP", floor=3, hp=80, gold=0))
    _step(service, _snap(screen="MAP", floor=4, hp=80, gold=0))

    assert service._state.route_log == []


def test_route_records_relics_and_potions() -> None:
    service = _service()
    _step(service, _snap(screen="CHEST", floor=6, hp=70, gold=0, relics=[], potions=[]))
    _step(service, _snap(screen="CHEST", floor=6, hp=70, gold=0, relics=["ANCHOR"], potions=["FLEX_POTION"]))
    _step(service, _snap(screen="MAP", floor=6, hp=70, gold=0))

    route = service._state.route_log
    assert route and route[0]["relics_gained"] == ["ANCHOR"]
    assert route[0]["potions_gained"] == ["FLEX_POTION"]


def test_route_resets_when_the_run_changes() -> None:
    service = _service()
    _step(service, _snap(screen="COMBAT", floor=3, hp=80, gold=0, enemies=["A"], run_id="run-a"))
    _step(service, _snap(screen="COMBAT", floor=3, hp=60, gold=0, enemies=["A"], run_id="run-a"))
    assert len(service._state.route_log) == 0  # 还没换房间，未收尾

    _step(service, _snap(screen="COMBAT", floor=1, hp=80, gold=0, enemies=["B"], run_id="run-b"))

    assert service._state.route_room_key.endswith("|B")
    assert service._state.route_start.get("hp") == 80


# ---- 落盘与展示 -------------------------------------------------------------------------


def test_history_payload_and_memory_carry_the_route(tmp_path) -> None:
    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    store = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    route = [
        {
            "room": "LEAF_SLIME_M+LEAF_SLIME_S",
            "screen": "COMBAT",
            "floor": 3,
            "hp_start": 80,
            "hp_end": 55,
            "lowest_hp": 55,
            "hp_lost": 25,
            "gold_gained": 12,
            "cards_gained": ["BASH"],
            "relics_gained": [],
            "potions_gained": [],
        }
    ]
    store.add_run("run-a", character="C", outcome="defeat", floor=9, act=1, decision_memory=[], route=route)

    payload = build_history_payload(store.runs)
    step = payload["runs"][0]["route"][0]

    assert step["room"] == "LEAF_SLIME_M+LEAF_SLIME_S"
    assert step["hp_lost"] == 25 and step["gold_gained"] == 12
    assert step["cards_gained"] == ["BASH"]


def test_history_page_renders_the_route_map() -> None:
    """历史页必须有路线图（一行式序列 + 逐房间表格）。"""
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "static" / "history.html").read_text(encoding="utf-8")

    assert "renderRoute" in html
    assert "路线图（每个房间的损血与收获）" in html
    assert "cards_gained" in html and "relics_gained" in html and "hp_lost" in html
