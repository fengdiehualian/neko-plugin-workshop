"""局末总结与反思：损血归因 → 战报 → 本体复盘 → 落盘并影响下一局。

用户口径："每次打完做一次总结（**每局结束一次**）…… 看看哪些地方损血导致失败，
下一次根据情况做出更好的选择"，反思"插件生成 + 也告诉本体"。

顺带修掉一个从来没生效过的东西：`catgirl_memory.summarize_run` 里那条"LLM 自然语言归纳"
是**同步**调用 `llm.generate(...)`（`async` 方法），拿到协程后 `.splitlines()` 抛错被吞；
外面还有 `self._engine == "llm"` 这道默认关闭的闸门 —— 那条路一直是死的。现在改成
service 事后异步生成、再 `attach_reflection` 贴回同一条记录。
"""

from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore
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


def _seed_trail(service: STS2AutoplayService, samples: list[tuple[int, str, int]]) -> None:
    service._state.hp_trail = [
        {"hp": hp, "max_hp": 80, "screen": screen, "floor": floor, "step_count": index, "at": float(index)}
        for index, (hp, screen, floor) in enumerate(samples)
    ]


# ---- 损血归因 ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hp_loss_report_finds_the_heaviest_hits() -> None:
    """**"看看哪些地方损血导致失败"**：要把单次掉血最多的几处连界面/层级一起报出来。"""
    service = _service()
    _seed_trail(
        service,
        [(80, "MAP", 3), (62, "COMBAT", 4), (55, "COMBAT", 4), (13, "COMBAT", 6), (2, "COMBAT", 7)],
    )

    report = service._hp_loss_report()

    assert report["total_lost"] == 18 + 7 + 42 + 11
    assert report["loss_events"] == 4
    assert report["min_hp"] == 2
    assert report["final_hp"] == 2
    assert report["heaviest"][0]["lost"] == 42
    assert report["heaviest"][0]["screen"] == "COMBAT"
    # 界面/层级按"观测到掉血的那一刻"记（掉血发生在两次采样之间那场战斗里）。
    assert report["heaviest"][0]["floor"] == 6


@pytest.mark.unit
def test_hp_loss_report_is_empty_without_samples() -> None:
    assert _service()._hp_loss_report() == {}


@pytest.mark.unit
def test_hp_trail_resets_when_the_run_changes() -> None:
    """换局必须清零，否则上一局的损血会算到这一局头上。"""
    service = _service()
    service._remember_hp_sample({"run_id": "run-a", "screen": "COMBAT", "floor": 2, "raw_state": {"combat": {"player": {"current_hp": 50, "max_hp": 80}}}})
    assert len(service._state.hp_trail) == 1

    service._remember_hp_sample({"run_id": "run-b", "screen": "COMBAT", "floor": 1, "raw_state": {"combat": {"player": {"current_hp": 80, "max_hp": 80}}}})

    assert len(service._state.hp_trail) == 1
    assert service._state.hp_trail[0]["hp"] == 80


@pytest.mark.unit
def test_hp_sample_is_not_duplicated_when_nothing_changes() -> None:
    service = _service()
    snapshot = {"run_id": "run-a", "screen": "COMBAT", "floor": 2, "raw_state": {"combat": {"player": {"current_hp": 50, "max_hp": 80}}}}

    service._remember_hp_sample(snapshot)
    service._remember_hp_sample(snapshot)

    assert len(service._state.hp_trail) == 1


# ---- 战报与反思 -------------------------------------------------------------------------


@pytest.mark.unit
def test_run_summary_digest_carries_outcome_character_and_losses() -> None:
    service = _service()
    _seed_trail(service, [(80, "COMBAT", 6), (38, "COMBAT", 6), (0, "COMBAT", 7)])
    report = service._hp_loss_report()
    record = {
        "run_id": "run-a",
        "outcome": "defeat",
        "character": "绯刃战姬",
        "floor": 7,
        "lessons": ["血量过低时别贪输出"],
        "decisions": [{"screen": "COMBAT", "action_type": "play_card"}],
    }

    digest = service._run_summary_digest(record, report)

    assert "defeat" in digest and "绯刃战姬" in digest and "第7层" in digest
    assert "共损血80点(2次)" in digest
    # 归因以**房间标识**为主，不是层数（尖塔地图随机，层数跨局不可复用）。
    assert "最痛:「COMBAT」-42(80->38)" in digest
    assert "血量过低时别贪输出" in digest


