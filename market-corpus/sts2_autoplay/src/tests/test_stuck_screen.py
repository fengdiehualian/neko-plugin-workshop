"""拿不到可执行动作时**不许静默空转**。

**实盘证据（2026-10-07）**：mod 认不出的界面（水晶球事件的"占卜"子界面）只暴露 `save_and_quit`，
插件循环每 0.5 秒空转一次、`step_count` 冻在 2395，**整整 2 小时 40 分**，而插件日志里
一句都没有 —— 玩家只能靠自己发现"它不动了"。

三件事必须同时成立：

1. 同一个「卡住指纹」只记**一次** WARNING（带未识别界面类型名，方便精确加 switch 分支）；
2. 写进状态（`stuck_reason`），状态查询/前端看得到；
3. 按「尖塔速报」开关推**一次**给本体；而且**绝不改 autoplay 意图** —— 玩家没喊停就不许自己停。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

_STUCK_TYPE = "MegaCrit.Sts2.Core.Nodes.Screens.CrystalSphereScreen"


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def error(self, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self.warnings.append(str(args[0]) % tuple(args[1:]) if len(args) > 1 else str(args[0]))


def _service() -> tuple[STS2AutoplayService, _Logger]:
    logger = _Logger()
    return STS2AutoplayService(logger, lambda payload: None, lambda **kwargs: None), logger


def _stuck_snapshot(*, screen: str = "UNKNOWN", unknown: str = _STUCK_TYPE, actions: tuple[str, ...] = ("save_and_quit",)) -> dict[str, Any]:
    return {
        "screen": screen,
        "raw_state": {
            "screen": screen,
            "unknown_screen_type": unknown,
            "available_actions": list(actions),
        },
    }


def _prepare(monkeypatch: pytest.MonkeyPatch, service: STS2AutoplayService) -> dict[str, list[Any]]:
    """把一步自动玩里所有会碰网络/事件循环的东西摁掉，只留"拿不到动作"这条路。"""
    seen: dict[str, list[Any]] = {"host": [], "status": []}

    async def _noop_refresh(**kwargs: Any) -> None:
        return None

    async def _fast_sleep(*args: Any, **kwargs: Any) -> None:
        return None

    service._state.transport_state = "connected"
    monkeypatch.setattr(service, "_should_hold_for_orders", lambda snapshot: False)
    monkeypatch.setattr(service, "_should_rebuild_operation", lambda snapshot: False)
    monkeypatch.setattr(service, "refresh_state", _noop_refresh)
    monkeypatch.setattr(service, "_emit_status", lambda: seen["status"].append(1))
    monkeypatch.setattr(service, "_push_proactive_to_host", lambda text, **kwargs: seen["host"].append(text))
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    return seen


def _step(service: STS2AutoplayService, snapshot: dict[str, Any]) -> dict[str, Any]:
    service._state.snapshot = snapshot
    return asyncio.run(service.run_autoplay_step())


@pytest.mark.unit
def test_cross_screen_gap_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """跨屏切换时短暂拿不到动作是正常的，不许报"卡住"。"""
    service, logger = _service()
    _prepare(monkeypatch, service)
    snapshot = _stuck_snapshot()

    _step(service, snapshot)
    _step(service, snapshot)

    assert logger.warnings == []
    assert service._state.stuck_reason == ""
    assert service._state.stuck_signature, "指纹要记下来，才谈得上'持续多久'"


@pytest.mark.unit
def test_persistent_stall_is_reported_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """实盘回归：持续卡住必须**报一次**，而且带未识别界面类型名。"""
    service, logger = _service()
    seen = _prepare(monkeypatch, service)
    snapshot = _stuck_snapshot()

    _step(service, snapshot)
    service._state.stuck_since -= 30  # 假装已经等了 30 秒
    _step(service, snapshot)

    assert len(logger.warnings) == 1, logger.warnings
    assert "sts2_stuck" in logger.warnings[0]
    assert "UNKNOWN" in logger.warnings[0]
    assert _STUCK_TYPE in logger.warnings[0], "要报出到底是哪个界面类型认不出来"
    assert "save_and_quit" in logger.warnings[0]
    assert _STUCK_TYPE in service._state.stuck_reason
    assert seen["host"], "要让本体开口问玩家一次"
    assert len(seen["status"]) == 1, "状态要刷一次，前端才看得到"

    _step(service, snapshot)
    _step(service, snapshot)
    assert len(logger.warnings) == 1, "同一处卡住只报一次，别刷屏"
    assert len(seen["host"]) == 1


@pytest.mark.unit
def test_stall_never_parks_autoplay(monkeypatch: pytest.MonkeyPatch) -> None:
    """卡住只报告，**绝不动 autoplay 意图** —— 玩家没喊停就不许自己停。"""
    service, _ = _service()
    _prepare(monkeypatch, service)
    service._state.autoplay_state = "running"
    snapshot = _stuck_snapshot()

    _step(service, snapshot)
    service._state.stuck_since -= 30
    _step(service, snapshot)

    assert service._state.autoplay_state == "running"


@pytest.mark.unit
def test_host_push_respects_the_digest_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """「尖塔速报」关掉时不许找本体说话（日志与状态照旧）。"""
    service, logger = _service()
    seen = _prepare(monkeypatch, service)
    service._state.host_digest_enabled = False
    snapshot = _stuck_snapshot()

    _step(service, snapshot)
    service._state.stuck_since -= 30
    _step(service, snapshot)

    assert len(logger.warnings) == 1
    assert seen["host"] == []


@pytest.mark.unit
def test_moving_to_another_stall_rearms_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """换了个界面卡住（指纹变了）要重新计时并重新报一次。"""
    service, logger = _service()
    _prepare(monkeypatch, service)

    _step(service, _stuck_snapshot())
    service._state.stuck_since -= 30
    _step(service, _stuck_snapshot())
    assert len(logger.warnings) == 1

    other = _stuck_snapshot(unknown="MegaCrit.Sts2.Core.Nodes.Screens.SomeOtherScreen")
    _step(service, other)
    assert len(logger.warnings) == 1, "刚换界面，还没到 20 秒"
    service._state.stuck_since -= 30
    _step(service, other)
    assert len(logger.warnings) == 2


@pytest.mark.unit
def test_no_report_when_the_game_is_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """游戏没开时快照是空的 —— "没有可执行动作"是必然的，别误报成"我卡住了"去问玩家。"""
    service, logger = _service()
    seen = _prepare(monkeypatch, service)
    service._state.transport_state = "error"
    snapshot = _stuck_snapshot()

    _step(service, snapshot)
    service._state.stuck_since -= 30
    _step(service, snapshot)

    assert logger.warnings == []
    assert seen["host"] == []
    assert service._state.stuck_reason == ""


@pytest.mark.unit
def test_no_report_without_screen_information(monkeypatch: pytest.MonkeyPatch) -> None:
    """连界面都不知道就没什么可报的（这种情况由 last_error 表达）。"""
    service, logger = _service()
    _prepare(monkeypatch, service)

    _step(service, {"raw_state": {}})
    service._state.stuck_since -= 30
    _step(service, {"raw_state": {}})

    assert logger.warnings == []


@pytest.mark.unit
def test_successful_step_clears_the_stall(monkeypatch: pytest.MonkeyPatch) -> None:
    """一旦走通一步，卡住痕迹要清掉（否则下一次卡住会被当成同一次而不报）。"""
    service, _ = _service()
    _prepare(monkeypatch, service)
    snapshot = _stuck_snapshot()

    _step(service, snapshot)
    service._state.stuck_since -= 30
    _step(service, snapshot)
    assert service._state.stuck_reason

    async def _ok(operation: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok"}

    monkeypatch.setattr(service, "execute_operation", _ok)
    moving = {"screen": "MAP", "agent_operation": {"action_type": "choose_map_node", "kwargs": {"option_index": 0}}}
    result = _step(service, moving)

    assert result["status"] == "ok"
    assert service._state.stuck_reason == ""
    assert service._state.stuck_signature == ""
    assert service._state.stuck_reported is False


# ---- 第二道闸：动作发出去了、mod 也回 ok，但局面没变（动作其实没生效） ----------------------


def _moving_snapshot(signature: str = "sig-1", *, screen: str = "EVENT") -> dict[str, Any]:
    return {
        "screen": screen,
        "state_signature": signature,
        "agent_operation": {"action_type": "choose_event_option", "kwargs": {"option_index": 0}},
    }


def _succeed_always(monkeypatch: pytest.MonkeyPatch, service: STS2AutoplayService) -> None:
    async def _ok(operation: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok"}

    monkeypatch.setattr(service, "execute_operation", _ok)


@pytest.mark.unit
def test_repeated_action_without_state_change_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """实盘回归：水晶球事件屏上 `choose_event_option` 重复 10 次而局面一字未变（step 83→93）。"""
    service, logger = _service()
    seen = _prepare(monkeypatch, service)
    _succeed_always(monkeypatch, service)
    service._state.autoplay_state = "running"

    for _ in range(service.NO_PROGRESS_REPEAT_LIMIT):
        _step(service, _moving_snapshot())

    assert len(logger.warnings) == 1, logger.warnings
    assert "无进展" in logger.warnings[0]
    assert "choose_event_option" in logger.warnings[0]
    assert "choose_event_option" in service._state.stuck_reason
    assert seen["host"], "要让本体开口问玩家一次"
    assert service._state.autoplay_state == "running", "只报告，不自己把自动玩停掉"


@pytest.mark.unit
def test_no_progress_report_is_not_repeated(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一处"没生效"只报一次，别每步刷屏。"""
    service, logger = _service()
    _prepare(monkeypatch, service)
    _succeed_always(monkeypatch, service)

    for _ in range(service.NO_PROGRESS_REPEAT_LIMIT * 3):
        _step(service, _moving_snapshot())

    assert len(logger.warnings) == 1


@pytest.mark.unit
def test_state_change_resets_the_no_progress_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """局面指纹变了（动作真生效了）就重新计数，绝不错杀正常推进。"""
    service, logger = _service()
    _prepare(monkeypatch, service)
    _succeed_always(monkeypatch, service)

    for _ in range(service.NO_PROGRESS_REPEAT_LIMIT - 1):
        _step(service, _moving_snapshot("sig-1"))
    for _ in range(service.NO_PROGRESS_REPEAT_LIMIT - 1):
        _step(service, _moving_snapshot("sig-2"))

    assert logger.warnings == []


@pytest.mark.unit
def test_different_actions_do_not_accumulate(monkeypatch: pytest.MonkeyPatch) -> None:
    """换了动作也重新计数（例如从"选事件选项"变成"选路"）。"""
    service, logger = _service()
    _prepare(monkeypatch, service)
    _succeed_always(monkeypatch, service)

    for index in range(service.NO_PROGRESS_REPEAT_LIMIT - 1):
        snapshot = _moving_snapshot("sig-1")
        snapshot["agent_operation"] = {"action_type": "choose_event_option", "kwargs": {"option_index": index}}
        _step(service, snapshot)

    assert logger.warnings == []
