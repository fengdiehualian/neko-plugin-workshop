"""「听安排」档位（advise）：只算不做，等玩家发话。

三条硬约束都是"会让游戏卡死"的地方，所以单独钉住：

* **战斗不停手** —— 玩家替不了猫娘出牌，停手会把回合永久卡住；
* **主菜单 / 组队大厅 / 选角色不停手** —— 那是猫娘加入房间的必经流程，停手就根本进不了局；
* **认不出的界面不停手** —— 宁可没听成安排，也不能让 mod 意外卡死。

另外 ``hold``（插件的 standby）语义不同：除上述"进局流程"外**全部**停手，**包括战斗**——
回合会停住，这是该档位的预期行为，不是 bug。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.mode_controller import STS2ModeController
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService
from plugin.plugins.sts2_autoplay.state_machine import (
    ADVISE_HOLD_SCREENS,
    HOLD_NEVER_SCREENS,
    should_hold_for_orders,
)


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
    return STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)


def _snapshot(state_name: str) -> dict[str, Any]:
    return {
        "classification": {"state_name": state_name},
        "agent_operation": {"action_type": "choose_map_node", "kwargs": {"option_index": 1}},
    }


# ---- 界面清单 -----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "screen",
    [
        "map",
        "event",
        "reward",
        "card_selection",
        "card_selection_reward",
        "shop",
        "shop_show",
        "rest",
        "chest",
        "bundle_selection",
        "capstone_selection",
        "relic_selection",
    ],
)
def test_advise_holds_on_non_combat_decision_screens(screen: str) -> None:
    assert should_hold_for_orders(screen, mode="advise") is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "screen",
    ["combat", "main_menu", "multiplayer_lobby", "character_select", "timeline", "unknown", ""],
)
def test_advise_never_holds_in_combat_or_the_join_flow(screen: str) -> None:
    assert should_hold_for_orders(screen, mode="advise") is False


@pytest.mark.unit
def test_combat_is_absent_from_the_advise_list() -> None:
    """战斗一旦进了停手清单，玩家替不了猫娘出牌，回合就永久卡住。"""
    assert "combat" not in ADVISE_HOLD_SCREENS


@pytest.mark.unit
def test_the_join_flow_is_never_held_in_any_mode() -> None:
    for screen in HOLD_NEVER_SCREENS:
        assert should_hold_for_orders(screen, mode="advise") is False, screen
        assert should_hold_for_orders(screen, mode="hold") is False, screen


@pytest.mark.unit
def test_hold_mode_stops_everything_except_the_join_flow() -> None:
    assert should_hold_for_orders("combat", mode="hold") is True
    assert should_hold_for_orders("map", mode="hold") is True
    assert should_hold_for_orders("card_selection", mode="hold") is True


@pytest.mark.unit
def test_program_mode_never_holds() -> None:
    for screen in ("map", "combat", "reward", "event"):
        assert should_hold_for_orders(screen, mode="program") is False, screen


# ---- 档位别名与能力 -----------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("program", "program"),
        ("自己玩", "program"),
        ("你自己玩", "program"),
        ("auto", "program"),
        ("advise", "advise"),
        ("听安排", "advise"),
        ("听我安排", "advise"),
        ("等我发话", "advise"),
        ("standby", "standby"),
        ("停手", "standby"),
        # 未知取值退化为自己玩：写错配置不该让猫娘莫名其妙地停手卡死。
        ("胡说八道", "program"),
        ("", "program"),
    ],
)
def test_control_mode_aliases(raw: str, expected: str) -> None:
    assert STS2ModeController().normalize(raw) == expected


@pytest.mark.unit
def test_advise_still_plans_but_must_not_execute() -> None:
    info = STS2ModeController().describe("advise")
    assert info["advise"] is True
    # 还要算：玩家问"你打算怎么走"得答得出来。
    assert info["allows_planner"] is True
    # 但不许自己动手。
    assert info["allows_execute"] is False


@pytest.mark.unit
def test_standby_stops_planning_too() -> None:
    info = STS2ModeController().describe("standby")
    assert info["allows_planner"] is False
    assert info["allows_execute"] is False


# ---- 执行闸门 -----------------------------------------------------------------------


@pytest.mark.unit
def test_autoplay_step_holds_on_a_map_screen_under_advise() -> None:
    service = _service()
    service._apply_control_mode("advise")
    service._state.snapshot = _snapshot("map")
    result = asyncio.run(service.run_autoplay_step())
    assert result["status"] == "idle"
    assert result.get("held") is True
    assert result["state_name"] == "map"


@pytest.mark.unit
def test_autoplay_step_does_not_hold_in_combat_under_advise() -> None:
    service = _service()
    service._apply_control_mode("advise")
    service._state.snapshot = _snapshot("combat")
    assert service._should_hold_for_orders(service._state.snapshot) is False


@pytest.mark.unit
def test_explicit_authorisation_bypasses_the_hold() -> None:
    """`sts2_execute_planned_operation` 是"我点头了"，必须能越过停手闸门。"""
    service = _service()
    service._apply_control_mode("advise")
    # 切档会置 interrupt，若不手动清掉，下一步会先去 refresh_state（需要真 client）。
    service._state.interrupt_requested = False
    service._state.snapshot = _snapshot("map")
    assert service._should_hold_for_orders(service._state.snapshot) is True

    seen: dict[str, Any] = {}

    async def fake_execute(operation: Any) -> dict[str, Any]:
        seen["operation"] = operation
        return {"status": "ok", "message": "executed"}

    service.execute_operation = fake_execute  # type: ignore[method-assign]
    result = asyncio.run(service.run_autoplay_step(force=True))
    assert seen.get("operation") is not None
    assert result.get("held") is None


@pytest.mark.unit
def test_switching_mode_interrupts_the_pending_decision() -> None:
    """切档必须打断已算好的那一步，否则切到"听安排"却仍然走了一步。"""
    service = _service()
    service._state.interrupt_requested = False
    service._apply_control_mode("advise")
    assert service._state.interrupt_requested is True
    assert service._state.control_mode == "advise"