@pytest.mark.unit
def test_reflect_run_end_stores_reflection_pushes_danmaku_and_tells_host(tmp_path) -> None:
    """反思要落盘（并成为一条教训）、作为弹幕说给玩家、同时推给本体。"""
    host: list[dict] = []
    pushed: list[str] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: host.append(k))
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    record = service._catgirl_memory.add_run(
        "run-a", character="绯刃战姬", outcome="defeat", floor=7, act=1,
        decision_memory=[{"screen": "COMBAT", "action_type": "play_card", "decision_reason": "贪输出"}],
        hp_report={"total_lost": 42, "loss_events": 1, "min_hp": 0, "max_hp": 80, "heaviest": []},
    )

    class Gen:
        available = True

        async def generate(self, **kw):
            assert kw["summary_kind"] == "run_summary"
            return "第6层硬顶那只精英掉了42血喵，下次血不过半就先垫格挡再打。"

    service._catgirl_llm = Gen()
    service._danmaku_enabled = True
    service._schedule_catgirl_push = lambda text: pushed.append(text)

    asyncio.run(service._reflect_run_end(dict(record), record["hp"]))

    rows = service._catgirl_memory.runs
    assert rows and rows[-1]["reflection"].startswith("第6层硬顶")
    assert any("下次血不过半" in str(item) for item in rows[-1]["lessons"]), "反思要作为教训参与下一局策略"
    assert pushed and "下次血不过半" in pushed[0]
    # 推给本体：不可见 + 不进记忆 + 让它主动开口（玩家口径："复盘也是不可见"）。
    assert host and host[-1]["visibility"] == []
    assert host[-1]["ai_behavior"] == "respond"
    assert host[-1]["metadata"]["prompt_ephemeral"] is True
    assert "复盘要点" in host[-1]["content"]


@pytest.mark.unit
def test_reflect_run_end_survives_a_silent_llm(tmp_path) -> None:
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())

    class Gen:
        available = True

        async def generate(self, **kw):
            return None

    service._catgirl_llm = Gen()
    record = {"run_id": "run-a", "outcome": "defeat", "character": "C", "floor": 3, "lessons": [], "decisions": []}

    asyncio.run(service._reflect_run_end(dict(record), {}))  # 不应抛异常

    assert service._catgirl_memory.runs == [], "没产出反思就不该改动记忆"


# ---- 房间标识：复盘必须按"哪个房间"，不是"第几层" --------------------------------------


@pytest.mark.unit
def test_room_identity_uses_enemy_composition_for_combat() -> None:
    """**"复盘说第 N 层没有用"的回归测试**：尖塔地图随机，同一层在不同局是不同的怪。

    mod 没有 encounter/room id，能给的是 `combat.enemies[].enemy_id`，所以战斗用**敌人组合**当房间标识。
    """
    raw = {
        "combat": {
            "enemies": [
                {"enemy_id": "LEAF_SLIME_M", "is_alive": True},
                {"enemy_id": "LEAF_SLIME_S", "is_alive": True},
            ]
        }
    }

    assert STS2AutoplayService._room_identity("COMBAT", raw) == "LEAF_SLIME_M+LEAF_SLIME_S"


@pytest.mark.unit
def test_room_identity_prefers_event_id_then_room_type() -> None:
    assert STS2AutoplayService._room_identity("EVENT", {"event": {"event_id": "THE_CLAW"}}) == "THE_CLAW"
    assert STS2AutoplayService._room_identity("SHOP", {}) == "room:SHOP"
    assert STS2AutoplayService._room_identity("REST", {}) == "room:REST"


@pytest.mark.unit
def test_room_identity_is_pinned_at_the_start_of_a_fight() -> None:
    """战斗中途敌人会死；若每帧重取，同一场战斗会被拆成几个"房间"，归因就散了。"""
    service = _service()
    start = {
        "run_id": "run-a",
        "screen": "COMBAT",
        "in_combat": True,
        "floor": 3,
        "raw_state": {
            "combat": {
                "player": {"current_hp": 70, "max_hp": 80},
                "enemies": [{"enemy_id": "A", "is_alive": True}, {"enemy_id": "B", "is_alive": True}],
            }
        },
    }

    service._remember_hp_sample(start)
    assert service._state.combat_room_id == "A+B"

    # 打死一只之后：HP 变了要记新采样，但房间标识仍是这场遭遇开始时的组合。
    later = {
        "run_id": "run-a",
        "screen": "COMBAT",
        "in_combat": True,
        "floor": 3,
        "raw_state": {
            "combat": {
                "player": {"current_hp": 55, "max_hp": 80},
                "enemies": [{"enemy_id": "A", "is_alive": True}, {"enemy_id": "B", "is_alive": False}],
            }
        },
    }
    service._remember_hp_sample(later)

    rooms = [sample["room"] for sample in service._state.hp_trail]
    assert rooms == ["A+B", "A+B"], "同一场战斗必须共用一个房间标识"
    report = service._hp_loss_report()
    assert report["heaviest"][0]["room"] == "A+B"


@pytest.mark.unit
def test_room_identity_resets_when_the_fight_ends() -> None:
    service = _service()
    service._remember_hp_sample(
        {
            "run_id": "run-a", "screen": "COMBAT", "in_combat": True, "floor": 3,
            "raw_state": {"combat": {"player": {"current_hp": 70, "max_hp": 80}, "enemies": [{"enemy_id": "A", "is_alive": True}]}},
        }
    )
    service._remember_hp_sample(
        {
            "run_id": "run-a", "screen": "REWARD", "in_combat": False, "floor": 3,
            "raw_state": {"run": {"current_hp": 70, "max_hp": 80}},
        }
    )

    assert service._state.combat_room_id == ""
    assert service._state.hp_trail[-1]["room"] == "room:REWARD"


@pytest.mark.unit
def test_loss_attribution_carries_the_room_not_just_the_floor() -> None:
    service = _service()
    service._state.hp_trail = [
        {"hp": 80, "max_hp": 80, "screen": "COMBAT", "room": "毛绒伏地虫", "floor": 6, "step_count": 1, "at": 0.0},
        {"hp": 38, "max_hp": 80, "screen": "COMBAT", "room": "毛绒伏地虫", "floor": 6, "step_count": 2, "at": 1.0},
    ]

    heaviest = service._hp_loss_report()["heaviest"][0]

    assert heaviest["room"] == "毛绒伏地虫"
    assert heaviest["lost"] == 42


# ---- 记忆存储本身 -----------------------------------------------------------------------


@pytest.mark.unit
def test_add_run_records_hp_report_and_decisions(tmp_path) -> None:
    store = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())

    record = store.add_run(
        "run-a", character="C", outcome="defeat", floor=7, act=1,
        decision_memory=[{"screen": "COMBAT", "action_type": "play_card", "decision_reason": "贪"}],
        hp_report={"total_lost": 42},
    )

    assert record["hp"]["total_lost"] == 42
    assert record["decisions"][0]["action_type"] == "play_card"
    assert record["reflection"] == ""


@pytest.mark.unit
def test_attach_reflection_is_idempotent_and_feeds_summarize_recent(tmp_path) -> None:
    store = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    store.add_run("run-a", character="C", outcome="defeat", floor=7, act=1, decision_memory=[])

    store.attach_reflection("run-a", "下次先垫格挡喵")
    store.attach_reflection("run-a", "下次先垫格挡喵")

    rows = store.runs
    assert rows[-1]["reflection"] == "下次先垫格挡喵"
    assert [item for item in rows[-1]["lessons"] if item == "下次先垫格挡喵"] == ["下次先垫格挡喵"]
    assert "下次先垫格挡喵" in store.summarize_recent()["lessons"]


@pytest.mark.unit
def test_attach_reflection_ignores_unknown_run() -> None:
    store = CatgirlMemoryStore(path=None, logger=DummyLogger())

    assert store.attach_reflection("nope", "无所谓") is None


# ---- 真实触发路径：终端屏 -> 汇总 -> 落盘 -> 写历史快照 ----------------------------------


@pytest.mark.unit
def test_terminal_screen_triggers_run_record_and_history_snapshot(tmp_path, monkeypatch) -> None:
    """**把"终端屏"这条真实入口整条串起来测。**

    上面那些测试是分别打各块的（损血归因 / 落盘 / 反思 / 历史快照），但把它们**接线**起来的
    `_maybe_record_run_memory` 才是真正被线上调用的那一处 —— 这里从"分类为 terminal 的快照"
    一路验到"记忆里有这条记录 + 历史快照文件被写出来"。
    """
    import plugin.plugins.sts2_autoplay.service as service_module

    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    service._state.recent_decision_memory = [
        {"screen": "COMBAT", "action_type": "play_card", "decision_reason": "贪输出", "step_count": 12}
    ]
    _seed_trail(service, [(80, "COMBAT", 6), (38, "COMBAT", 6), (0, "COMBAT", 7)])

    written: list[str] = []
    # service.py 是 `from .history_view import write_history_view` 的模块级绑定，
    # 所以要补 service 里的那个名字，而不是 history_view 里的。
    monkeypatch.setattr(
        service_module, "write_history_view",
        lambda runs, **kw: written.append("called") or str(tmp_path / "snap.json"),
    )

    class Gen:
        available = True

        async def generate(self, **kw):
            return "下次血不过半先垫格挡喵。"

    service._catgirl_llm = Gen()
    service._schedule_catgirl_push = lambda text, **kw: None

    snapshot = {
        "classification": {"screen_class": "terminal"},
        "screen": "GAME_OVER",
        "run_id": "run-e2e",
        "character": "IRONCLAAD",
        "floor": 7,
        "act": 1,
        "raw_state": {"run": {"character_id": "IRONCLAD", "character_name": "绯刃战姬"}},
    }

    async def drive() -> None:
        service._maybe_record_run_memory(snapshot)
        # 让 fire-and-forget 的反思任务跑完
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(drive())

    rows = service._catgirl_memory.runs
    assert len(rows) == 1, "终端屏必须立刻落一条本局记录"
    assert rows[0]["run_id"] == "run-e2e"
    assert rows[0]["outcome"] == "defeat"
    assert rows[0]["hp"]["total_lost"] == 80, "损血必须来自本局生命轨迹"
    assert rows[0]["decisions"][0]["action_type"] == "play_card"
    # 会被写两次：局末落记录时一次、异步反思贴回后再一次（让历史页也能看到反思）。
    assert written, "局末必须刷新历史快照"
    assert rows[0]["reflection"].startswith("下次血不过半"), "异步反思要贴回同一条记录"


@pytest.mark.unit
def test_terminal_screen_is_recorded_only_once_per_run(tmp_path) -> None:
    """终端屏会反复出现在快照里，同一局只能记一次（否则记忆里堆一堆重复记录）。"""
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    snapshot = {
        "classification": {"screen_class": "terminal"},
        "screen": "GAME_OVER",
        "run_id": "run-once",
        "character": "C",
        "floor": 3,
        "act": 1,
        "raw_state": {},
    }

    async def drive() -> None:
        service._maybe_record_run_memory(snapshot)
        service._maybe_record_run_memory(snapshot)

    asyncio.run(drive())

    assert len(service._catgirl_memory.runs) == 1


@pytest.mark.unit
def test_non_terminal_screen_does_not_record_a_run(tmp_path) -> None:
    service = _service()
    service._catgirl_memory = CatgirlMemoryStore(path=str(tmp_path / "mem.json"), logger=DummyLogger())
    snapshot = {"classification": {"screen_class": "combat"}, "screen": "COMBAT", "run_id": "run-x", "raw_state": {}}

    asyncio.run(service._maybe_record_run_memory(snapshot) or asyncio.sleep(0))

    assert service._catgirl_memory.runs == []
